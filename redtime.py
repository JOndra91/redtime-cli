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

filecache = FSCache(pathlib.Path(tempfile.gettempdir()) / 'redtime.cache', minutes=15)


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
            value = int(value.split(':')[-1])
            return get_project(value) if value else None
        except ValueError:
            self.fail('Project id is not a number', param, ctx)
        except ResourceNotFoundError:
            self.fail('Project not found', param, ctx)


class IssueType(click.ParamType):
    name = 'issue'

    def convert(self, value, param, ctx):
        try:
            value = int(value.split(':')[-1])
            return get_issue(value) if value else None
        except ValueError:
            self.fail('Issue id is not a number', param, ctx)
        except ResourceNotFoundError:
            self.fail('Issue not found', param, ctx)


class TimeEntryType(click.ParamType):
    name = 'time_entry'

    def convert(self, value, param, ctx):
        try:
            value = int(value.split(':')[-1])
            return redmine.time_entry.get(value) if value else None
        except ValueError:
            self.fail('Time entry id is not a number', param, ctx)
        except ResourceNotFoundError:
            self.fail('Time entry not found', param, ctx)


class ActivityType(click.ParamType):
    name = 'activity'

    def convert(self, value, param, ctx):
        try:
            if 'project' in ctx.params:
                activities = ctx.params['project'].time_entry_activities
            elif 'issue' in ctx.params:
                activities = get_project(ctx.params['issue'].project.id).time_entry_activities
            else:
                activities = _get_activities()

            try:
                return _with_activities(activities, id=int(int(value.split(':')[-1])))
            except ValueError:
                return _with_activities(activities, name=value.lower())
        except KeyError:
            self.fail('Activity not found', param, ctx)


class DateType(click.ParamType):
    name = 'date'

    def __init__(self, formats):
        self.formats = formats

    def convert(self, value, param, ctx):

        if isinstance(value, date):
            return value

        if value.isnumeric():
            return datetime.today().date().replace(day=int(value))

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
    return redmine.project.get(id, include='time_entry_activities')


@lru_cache()
def get_issue(id):
    return redmine.issue.get(id)


@lru_cache()
def _current_user():
    return redmine.user.get('current', include='memberships')


def _with_activities(activities, name=None, id=None, fuzzy=None, threshold=80):
    indexed = {
        'name': {},
        'id': {},
        'values': []
    }

    for activity in activities:
        indexed['values'].append(activity)
        indexed['name'][activity['name'].lower()] = activity
        indexed['id'][activity['id']] = activity

    if name:
        return indexed['name'][name]
    elif id:
        return indexed['id'][id]
    elif fuzzy:
        found = fw_process.extract(fuzzy, indexed['values'])
        return [fst for (fst, snd) in found if snd >= threshold]
    else:
        return indexed['values']


@lru_cache()
def _get_activities():
    def mk_dict(activity):
        return {
            "id": activity.id,
            "name": activity.name,
        }
    return list(map(
        mk_dict,
        redmine.enumeration.filter(resource='time_entry_activities')))


@lru_cache()
def _activities(name=None, id=None, fuzzy=None, threshold=80):
    return _with_activities(_get_activities(), name, id, fuzzy, threshold)


def _id_match(resource_list, num_prefix):
    if num_prefix is None:
        return []

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
@click.option('--yesterday', '-y', 'date', type=DATE,
    flag_value=date.today() - timedelta(days=1),
    help='Change date of log entry to yesterday')
@click.option('--date', type=DATE, default=date.today(),
    help='Change date of log entry')
@click.option('--until-date', type=DATE,
    help='Repeat log entry until given date (inclusive)')
@click.option('--weekdays', type=click.BOOL, default=False, flag_value=True,
    help='Allow weekend logging')
@click.option('--max-day-hours', type=click.INT, default=8,
    help='Max hours per day (entries exceeding this limit will be ignored)')
def log(project, issue, activity, hours, description, date, until_date, weekdays, max_day_hours):
    """Create new time entry"""

    skip_weekdays = not weekdays and until_date is not None
    for entry_date in date_range(date, until_date):

        if skip_weekdays and entry_date.isoweekday() > 5:
            continue

        entries = redmine.time_entry.filter(
            user_id=_current_user().id,
            from_date=entry_date,
            to_date=entry_date)

        already_logged = sum([e.hours for e in entries])
        total_hours = hours + already_logged

        if total_hours > max_day_hours:
            print("{fg}Log skipped: {}{reset} - hours ({}) > max hours ({})".format(
                entry_date, total_hours, max_day_hours,
                fg=colored.fg('yellow'),
                reset=colored.attr('reset')))
            continue

        entry = redmine.time_entry.create(
            project_id=project.id if project else None,
            issue_id=issue.id if issue else None,
            spent_on=entry_date,
            hours=hours,
            activity_id=activity['id'],
            comments=description)

        print("{fg}Log created: {}{reset} - #{}".format(
            entry_date, entry,
            fg=colored.fg('green'),
            reset=colored.attr('reset')))


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
@click.argument('time_entries', type=TimeEntryType(), nargs=-1)
@click.option('--rm', 'action', flag_value='remove',
    help='Remove the log entry')
@click.option('--project', 'project', type=ProjectType())
@click.option('--issue', 'issue', type=IssueType())
@click.option('--activity', 'activity', type=ActivityType())
def log_entry(time_entries, action, project, issue, activity):
    """Modifies time entry"""
    if action == 'remove':
        for time_entry in time_entries:
            time_entry.delete()
            print("Log removed: #{}".format(time_entry))
    elif any((project, issue, activity)):
        for time_entry in time_entries:
            if project:
                time_entry.project_id = project.id
            if issue:
                time_entry.issue_id = issue.id
            if activity:
                time_entry.activity_id = activity['id']

            time_entry.save()
            print("Log updated: #{}".format(time_entry))
    else:
        print("No action specified", file=sys.stderr)
        sys.exit(1)


@lru_cache()
def _all_projects():
    return list(redmine.project.all())


def _projects(name=None, threshold=80, projects=None):
    if projects is None:
        projects = _all_projects()

    if name:
        found = fw_process.extract(name, projects)
        projects = [fst for (fst, snd) in found if snd >= threshold]

    return projects


@cli.command()
@click.argument('subject', required=False)
@click.option('--subject-id', 'fmt', flag_value='{subject}: {id}', default=True)
@click.option('--id', 'fmt', flag_value='{id}')
@click.option('--subject', 'fmt', flag_value='{subject}')
@click.option('--one', 'one', flag_value=True)
def issues(subject, fmt, one):
    """Show issues"""
    issues = _issues(subject)
    if one:
        issues = issues[:1]

    for issue in issues:
        print(fmt.format_map(dict(issue)))


@filecache
def _issues(subject=None, project_id=None):
    kwargs = {
        'status_id': 'open',
        'project_id': project_id,
        'subject': '~{}'.format(subject) if subject else None,
        'limit': 50,
    }

    return list(redmine.issue.filter(**kwargs))


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
@click.option('--to-date', 'to_date', type=DATE, default=month_last_day)
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
            return "{name}:{id}".format_map(project)

        def show_issue(issue):
            issue = get_issue(issue['id'])
            return "{subject}:{id}".format_map(issue)

        def show_activity(activity):
            return f"- {activity['name'].lower()}:{activity['id']}"

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
            entry=f":{entry.id}" if entry.id else '',
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
@click.pass_context
@click.argument('args', nargs=-1)
@click.option('--options', flag_value=True, help="Show completion for options")
@click.option('--nth', type=int, help="Complete nth argument")
def complete(ctx, args, options, nth):
    """Show completion options for redtime command"""
    if not args:
        if options:
            return
        result=[f"{c.name}:{c.short_help}" for c in cli.commands.values()]
    else:
        cmd = cli.commands.get(args[0])
        if not cmd:
            return

        cmd_params = list([p for p in cmd.params if isinstance(p, click.core.Argument)])
        cmd_param_map = {p.name:p for p in cmd_params}
        cmd_options = {o:p for p in cmd.params for o in p.opts if isinstance(p, click.core.Option)}

        if options:
            result = []
            for opt in set(cmd_options.values()):
                opt_desc = []
                if opt.help:
                    opt_desc.append(opt.help)

                if opt.default is not None and opt.show_default:
                    opt_desc.append("(default: {opt.default})")

                opt_desc = ' '.join(opt_desc)

                for opt_name in set(opt.opts):
                    result.append(f"{opt_name}:{opt_desc}")
                if opt.secondary_opts:
                    raise NotImplementedError(
                        "Secondary options (e.g. --flag/--no-flag) are not supported")
        else:
            if nth is None:
                sys.exit(1)  # It's too difficult

            def _convert_param(key, value):
                param = cmd_param_map[key]
                return param.type.convert(value, param, ctx)

            def _complete_param(param, value_map):
                if param is None:
                    return None

                param_name = param.name
                param_type = param.type.name

                value = value_map.get(param_name)

                try:
                    project = _convert_param('project', value_map.get('project'))
                except:
                    pass

                result_id = []
                result_name = []
                result_other = []

                if param_type == 'project':
                    name_attr = 'name'
                    projects = list(_projects())
                    result_id = _id_match(projects, value)
                    result_name = _projects(value, projects=projects)
                elif param_type == 'issue':
                    name_attr = 'subject'
                    result_id = _id_match(_issues(project_id=project.id), value)
                    result_name = _issues(subject=value, project_id=project.id)
                elif param_type == 'activity':
                    name_attr = 'name'
                    activities = project.time_entry_activities
                    for activity in activities:
                        result_other.append(type('Activity', (object,), activity))
                elif param_name == 'hours':
                    return [f"{hours}:hours" for hours in [2, 4, 6, 8]]
                elif param_name == 'description':
                    return ["...:description"]
                elif param_type == 'date':
                    return ["{:%Y-%m-%d}:{}".format(datetime.today(), param_name)]
                else:
                    return None

                result = set(itertools.chain(result_id, result_name, result_other))

                return itertools.chain(
                    [f"{name}\\:{id}:{name}" for (id, name) in
                        ((r.id, getattr(r, name_attr)) for r in result)]
                )

            def _get_or_none(indexable, index):
                try:
                    return indexable[index]
                except IndexError:
                    return None

            skip = 0
            nth_param = 0
            arg_map = {}
            for arg in args[1:nth - 1]:
                if skip > 0:
                    skip -= 1
                elif arg.startswith('-'):
                    skip = cmd_options[args].nargs
                    nth -= skip
                else:
                    arg_map[cmd_params[nth_param].name] = arg
                    nth_param += 1

            nth -= 2  # Ignore the command and subcommand
            if nth < 0 or nth >= len(cmd_params):
                sys.exit(1)

            # TODO: Handle options
            result = _complete_param(cmd_params[nth], arg_map)

    if not result:
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
