# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0
"""
Research probe (hs-update-contended-writes, brief 02), Q3-Q5.

Point it at a database with the usual HYPERSHELL_* env vars (SQLite file, or a
postgres/timescale database you created), then:

    uv run python spec/hs-update-contended-writes/research/probes/02_core_probe.py

Q3: one combined tag expression vs today's sequence of UPDATEs.
Q4: the user filter as self-contained Core criteria, in SELECT/UPDATE/DELETE.
Q5: Query.update/delete with limit/order_by.
"""

import json
import sqlalchemy as sa
from sqlalchemy import text, func, literal, bindparam, select, update, delete, type_coerce
from sqlalchemy.dialects.postgresql import JSONB, ARRAY

from hypershell.data import initdb, DATABASE_DIALECT, DATABASE_PROVIDER
from hypershell.data.core import Session
from hypershell.data.model import Task, JSON
from hypershell.core.types import to_json_type
from hypershell.task import SearchableMixin, WhereClause

initdb()
T = Task.__table__
print(f'### dialect={DATABASE_DIALECT} provider={DATABASE_PROVIDER} '
      f'server={Session.execute(text("select sqlite_version()" if DATABASE_DIALECT == "sqlite" else "show server_version")).scalar()}')
Session.execute(delete(T)); Session.commit()


def new_row(tag: dict) -> str:
    task = Task.new(args='echo probe', tag=dict(tag), strict_tag=False)
    Task.add(task)
    return task.id


def raw_tag(id_: str) -> str:
    expr = sa.cast(T.c.tag, sa.Text)
    return Session.execute(select(expr).where(T.c.id == id_)).scalar()


# ---------------------------------------------------------------- Q3: today's sequence (copied from task.py)
def today(id_: str, sets: dict, removes: list) -> None:
    query = Session.query(Task).filter(Task.id == id_)
    if sets:
        if DATABASE_DIALECT == 'sqlite':
            tag_changes = {}
            change_expr = 'json_set(task.tag'
            for i, (k, v) in enumerate(sets.items()):
                tag_changes[f'k{i}'] = f'$.{k}'
                tag_changes[f'v{i}'] = v
                change_expr += f', :k{i}, :v{i}'
            change_expr += ')'
            query.update({Task.tag: text(change_expr).params(tag_changes)})
        else:
            for k, v in sets.items():
                params = {'key': '{' + k + '}', 'value': json.dumps(to_json_type(v))}
                query.update({Task.tag: text('jsonb_set(task.tag, :key, :value)').params(**params)})
    if removes:
        if DATABASE_DIALECT == 'sqlite':
            tag_changes = {}
            change_expr = 'json_remove(task.tag'
            for i, name in enumerate(removes):
                tag_changes[f'k{i}'] = f'$.{name}'
                change_expr += f', :k{i}'
            change_expr += ')'
            query.update({Task.tag: text(change_expr).params(tag_changes)})
        else:
            for name in removes:
                query.update({Task.tag: text('task.tag - :name').params(name=name)})
    Session.commit()


# ---------------------------------------------------------------- Q3: candidate combined expressions
def change_sqlite(sets: dict, removes: list):
    """json_remove(json_set(task.tag, '$.k0', v0, ...), '$.r0', ...) -- anonymous binds."""
    expr = T.c.tag
    if sets:
        args = []
        for k, v in sets.items():
            args += [literal(f'$.{k}', sa.Text), literal(v)]   # literal(): unique anon bind; bool -> 0/1 as today
        expr = func.json_set(expr, *args, type_=JSON)
    if removes:
        expr = func.json_remove(expr, *[literal(f'$.{n}', sa.Text) for n in removes], type_=JSON)
    return expr


def change_pg_concat(sets: dict, removes: list):
    """(task.tag || :patch::jsonb) - :names::text[]"""
    expr = T.c.tag
    if sets:
        patch = {k: to_json_type(v) for k, v in sets.items()}
        expr = expr.op('||', return_type=JSONB)(literal(patch, JSONB))
    if removes:
        expr = expr.op('-', return_type=JSONB)(literal(list(removes), ARRAY(sa.Text)))
    return expr


def change_pg_nested(sets: dict, removes: list):
    """jsonb_set(jsonb_set(tag, '{k0}', v0), ...) - 'r0' - 'r1' (chained)."""
    expr = T.c.tag
    for k, v in sets.items():
        expr = func.jsonb_set(expr, sa.cast(literal('{' + k + '}', sa.Text), ARRAY(sa.Text)),
                              literal(to_json_type(v), JSONB), type_=JSONB)
    for n in removes:
        expr = expr.op('-', return_type=JSONB)(literal(n, sa.Text))
    return expr


def combined(id_: str, builder, sets: dict, removes: list):
    stmt = update(T).where(T.c.id == id_).values(tag=builder(sets, removes))
    n = Session.execute(stmt).rowcount
    Session.commit()
    return n, stmt


SCENARIOS = [
    ('types', {}, {'a': 1, 'b': 1.5, 'c': True, 'd': False, 'e': 'x', 'f': None, 'g': ''}, []),
    ('overwrite', {'a': 'old', 'b': 1}, {'a': 'new'}, []),
    ('removes', {'a': 1, 'b': 2, 'c': 3}, {}, ['a', 'zz', 'c']),
    ('set+remove same', {'a': 1}, {'a': 5, 'b': 6}, ['a']),
    ('set+remove mix', {'x': 1, 'y': 2}, {'x': 9, 'z': 'n'}, ['y']),
    ('key a.b/c-d/e+f set', {}, {'a.b': 1, 'c-d': 2, 'e+f': 3}, []),
    ('key a.b/c-d remove', {'a.b': 1, 'c-d': 2, 'k': 0}, {}, ['a.b', 'c-d']),
]

print('\n## Q3 combined tag change vs today (tag read back via ORM; raw text in brackets)')
builders = {'sqlite': [('json_remove(json_set())', change_sqlite)]}.get(
    DATABASE_DIALECT, [('|| patch - text[]', change_pg_concat), ('nested jsonb_set, chained -', change_pg_nested)])
for name, start, sets, removes in SCENARIOS:
    a = new_row(start)
    today(a, sets, removes)
    t_raw = raw_tag(a)
    print(f'- {name}: start={start} sets={sets} removes={removes}')
    print(f'    today    -> [{t_raw}]')
    for label, builder in builders:
        b = new_row(start)
        n, _ = combined(b, builder, sets, removes)
        c_raw = raw_tag(b)
        same = json.loads(t_raw) == json.loads(c_raw)
        print(f'    {label:28s} -> [{c_raw}] rowcount={n} same_as_today={same}')

stmt = update(T).where(T.c.id == 'x').values(tag=builders[0][1]({'a': 1, 'b': 'x'}, ['c', 'd']))
print('\nSQL (first builder):', str(stmt.compile(dialect=Session.get_bind().dialect)).replace('\n', ' '))
if DATABASE_DIALECT != 'sqlite':
    stmt = update(T).where(T.c.id == 'x').values(tag=change_pg_nested({'a': 1, 'b': 'x'}, ['c', 'd']))
    print('SQL (nested):', str(stmt.compile(dialect=Session.get_bind().dialect)).replace('\n', ' '))


# ---------------------------------------------------------------- Q4: filter criteria
class Search(SearchableMixin):
    field_names = []

    def __init__(self, **kw):
        self.where_clauses = []
        self.taglist = []
        for k, v in kw.items():
            setattr(self, k, v)


def criteria(s: Search) -> list:
    """Candidate: the user filter as a list of self-contained Core criteria."""
    out = [wc.compile() for wc in s._SearchableMixin__build_filters()]
    from hypershell.core.tag import Tag
    tags = Tag.parse_cmdline_list(s.taglist)
    for name, value in tags.items():
        if isinstance(value, str) and not value:
            if DATABASE_DIALECT == 'sqlite':
                out.append(func.json_extract(T.c.tag, literal(f'$."{name}"', sa.Text)).isnot(None))
            else:
                out.append(Task.tag[name].isnot(None))
        else:
            if DATABASE_DIALECT == 'sqlite' and value in (True, False):
                value = int(value)
            out.append(Task.tag[name] == type_coerce(value, JSON))
    return out


Session.execute(delete(T)); Session.commit()
rows = {
    'ab': new_row({'a': '', 'b': ''}), 'a': new_row({'a': ''}), 'b': new_row({'b': ''}),
    'c_true_orm': new_row({'c': True, 'e': 'x', 'n': 1.5}), 'none': new_row({}),
}
c_set = new_row({})
combined(c_set, change_sqlite if DATABASE_DIALECT == 'sqlite' else change_pg_concat, {'c': True}, [])
rows['c_true_set'] = c_set
Session.execute(update(T).where(T.c.id == rows['a']).values(group=3, attempt=2, exit_status=-15, args='sleep 9'))
Session.execute(update(T).where(T.c.id == rows['b']).values(exit_status=0))
Session.commit()
inv = {v: k for k, v in rows.items()}
print('\n## Q4 filters: rows', {k: raw_tag(v) for k, v in rows.items()})

CASES = [
    ('-t a b', dict(taglist=['a', 'b'])),
    ('-t b a', dict(taglist=['b', 'a'])),
    ('-t a', dict(taglist=['a'])),
    ('-t c:true', dict(taglist=['c:true'])),
    ('-t e:x n:1.5', dict(taglist=['e:x', 'n:1.5'])),
    ('-w "args ~ ^sl"', dict(where_clauses=['args ~ ^sl'])),
    ('-w group==3', dict(where_clauses=['group==3'])),
    ('--signal TERM', dict(signal_filter='TERM')),
    ('-g 3 --retries', dict(group_filter=3, retry_filter=True)),
    ('-R', dict(show_remaining=True)),
    ('-C -t a', dict(show_completed=True, taglist=['a'])),
]
for label, kw in CASES:
    q = Search(**kw).build_query()
    today_ids = sorted(inv[t.id] for t in q.all())
    crit = criteria(Search(**kw))
    core_ids = sorted(inv[i] for i in Session.execute(select(T.c.id).where(*crit)).scalars())
    orm_ids = sorted(inv[t.id] for t in Session.query(Task).filter(*crit).all())
    # UPDATE and DELETE with the same criteria, rolled back
    n_upd = Session.execute(update(T).where(*crit).values(group=T.c.group)).rowcount
    Session.rollback()
    n_del = Session.execute(delete(T).where(*crit)).rowcount
    Session.rollback()
    by_id = Session.execute(select(T.c.id).where(T.c.id == rows['a'], *crit)).scalars().all()
    print(f'- {label:18s} today={today_ids}  core_select={core_ids}  orm_filter(crit)={orm_ids}  '
          f'update.rowcount={n_upd} delete.rowcount={n_del} id=a&filter={len(by_id)}')

s = Search(taglist=['a', 'b'])
q = s.build_query()
print('\ntoday -t a b SQL:', str(q.statement.compile(dialect=Session.get_bind().dialect)).split('WHERE')[1].strip(),
      '| query params:', dict(q._params))
print('core  -t a b SQL:', str(select(T.c.id).where(*criteria(Search(taglist=['a', 'b']))).compile(
    dialect=Session.get_bind().dialect)).split('WHERE')[1].strip())
# alternative: text clause with a unique bindparam
tc = [text('json_extract(task.tag, :key) is not null').bindparams(bindparam('key', f'$."{n}"', unique=True))
      for n in ('a', 'b')]
if DATABASE_DIALECT == 'sqlite':
    st = select(T.c.id).where(*tc)
    print('text+unique bind SQL:', str(st.compile()).split('WHERE')[1].strip(), '| params',
          st.compile().params, '| rows', sorted(inv[i] for i in Session.execute(st).scalars()))

# mutation of where_clauses on repeated __build_filters
s = Search(show_remaining=True, group_filter=3)
s._SearchableMixin__build_filters(); s._SearchableMixin__build_filters()
print('where_clauses after two __build_filters calls:', s.where_clauses)


# ---------------------------------------------------------------- Q5: Query.update/delete with limit/order_by
print('\n## Q5 Query.update/delete')
for label, mk in [
    ('limit(None).update', lambda: Session.query(Task).limit(None).update({Task.group: Task.group})),
    ('limit(2).update', lambda: Session.query(Task).limit(2).update({Task.group: Task.group})),
    ('order_by.update', lambda: Session.query(Task).order_by(Task.id).update({Task.group: Task.group})),
    ('limit(2).delete', lambda: Session.query(Task).limit(2).delete()),
    ('text-param filter .update (sync auto)', lambda: Session.query(Task).filter(
        text('1 = :one')).params(one=1).update({Task.group: Task.group})),
]:
    try:
        r = mk()
        print(f'- {label}: ok, returned {r}')
    except Exception as err:
        print(f'- {label}: {type(err).__name__}: {str(err).splitlines()[0][:160]}')
    Session.rollback()

n = Session.execute(update(T).values(group=T.c.group)).rowcount
Session.rollback()
print(f'- Core UPDATE that changes nothing: rowcount={n} of {Session.execute(select(func.count()).select_from(T)).scalar()} rows')
Session.execute(delete(T)); Session.commit()
Session.remove()
