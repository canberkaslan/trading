"""The one place an engine is built, so every process opens local.db the same way.

On the box, one SQLite file (/opt/ai-trader/agent/local.db) is shared by the
API (uvicorn, one worker, a threadpool of sync handlers), the daily run
(trade.py, manage_positions, the commentator fetch, kill_check), and the
timers (reconcile, backup, eval-report, retention). Each used to call
`create_engine(url)` itself, so each connection got SQLite's defaults: a
rollback journal and pysqlite's 5 s busy timeout. In that mode a commit needs
an EXCLUSIVE lock, so any open read (an API request mid-SELECT) holds up every
writer's commit, and five seconds later that commit fails with "database is
locked".

For a SQLite *file* this sets, on every new DBAPI connection:

- ``journal_mode=WAL``. Readers read a snapshot and never block a writer; a
  writer never blocks a reader. Writers still take turns: WAL does not make
  two writers concurrent, `busy_timeout` below is what makes the second one
  wait instead of fail. The mode is stored in the file, so the first
  connection to set it switches the database for every process, old code
  included. The switch itself needs a moment with no other connection
  mid-transaction; it waits up to `busy_timeout` for that and, failing it,
  logs and carries on in the old mode (exactly today's behaviour) so a deploy
  never turns an optimisation into a failed run. The next connection retries.

- ``synchronous=FULL``, kept rather than dropped to NORMAL. In WAL, NORMAL
  skips the fsync at commit and syncs only at checkpoints: an OS crash or
  power loss can roll back the last few committed transactions (never
  corrupt the file; a crashed or killed *process* loses nothing either way).
  What would roll back here is the trade log: the order this box just sent
  to Alpaca, the decision behind it, the kill-switch audit row. Measured on
  300 one-row commits with the flush forced to the device (macOS
  F_FULLFSYNC, roughly what fsync costs on Linux): rollback journal + FULL
  (the old default) 13.5 ms/commit, WAL + FULL 4.8 ms, WAL + NORMAL
  0.014 ms. WAL + FULL is already ~3x cheaper than what ran before, and the
  box commits an estimated tens to a few hundred times a day, so NORMAL
  would buy about a second a day for the durability of the one record this
  box exists to keep. Set explicitly, not left to the build's default.

- ``busy_timeout=15000``. Three times the old 5 s after which "database is
  locked" is raised. Every normal write transaction here lasts
  milliseconds, so a wait near 15 s means something is holding the write lock
  far longer than it should (see the commentator note below); the waiter then
  fails loudly instead of queueing, and an API request fails well inside
  Cloudflare's 100 s origin timeout rather than hanging the phone.

- ``foreign_keys`` is left OFF, as SQLite ships it. The models declare FKs but
  nothing has ever enforced them against this file. Enforced across the test
  suite, 3 tests fail with "FOREIGN KEY constraint failed", each saving an
  order whose decision was never stored, which the repository accepts today.
  That is a change in what the app accepts, not a tuning; it belongs in its
  own change, after the box's file is checked for orphans.

In-memory databases (tests: ``sqlite://``, ``:memory:``, ``mode=memory``) and
every non-SQLite URL (Postgres/Aurora) get plain `create_engine`, untouched. A
read-only file URL (``?mode=ro``) gets the timeout but no journal switch, which
it could not write.

What WAL changes around the file:

- Committed rows can live only in ``local.db-wal`` until a checkpoint (every
  1000 pages, and when the last connection closes). A plain ``cp local.db``
  is therefore no longer a backup. `scripts/backup.py` uses SQLite's backup
  API through a normal read connection, which reads through the WAL, so its
  copy carries every committed row; it then turns the copy back into a
  rollback-journal file before the ADR-009 scrub, so the scrub and VACUUM land
  in the artifact itself and not in a sidecar the artifact would not include
  (tests/test_backup.py proves both on a WAL file with an un-checkpointed row).
  For an ad hoc copy use ``sqlite3 local.db ".backup copy.db"``. The -wal and
  -shm files are never backed up separately: the copy does not need them.

- A purge of commentator items (ADR-009, "A purge removes bytes") no longer
  removes the bytes at commit. `secure_delete`'s zeroed pages are appended
  to local.db-wal; the old pages, purged text and ids included, stay in
  local.db until a checkpoint and in earlier -wal frames until overwritten,
  and the API's pooled connection keeps the close-time checkpoint from ever
  coming. So the retention pass that purged anything ends with
  ``wal_checkpoint(TRUNCATE)`` (`commentator.release_purged_bytes`), and
  fails its unit when another connection keeps that from finishing
  (tests/test_commentator_store.py, TestPurgedBytesInWAL, with an API
  connection held open).

- ``local.db-wal`` and ``local.db-shm`` sit next to local.db while any
  connection is open, i.e. all the time the API runs. Every unit runs as
  ``deploy`` and install.sh leaves /opt/ai-trader owned by deploy, so every
  process can create and write them. Open the file as deploy
  (``sudo -u deploy sqlite3 ...``): a root-owned -shm or -wal left behind
  would lock every deploy process out. The backup's read-only connection,
  on a file nothing else has open, creates both sidecars itself and, being
  read-only, leaves them; as deploy, like everything else. .gitignore
  ignores both so the box checkout stays clean.

Not fixed here: the commentator fetch holds the write lock across paid
extraction calls. In `ingest._store` the second item's upsert autoflushes the
first item's INSERT, and from then on every further extraction runs inside an
open write transaction until the session commits; a probe from a second
connection during the third call reads fine and cannot write ("database is
locked"). Readers were never blocked by that pending write in either journal
mode, only at its commit, which WAL removes. Writers still are: an API write
landing during a fetch of many new items waits, and fails at 15 s. That needs
the extraction moved out of the transaction, not a pragma.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine

log = logging.getLogger(__name__)

#: How long a connection waits for another's lock before "database is locked".
BUSY_TIMEOUT_MS = 15_000


def make_engine(url: str | URL, **kwargs: Any) -> Engine:
    """`create_engine(url, future=True, **kwargs)`, with WAL and a busy timeout on a SQLite file."""
    kwargs.setdefault("future", True)
    engine = create_engine(url, **kwargs)
    if is_sqlite_file(engine.url):
        if _read_only(engine.url):
            event.listen(engine, "connect", _set_busy_timeout)
        else:
            event.listen(engine, "connect", _configure_sqlite_file)
    return engine


def is_sqlite_file(url: URL) -> bool:
    """True for a SQLite database backed by a file; False for memory and other backends."""
    if url.get_backend_name() != "sqlite":
        return False
    database = url.database or ""
    if database in ("", ":memory:") or database.startswith("file::memory:"):
        return False
    return url.query.get("mode") != "memory"


def _read_only(url: URL) -> bool:
    return url.query.get("mode") == "ro"


def _set_busy_timeout(dbapi_conn: sqlite3.Connection, _record: object) -> None:
    cur = dbapi_conn.cursor()
    try:
        cur.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    finally:
        cur.close()


def _configure_sqlite_file(dbapi_conn: sqlite3.Connection, _record: object) -> None:
    # The timeout goes first: the journal switch needs a moment of exclusive
    # access and should wait for it like any other lock.
    _set_busy_timeout(dbapi_conn, _record)
    cur = dbapi_conn.cursor()
    try:
        try:
            mode = cur.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        except sqlite3.OperationalError as exc:  # another process mid-transaction
            mode = f"unchanged ({exc})"
        if mode != "wal":
            # A network filesystem answers with the old mode instead of raising.
            log.warning("sqlite: WAL not enabled, journal mode %s; next connection retries", mode)
        cur.execute("PRAGMA synchronous = FULL")
    finally:
        cur.close()
