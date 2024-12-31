# SPDX-FileCopyrightText: 2024 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Task based operations."""


# type annotations
from __future__ import annotations
from typing import List, Dict, Callable, IO, Tuple, Any, Optional, Type, Final

# standard libs
import os
import re
import sys
import csv
import json
import time
import functools
import itertools
from datetime import timedelta, datetime
from dataclasses import dataclass
from shutil import copyfileobj

# external libs
import yaml
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table
from cmdkit.app import Application, ApplicationGroup, exit_status
from cmdkit.cli import Interface, ArgumentError
from sqlalchemy import Column, type_coerce, JSON, text, func, distinct
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Query
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.sql.elements import BinaryExpression

# internal libs
from hypershell.core.platform import default_path
from hypershell.core.config import config
from hypershell.core.exceptions import handle_exception, handle_exception_silently, get_shared_exception_mapping
from hypershell.core.logging import Logger, HOSTNAME
from hypershell.core.remote import SSHConnection
from hypershell.core.types import smart_coerce, JSONValue
from hypershell.core.tag import Tag
from hypershell.data.core import Session
from hypershell.data.model import Task, to_json_type
from hypershell.data import ensuredb

# public interface
__all__ = ['TaskGroupApp', ]

# initialize logger
log = Logger.with_name(__name__)


SUBMIT_PROGRAM = 'hs task submit'
SUBMIT_SYNOPSIS = f'{SUBMIT_PROGRAM} [-h] [-t TAG [TAG...]] -- ARGS...'
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
  -h, --help             Show this message and exit.\
"""


class TaskSubmitApp(Application):
    """Submit task to database."""

    interface = Interface(SUBMIT_PROGRAM, SUBMIT_USAGE, SUBMIT_HELP)

    argv: List[str] = []
    interface.add_argument('argv', nargs='+')

    tags: Dict[str, JSONValue] = {}
    taglist: List[str] = []
    interface.add_argument('-t', '--tag', nargs='*', dest='taglist')

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskSubmitApp) -> None:
        """Submit task to database."""
        ensuredb()
        self.check_tags()
        task = Task.new(args=' '.join(self.argv), tag=self.tags)
        Task.add(task)
        print(task.id)

    def check_tags(self: TaskSubmitApp) -> None:
        """Ensure valid tags."""
        self.tags = {} if not self.taglist else Tag.parse_cmdline_list(self.taglist)
        try:
            Task.ensure_valid_tag(self.tags)
        except (ValueError, TypeError) as error:
            raise ArgumentError(str(error)) from error


# Catch bad UUID before we touch the database
UUID_PATTERN: re.Pattern = re.compile(
    r'^[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12}$'
)


def check_uuid(value: str) -> None:
    """Check for valid UUID."""
    if not UUID_PATTERN.match(value):
        raise ArgumentError(f'Bad UUID: \'{value}\'')


INFO_PROGRAM = 'hs task info'
INFO_SYNOPSIS = f'{INFO_PROGRAM} [-h] ID [--stdout | --stderr | -x FIELD] [-f FORMAT]'
INFO_USAGE = f"""\
Usage: 
  {INFO_SYNOPSIS}
  Get metadata and/or task outputs.\
"""

INFO_HELP = f"""\
{INFO_USAGE}

Arguments:
  ID                     Unique task UUID.

Options:
  -f, --format   FORMAT  Format task info ([normal], json, yaml).
      --json             Format task metadata as JSON.
      --yaml             Format task metadata as YAML.
  -x, --extract  FIELD   Print single field.
      --stdout           Print <stdout> from task.
      --stderr           Print <stderr> from task.
  -h, --help             Show this message and exit.\
"""


class TaskInfoApp(Application):
    """Get metadata/status/outputs of task."""

    interface = Interface(INFO_PROGRAM, INFO_USAGE, INFO_HELP)

    uuid: str
    interface.add_argument('uuid')

    print_stdout: bool = False
    print_stderr: bool = False
    extract_field: str = None
    print_interface = interface.add_mutually_exclusive_group()
    print_interface.add_argument('--stdout', action='store_true', dest='print_stdout')
    print_interface.add_argument('--stderr', action='store_true', dest='print_stderr')
    print_interface.add_argument('-x', '--extract', default=None, choices=Task.columns, dest='extract_field')

    output_format: str = 'normal'
    output_formats: List[str] = ['normal', 'json', 'yaml']
    output_interface = interface.add_mutually_exclusive_group()
    output_interface.add_argument('-f', '--format', default=output_format, dest='output_format', choices=output_formats)
    output_interface.add_argument('--json', action='store_const', const='json', dest='output_format')
    output_interface.add_argument('--yaml', action='store_const', const='yaml', dest='output_format')

    exceptions = {
        Task.NotFound: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskInfoApp) -> None:
        """Get metadata/status/outputs of task."""
        ensuredb()
        check_uuid(self.uuid)
        if self.extract_field:
            self.print_field()
        elif self.print_stdout:
            self.print_file(self.outpath, self.task.outpath, sys.stdout)
        elif self.print_stderr:
            self.print_file(self.errpath, self.task.errpath, sys.stderr)
        elif self.output_format == 'normal':
            print_normal(self.task)
        else:
            self.print_formatted()

    def print_field(self: TaskInfoApp) -> None:
        """Print single field."""
        if self.extract_field != 'tag':
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
        output = formatter(self.task.to_json())  # NOTE: to_json() just means dict with converted value types
        if sys.stdout.isatty():
            output = Syntax(output, self.output_format, word_wrap=True,
                            theme = config.console.theme, background_color = 'default')
            Console().print(output)
        else:
            print(output, file=sys.stdout, flush=True)

    def print_file(self: TaskInfoApp, local_path: str, task_path: Optional[str], out_stream: IO) -> None:
        """Print file contents, fetch from client if necessary."""
        if task_path is None:
            raise RuntimeError(f'No {out_stream.name} for task ({self.uuid})')
        if not os.path.exists(local_path) and self.task.client_host != HOSTNAME:
            self.copy_remote_files()
        with open(local_path, mode='r') as in_stream:
            copyfileobj(in_stream, out_stream)

    @functools.cached_property
    def format_method(self: TaskInfoApp) -> Dict[str, Callable[[dict], str]]:
        """Format data method."""
        return {
            'yaml': functools.partial(yaml.dump, indent=4, sort_keys=False),
            'json': functools.partial(json.dumps, indent=4),
        }

    def copy_remote_files(self: TaskInfoApp) -> None:
        """Copy output and error files and to local host."""
        log.debug(f'Fetching remote files ({self.task.client_host})')
        with SSHConnection(self.task.client_host) as remote:
            remote.get_file(self.task.outpath, self.outpath)
            remote.get_file(self.task.errpath, self.errpath)

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


# Time to wait between database queries
DEFAULT_INTERVAL = 5


WAIT_PROGRAM = 'hs task wait'
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
  -n, --interval  SEC     Time to wait between polling (default: {DEFAULT_INTERVAL}).
  -i, --info              Print info on task.
  -f, --format    FORMAT  Format task info ([normal], json, yaml).
      --json              Format info as JSON.
      --yaml              Format info as YAML.
  -s, --status            Print exit status for task.
  -r, --return            Use exit status from task.
  -h, --help              Show this message and exit.\
"""


class TaskWaitApp(Application):
    """Wait for task to complete."""

    interface = Interface(WAIT_PROGRAM, WAIT_USAGE, WAIT_HELP)

    uuid: str
    interface.add_argument('uuid')

    interval: int = DEFAULT_INTERVAL
    interface.add_argument('-n', '--interval', type=int, default=interval)

    print_info: bool = False
    print_status: bool = False
    return_status: bool = False
    print_interface = interface.add_mutually_exclusive_group()
    print_interface.add_argument('-i', '--info', action='store_true', dest='print_info')
    print_interface.add_argument('-s', '--status', action='store_true', dest='print_status')
    print_interface.add_argument('-r', '--return', action='store_true', dest='return_status')

    output_format: str = 'normal'
    output_formats: List[str] = ['normal', 'json', 'yaml']
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


RUN_PROGRAM = 'hs task run'
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
  -n, --interval  SEC     Time to wait between polling (default: {DEFAULT_INTERVAL}).
  -t, --tag       TAG...  Assign tags as `key:value`.
  -h, --help              Show this message and exit.\
"""


class TaskRunApp(Application):
    """Submit task and wait for completion."""

    interface = Interface(RUN_PROGRAM, RUN_USAGE, RUN_HELP)

    argv: List[str] = []
    interface.add_argument('argv', nargs='+')

    interval: int = DEFAULT_INTERVAL
    interface.add_argument('-n', '--interval', type=int, default=interval)

    taglist: List[str] = []
    interface.add_argument('-t', '--tag', nargs='*', dest='taglist')

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskRunApp) -> None:
        """Submit task and wait for completion."""
        ensuredb()
        task = Task.new(args=' '.join(self.argv),
                        tag=(None if not self.taglist else Tag.parse_cmdline_list(self.taglist)))
        Task.add(task)
        TaskWaitApp(uuid=task.id, interval=self.interval).run()
        TaskInfoApp(uuid=task.id, print_stdout=True).run()
        TaskInfoApp(uuid=task.id, print_stderr=True).run()


# Listing of all field names in order (default for search)
ALL_FIELDS = list(Task.columns)


# Reasonable limit on output delimiter (typically just single char).
DELIMITER_MAX_SIZE = 100


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

    def build_query(self: SearchableMixin) -> Query:
        """Build original query interface."""
        query = Task.query(*self.fields)
        query = self.__build_order_by_clause(query)
        query = self.__build_where_clause(query)
        query = self.__build_where_clause_for_tags(query)
        return query.limit(self.limit)

    def __build_where_clause_for_tags(self: SearchableMixin, query: Query) -> Query:
        """Add JSON-based tag where-clauses to query if necessary."""
        tags_name_only = []
        tags_with_value = Tag.parse_cmdline_list(self.taglist)
        for name in list(tags_with_value.keys()):
            if isinstance(tags_with_value[name], str) and not tags_with_value[name]:
                tags_name_only.append(name)
                tags_with_value.pop(name)
        for name in tags_name_only:
            if config.database.provider == 'sqlite':
                # NOTE: sqlalchemy adds `json_quote(json_extract(task.tag, ?)) is not null`
                # and cannot find a way to exclude `json_quote`, so we do it ourselves
                query = query.filter(text('json_extract(task.tag, :key) is not null')).params(key=f'$."{name}"')
            else:
                query = query.filter(Task.tag[name].isnot(None))
        for name, value in tags_with_value.items():
            if config.database.provider == 'sqlite' and value in (True, False):
                value = int(value)  # NOTE: SQLite stores as 0/1 not JSON true/false :(
            query = query.filter(Task.tag[name] == type_coerce(value, JSON))
        return query

    def __build_where_clause(self: SearchableMixin, query: Query) -> Query:
        """Add explicit where-clauses to query if necessary."""
        for where_clause in self.__build_filters():
            query = query.filter(where_clause.compile())
        return query

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


SEARCH_PROGRAM = 'hs task search'
SEARCH_SYNOPSIS = f'{SEARCH_PROGRAM} [-h] [FIELD [FIELD ...]] [-w COND [COND ...]] [-t TAG [TAG...]] ...'
SEARCH_USAGE = f"""\
Usage:
  hs task search [-h] [FIELD [FIELD ...]] [-w COND [COND ...]] [-t TAG [TAG...]]
                 [--failed | --succeeded | --completed | --remaining]
                 [--order-by FIELD [--desc]] [--count | --limit NUM]
                 [-f FORMAT | --json | --csv]  [-d CHAR]
  
  hs task search --tag-keys
  hs task search --tag-values KEY

  Search tasks in the database.\
"""

SEARCH_HELP = f"""\
{SEARCH_USAGE}

Arguments:
  FIELD                      Select specific named fields.

Options:
  -w, --where       COND...  Filter on conditional expression.
  -t, --with-tag    TAG...   Filter by tag.
  -s, --order-by    FIELD    Order output by field.
      --desc                 Descending order (requires --order-by).
  -F, --failed               Alias for `-w exit_status != 0`.
  -S, --succeeded            Alias for `-w exit_status == 0`.
  -C, --completed            Alias for `-w exit_status != null`.
  -R, --remaining            Alias for `-w exit_status == null`.
  -f, --format      FORMAT   Format output (normal, plain, table, csv, json).
      --json                 Format output as JSON (alias for `--format=json`).
      --csv                  Format output as CSV (alias for `--format=csv`.
  -d, --delimiter   CHAR     Field seperator for plain/csv formats.
  -l, --limit       NUM      Limit the number of results.
  -c, --count                Show count of results.
      --tag-keys             Show all distinct tag keys.
      --tag-values  KEY      Show all distinct tag values for given key.
  -h, --help                 Show this message and exit.\
"""


class TaskSearchApp(Application, SearchableMixin):
    """Search for tasks in database."""

    interface = Interface(SEARCH_PROGRAM, SEARCH_USAGE, SEARCH_HELP)

    field_names: List[str] = ALL_FIELDS
    interface.add_argument('field_names', nargs='*', default=field_names)

    where_clauses: List[str] = None
    interface.add_argument('-w', '--where', nargs='*', default=[], dest='where_clauses')

    taglist: List[str] = None
    interface.add_argument('-t', '--with-tag', nargs='*', default=[], dest='taglist')

    order_by: str = None
    order_desc: bool = False
    interface.add_argument('-s', '--order-by', default=None, choices=field_names)
    interface.add_argument('--desc', action='store_true', dest='order_desc')

    limit: int = None
    interface.add_argument('-l', '--limit', type=int, default=None)

    show_count: bool = False
    interface.add_argument('-c', '--count', action='store_true', dest='show_count')

    show_failed: bool = False
    show_completed: bool = False
    show_succeeded: bool = False
    show_remaining: bool = False
    search_alias_interface = interface.add_mutually_exclusive_group()
    search_alias_interface.add_argument('-F', '--failed', action='store_true', dest='show_failed')
    search_alias_interface.add_argument('-C', '--completed', action='store_true', dest='show_completed')
    search_alias_interface.add_argument('-S', '--succeeded', action='store_true', dest='show_succeeded')
    search_alias_interface.add_argument('-R', '--remaining', action='store_true', dest='show_remaining')
    search_alias_interface.add_argument('--finished', action='store_true', dest='show_completed')
    # NOTE: --finished retained for backwards compatibility

    output_format: str = '<default>'  # 'plain' if field_names else 'normal'
    output_formats: List[str] = ['normal', 'plain', 'table', 'json', 'csv']
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

    exceptions = {
        StatementError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskSearchApp) -> None:
        """Search for tasks in database."""
        ensuredb()
        if self.list_tag_keys:
            self.print_tag_keys()
            return
        if self.list_tag_values:
            self.print_tag_values()
            return
        self.check_field_names()
        self.check_output_format()
        if self.show_count:
            print(self.build_query().count())
        else:
            self.print_output(self.build_query().all())

    @staticmethod
    def print_tag_keys() -> None:
        """Print distinct tags present in the database."""
        if config.database.provider == 'sqlite':
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
        if config.database.provider == 'sqlite':
            for (value, ) in Session.execute(
                    text("""select distinct t.value from task, json_each(tag) as t
                            where json_extract(tag, :a) is not null and t.key = :b""")
                    .params(a=f'$."{name}"', b=name)
            ):
                print(value)
        else:
            for (value, ) in Session.query(distinct(Task.tag[name])).filter(Task.tag[name].isnot(None)):
                print(value)

    @functools.cached_property
    def print_output(self: TaskSearchApp) -> Callable[[List[Tuple]], None]:
        """The requested output formatter."""
        return getattr(self, f'print_{self.output_format}')

    def print_table(self: TaskSearchApp, results: List[Tuple]) -> None:
        """Print in table format."""
        table = Table(title=None)
        for name in self.field_names:
            table.add_column(name)
        for record in results:
            table.add_row(*[json.dumps(to_json_type(value)).strip('"') for value in record])
        Console().print(table)

    @staticmethod
    def print_normal(results: List[Tuple]) -> None:
        """Print semi-structured output with all field names."""
        for record in results:
            print('---')
            print_normal(Task.from_dict(dict(zip(Task.columns, record))))

    def print_plain(self: TaskSearchApp, results: List[Tuple]) -> None:
        """Print plain text output with given field names, one task per line."""
        for record in results:
            data = [json.dumps(to_json_type(value)).strip('"') for value in record]
            print(self.output_delimiter.join(map(str, data)))

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
            elif self.output_format == 'normal':
                raise ArgumentError('Cannot use --format=normal with subset of field names')
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


# Special exit status indicates cancellation
CANCEL_STATUS: Final[int] = -1


UPDATE_PROGRAM = 'hs task update'
UPDATE_SYNOPSIS = f'{UPDATE_PROGRAM} [-h] ARG [ARG...] [--cancel | --revert | --delete] ...'
UPDATE_USAGE = f"""\
Usage:
  hs task update [-h] ARG [ARG...] [--cancel | --revert | --delete] [--remove-tag TAG [TAG ...]]
                 [-w COND [COND ...]] [-t TAG [TAG...]] [--order-by FIELD [--desc]] [--limit NUM]
                 [--failed | --succeeded | --completed | --remaining] [--no-confirm]
  
  Update task metadata.\
"""

UPDATE_HELP = f"""\
{UPDATE_USAGE}

  Include any number of FIELD=VALUE or tag KEY:VALUE positional arguments.
  The -w/--where and -t/--with-tag operate just as in the search command.

  Using --cancel sets schedule_time to now and exit_status to {CANCEL_STATUS}. 
  Using --revert reverts everything as if the task was new again.
  Using --delete drops the row from the database entirely.

  The legacy interface for updating a single task with the ID, FIELD, 
  and VALUE as positional arguments remains valid.

Arguments:
  ARGS...                    Assignment pairs for update.

Options:
      --cancel               Cancel specified tasks.
      --revert               Revert specified tasks.
      --delete               Delete specified tasks.
      --remove-tag  TAG...   Remove specified tags by name.
  -w, --where       COND...  Filter on conditional expression.
  -t, --with-tag    TAG...   Filter by tag.
  -s, --order-by    FIELD    Order matches by FIELD.
      --desc                 Descending order (requires --order-by).
  -l, --limit       NUM      Limit matches.
  -F, --failed               Alias for `-w exit_status != 0`.
  -S, --succeeded            Alias for `-w exit_status == 0`.
  -C, --completed            Alias for `-w exit_status != null`.
  -R, --remaining            Alias for `-w exit_status == null`.
  -f, --no-confirm           Do not ask for confirmation.
  -h, --help                 Show this message and exit.\
"""


class TaskUpdateApp(Application, SearchableMixin):
    """Update task metadata."""

    interface = Interface(UPDATE_PROGRAM, UPDATE_USAGE, UPDATE_HELP)

    update_args: List[str]
    interface.add_argument('update_args', nargs='*')

    # Used by SearchableMixin and not part of this interface
    # Empty list results in Session.query(Task)
    field_names: List[str] = []

    where_clauses: List[str] = None
    interface.add_argument('-w', '--where', nargs='*', default=[], dest='where_clauses')

    taglist: List[str] = None
    interface.add_argument('-t', '--with-tag', nargs='*', default=[], dest='taglist')

    order_by: str = None
    order_desc: bool = False
    interface.add_argument('-s', '--order-by', default=None, choices=list(Task.columns))
    interface.add_argument('--desc', action='store_true', dest='order_desc')

    limit: int = None
    interface.add_argument('-l', '--limit', type=int, default=None)

    show_failed: bool = False
    show_completed: bool = False
    show_succeeded: bool = False
    show_remaining: bool = False
    search_alias_interface = interface.add_mutually_exclusive_group()
    search_alias_interface.add_argument('-F', '--failed', action='store_true', dest='show_failed')
    search_alias_interface.add_argument('-C', '--completed', action='store_true', dest='show_completed')
    search_alias_interface.add_argument('-S', '--succeeded', action='store_true', dest='show_succeeded')
    search_alias_interface.add_argument('-R', '--remaining', action='store_true', dest='show_remaining')

    revert_mode: bool = False
    cancel_mode: bool = False
    delete_mode: bool = False
    action_interface = interface.add_mutually_exclusive_group()
    action_interface.add_argument('--revert', action='store_true', dest='revert_mode')
    action_interface.add_argument('--cancel', action='store_true', dest='cancel_mode')
    action_interface.add_argument('--delete', action='store_true', dest='delete_mode')

    remove_tag: List[str] = None
    interface.add_argument('--remove-tag', nargs='*')

    no_confirm: bool = False
    interface.add_argument('-f', '--no-confirm', action='store_true')

    exceptions = {
        Task.NotFound: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        **get_shared_exception_mapping(__name__)
    }

    def run(self: TaskUpdateApp) -> None:
        """Update task attributes in bulk."""
        ensuredb()
        if len(self.update_args) == 3 and UUID_PATTERN.match(self.update_args[0]):
            self.update_legacy()
        else:
            self.update_tasks()

    def update_legacy(self: TaskUpdateApp) -> None:
        """Implement legacy interface to update single task."""
        uuid, field, value = self.update_args
        if field not in Task.columns:
            raise ArgumentError(f'Invalid field name "{field}"')
        try:
            if field == 'tag':
                Task.update(uuid, tag={**Task.from_id(uuid).tag,
                                       **Tag.parse_cmdline_list([value, ])})
            else:
                if Task.columns.get(field) is str:
                    value = None if value.lower() in {'none', 'null'} else value
                else:
                    value = smart_coerce(value)
                Task.update(uuid, **{field: value, })
        except StaleDataError as err:
            raise Task.NotFound(str(err)) from err

    def update_tasks(self: TaskUpdateApp) -> None:
        """Normal mode updates many tasks."""

        field_updates, tag_updates = self.process_arguments()

        if config.database.provider == 'sqlite':
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
            response = input(f'Update affects {count} tasks, continue? yes/[no]: ').strip().lower()
            if response in ['n', 'no', '']:
                print('Stopping')
                return
            if response not in ['y', 'yes']:
                print(f'Stopping (invalid response: "{response}")')
                return

        # Delete is handled later if a --limit is used with search
        if self.delete_mode and self.limit is None:
            query.delete()
            Session.commit()
            log.info(f'Deleted {count} tasks')
            return

        if self.cancel_mode:
            if self.limit:
                prev_count = sum([1 for task in query if task.schedule_time is not None])
            else:
                prev_count = query.filter(Task.schedule_time.isnot(None)).count()
            if prev_count > 0:
                log.warning(f'{prev_count} cancelled tasks already scheduled')
            field_updates['schedule_time'] = datetime.now().astimezone()
            field_updates['exit_status'] = CANCEL_STATUS

        if self.revert_mode:
            field_updates['schedule_time'] = None
            field_updates['server_host'] = None
            field_updates['server_id'] = None
            field_updates['client_host'] = None
            field_updates['client_id'] = None
            field_updates['command'] = None
            field_updates['start_time'] = None
            field_updates['completion_time'] = None
            field_updates['exit_status'] = None
            field_updates['outpath'] = None
            field_updates['errpath'] = None
            field_updates['waited'] = None
            field_updates['duration'] = None

        if self.limit is not None:
            # We cannot apply an UPDATE query with a LIMIT field
            # The alternative is to pull the data and batch the update
            # While terribly inefficient at least it has a LIMIT
            tasks = query.all()
            tasks_it = iter(tasks)
            if self.delete_mode:
                while batch := tuple(itertools.islice(tasks_it, 100)):
                    Task.delete_all(list(batch))
            if field_updates:
                while batch := tuple(itertools.islice(tasks_it, 100)):
                    Task.update_all([{'id': task.id, **field_updates} for task in batch])
            if tag_updates:
                while batch := tuple(itertools.islice(tasks_it, 100)):
                    Task.update_all([{'id': task.id, 'tag': {**task.tag, **tag_updates}} for task in batch])
            if self.remove_tag:
                while batch := tuple(itertools.islice(tasks_it, 100)):
                    Task.update_all([
                        {'id': task.id, 'tag': self.drop_items(task.tag, *self.remove_tag)}
                        for task in batch
                    ])
            if self.delete_mode:
                log.info(f'Deleted {count} tasks')
            else:
                log.info(f'Updated {count} tasks')
            return

        if field_updates:
            query.update(field_updates)

        if tag_updates:
            if config.database.provider == 'sqlite':
                tag_changes = {}
                change_expr = 'json_set(task.tag'
                for i, (k, v) in enumerate(tag_updates.items()):
                    tag_changes[f'k{i}'] = f'$.{k}'
                    tag_changes[f'v{i}'] = v
                    change_expr += f', :k{i}, :v{i}'
                change_expr += ')'
                query.update({Task.tag: text(change_expr).params(tag_changes)})
            else:
                # We cannot stack inserts with Postgres
                # Instead we do them with individual update queries
                for i, (k, v) in enumerate(tag_updates.items()):
                    change_expr = 'jsonb_set(task.tag, :key, :value)'
                    params = {'key': '{' + k + '}', 'value': json.dumps(to_json_type(v))}
                    query.update({Task.tag: text(change_expr).params(**params)})

        if self.remove_tag:
            if config.database.provider == 'sqlite':
                tag_changes = {}
                change_expr = 'json_remove(task.tag'
                for i, name in enumerate(self.remove_tag):
                    tag_changes[f'k{i}'] = f'$.{name}'
                    change_expr += f', :k{i}'
                change_expr += ')'
                query.update({Task.tag: text(change_expr).params(tag_changes)})
            else:
                # We cannot stack subtractions with Postgres
                # Instead we do them with individual update queries
                for name in self.remove_tag:
                    query.update({Task.tag: text('task.tag - :name').params(name=name)})

        Session.commit()
        log.info(f'Updated {count} tasks')

    def process_arguments(self: TaskUpdateApp) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Process positional arguments and build dictionaries for changes."""
        field_updates = {}
        tag_updates = {}
        for arg in self.update_args:
            if WhereClause.pattern.match(arg):
                raise ArgumentError(f'Positional argument matches conditional ({arg}), '
                                    f'maybe you intended to use -w/--where?')
            if '=' in arg:
                field, value = arg.split('=', 1)
                if field == 'id':
                    raise ArgumentError(f'Cannot alter task "id" (given: {field}={value})')
                if field not in Task.columns:
                    raise ArgumentError(f'Unrecognized task field "{field}"')
                if Task.columns.get(field) is str:
                    # We want to coerce the value (e.g., as an int or None)
                    # But also allow for, e.g., args==1 which expects type str.
                    value = None if value.lower() in {'none', 'null'} else value
                else:
                    value = smart_coerce(value)
                field_updates[field] = value
            elif ':' in arg:
                key, value = arg.split(':', 1)
                tag_updates[key] = smart_coerce(value)
            else:
                raise ArgumentError(f'Argument not recognized ({arg}): missing "=" or ":"')

        Task.ensure_valid_tag(tag_updates)

        if self.order_desc and not self.order_by:
            raise ArgumentError('Should not provide --desc if not using -s/--order-by')

        if self.order_by and not self.limit:
            raise ArgumentError('Using -s/--order-by without -l/--limit is meaningless')

        return field_updates, tag_updates

    @staticmethod
    def drop_items(d: dict, *keys: str) -> dict:
        """Drop items by key if they exist."""
        for key in keys:
            d.pop(key, None)
        return d


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


def print_normal(task: Task) -> None:
    """Print semi-structured task metadata with all field names."""
    task_data = {k: json.dumps(to_json_type(v)).strip('"') for k, v in task.to_dict().items()}
    task_data['waited'] = 'null' if not task.waited else timedelta(seconds=int(task_data['waited']))
    task_data['duration'] = 'null' if not task.duration else timedelta(seconds=int(task_data['duration']))
    task_data['tag'] = ', '.join(format_tag(k, v) for k, v in task.tag.items())
    print(f'          id: {task_data["id"]}')
    print(f'        args: {task_data["args"]}')
    print(f'     command: {task_data["command"]}')
    print(f' exit_status: {task_data["exit_status"]}')
    print(f'   submitted: {task_data["submit_time"]}')
    print(f'   scheduled: {task_data["schedule_time"]}')
    print(f'     started: {task_data["start_time"]} (waited: {task_data["waited"]})')
    print(f'   completed: {task_data["completion_time"]} (duration: {task_data["duration"]})')
    print(f' submit_host: {task_data["submit_host"]} ({task_data["submit_id"]})')
    print(f' server_host: {task_data["server_host"]} ({task_data["server_id"]})')
    print(f' client_host: {task_data["client_host"]} ({task_data["client_id"]})')
    print(f'     attempt: {task_data["attempt"]}')
    print(f'     retried: {task_data["retried"]}')
    print(f'     outpath: {task_data["outpath"]}')
    print(f'     errpath: {task_data["errpath"]}')
    print(f' previous_id: {task_data["previous_id"]}')
    print(f'     next_id: {task_data["next_id"]}')
    print(f'        tags: {task_data["tag"]}')


def format_tag(key: str, value: JSONValue) -> str:
    """Format as `key` or `key:value` if not empty string."""
    if isinstance(value, str) and not value:
        return key
    else:
        return f'{key}:{value}'
