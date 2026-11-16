"""Schema upgrade protects legacy data and is safe to retry."""
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from flow_base.runtime import FlowError, Runtime
from flow_base.storage.migrations import v001, v002


def _legacy(home):
    home.mkdir()
    with sqlite3.connect(home / 'state.sqlite3') as db:
        for statement in v001.STATEMENTS[:6]:
            db.execute(statement)
        db.execute("INSERT INTO flows VALUES('flow_old','task','0.1.0','hash','Title',1,1)")
        db.execute("INSERT INTO runs VALUES('run_old','flow_old','job_old','complete','input','output',1,2,NULL)")
        db.execute("INSERT INTO steps VALUES('key','done','artifact','hash',1)")
        db.execute("INSERT INTO requests VALUES('req','job_old','key',1,1,'done','receipt',1)")


def test_empty_database_initializes_once(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    runtime.initialize()
    with runtime.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT version,checksum FROM schema_migrations').fetchall()[0]['checksum'] == v001.CHECKSUM
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_legacy_upgrade_keeps_rows_and_backup(tmp_path):
    home = tmp_path / 'share_states'
    _legacy(home)
    runtime = Runtime(home)
    runtime.initialize()
    backup = home / 'state.sqlite3.before-v001.sqlite3'
    assert backup.exists()
    assert (home / 'state.sqlite3.before-v001.sqlite3.sha256').read_text().strip() == hashlib.sha256(backup.read_bytes()).hexdigest()
    with sqlite3.connect(backup) as old:
        assert old.execute('SELECT status FROM runs').fetchone()[0] == 'complete'
        assert old.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    with runtime.connect() as db:
        assert db.execute('SELECT status FROM runs').fetchone()[0] == 'complete'
        assert tuple(db.execute('SELECT original_status,inferred_execution_status FROM legacy_run_status').fetchone()) == ('complete', 'succeeded')
        assert db.execute('SELECT response FROM requests').fetchone()[0] == 'receipt'
        assert db.execute('SELECT artifact FROM steps').fetchone()[0] == 'artifact'
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('jobs','run_executions','step_attempts','artifact_sets','deliveries','diagnostics','event_index','workflow_revisions')").fetchone()[0] == 8
    runtime.initialize()
    assert len(list(home.glob('*.before-v001.sqlite3'))) == 1


def test_failed_migration_rolls_back_then_retries(tmp_path, monkeypatch):
    home = tmp_path / 'share_states'
    _legacy(home)
    runtime = Runtime(home)
    original = v001.apply

    def fail(db):
        original(db)
        raise RuntimeError('injected failure')

    monkeypatch.setattr(v001, 'apply', fail)
    with pytest.raises(RuntimeError, match='injected'):
        runtime.initialize()
    with sqlite3.connect(home / 'state.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        assert 'jobs' not in {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert db.execute('SELECT status FROM runs').fetchone()[0] == 'complete'
    monkeypatch.setattr(v001, 'apply', original)
    runtime.initialize()
    assert runtime.history()[0]['run_id'] == 'run_old'


def test_two_initializers_share_one_migration(tmp_path):
    home = tmp_path / 'share_states'
    _legacy(home)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: Runtime(home).initialize(), range(2)))
    with Runtime(home).connect() as db:
        assert db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 3
        assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == 1


def test_unknown_schema_refuses_write(tmp_path):
    home = tmp_path / 'share_states'
    home.mkdir()
    with sqlite3.connect(home / 'state.sqlite3') as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(FlowError) as error:
        Runtime(home).initialize()
    assert error.value.code == 'SCHEMA_TOO_NEW'


def test_mismatched_legacy_schema_refuses_upgrade(tmp_path):
    home = tmp_path / 'share_states'
    home.mkdir()
    with sqlite3.connect(home / 'state.sqlite3') as db:
        db.execute('CREATE TABLE runs(run_id TEXT PRIMARY KEY)')
    with pytest.raises(FlowError) as error:
        Runtime(home).initialize()
    assert error.value.code == 'SCHEMA_MISMATCH'
    assert not (home / 'state.sqlite3.before-v001.sqlite3').exists()


def test_marked_schema_missing_new_table_refuses_use(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    with runtime.connect() as db:
        db.execute('DROP TABLE diagnostics')
    with pytest.raises(FlowError) as error:
        runtime.initialize()
    assert error.value.code == 'SCHEMA_MISMATCH'


def test_v001_database_upgrades_to_latest_with_own_backup(tmp_path):
    home = tmp_path / 'share_states'
    home.mkdir()
    database = home / 'state.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)')
        v001.apply(db)
        db.execute('INSERT INTO schema_migrations VALUES(1,?,?)', ('2026-01-01', v001.CHECKSUM))
        db.execute('PRAGMA user_version=1')
        db.execute("INSERT INTO flows VALUES('flow_old','task','0.1.0','hash','Title',1,1)")
    runtime = Runtime(home)
    runtime.initialize()
    backup = home / 'state.sqlite3.before-v002.sqlite3'
    assert backup.is_file()
    with sqlite3.connect(backup) as old:
        assert old.execute('PRAGMA user_version').fetchone()[0] == 1
    with runtime.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT count(*) FROM flows').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 3


def test_v002_upgrade_keeps_legacy_and_adds_artifact_links(tmp_path):
    home = tmp_path / 'share_states'
    home.mkdir()
    database = home / 'state.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)')
        v001.apply(db)
        v002.apply(db)
        db.execute('INSERT INTO schema_migrations VALUES(1,?,?)', ('2026-01-01', v001.CHECKSUM))
        db.execute('INSERT INTO schema_migrations VALUES(2,?,?)', ('2026-01-02', v002.CHECKSUM))
        db.execute('PRAGMA user_version=2')
        db.execute("INSERT INTO flows VALUES('flow_old','task','0.1.0','hash','Title',1,1)")
    runtime = Runtime(home)
    runtime.initialize()
    with sqlite3.connect(home / 'state.sqlite3.before-v003.sqlite3') as old:
        assert old.execute('PRAGMA user_version').fetchone()[0] == 2
    with runtime.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT count(*) FROM flows').fetchone()[0] == 1
        assert {'step_attempt_id', 'cache_key'} <= {row[1] for row in db.execute('PRAGMA table_info(artifact_sets)')}
