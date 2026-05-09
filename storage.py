import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional
from cryptography.fernet import Fernet, InvalidToken
from .config import DEFAULTS

DB_PATH = Path(os.getenv("DB_PATH", "bot.db"))
_LOCK = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _LOCK, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                settings TEXT NOT NULL,
                api_keys TEXT
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                exchange TEXT,
                symbol TEXT,
                side TEXT,
                entry REAL,
                stop REAL,
                take_profit REAL,
                rr REAL,
                probability REAL,
                status TEXT,
                mode TEXT,
                raw TEXT
            );
            """
        )


def default_settings() -> dict[str, Any]:
    return DEFAULTS.__dict__.copy()


def get_settings(user_id: int) -> dict[str, Any]:
    init_db()
    with _LOCK, _conn() as conn:
        row = conn.execute("SELECT settings FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            s = default_settings()
            conn.execute("INSERT INTO users(user_id, settings) VALUES (?, ?)", (user_id, json.dumps(s)))
            return s
        s = default_settings()
        s.update(json.loads(row["settings"]))
        return s


def save_settings(user_id: int, settings: dict[str, Any]) -> None:
    init_db()
    with _LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO users(user_id, settings) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET settings=excluded.settings",
            (user_id, json.dumps(settings)),
        )


def fernet() -> Optional[Fernet]:
    key = os.getenv("FERNET_KEY")
    if not key:
        return None
    return Fernet(key.encode())


def save_api_keys(user_id: int, exchange: str, api_key: str, api_secret: str, password: str = "") -> None:
    f = fernet()
    if not f:
        raise RuntimeError("FERNET_KEY не задан. Нельзя безопасно сохранить API ключи.")
    current = load_api_keys(user_id, allow_missing=True) or {}
    current[exchange] = {"api_key": api_key, "api_secret": api_secret, "password": password}
    blob = f.encrypt(json.dumps(current).encode()).decode()
    with _LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO users(user_id, settings, api_keys) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET api_keys=excluded.api_keys",
            (user_id, json.dumps(get_settings(user_id)), blob),
        )


def load_api_keys(user_id: int, allow_missing: bool = False) -> Optional[dict[str, Any]]:
    init_db()
    with _LOCK, _conn() as conn:
        row = conn.execute("SELECT api_keys FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row or not row["api_keys"]:
        return {} if allow_missing else None
    f = fernet()
    if not f:
        if allow_missing:
            return {}
        raise RuntimeError("FERNET_KEY не задан.")
    try:
        return json.loads(f.decrypt(row["api_keys"].encode()).decode())
    except InvalidToken as exc:
        raise RuntimeError("FERNET_KEY не подходит к сохранённым ключам.") from exc


def log_trade(user_id: int, signal: dict[str, Any], mode: str, status: str = "signal") -> None:
    with _LOCK, _conn() as conn:
        conn.execute(
            """INSERT INTO trades(user_id, exchange, symbol, side, entry, stop, take_profit, rr, probability, status, mode, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, signal.get("exchange"), signal.get("symbol"), signal.get("side"), signal.get("entry"),
                signal.get("stop"), signal.get("take_profit"), signal.get("rr"), signal.get("probability"),
                status, mode, json.dumps(signal)
            ),
        )
