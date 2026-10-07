"""Guards that keep one container healthy under load: provider timeouts,
coach limits, one-time schema setup, caches and backups."""
from __future__ import annotations

from server import coach, config, db
from server import store as store_mod


def test_coach_clients_time_out(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test")
    assert coach._openai_client().timeout == config.COACH_TIMEOUT_SECONDS
    assert coach._anthropic_client().timeout == config.COACH_TIMEOUT_SECONDS
    assert config.COACH_TIMEOUT_SECONDS <= 120


def test_coach_has_a_daily_ceiling_by_default():
    assert config.COACH_DAILY_LIMIT > 0


def test_schema_runs_once_per_file(tmp_path, monkeypatch):
    calls = []
    original = store_mod.Store._migrate
    monkeypatch.setattr(store_mod.Store, "_migrate",
                        lambda self: (calls.append(1), original(self)))
    path = tmp_path / "site.db"
    for _ in range(3):
        with store_mod.open_store(path) as handle:
            handle.user_count()
    assert len(calls) == 1
    with store_mod.open_store(path) as handle:
        mode = handle.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_schema_comes_back_if_the_file_is_replaced(tmp_path):
    path = tmp_path / "site.db"
    with store_mod.open_store(path) as handle:
        handle.user_count()
    path.unlink()
    with store_mod.open_store(path) as handle:
        assert handle.user_count() == 0


def test_full_history_builds_are_cached_until_new_data(tmp_path, monkeypatch):
    from garmin_sync import metrics

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "site.db")
    with db.store() as handle:
        uid = handle.create_user("a@example.com", "x")["id"]
    calls = []
    original = metrics.build
    monkeypatch.setattr(metrics, "build", lambda *a, **k: (calls.append(1), original(*a, **k))[1])
    first = db.full_metrics(uid)
    assert db.full_metrics(uid) is first
    assert len(calls) == 1
    db.ingest(uid, [], [{"date": "2026-09-01", "steps": 9000}])
    assert db.full_metrics(uid) is not first
    assert len(calls) == 2


def test_daily_backup_is_a_readable_copy_and_old_ones_go(tmp_path, monkeypatch):
    from datetime import date, timedelta

    from server import backup

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "site.db")
    monkeypatch.setattr(config, "BACKUP_KEEP", 3)
    with db.store() as handle:
        handle.create_user("a@example.com", "x")
    start = date(2026, 1, 1)
    for i in range(5):
        made = backup.run(start + timedelta(days=i))
    assert backup.run(start + timedelta(days=4)) == made
    names = sorted(p.name for p in backup.backup_dir().glob("site-*.db"))
    assert names == ["site-2026-01-03.db", "site-2026-01-04.db", "site-2026-01-05.db"]
    with store_mod.open_store(made) as handle:
        assert handle.user_count() == 1


def test_no_database_no_backup(tmp_path, monkeypatch):
    from server import backup

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "missing" / "site.db")
    assert backup.run() is None


def test_session_secret_is_read_from_the_database_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "site.db")
    monkeypatch.setattr(config, "SESSION_SECRET", "")
    first = db.session_secret()
    opened = []
    monkeypatch.setattr(db, "store", lambda: opened.append(1))
    assert db.session_secret() == first
    assert opened == []
