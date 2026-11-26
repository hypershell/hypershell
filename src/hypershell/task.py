# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Task-based operations."""


# Type annotations
from __future__ import annotations
from typing import List, Dict, Callable, IO, Tuple, Any, Optional, Type, Final, NamedTuple

# Standard libs
import os
import re
import sys
import csv
import json
import time
import functools
from datetime import timedelta, datetime, timezone
from dataclasses import dataclass
from shutil import copyfileobj

# External libs
import yaml
from rich.console import Console, Group
from rich.syntax import Syntax
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from cmdkit.app import Application, ApplicationGroup, exit_status
from cmdkit.cli import Interface, ArgumentError
from cmdkit.ansi import red, green, yellow, faint, COLOR_STDOUT
from sqlalchemy import Column, type_coerce, text, func, distinct, literal
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Query
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.sql.elements import BinaryExpression, ColumnElement
from sqlalchemy.types import Text as TEXT

# Internal libs
from hypershell.core.platform import default_path
from hypershell.core.config import config
from hypershell.core.exceptions import (handle_exception, handle_exception_silently, handle_broken_pipe,
                                        get_shared_exception_mapping, ReadingMixin)
from hypershell.core.logging import Logger, HOSTNAME
from hypershell.core.remote import SSHConnection
from hypershell.core.signal import exit_status_for_signal
from hypershell.core.types import smart_coerce, JSONData, to_json_type, ranged
from hypershell.core.config_schema import parse_setting, SettingError, advise, shown, read_value, Kind, FORMS
from hypershell.core.pretty_print import format_tag, format_json, format_bytes, format_source
from hypershell.core.tag import Tag, TagError, split_token, near_names, read_token, check_tag as check_tag_rules
from hypershell.data.core import Session
from hypershell.data.model import (Task, Source, Zone, JSON, CANCEL_STATUS, zone_name, zone_id, DEFAULT_ZONE_NAME,
                                   DEFAULT_ZONE_ID, TaskGroupError, TaskInputError, parse_group)
from hypershell.data import ensuredb, DATABASE_DIALECT
from hypershell.submit import read_tag_options, advise_one_task, input_zone

# Public interface
__all__ = ['TaskGroupApp',
           'TaskSubmitApp', 'TaskInfoApp', 'TaskWaitApp', 'TaskRunApp', 'TaskSearchApp', 'TaskUpdateApp']

# Initialize logger
log = Logger.with_name(__name__)


SUBMIT_PROGRAM = 'hs task submit'
SUBMIT_SYNOPSIS = f'{SUBMIT_PROGRAM} [-h] [-t TAG [TAG...]] [--zone NAME] -- ARGS...'
SUBMIT_USAGE = f"""\
Usage:
  {SUBMIT_SYNOPSIS}
  Submit individual task to database.\
"""

SUBMIT_HELP = f"""\
{SUBMIT_USAGE}

Arguments:
  ARGS...                Command-line arguments.

Options:
  -t, --tag    TAG...    Assign tags as `key:value`.
      --zone   NAME      Zone of the task; only clients of this zone run it.
  -h, --help             Show this message and exit.\
"""


class TaskSubmitApp(ReadingMixin, Application):
    """Submit task to database."""

    interface = Interface(SUBMIT_PROGRAM, SUBMIT_USAGE, SUBMIT_HELP)

    argv: List[str] = []
    interface.add_argument('argv', nargs='+')

    tags: Dict[str, JSONData] = {}
    taglist: List[str] = []
    interface.add_argument('-t', '--tag', nargs='+', dest='taglist')

    zone: Optional[str] = None
    interface.add_argument('--zone', default=None)

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskSubmitApp) -> None:
        """Submit task to database."""
        if self.zone is not None:
            try:
                self.zone = zone_name(self.zone)
            except ValueError as error:
                raise ArgumentError(str(error)) from error
        self.check_tags()
        ensuredb()
        zone = None if self.zone is None else Zone.ensure(self.zone)
        args = ' '.join(self.argv)
        task = Task.new(args=args, tag=self.tags, zone=zone)
        advise_one_task(args, self.tags)
        if (name := input_zone({**self.tags, **Task.split_argline(args)[1]})) is not None:
            Zone.ensure(name)  # Recorded as `--zone` records its zone
        Task.add(task)
        print(task.id)

    def check_tags(self: TaskSubmitApp) -> None:
        """Ensure valid tags, before the database is opened: a resource tag is read by its `task.*` entry."""
        try:
            self.tags = read_tag_options(self.taglist)
        except (ValueError, TypeError, TaskInputError) as error:
            raise ArgumentError(str(error)) from error


# Catch bad UUID before we touch the database
UUID_PATTERN: re.Pattern = re.compile(
    r'^[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12}$'
)


def check_uuid(value: str) -> None:
    """Check for valid UUID."""
    if not UUID_PATTERN.match(value):
        raise ArgumentError(f'Bad UUID: \'{value}\'')


INFO_PROGRAM = 'hs info'
INFO_SYNOPSIS = f'{INFO_PROGRAM} [-h] ID [-i] [--stdout | --stderr | --perf | -x FIELD] [-f FORMAT]'
INFO_USAGE = f"""\
Usage: 
  {INFO_SYNOPSIS}
  Get metadata and/or task outputs.\
"""

INFO_HELP = f"""\
{INFO_USAGE}

Arguments:
  ID                        Unique task UUID.

Options:
  -f, --format     FORMAT   Format task info ([normal], card, json, yaml).
      --json                Format task metadata as JSON.
      --yaml                Format task metadata as YAML.
  -x, --extract    FIELD    Print single field.
      --stdout              Print <stdout> from task.
      --stderr              Print <stderr> from task.
      --perf                Print captured resource metrics (CSV) from task.
  -i, --ignore-partitions   Suppress auto-union feature (SQLite only).
  -h, --help                Show this message and exit.\
"""


class TaskInfoApp(ReadingMixin, Application):
    """Get metadata/status/outputs of task."""

    interface = Interface(INFO_PROGRAM, INFO_USAGE, INFO_HELP)

    uuid: str
    interface.add_argument('uuid')

    print_stdout: bool = False
    print_stderr: bool = False
    print_perf: bool = False
    extract_field: str = None
    print_interface = interface.add_mutually_exclusive_group()
    print_interface.add_argument('--stdout', action='store_true', dest='print_stdout')
    print_interface.add_argument('--stderr', action='store_true', dest='print_stderr')
    print_interface.add_argument('--perf', action='store_true', dest='print_perf')
    print_interface.add_argument('-x', '--extract', default=None, choices=Task.columns, dest='extract_field')

    output_format: str = 'normal'
    output_formats: List[str] = ['normal', 'card', 'json', 'yaml']
    output_interface = interface.add_mutually_exclusive_group()
    output_interface.add_argument('-f', '--format', default=output_format, dest='output_format', choices=output_formats)
    output_interface.add_argument('--json', action='store_const', const='json', dest='output_format')
    output_interface.add_argument('--yaml', action='store_const', const='yaml', dest='output_format')

    ignore_partitions: bool = False
    interface.add_argument('-i', '--ignore-partitions', action='store_true', dest='ignore_partitions')

    exceptions = {
        Task.NotFound: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        BrokenPipeError: handle_broken_pipe,  # e.g. `hs info UUID --stdout | head`
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskInfoApp) -> None:
        """Get metadata/status/outputs of task."""
        ensuredb()
        check_uuid(self.uuid)
        if not self.ignore_partitions:
            auto_union_sqlite()
        if self.extract_field:
            self.print_field()
        elif self.print_stdout:
            self.print_file('outpath')
        elif self.print_stderr:
            self.print_file('errpath')
        elif self.print_perf:
            self.print_file('csvpath')
        elif self.output_format == 'normal':
            print_normal(self.task)
        elif self.output_format == 'card':
            console = card_console()
            console.print(render_card(self.task, console.width))
        else:
            self.print_formatted()

    def print_field(self: TaskInfoApp) -> None:
        """Print single field."""
        if self.extract_field == 'zone':
            print(resolve_zone(self.task.zone))
        elif self.extract_field != 'tag':
            print(json.dumps(self.task.to_json().get(self.extract_field)).strip('"'))
        elif self.output_format == 'normal':
            print(', '.join(f'{k}:{v}' if v else k for k, v in self.task.tag.items()))
        else:
            formatter = self.format_method[self.output_format]
            output = formatter(self.task.tag)
            if sys.stdout.isatty():
                output = Syntax(output, self.output_format, word_wrap=True,
                                theme=config.console.theme, background_color='default')
                Console().print(output)
            else:
                print(output, file=sys.stdout, flush=True)

    def print_formatted(self: TaskInfoApp) -> None:
        """Format and print task metadata to console."""
        formatter = self.format_method[self.output_format]
        data = self.task.to_json()  # NOTE: to_json() just means dict with converted value types
        data['zone'] = resolve_zone(self.task.zone)
        output = formatter(data)
        if sys.stdout.isatty():
            output = Syntax(output, self.output_format, word_wrap=True,
                            theme = config.console.theme, background_color = 'default')
            Console().print(output)
        else:
            print(output, file=sys.stdout, flush=True)

    def print_file(self: TaskInfoApp, filetype: str) -> None:
        """Print file contents, fetch from client if necessary."""
        remote_path, local_path, stream = self.remote_files[filetype]
        if remote_path is None:
            raise RuntimeError(f'"{filetype}" unavailable for task ({self.uuid})')
        if not os.path.exists(local_path) and self.task.client_host != HOSTNAME:
            self.copy_remote_files()
        with open(local_path, mode='r') as in_stream:
            copyfileobj(in_stream, stream)

    @functools.cached_property
    def format_method(self: TaskInfoApp) -> Dict[str, Callable[[dict], str]]:
        """Format data method."""
        return {
            'yaml': functools.partial(yaml.dump, indent=4, sort_keys=False),
            'json': functools.partial(json.dumps, indent=4),
        }

    @functools.cached_property
    def remote_files(self: TaskInfoApp) -> Dict[str, Tuple[str | None, str, IO]]:
        """Mapping of remote to local file paths with associated ."""
        return {
            'outpath': (self.task.outpath, self.outpath, sys.stdout),
            'errpath': (self.task.errpath, self.errpath, sys.stderr),
            'csvpath': (self.task.csvpath, self.csvpath, sys.stdout),
        }

    def copy_remote_files(self: TaskInfoApp) -> None:
        """Copy output and error files and to local host."""
        log.debug(f'Fetching remote files ({self.task.client_host})')
        with SSHConnection(self.task.client_host) as remote:
            for filetype, (remote_path, local_path, _) in self.remote_files.items():
                if remote_path:
                    remote.get_file(remote_path, local_path)

    @functools.cached_property
    def task(self: TaskInfoApp) -> Task:
        """Look up the task from the database."""
        return Task.from_id(self.uuid)

    @functools.cached_property
    def outpath(self: TaskInfoApp) -> str:
        """Local task output file path."""
        return os.path.join(default_path.lib, 'task', f'{self.task.id}.out')

    @functools.cached_property
    def errpath(self: TaskInfoApp) -> str:
        """Local task error file path."""
        return os.path.join(default_path.lib, 'task', f'{self.task.id}.err')

    @functools.cached_property
    def csvpath(self: TaskInfoApp) -> str:
        """Local task perf file path."""
        return os.path.join(default_path.lib, 'task', f'{self.task.id}.csv')


# Time to wait between database queries
DEFAULT_INTERVAL: Final[int] = 5


WAIT_PROGRAM = 'hs wait'
WAIT_SYNOPSIS = f'{WAIT_PROGRAM} [-h] ID [-n SEC] [--info [-f FORMAT] | --status | --return]'
WAIT_USAGE = f"""\
Usage: 
  {WAIT_SYNOPSIS}
  Wait for task to complete.\
"""

WAIT_HELP = f"""\
{WAIT_USAGE}

Arguments:
  ID                      Unique UUID.

Options:
  -n, --interval  SEC     Time to wait between polling; seconds, 30s, 5m or [D-]HH:MM:SS (default: {DEFAULT_INTERVAL}).
  -i, --info              Print info on task.
  -f, --format    FORMAT  Format task info ([normal], card, json, yaml).
      --json              Format info as JSON.
      --yaml              Format info as YAML.
  -s, --status            Print exit status for task.
  -r, --return            Use exit status from task.
  -h, --help              Show this message and exit.\
"""


class TaskWaitApp(ReadingMixin, Application):
    """Wait for task to complete."""

    interface = Interface(WAIT_PROGRAM, WAIT_USAGE, WAIT_HELP)

    uuid: str
    interface.add_argument('uuid')

    interval: int = DEFAULT_INTERVAL
    ranged(interface, '-n', '--interval', setting='option.interval', default=interval)

    print_info: bool = False
    print_status: bool = False
    return_status: bool = False
    print_interface = interface.add_mutually_exclusive_group()
    print_interface.add_argument('-i', '--info', action='store_true', dest='print_info')
    print_interface.add_argument('-s', '--status', action='store_true', dest='print_status')
    print_interface.add_argument('-r', '--return', action='store_true', dest='return_status')

    output_format: str = 'normal'
    output_formats: List[str] = ['normal', 'card', 'json', 'yaml']
    output_interface = interface.add_mutually_exclusive_group()
    output_interface.add_argument('-f', '--format', default=output_format,
                                  dest='output_format', choices=output_formats)
    output_interface.add_argument('--json', action='store_const', const='json', dest='output_format')
    output_interface.add_argument('--yaml', action='store_const', const='yaml', dest='output_format')

    class NonZeroStatus(Exception):
        """Exception holds non-zero exit status of returned task."""

    exceptions = {
        Task.NotFound: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        NonZeroStatus: handle_exception_silently,
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskWaitApp) -> None:
        """Wait for task to complete."""
        ensuredb()
        check_uuid(self.uuid)
        self.wait_task()
        if self.print_info:
            TaskInfoApp(uuid=self.uuid, output_format=self.output_format).run()
        elif self.print_status:
            TaskInfoApp(uuid=self.uuid, extract_field='exit_status').run()
        elif self.return_status:
            if status := Task.from_id(self.uuid).exit_status:
                raise self.NonZeroStatus(status)

    def wait_task(self: TaskWaitApp):
        """Wait for the task to complete."""
        log.info(f'Waiting on task ({self.uuid})')
        while True:
            task = Task.from_id(self.uuid, caching=False)
            if task.exit_status is None:
                log.trace(f'Waiting ({self.uuid})')
                time.sleep(self.interval)
                continue
            if task.exit_status != 0:
                log.warning(f'Non-zero exit status ({task.exit_status}) for task ({task.id})')
            log.info(f'Task completed ({task.completion_time})')
            break


RUN_PROGRAM = 'hs run'
RUN_SYNOPSIS = f'{RUN_PROGRAM} [-h] [-n SEC] [-t TAG [TAG...]] -- ARGS...'
RUN_USAGE = f"""\
Usage: 
  {RUN_SYNOPSIS}
  Submit individual task and wait for completion.\
"""

RUN_HELP = f"""\
{RUN_USAGE}

Arguments:
  ARGS                    Command-line arguments.

Options:
  -n, --interval  SEC     Time to wait between polling; seconds, 30s, 5m or [D-]HH:MM:SS (default: {DEFAULT_INTERVAL}).
  -t, --tag       TAG...  Assign tags as `key:value`.
  -h, --help              Show this message and exit.\
"""


class TaskRunApp(ReadingMixin, Application):
    """Submit task and wait for completion."""

    interface = Interface(RUN_PROGRAM, RUN_USAGE, RUN_HELP)

    argv: List[str] = []
    interface.add_argument('argv', nargs='+')

    interval: int = DEFAULT_INTERVAL
    ranged(interface, '-n', '--interval', setting='option.interval', default=interval)

    taglist: List[str] = []
    interface.add_argument('-t', '--tag', nargs='+', dest='taglist')

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskRunApp) -> None:
        """Submit task and wait for completion."""
        try:
            tags = read_tag_options(self.taglist)  # Before the database is opened: exit 2 writes nothing
        except (ValueError, TypeError, TaskInputError) as error:
            raise ArgumentError(str(error)) from error
        args = ' '.join(self.argv)
        try:
            _, inline_tags = Task.split_argline(args)  # The inline tags too, before the database is opened (exit 5)
        except TaskInputError as error:
            raise error.at('arguments') from error
        ensuredb()
        task = Task.new(args=args, tag=tags or None)
        advise_one_task(args, tags)
        if (name := input_zone({**tags, **inline_tags})) is not None:
            Zone.ensure(name)  # Recorded as `--zone` records its zone
        Task.add(task)
        TaskWaitApp(uuid=task.id, interval=self.interval).run()
        TaskInfoApp(uuid=task.id, print_stdout=True).run()
        TaskInfoApp(uuid=task.id, print_stderr=True).run()


# Listing of all field names in order (default for search)
ALL_FIELDS: Final[List[str]] = list(Task.columns)


# Reasonable limit on output delimiter (typically just single char).
DELIMITER_MAX_SIZE: Final[int] = 100


# A result set larger than this is refused by `hs list` unless the intent is made explicit
# (--all to dump it, --limit to bound it, or --count to just count it). This guards against
# accidentally dumping an entire production database.
DATABASE_TOO_BIG: Final[int] = 1_000


class SearchableMixin:
    """Mixin class implements task search for multiple commands."""

    field_names: List[str] = ALL_FIELDS
    where_clauses: List[str] = None
    taglist: List[str] = None
    limit: int = None

    # Not needed by some applications
    order_by: str = None
    order_desc: bool = False

    show_failed: bool = False
    show_completed: bool = False
    show_succeeded: bool = False
    show_remaining: bool = False
    show_cancelled: bool = False

    group_filter: Optional[int] = None
    part_filter: Optional[int] = None
    retry_filter: bool = False
    signal_filter: Optional[str] = None

    # The filter, once built (see `filter_criteria`).
    __criteria: Optional[Tuple[ColumnElement, ...]] = None

    def build_query(self: SearchableMixin) -> Query:
        """Build original query interface."""
        query = Task.query(*self.fields)
        query = self.__build_order_by_clause(query)
        query = query.filter(*self.filter_criteria())
        return query.limit(self.limit)

    def filter_criteria(self: SearchableMixin) -> Tuple[ColumnElement, ...]:
        """The filter as self-contained criteria: the where-clauses (and their aliases), then the tags.

        Each criterion carries its own binds, so the same criteria serve a query, an UPDATE or DELETE
        of every matching row, and the write of one row by `id`. Built once.
        """
        if self.__criteria is None:
            self.__criteria = (*(clause.compile() for clause in self.__build_filters()),
                               *self.__build_tag_criteria())
        return self.__criteria

    def __build_tag_criteria(self: SearchableMixin) -> List[ColumnElement]:
        """JSON-based tag criteria: a name alone requires the key, a `key:value` requires that value."""
        sqlite = Session.get_bind().dialect.name == 'sqlite'
        criteria = []
        for name, value in Tag.parse_cmdline_list(self.taglist).items():
            if isinstance(value, str) and not value:
                if sqlite:
                    # NOTE: sqlalchemy adds `json_quote(json_extract(task.tag, ?)) is not null`,
                    # which is never null, so the path is extracted directly (one bind per name).
                    criteria.append(func.json_extract(Task.tag, literal(f'$."{name}"', TEXT())).isnot(None))
                else:
                    criteria.append(Task.tag[name].isnot(None))
            else:
                if sqlite and value in (True, False):
                    value = int(value)  # NOTE: SQLite stores as 0/1 not JSON true/false :(
                criteria.append(Task.tag[name] == type_coerce(value, JSON))
        return criteria

    def __build_order_by_clause(self: SearchableMixin, query: Query) -> Query:
        """Add order by clause to query if necessary."""
        if self.order_by:
            field = getattr(Task, self.order_by)
            if self.order_desc:
                field = field.desc()
            query = query.order_by(field)
        return query

    def __build_filters(self: SearchableMixin) -> List[WhereClause]:
        """Create list of field selectors from command-line arguments."""
        if self.show_failed:
            self.where_clauses.append('exit_status != 0')
        if self.show_succeeded:
            self.where_clauses.append('exit_status == 0')
        if self.show_completed:
            self.where_clauses.append('exit_status != null')
        if self.show_remaining:
            self.where_clauses.append('exit_status == null')
        if self.show_cancelled:
            self.where_clauses.append(f'exit_status == {CANCEL_STATUS}')
        if self.signal_filter is not None:
            try:
                signal_status = exit_status_for_signal(self.signal_filter)
            except ValueError as error:
                raise ArgumentError(str(error)) from error
            self.where_clauses.append(f'exit_status == {signal_status}')
        if self.group_filter is not None:
            self.where_clauses.append(f'group == {self.group_filter}')
        if self.part_filter is not None:
            self.where_clauses.append(f'part == {self.part_filter}')  # 0 is valid (main partition)
        if self.retry_filter:
            self.where_clauses.append('attempt > 1')
        if not self.where_clauses:
            return []
        else:
            return [WhereClause.from_cmdline(arg) for arg in self.where_clauses]

    @functools.cached_property
    def fields(self: SearchableMixin) -> List[Column]:
        """Field instances to query against."""
        return [getattr(Task, name) for name in self.field_names]

    def check_field_names(self: SearchableMixin) -> None:
        """Check field names are valid."""
        for name in self.field_names:
            if name not in Task.columns:
                raise ArgumentError(f'Invalid field name "{name}"')


SEARCH_PROGRAM = 'hs list'
SEARCH_SYNOPSIS = f'{SEARCH_PROGRAM} [-h] [FIELD [FIELD ...]] [-w COND [COND ...]] [-t TAG [TAG...]] ...'
SEARCH_USAGE = f"""\
Usage:
  hs list [-h] [FIELD [FIELD ...]] [-w COND [COND ...]] [-t TAG [TAG...]] [-g GROUP] [--part N]
          [--failed | --succeeded | --completed | --remaining | --cancelled] [--retries]
          [--signal NAME] [--order-by FIELD [--desc]] [--all | --count | --limit LIMIT]
          [-f FORMAT | --json | --csv]  [-d CHAR] [-i]

  hs list --fields
  hs list --tag-keys
  hs list --tag-values KEY

  Search tasks in the database.\
"""

SEARCH_HELP = f"""\
{SEARCH_USAGE}

Arguments:
  FIELD                      Select specific named fields.

Options:
  -w, --where       COND...  Filter on conditional expression.
  -t, --with-tag    TAG...   Filter by tag.
  -g, --group       GROUP    Filter by group.
      --part        N        Filter by partition (0 is the main database).
  -s, --order-by    FIELD    Order output by field.
      --desc                 Descending order (requires --order-by).
  -F, --failed               Alias for `-w exit_status != 0`.
  -S, --succeeded            Alias for `-w exit_status == 0`.
  -C, --completed            Alias for `-w exit_status != null`.
  -R, --remaining            Alias for `-w exit_status == null`.
  -X, --cancelled            Alias for `-w exit_status == {CANCEL_STATUS}`.
      --retries              Alias for `-w attempt > 1`.
      --signal      NAME     Match tasks killed by signal NAME (e.g. TERM, KILL, HUP).
  -f, --format      FORMAT   Format output (normal, card, plain, table, csv, json).
      --json                 Format output as JSON (alias for `--format=json`).
      --csv                  Format output as CSV (alias for `--format=csv`).
  -d, --delimiter   CHAR     Field seperator for plain/csv formats.
      --all                  Dump all results (required past {DATABASE_TOO_BIG:,} tasks).
  -l, --limit       NUM      Limit the number of results.
  -c, --count                Show count of results.
      --tag-keys             Show all distinct tag keys.
      --tag-values  KEY      Show all distinct tag values for given key.
  -i, --ignore-partitions    Suppress auto-union feature (SQLite only).
  -h, --help                 Show this message and exit.\
"""


class TaskSearchApp(ReadingMixin, Application, SearchableMixin):
    """Search for tasks in database."""

    interface = Interface(SEARCH_PROGRAM, SEARCH_USAGE, SEARCH_HELP)
    interface.add_argument('--fields', action='version', version=' '.join(Task.columns))

    field_names: List[str] = ALL_FIELDS
    interface.add_argument('field_names', nargs='*', default=field_names)

    where_clauses: List[str] = None
    interface.add_argument('-w', '--where', nargs='+', default=[], dest='where_clauses')

    taglist: List[str] = None
    interface.add_argument('-t', '--with-tag', nargs='+', default=[], dest='taglist')

    order_by: str = None
    order_desc: bool = False
    interface.add_argument('-s', '--order-by', default=None, choices=field_names)
    interface.add_argument('--desc', action='store_true', dest='order_desc')

    # NOTE: --all pre-authorizes a large dump, --limit bounds it, --count is cheap; each is a
    # distinct answer to "this could be a lot of data", so they are mutually exclusive. Without
    # one of them a result set over DATABASE_TOO_BIG is refused (see run()).
    show_all: bool = False
    limit: int = None
    show_count: bool = False
    guard_interface = interface.add_mutually_exclusive_group()
    guard_interface.add_argument('--all', action='store_true', dest='show_all')
    ranged(guard_interface, '-l', '--limit', setting='option.limit', default=None)
    guard_interface.add_argument('-c', '--count', action='store_true', dest='show_count')

    show_failed: bool = False
    show_completed: bool = False
    show_succeeded: bool = False
    show_remaining: bool = False
    show_cancelled: bool = False
    search_alias_interface = interface.add_mutually_exclusive_group()
    search_alias_interface.add_argument('-F', '--failed', action='store_true', dest='show_failed')
    search_alias_interface.add_argument('-C', '--completed', action='store_true', dest='show_completed')
    search_alias_interface.add_argument('-S', '--succeeded', action='store_true', dest='show_succeeded')
    search_alias_interface.add_argument('-R', '--remaining', action='store_true', dest='show_remaining')
    search_alias_interface.add_argument('-X', '--cancelled', action='store_true', dest='show_cancelled')
    search_alias_interface.add_argument('--finished', action='store_true', dest='show_completed')
    # NOTE: --finished retained for backwards compatibility

    group_filter: Optional[int] = None
    ranged(interface, '-g', '--group', setting='option.group', default=None, dest='group_filter')

    part_filter: Optional[int] = None
    ranged(interface, '--part', setting='option.part', default=None, dest='part_filter')

    retry_filter: bool = False
    interface.add_argument('--retries', action='store_true', dest='retry_filter')

    signal_filter: Optional[str] = None
    interface.add_argument('--signal', default=None, dest='signal_filter')

    output_format: str = '<default>'  # 'plain' if field_names else 'normal'
    output_formats: List[str] = ['normal', 'card', 'plain', 'table', 'json', 'csv']
    output_interface = interface.add_mutually_exclusive_group()
    output_interface.add_argument('-f', '--format', default=output_format,
                                  dest='output_format', choices=output_formats)
    output_interface.add_argument('--json', action='store_const', const='json', dest='output_format')
    output_interface.add_argument('--csv', action='store_const', const='csv', dest='output_format')

    output_delimiter: str = '<default>'  # <space> if plain, ',' if --csv, else not valid
    interface.add_argument('-d', '--delimiter', default=output_delimiter, dest='output_delimiter')

    list_tag_keys: bool = False
    list_tag_values: bool = False
    tag_search_interface = interface.add_mutually_exclusive_group()
    tag_search_interface.add_argument('--tag-keys', action='store_true', dest='list_tag_keys')
    tag_search_interface.add_argument('--tag-values', action='store_true', dest='list_tag_values')

    ignore_partitions: bool = False
    interface.add_argument('-i', '--ignore-partitions', action='store_true', dest='ignore_partitions')

    exceptions = {
        StatementError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        BrokenPipeError: handle_broken_pipe,  # e.g. `hs list --all --csv | head`
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskSearchApp) -> None:
        """Search for tasks in database."""
        ensuredb()
        if not self.ignore_partitions:
            auto_union_sqlite()
        if self.list_tag_keys:
            self.print_tag_keys()
            return
        if self.list_tag_values:
            self.print_tag_values()
            return
        self.check_field_names()
        self.check_output_format()
        query = self.build_query()
        if self.show_count:
            print(query.count())
            return
        if not self.show_all and self.limit is None:
            matched = query.count()
            if matched > DATABASE_TOO_BIG:
                raise ArgumentError(
                    f'Refusing to list {matched:,} tasks (over {DATABASE_TOO_BIG:,}); pass --all to '
                    f'dump everything, --limit N to bound the result, or --count to count them')
        self.print_output(self.zone_names(query.all()))

    @staticmethod
    def print_tag_keys() -> None:
        """Print distinct tags present in the database."""
        if DATABASE_DIALECT == 'sqlite':
            for (key, ) in Session.execute(
                    text('select distinct tag.key from task, json_each(tag) as tag')
            ):
                print(key)
        else:
            subquery = Session.query(func.jsonb_object_keys(Task.tag).label('tag_key')).subquery()
            for (key, ) in Session.query(subquery.c.tag_key.distinct()):
                print(key)

    def print_tag_values(self: TaskSearchApp) -> None:
        """Print distinct values for given tag in the database."""
        if len(self.field_names) != 1:
            raise ArgumentError(f'Expected single name for --tag-values')
        name, = self.field_names
        if DATABASE_DIALECT == 'sqlite':
            for (value, ) in Session.execute(
                    text("""select distinct t.value from task, json_each(tag) as t
                            where json_extract(tag, :a) is not null and t.key = :b""")
                    .params(a=f'$."{name}"', b=name)
            ):
                print(value)
        else:
            for (value, ) in Session.query(distinct(Task.tag[name])).filter(Task.tag[name].isnot(None)):
                print(value)

    def zone_names(self: TaskSearchApp, results: List[Tuple]) -> List[Tuple]:
        """Records with the zone id replaced by its name, for the formats that print fields as they are."""
        if 'zone' not in self.field_names or self.output_format in ('normal', 'card'):
            return results
        index = self.field_names.index('zone')
        names = Zone.names_for_ids([record[index] for record in results if record[index]])
        return [(*record[:index], resolve_zone(record[index], names), *record[index + 1:]) for record in results]

    @functools.cached_property
    def print_output(self: TaskSearchApp) -> Callable[[List[Tuple]], None]:
        """The requested output formatter."""
        return getattr(self, f'print_{self.output_format}')

    @functools.cached_property
    def colorize_rows(self: TaskSearchApp) -> bool:
        """Whether exit status should colorize plain/table rows."""
        return not self.show_count and self.output_format in ('plain', 'table') and COLOR_STDOUT

    @functools.cached_property
    def status_injected(self: TaskSearchApp) -> bool:
        """True when `exit_status` was added to the query solely to colorize output."""
        return self.colorize_rows and 'exit_status' not in self.field_names

    @functools.cached_property
    def status_index(self: TaskSearchApp) -> Optional[int]:
        """Index of `exit_status` within each record when colorizing (else None)."""
        if not self.colorize_rows:
            return None
        return len(self.field_names) if self.status_injected else self.field_names.index('exit_status')

    @functools.cached_property
    def fields(self: TaskSearchApp) -> List[Column]:
        """Query columns: the requested display fields, plus `exit_status` if injected for color."""
        columns = [getattr(Task, name) for name in self.field_names]
        if self.status_injected:
            columns.append(Task.exit_status)
        return columns

    def print_table(self: TaskSearchApp, results: List[Tuple]) -> None:
        """Print in table format."""
        table = Table(title=None)
        for name in self.field_names:
            table.add_column(name)
        for record in results:
            row = [format_json(value) for value in record[:len(self.field_names)]]
            style = select_style(record[self.status_index]) if self.colorize_rows else None
            table.add_row(*row, style=style)
        Console().print(table)

    @staticmethod
    def print_normal(results: List[Tuple]) -> None:
        """Print semi-structured output with all field names."""
        tasks = [Task.from_dict(dict(zip(Task.columns, record))) for record in results]
        source_map = Source.paths_for_ids([task.source for task in tasks if task.source])
        zone_map = Zone.names_for_ids([task.zone for task in tasks if task.zone])
        for task in tasks:
            print('---')
            print_normal(task, source_map=source_map, zone_map=zone_map)

    @staticmethod
    def print_card(results: List[Tuple]) -> None:
        """Print each task as a richly-formatted, width-responsive card (see `render_card`)."""
        tasks = [Task.from_dict(dict(zip(Task.columns, record))) for record in results]
        source_ids = [task.source for task in tasks if task.source]
        source_map = Source.paths_for_ids(source_ids)
        fingerprint_map = Source.fingerprints_for_ids(source_ids)
        zone_map = Zone.names_for_ids([task.zone for task in tasks if task.zone])
        console = card_console()
        width = console.width
        for i, task in enumerate(tasks):
            if i:
                console.print()  # A blank line separates adjacent cards.
            console.print(render_card(task, width, source_map=source_map, fingerprint_map=fingerprint_map,
                                      zone_map=zone_map))

    def print_plain(self: TaskSearchApp, results: List[Tuple]) -> None:
        """Print plain text output with given field names, one task per line."""
        for record in results:
            data = [format_json(value) for value in record[:len(self.field_names)]]
            line = self.output_delimiter.join(map(str, data))
            if self.colorize_rows:
                line = select_color(record[self.status_index])(line)
            print(line)

    def print_json(self: TaskSearchApp, results: List[Tuple]) -> None:
        """Print in output in JSON format."""
        data = [{field: to_json_type(value) for field, value in zip(self.field_names, record)}
                for record in results]
        if sys.stdout.isatty():
            Console().print(Syntax(json.dumps(data, indent=4, sort_keys=False), 'json',
                                   word_wrap=True, theme=config.console.theme,
                                   background_color='default'))
        else:
            print(json.dumps(data, indent=4, sort_keys=False), file=sys.stdout, flush=True)

    def print_csv(self: TaskSearchApp, results: List[Tuple]) -> None:
        """Print output in CVS format."""
        writer = csv.writer(sys.stdout, delimiter=self.output_delimiter)
        writer.writerow(self.field_names)
        for record in results:
            data = [to_json_type(value) for value in record]
            data = [value if isinstance(value, str) else json.dumps(value) for value in data]
            writer.writerow(data)

    def check_output_format(self: TaskSearchApp) -> None:
        """Check given output format is valid."""
        if self.field_names == ALL_FIELDS:
            if self.output_format == '<default>':
                self.output_format = 'normal'
        else:
            if self.output_format == '<default>':
                self.output_format = 'plain'
            elif self.output_format in ('normal', 'card'):
                raise ArgumentError(f'Cannot use --format={self.output_format} with subset of field names')
        if self.output_delimiter != '<default>' and self.output_format not in ['plain', 'csv']:
            raise ArgumentError(f'Unused --delimiter for --format={self.output_format}')
        if len(self.output_delimiter) > DELIMITER_MAX_SIZE:
            raise ArgumentError(f'Output delimiter exceeds max size ({len(self.output_delimiter)} '
                                f'> {DELIMITER_MAX_SIZE})')
        if self.output_delimiter == '<default>':
            if self.output_format == 'csv':
                self.output_delimiter = ','
            else:
                self.output_delimiter = '\t'
        elif self.output_format == 'csv' and len(self.output_delimiter) != 1:
            # NOTE: csv module demands single-char delimiter
            raise ArgumentError(f'Valid --csv output must use single-char delimiter')


def auto_union_sqlite() -> None:
    """
    Automatically search for partitions and create a temporary view.

    The view names every model column. Partitions created by older versions may lack newer
    columns (read as NULL, or 0 for `part`), and an upgraded table orders appended columns
    differently than a new one, so columns are matched by name, never by position.
    Partition files are only read.
    """
    if DATABASE_DIALECT != 'sqlite':
        return
    dirname = os.path.dirname(config.database.file)
    basename, _ = os.path.splitext(os.path.basename(config.database.file))
    parts = sorted([
        os.path.join(dirname, filename) for filename in os.listdir(dirname)
        if re.match(f'^{basename}.[0-9]+$', filename)
    ], key=(lambda fn: int(fn.split('.')[-1])))
    if len(parts) == 0:
        return
    for i, path in enumerate(parts):
        log.debug(f'Attached database partition ({path})')
        Session.execute(text(f'attach database \'{path}\' as \'part_{i+1}\''))
    selects = [select_task_columns(f'part_{i+1}') for i, _ in enumerate(parts)]
    Session.execute(text(
        'create temp view \'task\' as\n' +
        '\nunion all\n'.join([select_task_columns('main'), *selects])
    ))
    Session.commit()


def select_task_columns(database: str) -> str:
    """Select every model `task` column by name from one attached database, filling missing ones."""
    quote = Session.get_bind().dialect.identifier_preparer.quote
    present = {row[1] for row in Session.execute(text(f'PRAGMA {database}.table_info(task)'))}
    columns = []
    for column in Task.__table__.columns:
        name = quote(column.name)
        if column.name in present:
            columns.append(name)
        else:
            columns.append(f'{MISSING_COLUMN_DEFAULTS.get(column.name, "NULL")} AS {name}')
    return f'select {", ".join(columns)} from {database}.task'


# Values for `task` columns that a partition predates (others read as NULL).
# Rows rotated before the `part` column existed read as partition 0.
MISSING_COLUMN_DEFAULTS: Final[Dict[str, str]] = {'part': '0'}


UPDATE_PROGRAM = 'hs update'
UPDATE_SYNOPSIS = f'{UPDATE_PROGRAM} [-h] ARG [ARG...] [--cancel | --revert | --delete] ...'
UPDATE_USAGE = f"""\
Usage:
  hs update [-h] ARG [ARG...] [--cancel | --revert | --delete] [--remove-tag TAG [TAG ...]]
            [-w COND [COND ...]] [-t TAG [TAG...]] [-g GROUP] [--order-by FIELD [--desc]]
            [-l LIMIT] [--failed | --succeeded | --completed | --remaining | --cancelled]
            [--retries] [--signal NAME] [--no-confirm]
  
  Update task metadata.\
"""

UPDATE_HELP = f"""\
{UPDATE_USAGE}

  Include any number of FIELD=VALUE or tag KEY:VALUE positional arguments.
  The -w/--where and -t/--with-tag operate just as in the search command.

  Any key that is not a column is a tag: KEY=VALUE with such a key is a tag
  change, as KEY:VALUE is, so a misspelled column becomes a tag; the prompt
  lists each change as a column, a tag or a removal before anything is
  written. A key one edit from a column (exit_stauts, Cores, tags) exits with
  status 2, naming the column; KEY:VALUE always sets a tag, the way to a tag
  named like a column. tag=VALUE is refused: change tags with KEY=VALUE or
  KEY:VALUE, remove them with --remove-tag. An argument is split at its first
  = or :. Every tag is checked by the tag rules, exit status 2 for one that
  breaks them.

  Every column is read by its kind, in both forms, within the range the
  documentation lists for it. cores, exit_status, attempt and part take a
  whole number; memory a size, as hs submit -m reads it (4G, 512MB, 1.5GiB,
  or bytes); timeout, waited and duration a duration, as hs submit -W reads it
  (90m, 1h30m, 1:30:00, 1-12, or seconds); cores_max and memory_max a number.
  retried takes true/false, yes/no, on/off or 1/0, in any case. submit_id,
  server_id, client_id, previous_id, next_id and source take a uuid, the
  8-4-4-4-12 hexadecimal form in any case, stored in lower case. zone takes
  a zone name as hs submit --zone reads it; default or none is the default
  zone (so a zone named none or null cannot be set here). args takes any text
  but none or null; the other text columns take any text. submit_time,
  schedule_time, start_time and completion_time take ISO 8601 only: a date
  (2026-01-01), or a date and time joined by T or a space, with optional
  seconds, fraction and offset (2026-01-01T09:00:00-05:00, or Z); a value
  without an offset is local time. now, today and epoch are refused: use
  $(date -Iseconds).
  none or null clears a column that may be empty (not attempt, part,
  retried, submit_id, submit_time or args).
  Any other value exits with status 2 before anything is counted, asked or
  written, and no other change given with it is written. A change to id is
  refused in both forms.

  A group change (group=VALUE, or ID group VALUE in the single-task form)
  takes an integer as hs submit -g does; any other value exits with status 2
  before anything is counted, asked or written.

  Using --cancel marks tasks terminal (schedule_time and completion_time to now,
  exit_status to {CANCEL_STATUS}); they will not be scheduled, retried, or reverted.
  Using --revert reverts everything as if the task was new again.
  Using --delete drops the row from the database entirely.
  --delete takes no change: given a FIELD=VALUE, a KEY:VALUE or --remove-tag,
  hs update exits with status 2 before it counts or asks, and deletes nothing.

  The legacy interface for updating a single task with the ID, FIELD,
  and VALUE as positional arguments remains valid.
  The single-task form takes no --cancel, --revert, --delete or --remove-tag;
  given one, it exits with status 2 and writes nothing.
  The single-task form takes no option that selects tasks (-w, -t, -g, -l, -s,
  --desc, -F, -S, -C, -R, -X, --retries, --signal); given one, it exits with
  status 2 and writes nothing. To write a task only if it matches, use the
  filtered form: hs update FIELD=VALUE -w "id == ID" -F.

Arguments:
  ARGS...                    Assignment pairs for update.

Options:
      --cancel               Cancel specified tasks.
      --revert               Revert specified tasks.
      --delete               Delete specified tasks.
      --remove-tag  TAG...   Remove specified tags by name.
  -w, --where       COND...  Filter on conditional expression.
  -t, --with-tag    TAG...   Filter by tag.
  -g, --group       GROUP    Filter by group.
  -s, --order-by    FIELD    Order matches by FIELD.
      --desc                 Descending order (requires --order-by).
  -l, --limit       NUM      Limit matches.
  -F, --failed               Alias for `-w exit_status != 0`.
  -S, --succeeded            Alias for `-w exit_status == 0`.
  -C, --completed            Alias for `-w exit_status != null`.
  -R, --remaining            Alias for `-w exit_status == null`.
  -X, --cancelled            Alias for `-w exit_status == {CANCEL_STATUS}`.
      --retries              Alias for `-w attempt > 1`.
      --signal      NAME     Match tasks killed by signal NAME (e.g. TERM, KILL, HUP).
  -f, --no-confirm           Do not ask for confirmation.
  -h, --help                 Show this message and exit.\
"""


SELECTION_OPTIONS: Final[List[Tuple[str, str]]] = [
    ('where_clauses', '-w/--where'),
    ('taglist', '-t/--with-tag'),
    ('group_filter', '-g/--group'),
    ('limit', '-l/--limit'),
    ('order_by', '-s/--order-by'),
    ('order_desc', '--desc'),
    ('show_failed', '-F/--failed'),
    ('show_succeeded', '-S/--succeeded'),
    ('show_completed', '-C/--completed'),
    ('show_remaining', '-R/--remaining'),
    ('show_cancelled', '-X/--cancelled'),
    ('retry_filter', '--retries'),
    ('signal_filter', '--signal'),
]
"""The options of `hs update` that select tasks, as (attribute, name); the single-task form takes none."""


def option_given(value: Any) -> bool:
    """True when an option's parsed `value` is not its default (`[]`, `None` or `False`); `0` is given."""
    if isinstance(value, bool):
        return value is True
    if isinstance(value, list):
        return value != []
    return value is not None


NULL_WORDS: Final[frozenset] = frozenset({'none', 'null'})
"""The words, in any case, that clear a column that may be empty."""


def refusal(column: str, form: str, text: str) -> str:
    """A refusal in the range table's one form, for a column it holds no range for: `<column>: expected <form>, given <V>`."""
    return f'{column}: expected {form}, given {shown(text)}'


@dataclass(frozen=True)
class ColumnKind:
    """How `hs update` reads a value given for a task column.

    `read(column, text)` returns the value to store or raises :class:`ArgumentError` naming the column.
    `clears` says that `none` or `null` (any case) writes NULL; otherwise the word goes to `read`, or is
    refused as `form` with the column named as one that may not be empty when `form` is given.
    """

    read: Callable[[str, str], Any]
    clears: bool = True
    form: Optional[str] = None


def by_range(setting: str, number: bool = False) -> Callable[[str, str], Any]:
    """A reader by the range table's entry `setting`; a `number` is stored as a float (never -0.0)."""
    def read(column: str, text: str) -> Any:
        try:
            value = parse_setting(setting, text, name=column)
        except SettingError as error:
            raise ArgumentError(str(error)) from None
        return float(value) + 0.0 if number else value
    return read


def read_group(column: str, text: str) -> int:
    """A task group, as `hs submit -g` reads it."""
    try:
        return parse_group(text)
    except TaskGroupError as error:
        raise ArgumentError(str(error)) from error


def read_text(column: str, text: str) -> Optional[str]:
    """The text as given (e.g., `args=1` stays text); `none` and `null` clear it."""
    return None if text.lower() in NULL_WORDS else text


UUID_FORM: Final[str] = 'a uuid (8-4-4-4-12 hexadecimal digits)'
"""What a uuid column expects, for its refusal."""

ZONE_FORM: Final[str] = 'a zone name (1 to 256 printable characters; default or none for the default zone)'
"""What `zone` expects, for its refusal."""


def read_bool(column: str, text: str) -> bool:
    """A boolean: the words a boolean configuration key takes, in any case."""
    try:
        return read_value(Kind.BOOL, text)
    except ValueError:
        raise ArgumentError(refusal(column, FORMS[Kind.BOOL], text)) from None


def read_uuid(column: str, text: str) -> str:
    """A uuid in the 8-4-4-4-12 form, in any case, as lower case (surrounding spaces aside)."""
    value = text.strip(' ')
    if not UUID_PATTERN.fullmatch(value):
        raise ArgumentError(refusal(column, UUID_FORM, text))
    return value.lower()


def read_zone(column: str, text: str) -> Optional[str]:
    """The id of the zone named `text`, read as `hs submit --zone` reads it; None for the default zone."""
    try:
        ident = zone_id(zone_name(text))
    except ValueError:
        raise ArgumentError(refusal(column, ZONE_FORM, text)) from None
    return None if ident == DEFAULT_ZONE_ID else ident


def read_args(column: str, text: str) -> str:
    """The text as given; a task's command may not be empty, so `none` and `null` are refused."""
    if text.lower() in NULL_WORDS:
        raise ArgumentError(refusal(column, f'text ({column} may not be empty)', text))
    return text


DATETIME_PATTERN: Final[re.Pattern] = re.compile(
    r'\d{4}-\d{2}-\d{2}(?:[T ](?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?', re.ASCII)
"""ISO 8601 as the datetime columns take it: a date, or a date and a time (`T` or a space), with optional
seconds and fraction (up to six digits) and an optional `Z` or `±HH:MM` offset."""

DATETIME_FORM: Final[str] = 'an ISO 8601 date, or date and time (e.g., 2026-01-01T09:00:00-05:00)'
"""What a datetime column expects, for its refusal."""

DATETIME_SPAN: Final[Tuple[datetime, datetime]] = (datetime.min.replace(tzinfo=timezone.utc) + timedelta(days=1),
                                                     datetime.max.replace(tzinfo=timezone.utc) - timedelta(days=1))
"""The earliest and latest instants a datetime column takes: a day inside either end of the calendar,
so the instant is a date in any zone it is read in."""


def read_datetime(column: str, text: str) -> datetime:
    """An ISO 8601 date or date and time, as an aware local time; a value without an offset is local time.

    The form is checked before Python reads it, as Python's reader takes more forms than these, and
    more on some versions than others. A local time that does not exist (in a daylight-saving gap) is
    refused, since where it would land differs by version; an ambiguous one is its first occurrence.
    An instant within a day of either end of the calendar (years 1 and 9999) is refused too: a reader
    in another zone could not hold it, nor can Python in some zones.
    """
    try:
        value = datetime.fromisoformat(text) if DATETIME_PATTERN.fullmatch(text) else None
        if value is not None:
            local = value.astimezone()
            if not DATETIME_SPAN[0] <= local <= DATETIME_SPAN[1]:
                value = None
    except (ValueError, OverflowError):  # Not a day of the calendar, or an instant past either end of it
        value = None
    if value is None:
        raise ArgumentError(refusal(column, DATETIME_FORM, text))
    if value.tzinfo is None and local.replace(tzinfo=None) != value:
        raise ArgumentError(refusal(column, DATETIME_FORM, text) + ', which does not exist in local time')
    return local


def read_whole_tag(column: str, text: str) -> None:
    """A task's tags are never assigned whole."""
    raise ArgumentError(f'tag: tags are not assigned whole; change one with KEY=VALUE or KEY:VALUE, '
                        f'remove one with --remove-tag (given: tag={text})')


def read_id(column: str, text: str) -> None:
    """A task's id is never written."""
    raise ArgumentError(f'Cannot alter task "id" (given: id={text})')


RANGED_COLUMNS: Final[Dict[str, str]] = {
    'cores': 'task.cores', 'memory': 'task.memory', 'timeout': 'task.timeout',
    'waited': 'task.timeout', 'duration': 'task.timeout', 'exit_status': 'column.exit_status',
    'attempt': 'server.attempts', 'part': 'option.part',
    'cores_max': 'column.cores_max', 'memory_max': 'column.memory_max',
}
"""Each column read by an entry of the range table, and that entry."""

ADVISED_COLUMNS: Final[Tuple[str, ...]] = ('cores', 'attempt')
"""Ranged columns whose entry has an advisory limit."""

UUID_COLUMNS: Final[Tuple[str, ...]] = ('server_id', 'client_id', 'previous_id', 'next_id', 'source')
"""The uuid columns that may be empty (`submit_id` may not)."""

DATETIME_COLUMNS: Final[Tuple[str, ...]] = ('schedule_time', 'start_time', 'completion_time')
"""The datetime columns that may be empty (`submit_time` may not)."""

TEXT_COLUMNS: Final[Tuple[str, ...]] = ('command', 'outpath', 'errpath', 'csvpath', 'submit_host', 'server_host',
                                        'client_host', 'fingerprint')
"""The text columns that may be empty."""

COLUMN_KINDS: Final[Dict[str, ColumnKind]] = {
    **{column: ColumnKind(by_range(setting, number=column in ('cores_max', 'memory_max')),
                          clears=column not in ('attempt', 'part'))
       for column, setting in RANGED_COLUMNS.items()},
    'group': ColumnKind(read_group, clears=False),
    'retried': ColumnKind(read_bool, clears=False, form=FORMS[Kind.BOOL]),
    'submit_id': ColumnKind(read_uuid, clears=False, form=UUID_FORM),
    **{column: ColumnKind(read_uuid) for column in UUID_COLUMNS},
    'zone': ColumnKind(read_zone),
    'submit_time': ColumnKind(read_datetime, clears=False, form=DATETIME_FORM),
    **{column: ColumnKind(read_datetime) for column in DATETIME_COLUMNS},
    'args': ColumnKind(read_args, clears=False),
    **{column: ColumnKind(read_text, clears=False) for column in TEXT_COLUMNS},
    'id': ColumnKind(read_id, clears=False),
    'tag': ColumnKind(read_whole_tag, clears=False),
}
"""How `hs update` reads each task column, by name."""


def change_value(field: str, text: str) -> Any:
    """The value a change writes to task column `field`, given as `text`; :class:`ArgumentError` (exit 2) if refused."""
    kind = COLUMN_KINDS[field]
    word = text.strip().lower()
    if word in NULL_WORDS and kind.clears:
        return None
    if word in NULL_WORDS and kind.form is not None:
        raise ArgumentError(refusal(field, f'{kind.form} ({field} may not be empty)', text))
    return kind.read(field, text)


class Changes(NamedTuple):
    """The changes `hs update` was given, read before the database is opened.

    `columns` maps each column to the value stored; `tags` each tag to its value; `zones` the zone
    names to record before the write; `advice` the `(setting, value, column, text)` of each value to
    check against an advisory limit; `shown` one `(kind, text)` per change, for the prompt.
    """

    columns: Dict[str, Any]
    tags: Dict[str, Any]
    zones: List[str]
    advice: List[Tuple[str, Any, str, str]]
    shown: List[Tuple[str, str]]


def near_columns(key: str) -> List[str]:
    """The task columns within one edit of `key`, ignoring letter case, in the order `hs info` shows them."""
    return near_names(key, Task.columns)


def check_tag(key: str, value: Any) -> None:
    """Refuse a tag that is not one by the tag rules (exit 2); each is checked alone, so none is skipped."""
    try:
        check_tag_rules(key, value)
    except (ValueError, TypeError) as error:
        raise ArgumentError(str(error)) from error


def read_changes(arguments: List[str], single: bool = False) -> Changes:
    """The changes `hs update` was given as `arguments`, each read by its kind; nothing is opened.

    In the `single` form the arguments are `ID FIELD VALUE`; otherwise each is `KEY=VALUE` or
    `KEY:VALUE`, split at the first of the two. A key that is a column is read by the column's kind; a
    key one edit from a column (case aside) is refused; any other key, and every `KEY:VALUE`, is a tag.
    The first argument refused raises :class:`ArgumentError` (exit 2), so nothing is counted, asked or
    written. The split, the near-column rule and the tag rules are the shared token reader's
    (:mod:`hypershell.core.tag`), which task input reads by too.
    """
    changes = Changes(columns={}, tags={}, zones=[], advice=[], shown=[])
    if single:
        ident, field, value = arguments
        if field == 'tag':
            for key, tag_value in Tag.parse_cmdline_list([value, ]).items():
                add_tag(changes, key, tag_value)
        else:
            read_assignment(changes, field, value, f'write {ident} tag {field}:VALUE',
                            lambda: Tag.parse_cmdline_list([f'{field}:{value}']))
        return changes
    for arg in arguments:
        if WhereClause.pattern.match(arg):
            raise ArgumentError(f'Positional argument matches conditional ({arg}), '
                                f'maybe you intended to use -w/--where?')
        try:
            key, sep, value = split_token(arg, naked=False)
        except TagError as error:
            raise ArgumentError(str(error)) from None
        if sep == ':':
            add_tag(changes, key, smart_coerce(value), value)
        else:
            read_assignment(changes, key, value, f'write {key}:VALUE', lambda: {key: smart_coerce(value)})
    return changes


def read_assignment(changes: Changes, key: str, text: str, way_out: str,
                    as_tags: Callable[[], Dict[str, Any]]) -> None:
    """Read `key` given `text` into `changes`: a column by its kind, else a tag (`as_tags`), unless one edit from a column."""
    try:
        kind, _, _ = read_token(key, '=', text, columns=Task.columns, fields=Task.columns, colon_fields=(),
                                way_out=way_out)
    except TagError as error:
        raise ArgumentError(str(error)) from None
    if kind == 'field':
        read_column(changes, key, text)
        return
    for tag, value in as_tags().items():
        add_tag(changes, tag, value, text)


def add_tag(changes: Changes, key: str, value: Any, text: Optional[str] = None) -> None:
    """Add the tag `key` with `value` (given as `text`) to `changes`, refused if not one by the tag rules."""
    check_tag(key, value)
    changes.tags[key] = value
    changes.shown.append(('tag', f'{key}:{value if text is None else text}'))


def read_column(changes: Changes, column: str, text: str) -> None:
    """Read `text` for `column` into `changes`."""
    value = change_value(column, text)
    changes.columns[column] = value
    changes.shown.append(('column', f'{column}={text}'))
    if column == 'zone' and value is not None:
        changes.zones.append(zone_name(text))
    if column in ADVISED_COLUMNS and value is not None:
        changes.advice.append((RANGED_COLUMNS[column], value, column, text))


class TaskUpdateApp(ReadingMixin, Application, SearchableMixin):
    """Update task metadata."""

    interface = Interface(UPDATE_PROGRAM, UPDATE_USAGE, UPDATE_HELP)

    update_args: List[str]
    interface.add_argument('update_args', nargs='*')

    # Used by SearchableMixin and not part of this interface
    # Empty list results in Session.query(Task)
    field_names: List[str] = []

    where_clauses: List[str] = None
    interface.add_argument('-w', '--where', nargs='+', default=[], dest='where_clauses')

    taglist: List[str] = None
    interface.add_argument('-t', '--with-tag', nargs='+', default=[], dest='taglist')

    order_by: str = None
    order_desc: bool = False
    interface.add_argument('-s', '--order-by', default=None, choices=list(Task.columns))
    interface.add_argument('--desc', action='store_true', dest='order_desc')

    limit: int = None
    ranged(interface, '-l', '--limit', setting='option.limit', default=None)

    show_failed: bool = False
    show_completed: bool = False
    show_succeeded: bool = False
    show_remaining: bool = False
    show_cancelled: bool = False
    search_alias_interface = interface.add_mutually_exclusive_group()
    search_alias_interface.add_argument('-F', '--failed', action='store_true', dest='show_failed')
    search_alias_interface.add_argument('-C', '--completed', action='store_true', dest='show_completed')
    search_alias_interface.add_argument('-S', '--succeeded', action='store_true', dest='show_succeeded')
    search_alias_interface.add_argument('-R', '--remaining', action='store_true', dest='show_remaining')
    search_alias_interface.add_argument('-X', '--cancelled', action='store_true', dest='show_cancelled')

    group_filter: Optional[int] = None
    ranged(interface, '-g', '--group', setting='option.group', default=None, dest='group_filter')

    retry_filter: bool = False
    interface.add_argument('--retries', action='store_true', dest='retry_filter')

    signal_filter: Optional[str] = None
    interface.add_argument('--signal', default=None, dest='signal_filter')

    revert_mode: bool = False
    cancel_mode: bool = False
    delete_mode: bool = False
    action_interface = interface.add_mutually_exclusive_group()
    action_interface.add_argument('--revert', action='store_true', dest='revert_mode')
    action_interface.add_argument('--cancel', action='store_true', dest='cancel_mode')
    action_interface.add_argument('--delete', action='store_true', dest='delete_mode')

    remove_tag: List[str] = None
    interface.add_argument('--remove-tag', nargs='+')

    no_confirm: bool = False
    interface.add_argument('-f', '--no-confirm', action='store_true')

    exceptions = {
        Task.NotFound: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        Task.Contended: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        **get_shared_exception_mapping(__name__)
    }

    changes: Changes = None

    def run(self: TaskUpdateApp) -> None:
        """Update task attributes in bulk."""
        legacy = len(self.update_args) == 3 and UUID_PATTERN.match(self.update_args[0])
        # Refused before the database is opened, so nothing is counted, asked or written.
        if legacy:
            options = [name for name, given in (('--cancel', self.cancel_mode), ('--revert', self.revert_mode),
                                                ('--delete', self.delete_mode), ('--remove-tag', self.remove_tag))
                       if given]
            selection = [name for attribute, name in SELECTION_OPTIONS if option_given(getattr(self, attribute))]
            if options or selection:
                hint = ' (to write a task only if it matches: hs update FIELD=VALUE -w "id == ID")' if selection else ''
                raise ArgumentError(f'Cannot use {", ".join(options + selection)} '
                                    f'with the single-task form ID FIELD VALUE{hint}')
        elif self.delete_mode and (self.update_args or self.remove_tag):
            words = [*self.update_args, *(['--remove-tag', *self.remove_tag] if self.remove_tag else [])]
            raise ArgumentError(f'Cannot use --delete with changes (given: {" ".join(words)})')
        self.changes = read_changes(self.update_args, single=bool(legacy))
        if not legacy:
            if self.order_desc and not self.order_by:
                raise ArgumentError('Should not provide --desc if not using -s/--order-by')
            if self.order_by and not self.limit:
                raise ArgumentError('Using -s/--order-by without -l/--limit is meaningless')
        for setting, value, column, given in self.changes.advice:
            advise(setting, value, column, given=given)
        ensuredb()
        if legacy:
            self.update_legacy()
        else:
            self.update_tasks()

    def update_legacy(self: TaskUpdateApp) -> None:
        """Implement legacy interface to update single task."""
        uuid = self.update_args[0]
        columns, tags = self.changes.columns, self.changes.tags
        try:
            self.record_zones()
            if tags:
                Task.update(uuid, tag={**Task.from_id(uuid).tag, **tags}, **columns)
            else:
                Task.update(uuid, **columns)
        except StaleDataError as err:
            raise Task.NotFound(str(err)) from err

    def update_tasks(self: TaskUpdateApp) -> None:
        """Normal mode updates many tasks."""

        field_updates, tag_updates = dict(self.changes.columns), dict(self.changes.tags)

        if DATABASE_DIALECT == 'sqlite':
            site = config.database.file
        else:
            site = config.database.get('host', 'localhost')

        log.info(f'Searching database: {config.database.provider} ({site})')

        query = self.build_query()
        count = query.count()

        if count == 0:
            log.info(f'Update affects {count} tasks - stopping')
            return

        if not self.no_confirm:
            self.show_changes()
            response = input(f'Update affects {count} tasks, continue? yes/[no]: ').strip().lower()
            if response in ['n', 'no', '']:
                print('Stopping')
                return
            if response not in ['y', 'yes']:
                print(f'Stopping (invalid response: "{response}")')
                return

        # Delete is handled later if a --limit is used with search
        if self.delete_mode and self.limit is None:
            self.report_written(count, Task.delete_matching(self.filter_criteria()))
            return

        if self.cancel_mode:
            if self.limit:
                prev_count = sum([1 for task in query if task.schedule_time is not None])
            else:
                prev_count = query.filter(Task.schedule_time.isnot(None)).count()
            if prev_count > 0:
                log.warning(f'{prev_count} cancelled tasks already scheduled')
            # NOTE: completion_time is set so the task is terminal - otherwise it reads as
            # "interrupted" (scheduled but incomplete) and gets reverted/re-run on restart.
            now = datetime.now().astimezone()
            field_updates['schedule_time'] = now
            field_updates['completion_time'] = now
            field_updates['exit_status'] = CANCEL_STATUS

        if self.revert_mode:
            field_updates.update(dict.fromkeys(Task.revert_columns))

        if not (self.delete_mode or field_updates or tag_updates or self.remove_tag):
            log.info('Updated 0 tasks')
            return

        self.record_zones()

        if self.limit is not None:
            # An UPDATE cannot take a LIMIT, so the tasks are selected first, then each is written
            # by id while it still matches the filter, all in one transaction.
            ids = [task_id for task_id, in query.with_entities(Task.id)]
            if self.delete_mode:
                written = Task.delete_selected(ids, self.filter_criteria())
            else:
                written = Task.update_selected(ids, self.filter_criteria(), field_updates, tag_updates,
                                               self.remove_tag or [])
            self.report_written(count, written)
            return

        # One statement writes every matching task with every change; on PostgreSQL, the tasks it
        # missed under contention are found and written by id (see `Task.update_matching`).
        written = Task.update_matching(self.filter_criteria(), field_updates, tag_updates, self.remove_tag or [])
        self.report_written(count, written)

    def show_changes(self: TaskUpdateApp) -> None:
        """Print each change before the prompt, as a column, a tag or a removal, so a misspelled column shows as a tag."""
        lines = [*self.changes.shown, *(('remove', f'tag {name}') for name in self.remove_tag or [])]
        if lines:
            print('Changes:')
            for kind, text in lines:
                print(f'  {kind:<7} {text}')

    def record_zones(self: TaskUpdateApp) -> None:
        """Record each zone a change names, as `hs submit --zone` does, so its tasks show the name."""
        for name in self.changes.zones:
            Zone.ensure(name)

    def report_written(self: TaskUpdateApp, count: int, written: int) -> None:
        """Log the number of tasks written, and how many of those counted were not, if any."""
        if written < count:
            log.warning(f'{count - written} of the {count} tasks counted no longer matched '
                        f'and were left as they are')
        log.info(f'{"Deleted" if self.delete_mode else "Updated"} {written} tasks')


TASK_PROGRAM = 'hs task'
TASK_USAGE = f"""\
Usage: 
  {TASK_PROGRAM} [-h]
  {SUBMIT_SYNOPSIS}
  {INFO_SYNOPSIS}
  {WAIT_SYNOPSIS}
  {RUN_SYNOPSIS}
  {SEARCH_SYNOPSIS}
  {UPDATE_SYNOPSIS}
  
  Search, submit, track, and manage tasks.\
"""

TASK_HELP = f"""\
{TASK_USAGE}

Commands:
  submit           {TaskSubmitApp.__doc__}
  info             {TaskInfoApp.__doc__}
  wait             {TaskWaitApp.__doc__}
  run              {TaskRunApp.__doc__}
  search           {TaskSearchApp.__doc__}
  update           {TaskUpdateApp.__doc__}

Options:
  -h, --help       Show this message and exit.\
"""


class TaskGroupApp(ApplicationGroup):
    """Search, submit, track, and manage individual tasks."""

    interface = Interface(TASK_PROGRAM, TASK_USAGE, TASK_HELP)
    interface.add_argument('--list-columns', action='version', version=' '.join(Task.columns))
    interface.add_argument('command')

    commands = {
        'submit': TaskSubmitApp,
        'info': TaskInfoApp,
        'wait': TaskWaitApp,
        'run': TaskRunApp,
        'search': TaskSearchApp,
        'update': TaskUpdateApp,
    }


@dataclass
class WhereClause:
    """Parse and prepare query filters based on command-line argument."""

    field: str
    value: Any
    operand: str

    pattern = re.compile(r'^([a-z_]+)\s*(==|!=|>|>=|<|<=|~)\s*(.*)$')
    op_call = {
        '==': lambda lhs, rhs: lhs == rhs,
        '!=': lambda lhs, rhs: lhs != rhs,
        '>=': lambda lhs, rhs: lhs >= rhs,
        '<=': lambda lhs, rhs: lhs <= rhs,
        '>':  lambda lhs, rhs: lhs > rhs,
        '<':  lambda lhs, rhs: lhs < rhs,
        '~':  lambda lhs, rhs: lhs.regexp_match(rhs),
    }

    def compile(self: WhereClause) -> BinaryExpression:
        """Build binary expression object out of elements."""
        op_call = self.op_call.get(self.operand)
        return op_call(getattr(Task, self.field), self.value)

    @classmethod
    def from_cmdline(cls: Type[WhereClause], argument: str) -> WhereClause:
        """
        Construct from command-line `argument`.

        Example:
            >>> WhereClause.from_cmdline('exit_status != 0')
            WhereClause(field='exit_status', value=0, operand='!=')
        """
        match = cls.pattern.match(argument)
        if match:
            field, operand, value = match.groups()
            if Task.columns.get(field) is str:
                # We want to coerce the value (e.g., as an int or None)
                # But also allow for, e.g., args==1 which expects type str.
                value = None if value.lower() in {'none', 'null'} else value
            else:
                value = smart_coerce(value)
            return WhereClause(field=field, value=value, operand=operand)
        else:
            raise ArgumentError(f'Where clause not understood ({argument})')


NORMAL_MODE_TEMPLATE: Final[str] = """\
          id: {id} ({fingerprint})
       group: {group}
      source: {source} ({source_id})
        zone: {zone}
        args: {args}
     command: {command}
       cores: {cores} (used: {cores_max})
      memory: {memory} (used: {memory_max})
     timeout: {timeout}
 exit_status: {exit_status}
   submitted: {submit_time}
   scheduled: {schedule_time}
     started: {start_time} (waited: {waited})
   completed: {completion_time} (duration: {duration})
 submit_host: {submit_host} ({submit_id})
 server_host: {server_host} ({server_id})
 client_host: {client_host} ({client_id})
     attempt: {attempt}
     retried: {retried}
     outpath: {outpath}
     errpath: {errpath}
     csvpath: {csvpath}
 previous_id: {previous_id}
     next_id: {next_id}
        part: {part}
        tags: {tag}
"""


def no_color(_text: str) -> str:
    """No-op colorization for task output."""
    return _text


SPECIAL_TASK_COLORS: Final[Dict[Optional[int], Callable[[str], str]]] = {
    None: no_color,
    0: green,
    CANCEL_STATUS: faint,
}


def select_color(status: Optional[int]) -> Callable[[str], str]:
    """
    Delegate function for colorization based on task exit status.

    Remaining (unrun) tasks are uncolored, successful tasks are green, and cancelled tasks
    (exit_status == CANCEL_STATUS, including those terminated by SIGHUP) are faint. Any other
    negative status - a signal death or internal sentinel (e.g., template/resource error) - is
    an abnormal termination shown yellow, while a genuine non-zero exit code is red.
    """
    # NOTE: dict.get evaluates its default eagerly, so guard against None (a remaining task,
    # and a key here) before comparing - `None < 0` would raise before the lookup returns.
    return SPECIAL_TASK_COLORS.get(status, yellow if status is not None and status < 0 else red)


# Rich style names parallel to SPECIAL_TASK_COLORS, used to colorize table rows.
# (Table cells render through `rich`, which won't consume raw ANSI from cmdkit.ansi; `dim`
# is the same SGR as `faint`, so table and plain/normal stay visually consistent.)
SPECIAL_TASK_STYLES: Final[Dict[Optional[int], Optional[str]]] = {
    None: None,
    0: 'green',
    CANCEL_STATUS: 'dim',
}


def select_style(status: Optional[int]) -> Optional[str]:
    """Rich style name for a task exit status (table analog of `select_color`)."""
    return SPECIAL_TASK_STYLES.get(status, 'yellow' if status is not None and status < 0 else 'red')


# Rich styles for the `card` view's virtual status badge and border, keyed by
# `Task.status_label`. Unlike `select_style` (exit-status only), the whole-lifecycle
# derivation tells WAITING from RUNNING and colors CANCELLED yellow - a deliberate card
# palette; normal/plain/table keep the exit-status coloring above.
STATUS_STYLES: Final[Dict[str, str]] = {
    'OK': 'bold green',
    'FAILED': 'bold red',
    'CANCELLED': 'yellow',
    'RUNNING': 'cyan',
    'WAITING': 'dim',
    'ERROR': 'magenta',
    'KILLED': 'magenta',
    'UNKNOWN': 'magenta',
}


def resolve_source(source: Optional[str], source_map: Optional[Dict[str, str]] = None) -> str:
    """
    Resolve a task's `source` UUID to a display path/sentinel for the normal view.

    NULL (historical rows) renders as ``null``. A batched `source_map` (id -> path) is
    consulted first to avoid an N+1 lookup; otherwise a single `Source.from_id` resolves
    it. An id with no matching source row degrades to the raw id rather than raising.
    """
    if not source:
        return 'null'
    if source_map is not None:
        path = source_map.get(source)
    else:
        try:
            path = Source.from_id(source).path
        except Source.NotFound:
            path = None
    return format_source(path) if path else source


def resolve_zone(zone: Optional[str], zone_map: Optional[Dict[str, str]] = None) -> str:
    """
    Resolve a task's `zone` id to the zone's name.

    NULL is the default zone. A batched `zone_map` (id -> name) is consulted first to avoid an
    N+1 lookup; otherwise a single `Zone.from_id` resolves it. An id with no zone row degrades to
    the raw id rather than raising.
    """
    if not zone:
        return DEFAULT_ZONE_NAME
    if zone_map is not None:
        return zone_map.get(zone, zone)
    try:
        return Zone.from_id(zone).name
    except Zone.NotFound:
        return zone


def resolve_source_fingerprint(source: Optional[str],
                               fingerprint_map: Optional[Dict[str, str]] = None) -> Optional[str]:
    """
    Resolve a task's `source` UUID to its Source content-fingerprint for the card's `source id`.

    Mirrors `resolve_source`: a batched `fingerprint_map` (id -> fingerprint) is consulted first to
    avoid an N+1 lookup; otherwise a single `Source.from_id` resolves it. Returns None when the task
    has no source, the source row is missing, or the source predates fingerprinting - callers then
    render no parenthetical.
    """
    if not source:
        return None
    if fingerprint_map is not None:
        return fingerprint_map.get(source)
    try:
        return Source.from_id(source).fingerprint
    except Source.NotFound:
        return None


def format_task_fields(task: Task, source_map: Optional[Dict[str, str]] = None,
                       zone_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Prepare a task's columns as display values shared by the `normal` and `card` views.

    Every column is JSON-formatted first, then the human-friendly overrides are applied:
    durations/timeout as `timedelta`, memory via `format_bytes`, the resolved `source` path
    plus its raw id (`source_id`), the zone's name with its id (`zone`; the default zone by name
    alone, and the raw id as `zone_id`), and tags flattened to `key:value` pairs. `source_map`
    (id -> path) and `zone_map` (id -> name) let a multitask caller batch-resolve and avoid N+1 lookups.
    """
    data = {k: format_json(v) for k, v in task.to_dict().items()}
    data['waited'] = 'null' if task.waited is None else timedelta(seconds=int(task.waited))
    data['duration'] = 'null' if task.duration is None else timedelta(seconds=int(task.duration))
    data['tag'] = ', '.join(format_tag(k, v) for k, v in task.tag.items())
    data['cores'] = int(task.cores or 0)
    data['cores_max'] = 'null' if not task.cores_max else f'{task.cores_max:.2f}'
    data['memory'] = format_bytes(int(task.memory or 0))
    data['memory_max'] = 'null' if not task.memory_max else format_bytes(int(task.memory_max))
    data['timeout'] = 'null' if not task.timeout else timedelta(seconds=int(task.timeout))
    data['source_id'] = format_json(task.source)          # raw Source.id, shown in parens on `source`
    data['source'] = resolve_source(task.source, source_map)
    data['zone_id'] = format_json(task.zone)
    name = resolve_zone(task.zone, zone_map)
    data['zone'] = name if not task.zone else f'{name} ({task.zone})'
    return data


def print_normal(task: Task, source_map: Optional[Dict[str, str]] = None,
                 zone_map: Optional[Dict[str, str]] = None) -> None:
    """
    Print semi-structured task metadata with all field names.

    `source_map` (id -> path) lets a multitask caller batch-resolve sources up front and
    avoid an N+1 lookup; when omitted (e.g. `hs info`), the single source is resolved directly.
    """
    color = select_color(task.exit_status)
    print(color(NORMAL_MODE_TEMPLATE.format(**format_task_fields(task, source_map, zone_map))))


# The `card` view supports terminal widths in this range; a raw console width is clamped
# into it and the region layout (one/two/three columns) is chosen from the result.
CARD_MIN_WIDTH: Final[int] = 60
CARD_MAX_WIDTH: Final[int] = 160


def card_width(console_width: int) -> int:
    """Clamp a raw console width to the card's supported [60, 160] range."""
    return max(CARD_MIN_WIDTH, min(CARD_MAX_WIDTH, console_width))


def card_console() -> Console:
    """
    A `Console` whose render width is the clamped card width for the current terminal.

    Sizing the console itself (not just the Panel) to `card_width` is what honors the minimum-60
    floor: a bare `Console` renders at the raw terminal width and clips a wider Panel, truncating
    the id below 60 columns. Fixing `width` leaves `is_terminal` detection intact, so a non-TTY
    still drops color and the status stays literal text.
    """
    return Console(width=card_width(Console().size.width))


def card_region(title: str, rows: List[Tuple[str, Any]]) -> Group:
    """
    A titled label:value block for one logical group of card fields.

    Each block carries one leading blank line so regions stay evenly spaced whether stacked
    in one column or laid side by side in a grid, without doubling separators.
    """
    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify='right', style='dim', no_wrap=True)
    grid.add_column(overflow='fold')
    for key, value in rows:
        # Values are task data (commands, paths, tags) and must render literally: a bare string
        # cell is parsed as `rich` console markup, so brackets (`sed 's/[/]/'`, bash arrays) would
        # be silently stripped or raise MarkupError. Text renders verbatim, as the header does.
        grid.add_row(key, Text(str(value)))
    return Group(Text(''), Text(title, style='bold underline'), grid)


def render_card(task: Task, width: int, source_map: Optional[Dict[str, str]] = None,
                fingerprint_map: Optional[Dict[str, str]] = None,
                zone_map: Optional[Dict[str, str]] = None) -> Panel:
    """
    Build a `rich` card for one task (the `card` output format).

    `width` is clamped to [60, 160]; the metadata region layout is chosen from it (one column
    below 90, two below 150, three at or above 150). Identity fields sit horizontally across the
    top - the id on its own line below 130 so it is never truncated - and the virtual `status:`
    badge from `Task.status_label` is pinned to the lower-right via the Panel subtitle. Renders
    legibly with no color: rich drops styling on a non-terminal and the status stays literal
    text. `source_map`/`fingerprint_map` (id -> path / id -> fingerprint) let a multi-task caller
    batch-resolve the source's path and content-fingerprint and avoid N+1 lookups.
    """
    width = card_width(width)
    # One column below 90; two through 149; three only when wide enough that a full UUID
    # value does not fold mid-token. The identity header goes fully horizontal at >= 130.
    ncols = 1 if width < 90 else 2 if width < 150 else 3
    data = format_task_fields(task, source_map, zone_map)
    label = task.status_label
    style = STATUS_STYLES.get(label, 'magenta')
    # The Source content-fingerprint rides in parens on the `source id` line, when the source has one.
    source_fingerprint = resolve_source_fingerprint(task.source, fingerprint_map)
    source_id = data['source_id'] if not source_fingerprint else f"{data['source_id']} ({source_fingerprint})"

    # Identity header. A future `zone` column adds another cell here without further change.
    def field(key: str, value: Any) -> Text:
        return Text.assemble((f'{key} ', 'dim'), str(value))
    identity = [field('fp', data['fingerprint']), field('group', data['group']), field('part', data['part'])]
    header = Table.grid(padding=(0, 3))
    if width >= 130:
        for _ in range(4):
            header.add_column(no_wrap=True)
        header.add_row(field('id', data['id']), *identity)
    else:
        header.add_column()
        header.add_row(field('id', data['id']))
        second = Table.grid(padding=(0, 2))
        for _ in range(3):
            second.add_column(no_wrap=True)
        second.add_row(*identity)
        header.add_row(second)

    # The command and its provenance/outcome (source, exit status) go full-width, above the
    # responsive grid, so long source paths and the command line are never cramped into a cell.
    submission = card_region('submission', [('args', data['args']), ('command', data['command']),
                                            ('source', data['source']), ('source id', source_id),
                                            ('zone', data['zone']), ('exit status', data['exit_status'])])
    metadata = [
        card_region('timing', [('submitted', data['submit_time']), ('scheduled', data['schedule_time']),
                               ('started', data['start_time']), ('completed', data['completion_time']),
                               ('waited', data['waited']), ('duration', data['duration']),
                               ('timeout', data['timeout'])]),
        card_region('resources', [('cores', f"{data['cores']} (max {data['cores_max']})"),
                                  ('memory', f"{data['memory']} (max {data['memory_max']})")]),
        card_region('execution', [('submit', f"{data['submit_host']} ({data['submit_id']})"),
                                  ('server', f"{data['server_host']} ({data['server_id']})"),
                                  ('client', f"{data['client_host']} ({data['client_id']})")]),
        card_region('output', [('out', data['outpath']), ('err', data['errpath']),
                               ('csv', data['csvpath'])]),
        card_region('retry', [('attempt', data['attempt']), ('retried', data['retried']),
                              ('prev', data['previous_id']), ('next', data['next_id'])]),
    ]
    columns = Table.grid(expand=True, padding=(0, 3))
    for _ in range(ncols):
        columns.add_column(ratio=1, overflow='fold')
    for i in range(0, len(metadata), ncols):
        cells = list(metadata[i:i + ncols])
        cells += [''] * (ncols - len(cells))
        columns.add_row(*cells)

    body = [header, submission, columns]
    if task.tag:
        body.append(card_region('tags', [('', data['tag'])]))
    badge = Text.assemble(('status: ', 'dim'), (label, style))
    return Panel(Group(*body), title='task', title_align='left', box=box.ROUNDED,
                 width=width, padding=(0, 1), subtitle=badge, subtitle_align='right',
                 border_style=style)
