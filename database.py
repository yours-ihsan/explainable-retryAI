"""SQLite storage for simulated runs and their explanations.

The database file is created automatically the first time the app starts.
Override the location with the RETRY_DB_PATH environment variable
(our tests use it to write to a temporary file).
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DEFAULT_DB_PATH = "retry_history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS simulations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario         TEXT    NOT NULL,
    classification   TEXT    NOT NULL,
    retry_recommended INTEGER NOT NULL,
    next_action      TEXT    NOT NULL,
    explanation      TEXT    NOT NULL,
    rule_id          TEXT    NOT NULL,
    attempt          INTEGER NOT NULL DEFAULT 1,
    amount           REAL,
    currency         TEXT,
    client_reference TEXT,
    is_simulation    INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL
);
"""


def get_db_path() -> str:
    """Where the SQLite file lives (env var wins so tests stay isolated)."""
    return os.environ.get("RETRY_DB_PATH", DEFAULT_DB_PATH)


def _connect() -> sqlite3.Connection:
    path = get_db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the table if it does not exist yet."""
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def save_simulation(record: dict[str, Any]) -> int:
    """Insert one simulation result and return its id."""
    sql = """
        INSERT INTO simulations
            (scenario, classification, retry_recommended, next_action, explanation,
             rule_id, attempt, amount, currency, client_reference, is_simulation, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    """
    params = (
        record["scenario"],
        record["classification"],
        1 if record["retry_recommended"] else 0,
        record["next_action"],
        record["explanation"],
        record["rule_id"],
        record.get("attempt", 1),
        record.get("amount"),
        record.get("currency"),
        record.get("client_reference"),
        record["created_at"],
    )
    with _connect() as conn:
        cursor = conn.execute(sql, params)
        return int(cursor.lastrowid)


def get_history(limit: int = 20, scenario: str | None = None) -> list[dict[str, Any]]:
    """Return the most recent simulations, newest first."""
    sql = """
        SELECT id, scenario, classification, retry_recommended, next_action,
               explanation, rule_id, attempt, amount, currency, client_reference,
               is_simulation, created_at
        FROM simulations
    """
    params: list[Any] = []
    if scenario is not None:
        sql += " WHERE scenario = ?"
        params.append(scenario)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def count_simulations() -> int:
    """How many simulations are stored (used by /health)."""
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM simulations").fetchone()
    return int(row["n"])


def utc_now() -> str:
    """Timestamp stored with every simulation (UTC, second precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["retry_recommended"] = bool(data["retry_recommended"])
    data["is_simulation"] = bool(data["is_simulation"])
    return data
