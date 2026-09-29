"""Every engine goes through storage.make_engine.

The factory is what puts local.db in WAL mode with a busy timeout. An engine
built with create_engine() directly gets SQLite's defaults again, and the
API and the daily run are back to failing each other's writes after 5 s,
with nothing going red. The one direct sqlite3 connection allowed is the
backup's read-only copy.
"""

from __future__ import annotations

import ast
from pathlib import Path

_AGENT = Path(__file__).resolve().parent.parent
_ROOTS = ("tradingagents_us", "scripts", "api", "backtest")
_ALLOWED = {
    "create_engine": {"tradingagents_us/storage/engine.py"},
    "sqlite3.connect": {"scripts/backup.py"},
}


def _sources() -> list[Path]:
    return [p for root in _ROOTS for p in sorted((_AGENT / root).rglob("*.py"))]


def _called(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f"{f.value.id}.{f.attr}"
    return None


def test_the_roots_hold_code() -> None:
    # Guards the premise: a moved tree would make the next test pass on nothing.
    assert len(_sources()) > 50


def test_no_engine_is_built_around_the_factory() -> None:
    offenders = []
    for path in _sources():
        rel = path.relative_to(_AGENT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = _called(node) if isinstance(node, ast.Call) else None
            if name in _ALLOWED and rel not in _ALLOWED[name]:
                offenders.append(f"{rel}:{node.lineno}: {name}()")
    assert not offenders, "use tradingagents_us.storage.make_engine:\n" + "\n".join(offenders)
