"""Bookings DB backup/restore (S8).

    python -m receptionist.backup backup  <db> <dir> [--keep N]
    python -m receptionist.backup restore <snapshot.sqlite> <target.sqlite>

backup: SQLite online-backup API (a consistent snapshot even while the service
writes under WAL), integrity-checked, sha256 sidecar, newest N kept.
restore: verifies the sidecar checksum and integrity, never overwrites an
existing file (stop the service and move the old DB aside yourself)."""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


class BackupError(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _integrity(path: Path) -> str:
    db = sqlite3.connect(path)
    try:
        return db.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        db.close()


def backup(db_path, out_dir, keep: int = 14) -> Path:
    src_path, out = Path(db_path), Path(out_dir)
    if not src_path.is_file():
        raise BackupError(f"database not found: {src_path}")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap = out / f"bookings-{stamp}.sqlite"
    tmp = snap.with_suffix(".partial")
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if _integrity(tmp) != "ok":
        tmp.unlink()
        raise BackupError("snapshot failed integrity_check")
    os.replace(tmp, snap)
    snap.with_suffix(".sha256").write_text(f"{_sha256(snap)}  {snap.name}\n")
    for old in sorted(out.glob("bookings-*.sqlite"))[:-keep]:
        old.unlink()
        old.with_suffix(".sha256").unlink(missing_ok=True)
    return snap


def restore(snapshot, target) -> Path:
    snap, dest = Path(snapshot), Path(target)
    if dest.exists():
        raise BackupError(f"target exists, refusing to overwrite: {dest}")
    side = snap.with_suffix(".sha256")
    if not side.is_file() or side.read_text().split()[0] != _sha256(snap):
        raise BackupError("checksum mismatch or missing sidecar; snapshot not trusted")
    if _integrity(snap) != "ok":
        raise BackupError("snapshot failed integrity_check")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".restoring")
    src, dst = sqlite3.connect(snap), sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(tmp, dest)
    return dest


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="receptionist.backup")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("db")
    b.add_argument("dir")
    b.add_argument("--keep", type=int, default=14)
    r = sub.add_parser("restore")
    r.add_argument("snapshot")
    r.add_argument("target")
    a = ap.parse_args(argv[1:])
    try:
        path = backup(a.db, a.dir, a.keep) if a.cmd == "backup" else restore(a.snapshot, a.target)
    except BackupError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"OK {a.cmd}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
