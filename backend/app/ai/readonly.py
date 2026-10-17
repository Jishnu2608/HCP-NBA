"""Proof that building a context writes nothing.

`guard(db)` flushes whatever the caller already had pending (that is the caller's write, made
before the context build), then refuses any flush while the context is being read: a read
service that tried to write would raise `ReadOnlyViolation` instead of changing a record. The
read services are called with `refresh=False`, so in normal operation nothing is attempted.
"""

from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.orm import Session


class ReadOnlyViolation(RuntimeError):
    """A context source tried to write to the database."""


@contextmanager
def guard(db: Session):
    if db.new or db.dirty or db.deleted:
        db.flush()

    def _refuse(session, _flush_context, _instances):
        if session.new or session.dirty or session.deleted:
            raise ReadOnlyViolation("a context read tried to write")

    def _refuse_bulk(orm_execute_state):
        if (
            orm_execute_state.is_insert
            or orm_execute_state.is_update
            or orm_execute_state.is_delete
        ):
            raise ReadOnlyViolation("a context read tried to write")

    event.listen(db, "before_flush", _refuse)
    event.listen(db, "do_orm_execute", _refuse_bulk)
    try:
        yield
    finally:
        event.remove(db, "before_flush", _refuse)
        event.remove(db, "do_orm_execute", _refuse_bulk)
