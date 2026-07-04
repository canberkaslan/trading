#!/usr/bin/env python3
"""Off-box backup of the eval audit trail — local.db + snapshots + memory.

Everything behind the GO/NO-GO verdict (every LLM decision, order, update,
the equity snapshots, and the agent's learned reflections) lives on a single
Hetzner disk. This ships a dated copy to S3 daily so a dead box can't erase
the eval evidence.

Targets (each best-effort; missing files are skipped with a note):
  - TRADE_LOG_DB_URL sqlite file  -> sqlite3 online .backup -> gzip
  - EVAL_SNAPSHOT_FILE (JSONL)    -> gzip
  - ~/.tradingagents/memory/      -> tar.gz (reflection memory)

Destinations (any combination; configured via env):
  BACKUP_GIT_DIR     local clone of a PRIVATE backup repo with a write deploy
                     key — artifacts are committed and pushed (repo history
                     doubles as retention)
  BACKUP_S3_BUCKET   S3 bucket (with optional BACKUP_S3_PREFIX, default
                     "ai-trader", and BACKUP_AWS_ACCESS_KEY_ID /
                     BACKUP_AWS_SECRET_ACCESS_KEY / BACKUP_AWS_REGION
                     dedicated put-only credentials)

Exit 1 + push alert if any configured destination fails — a silently rotting
backup is worse than no backup. Read-only against live data (sqlite online
backup API). With no destination configured it logs and exits 0.
"""

from __future__ import annotations

import gzip
import io
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _s3_client():
    import boto3

    key = os.environ.get("BACKUP_AWS_ACCESS_KEY_ID")
    secret = os.environ.get("BACKUP_AWS_SECRET_ACCESS_KEY")
    region = os.environ.get("BACKUP_AWS_REGION", "eu-west-1")
    if key and secret:
        return boto3.client(
            "s3", aws_access_key_id=key, aws_secret_access_key=secret, region_name=region
        )
    return boto3.client("s3", region_name=region)


def _sqlite_path() -> Path | None:
    from sqlalchemy.engine import make_url

    url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
    u = make_url(url)
    if u.get_backend_name() != "sqlite" or not u.database:
        return None
    return Path(u.database)


def _dump_sqlite_gz(db_path: Path) -> bytes:
    """Consistent online backup (sqlite3 backup API — safe against live writers),
    gzipped in memory. local.db is ~2MB; fine to buffer."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp.name)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return gzip.compress(Path(tmp.name).read_bytes())


def _tar_gz_dir(dir_path: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(dir_path, arcname=dir_path.name)
    return buf.getvalue()


def _alert(body: str) -> None:
    try:
        from sqlalchemy import create_engine

        from tradingagents_us.notifications import send_expo_push
        from tradingagents_us.notifications.sender import PushMessage
        from tradingagents_us.storage import TradeLogRepository
        from tradingagents_us.storage.device_tokens import list_all_tokens

        url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
        repo = TradeLogRepository(engine=create_engine(url, future=True))
        with repo.session() as s:
            tokens = list_all_tokens(s)
        send_expo_push([
            PushMessage(
                to=t,
                title="⚠️ Backup FAILED",
                body=body[:200],
                data={"type": "ops_alert", "kind": "backup"},
            )
            for t in tokens
        ])
    except Exception as exc:
        print(f"backup: alert push failed: {exc}", file=sys.stderr)


def _push_git(git_dir: Path, stamp: str, artifacts: list[tuple[str, bytes]]) -> list[str]:
    """Write artifacts into the backup clone, commit, push. Returns errors.

    Latest-only layout (current/<name>) — git history IS the retention, so
    the working tree stays small while every day remains recoverable via
    `git log`."""
    errors: list[str] = []
    try:
        dest = git_dir / "current"
        dest.mkdir(parents=True, exist_ok=True)
        for key, blob in artifacts:
            name = key.rsplit("/", 1)[-1]
            (dest / name).write_bytes(blob)
        run = lambda *args: subprocess.run(  # noqa: E731
            ["git", "-C", str(git_dir), *args],
            check=True, capture_output=True, text=True, timeout=120,
        )
        run("add", "-A")
        status = run("status", "--porcelain")
        if status.stdout.strip():
            run("commit", "-m", f"backup {stamp}")
        run("push", "origin", "HEAD")
        print(f"backup: git push OK ({git_dir})")
    except subprocess.CalledProcessError as exc:
        errors.append(f"git: {exc.stderr or exc.stdout or exc}")
    except Exception as exc:
        errors.append(f"git: {exc}")
    return errors


def _upload_s3(bucket: str, artifacts: list[tuple[str, bytes]]) -> list[str]:
    errors: list[str] = []
    s3 = _s3_client()
    for key, blob in artifacts:
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=blob)
            print(f"backup: s3://{bucket}/{key} ({len(blob) / 1024:.0f} KiB)")
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            print(f"backup: FAILED {key}: {exc}", file=sys.stderr)
    return errors


def main() -> int:
    bucket = os.environ.get("BACKUP_S3_BUCKET")
    git_dir = os.environ.get("BACKUP_GIT_DIR")
    if not bucket and not git_dir:
        print("backup: no destination configured (BACKUP_GIT_DIR/BACKUP_S3_BUCKET) — skipping",
              file=sys.stderr)
        return 0
    prefix = os.environ.get("BACKUP_S3_PREFIX", "ai-trader")
    # The timer fires post-midnight UTC (after the worst-case run window), so
    # stamp with the TRADING day (now - 6h), not the calendar day.
    stamp = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%d")

    artifacts: list[tuple[str, bytes]] = []
    errors: list[str] = []

    # Each artifact is collected independently — a locked DB or unreadable
    # file must not abort the others, and MUST surface in the alert (a
    # silently rotting backup is the exact failure mode this exists to stop).
    db_path = _sqlite_path()
    if db_path and db_path.exists():
        try:
            artifacts.append((f"{prefix}/{stamp}/local.db.gz", _dump_sqlite_gz(db_path)))
        except Exception as exc:
            errors.append(f"local.db dump: {exc}")
    else:
        print(f"backup: sqlite db not found ({db_path}) — skipped", file=sys.stderr)

    snap = Path(os.environ.get("EVAL_SNAPSHOT_FILE", "./data/eval_snapshots.jsonl"))
    if snap.exists():
        try:
            artifacts.append(
                (f"{prefix}/{stamp}/eval_snapshots.jsonl.gz", gzip.compress(snap.read_bytes()))
            )
        except Exception as exc:
            errors.append(f"snapshots: {exc}")
    else:
        print(f"backup: snapshot file not found ({snap}) — skipped", file=sys.stderr)

    memory_dir = Path(
        os.environ.get("TRADING_MEMORY_DIR", str(Path.home() / ".tradingagents" / "memory"))
    )
    if memory_dir.is_dir():
        try:
            artifacts.append((f"{prefix}/{stamp}/memory.tar.gz", _tar_gz_dir(memory_dir)))
        except Exception as exc:
            errors.append(f"memory tar: {exc}")
    else:
        print(f"backup: memory dir not found ({memory_dir}) — skipped", file=sys.stderr)

    if not artifacts and not errors:
        _alert("nothing to back up — all targets missing")
        return 1

    if git_dir and artifacts:
        errors += _push_git(Path(git_dir), stamp, artifacts)
    if bucket and artifacts:
        errors += _upload_s3(bucket, artifacts)

    if errors:
        _alert("; ".join(errors))
        return 1
    print(f"backup OK — {len(artifacts)} artifact(s) for {stamp}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # crash-before-alert must still alert
        _alert(f"backup crashed: {exc}")
        raise SystemExit(1)
