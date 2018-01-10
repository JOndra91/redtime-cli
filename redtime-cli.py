#!/usr/bin/python3

import appdirs
import calendar
import click
import colored
import json
import pathlib
import sys
from datetime import date, datetime, timedelta
from redminelib import Redmine
from functools import lru_cache
from redminelib.packages import requests as redmine_requests
from redminelib.exceptions import ResourceNotFoundError, ValidationError

redmine_requests.urllib3.disable_warnings()

# Redmine connection is established lazily.
redmine = Redmine('http://redmine')


class ProjectType(click.ParamType):
    name = 'project'

    def convert(self, value, param, ctx):
        try:
            value = int(value)
            return get_project(value) if value else None
        except ValueError:
            self.fail('Project is not a number', param, ctx)
        except ResourceNotFoundError:
            self.fail('Project not found', param, ctx)


class IssueType(click.ParamType):
    name = 'issue'

    def convert(self, value, param, ctx):
        try:
            value = int(value)
            return get_issue(value) if value else None
        except ValueError:
            self.fail('Issue is not a number', param, ctx)
        except ResourceNotFoundError:
            self.fail('Issue not found', param, ctx)


class ActivityType(click.ParamType):
    name = 'activity'

    def convert(self, value, param, ctx):
        try:
            try:
                return activity_by_id[int(value)]
            except ValueError:
                return activity_by_name[value.lower()]
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
    day=calendar.monthrange(today.year, today.day)[1])


@lru_cache()
def get_project(id):
    return redmine.project.get(id)


@lru_cache()
def get_issue(id):
    return redmine.issue.get(id)


@click.group()
@click.pass_context
def cli(ctx):
    if not redmine_ok and ctx.invoked_subcommand != 'configure':
        print(
            "Check your connection to Redmine or reconfigure using "
            "'configure' sub-command", file=sys.stderr)
        sys.exit(1)

# project issue activity hours comment [date]
@cli.command()
@click.argument('project', type=ProjectType(), required=False)
@click.argument('issue', type=IssueType(), required=False)
@click.argument('activity', type=ActivityType(), required=True)
@click.argument('hours', type=click.FLOAT, required=True)
@click.argument('description', required=True)
@click.option('--date', type=DATE, default=date.today())
def log(project, issue, activity, hours, description, date):
    entry = redmine.time_entry.create(
        project_id=project.id if project else None,
        issue_id=issue.id if issue else None,
        spent_on=date,
        hours=hours,
        activity_id=activity.id,
        comments=description)

    print("Issue created: #{}".format(entry))


@cli.command()
def projects():
    pass


@cli.command()
def issues():
    pass


@cli.command()
@click.option('--api-url', 'api_url', required=True, prompt=True)
@click.option('--api-key', 'api_key', required=True, prompt=True)
def configure(**kwargs):
    cfg_dir.exists() or cfg_dir.mkdir(parents=True)
    with open(cfg_file, 'w') as fd:
        json.dump(kwargs, fd)


@cli.command()
@click.option('--from-date', 'from_date', type=DATE, default=month_first_day)
@click.option('--to-date', 'to_date', type=DATE, default=today)
@click.option('--limit', 'limit', type=click.INT)
@click.option('--offset', 'offset', type=click.INT)
def overview(**kwargs):
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
    for entry in reversed(redmine.time_entry.filter(user_id=current_user.id, **kwargs)):
        fill_blanks(last_date, entry.spent_on)
        last_date = entry.spent_on
        print_entry(entry)

    fill_blanks(last_date, kwargs['to_date'] + timedelta(days=1))


@cli.command()
@click.argument('name')
@click.option('--name-id', 'fmt', flag_value='{name}: {id}', default=True)
@click.option('--id', 'fmt', flag_value='{id}')
@click.option('--name', 'fmt', flag_value='{name}')
def activities(name, fmt):
    if name:
        print(fmt.format_map(dict(activity_by_name[name.lower()])))
    else:
        for activity in activities:
            print(fmt.format_map(dict(activity)))


if __name__ == "__main__":

    cfg_dir = pathlib.Path(appdirs.user_config_dir('redtime-cli'))
    cfg_file = cfg_dir / 'config.json'
    try:
        with open(cfg_file) as fd:
            cfg = json.load(fd)
        redmine = Redmine(
            cfg['api_url'], key=cfg['api_key'], requests={'verify': False})

        current_user = redmine.user.get('current', include='memberships')

        activities = redmine.enumeration.filter(resource='time_entry_activities')
        activity_by_name = {}
        activity_by_id = {}
        for activity in activities:
            activity_by_name[activity.name.lower()] = activity
            activity_by_id[activity.id] = activity
        redmine_ok = True
    except Exception as e:
        redmine_ok = False

    try:
        cli()
    except ValidationError as e:
        print("Error: {}".format(e))
        sys.exit(1)
