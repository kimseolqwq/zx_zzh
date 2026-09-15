import sqlite3

from app.database import checkpoint_database


def test_checkpoint_database_flushes_wal(tmp_path) -> None:
    path = tmp_path / "checkpoint.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO samples DEFAULT VALUES")
        connection.commit()
    result = checkpoint_database(path)
    assert result == (0, 0, 0)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
