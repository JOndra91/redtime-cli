#!/usr/bin/python3

import appdirs
import base64
import calendar
import click
import colored as colored_lib
import json
import itertools
import pathlib
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from functools import lru_cache
from fuzzywuzzy import process as fw_process
from pyfscache import FSCache
from redminelib import Redmine
from redminelib.packages import requests as redmine_requests
from redminelib.exceptions import ResourceNotFoundError, ValidationError

redmine_requests.packages.urllib3.disable_warnings()

# Redmine connection is established lazily.
redmine = Redmine('http://redmine')

filecache = FSCache(pathlib.Path(tempfile.gettempdir()) / 'redtime.cache', minutes=5)


class FakeColored():
    @staticmethod
    def fg(*args):
        return ''

    @staticmethod
    def attr(*args):
        return ''


class ProjectType(click.ParamType):
    name = 'project'

    def convert(self, value, param, ctx):
        try:
            value = int(value.split('#')[-1])
            return get_project(value) if value else None
        except ValueError:
            self.fail('Project id is not a number', param, ctx)
        except ResourceNotFoundError:
            self.fail('Project not found', param, ctx)


class IssueType(click.ParamType):
    name = 'issue'

    def convert(self, value, param, ctx):
        try:
            value = int(value.split('#')[-1])
            return get_issue(value) if value else None
        except ValueError:
            self.fail('Issue id is not a number', param, ctx)
        except ResourceNotFoundError:
            self.fail('Issue not found', param, ctx)


class TimeEntryType(click.ParamType):
    name = 'time_entry'

    def convert(self, value, param, ctx):
        try:
            value = int(value.split('#')[-1])
            return redmine.time_entry.get(value) if value else None
        except ValueError:
            self.fail('Time entry id is not a number', param, ctx)
        except ResourceNotFoundError:
            self.fail('Time entry not found', param, ctx)


class ActivityType(click.ParamType):
    name = 'activity'

    def convert(self, value, param, ctx):
        try:
            try:
                return _activities(id=int(int(value.split('#')[-1])))
            except ValueError:
                return _activities(name=value.lower())
        except KeyError:
            self.fail('Activity not found', param, ctx)


class DateType(click.ParamType):
    name = 'date'

    def __init__(self, formats):
        self.formats = formats

    def convert(self, value, param, ctx):

        if isinstance(value, date):
            return value

        for fmt in self.formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass

        self.fail('Not a date format', param, ctx)


DATE = DateType([
    '%d-%m-%Y',
    '%d/%m/%Y',
    '%d.%m.%Y',
    '%Y-%m-%d',
    '%Y/%m/%d',
    '%Y.%m.%d',
])


today = datetime.today().date()
month_first_day = today.replace(day=1)
month_last_day = today.replace(
    day=calendar.monthrange(today.year, today.month)[1])


class Password:

    base16_chars = list(b"0123456789ABCDEF")

    base16_shuffled_chars = base16_chars.copy()
    random.shuffle(base16_shuffled_chars, lambda: 0.42)

    @classmethod
    def encrypt(cls, password):
        encoded = base64.b16encode(password.encode('utf-8'))
        tr = dict(zip(cls.base16_chars, cls.base16_shuffled_chars))

        return bytes(map(lambda b: tr[b], encoded)).decode('utf-8')

    @classmethod
    def decrypt(cls, password):
        encoded = password.encode('utf-8')
        tr = dict(zip(cls.base16_shuffled_chars, cls.base16_chars))

        return base64.b16decode(bytes(map(lambda b: tr[b], encoded))).decode('utf-8')


def date_range(start, end):
    if end is None:
        end = start

    date = start
    while date <= end:
        yield date
        date += timedelta(days=1)


@lru_cache()
def get_project(id):
    return redmine.project.get(id)


@lru_cache()
def get_issue(id):
    return redmine.issue.get(id)


@lru_cache()
def _current_user():
    return redmine.user.get('current', include='memberships')


@lru_cache()
def _activities(name=None, id=None, fuzzy=None, threshold=80):

    if not hasattr(_activities, 'my_cache') is None:
        activities = redmine.enumeration.filter(resource='time_entry_activities')
        cache = {
            'name': {},
            'id': {},
            'values': []
        }
        for activity in activities:
            cache['values'].append(activity)
            cache['name'][activity.name.lower()] = activity
            cache['id'][activity.id] = activity

        _activities.my_cache = cache

    cache = _activities.my_cache

    if name:
        return cache['name'][name]
    elif id:
        return cache['id'][id]
    elif fuzzy:
        found = fw_process.extract(fuzzy, cache['values'])
        return [fst for (fst, snd) in found if snd >= threshold]
    else:
        return cache['values']


def _id_match(resource_list, num_prefix):
    try:
        prefix = str(int(num_prefix))
        return sorted(
            [res for res in resource_list if str(res.id).startswith(prefix)],
            key=lambda res: res.id
        )
    except:
        return []


@click.group()
@click.pass_context
def cli(ctx=None):
    if not redmine_ok and ctx.invoked_subcommand != 'configure':
        print(
            "Check your connection to Redmine or reconfigure using "
            "'configure' sub-command.", file=sys.stderr)
        sys.exit(1)


@cli.command()
@click.argument('project', type=ProjectType(), required=False)
@click.argument('issue', type=IssueType(), required=False)
@click.argument('activity', type=ActivityType(), required=True)
@click.argument('hours', type=click.FLOAT, required=True)
@click.argument('description', required=True)
@click.option('--yesterday', 'date', type=DATE,
    flag_value=date.today() - timedelta(days=1),
    help='Change date of log entry to yesterday')
@click.option('--date', type=DATE, default=date.today(),
    help='Change date of log entry')
@click.option('--until-date', type=DATE,
    help='Repeat log entry until given date (inclusive)')
@click.option('--weekdays', type=click.BOOL, default=False, flag_value=True,
    help='Enable weekend logging with --until-date option')
def log(project, issue, activity, hours, description, date, until_date, weekdays):
    """Create new time entry"""

    skip_weekdays = not weekdays and until_date is not None
    for entry_date in date_range(date, until_date):
        if skip_weekdays and entry_date.isoweekday() > 5:
            continue

        entry = redmine.time_entry.create(
            project_id=project.id if project else None,
            issue_id=issue.id if issue else None,
            spent_on=entry_date,
            hours=hours,
            activity_id=activity.id,
            comments=description)

        print("Log created: {} - #{}".format(entry_date, entry))


@cli.command()
@click.argument('name', required=False)
@click.option('--name-id', 'fmt', flag_value='{name}: {id}', default=True)
@click.option('--id', 'fmt', flag_value='{id}')
@click.option('--name', 'fmt', flag_value='{name}')
@click.option('--one', 'one', flag_value=True)
@click.option('--threshold', 'threshold', type=click.FLOAT, default=80)
def projects(name, fmt, one, threshold):
    """Show projects"""
    projects = _projects(name, threshold)
    if one:
        projects = projects[:1]

    for project in projects:
        print(fmt.format_map(dict(project)))


@cli.command()
@click.argument('time_entry', type=TimeEntryType())
@click.option('--rm', 'action', flag_value='remove',
    help='Remove the log entry')
def log_entry(time_entry, action):
    """Modifies time entry"""
    if action == 'remove':
        time_entry.delete()
        print("Log removed: #{}".format(time_entry))
    else:
        print("No action specified", file=sys.stderr)
        sys.exit(1)



@filecache
def _all_projects():
    return list(redmine.project.all())


def _projects(name=None, threshold=80, projects=None):
    if projects is None:
        projects = _all_projects()

    if name:
        found = fw_process.extract(name, projects)
        projects = [fst for (fst, snd) in found if snd >= threshold]
    else:
        projects = sorted(projects, key=lambda x: x.name)

    return projects

@cli.command()
@click.argument('subject', required=False)
@click.option('--subject-id', 'fmt', flag_value='{subject}: {id}', default=True)
@click.option('--id', 'fmt', flag_value='{id}')
@click.option('--subject', 'fmt', flag_value='{subject}')
@click.option('--one', 'one', flag_value=True)
@click.option('--threshold', 'threshold', type=click.FLOAT, default=80)
def issues(subject, fmt, one, threshold):
    """Show issues"""
    issues = _issues(subject, threshold)
    if one:
        issues = issues[:1]

    for issue in issues:
        print(fmt.format_map(dict(issue)))


@filecache
def _all_issues():
    return list(redmine.issue.filter(status_id='open'))


def _issues(subject=None, threshold=80, issues=None):
    if issues is None:
        issues = _all_issues()

    if subject:
        found = fw_process.extract(subject, issues)
        issues = [fst for (fst, snd) in found if snd >= threshold]
    else:
        issues = sorted(issues, key=lambda x: x.subject)

    return issues


@cli.command()
@click.argument('name', required=False)
@click.option('--name-id', 'fmt', flag_value='{name}: {id}', default=True)
@click.option('--id', 'fmt', flag_value='{id}')
@click.option('--name', 'fmt', flag_value='{name}')
def activities(name, fmt=True):
    """Show activities"""
    if name:
        print(fmt.format_map(dict(activities(name=name.lower()))))
    else:
        for activity in _activities():
            print(fmt.format_map(dict(activity)))


@cli.command()
@click.option('--api-url', 'api_url', required=True, prompt=True)
@click.option('--api-key', 'api_key')
@click.option('--username', 'username')
@click.option('--password', 'password')
@click.option('--ask-password', 'ask_password', is_flag=True)
def configure(api_url, api_key, username, password, ask_password):
    """Configure redtime utility"""

    cfg = {
        'api_url': api_url
    }

    if ask_password or password or username:
        if username is None:
            username = click.prompt('Username')
        if password is None:
            password = click.prompt('Password', hide_input=True)

        cfg['username'] = username
        cfg['password'] = Password.encrypt(password)
    else:
        if api_key is None:
            api_key = click.prompt('Api key')
        cfg['api_key'] = Password.encrypt(api_key)

    cfg_dir.exists() or cfg_dir.mkdir(parents=True)
    with open(cfg_file, 'w') as fd:
        json.dump(cfg, fd)


@cli.command()
@click.option('--from-date', 'from_date', type=DATE, default=month_first_day)
@click.option('--to-date', 'to_date', type=DATE, default=today)
@click.option('--limit', 'limit', type=click.INT)
@click.option('--offset', 'offset', type=click.INT)
def overview(**kwargs):
    """Show time entry overview"""
    def fill_blanks(from_date, to_date):
        delta = to_date - from_date
        for dt in range(1, delta.days):
            print_entry(type('FakeTimeEntry', (), {
                'id': None,
                'spent_on': from_date + timedelta(days=dt),
                'hours': 0,
                'comments': '-----',
            }))

    def print_entry(entry):
        cdate = 'yellow' if entry.spent_on.isoweekday() > 5 else 'green'
        cfill = 'white' if entry.id else 'grey_35'

        def show_project(project):
            return "{name} #{id}".format_map(project)

        def show_issue(issue):
            issue = get_issue(issue['id'])
            return "{subject} #{id}".format_map(issue)

        def show_activity(activity):
            return f"- {activity['name'].lower()} #{activity['id']}"

        if hasattr(entry, 'issue'):
            if hasattr(entry, 'project'):
                slash = ' / '
            else:
                '/ '
        else:
            slash = ''

        print("{cdate}{date}{reset}: ({cfill}{hours:05.2f}{reset}) [{project}{slash}{issue}] "
              "{cfill}{comment}{centry}{entry}{reset} {cactivity}{activity}{reset}".format(
            cdate=colored.fg(cdate),
            date=entry.spent_on,
            hours=entry.hours,
            project=show_project(entry.project) if hasattr(entry, 'project') else '',
            slash=slash,
            issue=show_issue(entry.issue) if hasattr(entry, 'issue') else '',
            comment=entry.comments,
            entry=f" #{entry.id}" if entry.id else '',
            centry=colored.fg('gold_1'),
            cactivity=colored.fg('turquoise_4'),
            activity=show_activity(entry.activity) if hasattr(entry, 'activity') else '',
            cfill=colored.fg(cfill),
            reset=colored.attr('reset')))

    last_date = kwargs['from_date'] + timedelta(days=-1)
    for entry in reversed(redmine.time_entry.filter(user_id=_current_user().id, **kwargs)):
        fill_blanks(last_date, entry.spent_on)
        last_date = entry.spent_on
        print_entry(entry)

    fill_blanks(last_date, kwargs['to_date'] + timedelta(days=1))


@cli.command()
@click.argument('args', nargs=-1)
@click.option('--options', flag_value=True, help="Show completion for options")
@click.option('--nth', type=int, help="Complete nth argument")
def complete(args, options, nth):
    """Show completion options for redtime command"""
    if not args:
        if options:
            return
        result=[f"{c.name}:{c.short_help}" for c in cli.commands.values()]
    else:
        cmd = cli.commands.get(args[0])
        if not cmd:
            return

        if options:
            result = [f"{o}:{p.help}" for p in cmd.params for o in p.opts if isinstance(p, click.core.Option)]
        else:
            if nth is None:
                sys.exit(1)  # It's too difficult

            params = list([p for p in cmd.params])

            def _complete(param, value):
                if param is None:
                    return None
                type_name = param.type.name

                if type_name == "project":
                    name_attr = 'name'
                    projects = list(_projects())
                    result_id = _id_match(projects, value) if value is not None else []
                    result_name = _projects(value, projects=projects)
                elif type_name == "issue":
                    name_attr = 'subject'
                    issues = list(_issues())
                    result_id = _id_match(issues, value) if value is not None else []
                    result_name = _issues(value, issues=issues)
                elif type_name == "activity":
                    name_attr = 'name'
                    activities = _activities()
                    result_id = _id_match(activities, value) if value is not None else []
                    result_name = _activities(fuzzy=value)
                else:
                    return None

                return itertools.chain(
                    [f"{id}:{name}" for (id, name) in
                        ((r.id, getattr(r, name_attr)) for r in result_id)],
                    [f"{name} #{id}:{name}" for (id, name) in
                        ((r.id, getattr(r, name_attr)) for r in result_name)]
                )


            result = _complete(
                params[nth-2],
                args[nth-1] if len(args) >= nth else None)

    if result is None:
        sys.exit(1)

    print('\n'.join(list(result)))


if __name__ == "__main__":

    colored = colored_lib if sys.stdout.isatty() else FakeColored()

    cfg_dir = pathlib.Path(appdirs.user_config_dir('redtime-cli'))
    cfg_file = cfg_dir / 'config.json'
    try:
        with open(cfg_file) as fd:
            cfg = json.load(fd)

        if 'api_key' in cfg:
            credentials = { 'key': Password.decrypt(cfg['api_key'])}
        else:
            credentials = {
                'username': cfg['username'],
                'password': Password.decrypt(cfg['password'])
            }


        redmine = Redmine(
            cfg['api_url'], **credentials, requests={'verify': False})

        redmine_ok = True
    except Exception as e:
        redmine_ok = False

    try:
        cli()
    except ValidationError as e:
        print("Error: {}".format(e))
        sys.exit(1)
