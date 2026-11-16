"""Transactional, forward-only SQLite schema migrations."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import v001

MIGRATIONS = (v001,)


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _backup(db, path: Path):
    """Use SQLite backup API so a WAL database is captured consistently."""
    destination = path.with_name(path.name + '.before-v001.sqlite3')
    temporary = destination.with_name(destination.name + '.tmp')
    checksum_path = destination.with_name(destination.name + '.sha256')
    if destination.exists():
        raise RuntimeError(f'Migration backup already exists: {destination}')
    try:
        with closing(sqlite3.connect(temporary)) as copy:
            db.backup(copy)
            if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Migration backup failed integrity check')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum_path.write_text(checksum + '\n', encoding='ascii')
    return destination, checksum


def migrate(db: sqlite3.Connection, path: Path, *, existed: bool):
    """Caller holds state.initialize.lock; a failed migration leaves no partial schema."""
    from ...runtime import FlowError

    version = db.execute('PRAGMA user_version').fetchone()[0]
    latest = MIGRATIONS[-1].VERSION
    if version > latest:
        raise FlowError('SCHEMA_TOO_NEW', '資料庫由較新版底座建立；請使用相容版本', schema_version=version, supported=latest)
    tables = _tables(db)
    for name, required in v001.LEGACY_COLUMNS.items():
        if name in tables:
            actual = {row[1] for row in db.execute(f'PRAGMA table_info({name})')}
            if not required <= actual:
                raise FlowError('SCHEMA_MISMATCH', '既有資料表欄位與相容基線不符', table=name, missing=sorted(required - actual))
    if version and 'schema_migrations' not in tables:
        raise FlowError('SCHEMA_MISMATCH', '資料庫版本標記與遷移紀錄不一致', schema_version=version)
    if 'schema_migrations' in tables:
        records = {row[0]: row[1] for row in db.execute('SELECT version, checksum FROM schema_migrations')}
        if any(item > latest for item in records):
            raise FlowError('SCHEMA_TOO_NEW', '資料庫包含未支援的遷移版本', supported=latest)
        for migration in MIGRATIONS:
            if migration.VERSION in records and records[migration.VERSION] != migration.CHECKSUM:
                raise FlowError('SCHEMA_MISMATCH', '資料庫遷移 checksum 不一致', schema_version=migration.VERSION)
        if version != max(records, default=0):
            raise FlowError('SCHEMA_MISMATCH', '資料庫版本標記與遷移紀錄不一致', schema_version=version)
    if version == latest:
        missing = v001.NEW_TABLES - tables
        if missing:
            raise FlowError('SCHEMA_MISMATCH', '已標記升級的資料庫缺少必要資料表', missing=sorted(missing))
        return

    # Do not overwrite a previous recovery point. An old backup remains usable
    # after a failed attempt, and the transaction below is safe to retry.
    if existed and tables:
        backup = path.with_name(path.name + '.before-v001.sqlite3')
        checksum_file = backup.with_name(backup.name + '.sha256')
        if backup.exists():
            if not checksum_file.exists() or hashlib.sha256(backup.read_bytes()).hexdigest() != checksum_file.read_text(encoding='ascii').strip():
                raise FlowError('SCHEMA_BACKUP_INVALID', '既有遷移備份的 checksum 不符，拒絕繼續升級')
        else:
            _backup(db, path)
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)')
        for migration in MIGRATIONS:
            if migration.VERSION > version:
                migration.apply(db)
                db.execute('INSERT INTO schema_migrations(version, applied_at, checksum) VALUES(?,?,?)',
                           (migration.VERSION, datetime.now(timezone.utc).isoformat(), migration.CHECKSUM))
                db.execute(f'PRAGMA user_version={migration.VERSION}')
        missing = v001.NEW_TABLES - _tables(db)
        if missing:
            raise FlowError('SCHEMA_MISMATCH', '遷移未建立必要資料表', missing=sorted(missing))
        db.commit()
    except BaseException:
        db.rollback()
        raise
