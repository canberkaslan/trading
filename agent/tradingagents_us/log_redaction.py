"""Keep credentials out of the logs.

`httpx` logs every request at INFO as `HTTP Request: GET <full url>`, and some
vendors carry the key in the query string rather than a header — Polygon uses
`?apiKey=...`. So a routine, correctly-configured run writes a live credential
into its own log file in plain text, once per call.

Logs are the wrong trust boundary for this. They are read over someone's
shoulder during a debug session, pasted into chat when something breaks,
shipped to whatever aggregator gets wired up next, and kept far longer than a
key rotation cycle. The backup here happens to cover only local.db, snapshots
and memory — but "the leak was contained because of what today's backup script
copies" is luck, not a control.

The fix is a filter rather than silencing httpx: the request lines are useful,
it is the secret inside them that is not. Anything matching a known
credential-bearing parameter is replaced with a marker that still shows the
parameter was present, so a redacted URL reads as redacted rather than as a
request that never carried a key.

Applied when each record is created (a log-record factory), so it covers
every logger and every handler, including records that propagate up from a
child logger and handlers added after `install()` ran. A filter on the root
logger alone sees neither: logger-level filters run only for records logged
on that logger, and handlers attached later never got the handler filter.

Exceptions are the other way in. A vendor that keeps its key in the query
string raises errors whose text is the URL: `requests` puts it in an
HTTPError ("... for url: https://...?api_key=...") and in a connection error
("Max retries exceeded with url: /path?api_key=..."), and the fallback logs
the exception object itself (`log.warning("... %s", e)`), not a string. So
non-string arguments, and a traceback attached with `exc_info`, are rewritten
too whenever their text carries a credential.
"""

from __future__ import annotations

import contextlib
import logging
import re

# Query parameters that carry secrets. Matched case-insensitively, and the
# value runs to the next separator so a trailing `&other=...` survives intact.
_SECRET_PARAMS = (
    "apikey",
    "api_key",
    "token",
    "access_token",
    "key",
    "secret",
    "password",
    "signature",
)

_QUERY_SECRET = re.compile(
    r"(?i)\b(" + "|".join(_SECRET_PARAMS) + r")=([^&\s\"']+)"
)

# Bearer tokens can appear in logged headers or exception text.
_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._\-]{8,})")

_MARK = "<redacted>"


def redact(text: str) -> str:
    """Replace credential values in `text`, keeping the surrounding structure."""
    out = _QUERY_SECRET.sub(lambda m: f"{m.group(1)}={_MARK}", text)
    return _BEARER.sub(lambda m: f"{m.group(1)}{_MARK}", out)


def _scrub_arg(value: object) -> object:
    """One exception argument with any credential in its text replaced."""
    if isinstance(value, str):
        return redact(value)
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 — an unprintable argument stays as it was
        return value
    clean = redact(text)
    return value if clean == text else clean


def scrub_exception(exc: BaseException) -> BaseException:
    """Redact credentials from `exc`'s message, and from the exceptions chained to it.

    Log redaction only covers logging. An exception also reaches print(), an
    HTTP response body, the text handed to the LLM when a data source fails,
    and the traceback of a crash; `requests` and `httpx` put the request URL,
    query string and key included, in the message. So a client that keeps its
    key in the query string scrubs what it raises before it leaves the client.
    The exception keeps its type and traceback; only its arguments change.
    Returns `exc`, so `raise scrub_exception(e)` reads as it runs.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        with contextlib.suppress(Exception):
            args = tuple(_scrub_arg(a) for a in current.args)
            if args != current.args:
                current.args = args
        current = current.__cause__ or current.__context__
    return exc


class _Redacted:
    """A stand-in for a non-string log argument whose text carried a credential.

    Keeps `%s` and `%r` working (an exception logged with `%r` still reads as
    its repr), with the credential replaced in both.
    """

    __slots__ = ("_text", "_repr")

    def __init__(self, text: str, text_repr: str) -> None:
        self._text = text
        self._repr = text_repr

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return self._repr

    def __format__(self, spec: str) -> str:
        return format(self._text, spec)


def _redact_value(value: object) -> object:
    """`value` with any credential in its text replaced; unchanged if it has none.

    Numbers pass through untouched so `%d` and `%.2f` keep working; any other
    object is only replaced when its text actually changes.
    """
    if isinstance(value, str):
        return redact(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    try:
        text, text_repr = str(value), repr(value)
    except Exception:  # noqa: BLE001 — an unprintable object stays as it was
        return value
    clean, clean_repr = redact(text), redact(text_repr)
    if clean == text and clean_repr == text_repr:
        return value
    return _Redacted(clean, clean_repr)


def _scrub(record: logging.LogRecord) -> None:
    """Replace credentials in a record's message, arguments and traceback."""
    record.msg = _redact_value(record.msg)
    if record.args:
        if isinstance(record.args, dict):
            record.args = {k: _redact_value(v) for k, v in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(_redact_value(a) for a in record.args)
    if record.exc_info and not record.exc_text:
        # Formatter.format() uses exc_text when it is set, so a redacted copy
        # here replaces the traceback every standard formatter would print.
        text = logging.Formatter().formatException(record.exc_info)
        clean = redact(text)
        if clean != text:
            record.exc_text = clean
    if isinstance(record.stack_info, str):
        record.stack_info = redact(record.stack_info)


class RedactingFilter(logging.Filter):
    """Strip credentials from a record before any handler formats it.

    Rewrites `record.msg` and `record.args` rather than the formatted output,
    because a Filter runs before formatting and that is the only point where
    every handler is covered by one change.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # A redaction bug must not drop the log line.
        with contextlib.suppress(Exception):
            _scrub(record)
        # Always emit: this filter censors, it never suppresses. A filter that
        # could return False would silently lose lines on a regex edge case.
        return True


def _install_record_factory() -> None:
    """Scrub every record as it is created, whatever logger or handler it meets."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_redacting", False):
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        # A redaction bug must not drop the log line.
        with contextlib.suppress(Exception):
            _scrub(record)
        return record

    factory._redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def install() -> None:
    """Scrub records at creation, and filter the root logger and its handlers. Idempotent.

    The record factory is what covers everything: child loggers, and handlers
    added after this runs. The filters stay for records made by code that
    builds LogRecord directly instead of through a logger.
    """
    _install_record_factory()
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in root.filters):
        root.addFilter(RedactingFilter())
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
