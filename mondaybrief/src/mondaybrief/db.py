"""Postgres + pgvector helpers — thin wrapper around psycopg + pgvector."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Iterator
import psycopg
from pgvector.psycopg import register_vector
from .config import get_settings


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Single-connection context manager. pgvector type registered on every connect."""
    conn = psycopg.connect(get_settings().database_url, autocommit=False)
    try:
        register_vector(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(sql: str, params: tuple | dict | None = None) -> list[tuple]:
    """One-shot query helper. Returns rows for SELECT, [] for DML."""
    with connect() as conn:
        cur = conn.execute(sql, params or {})
        if cur.description is None:
            return []
        return cur.fetchall()


def insert_returning_id(sql: str, params: tuple | dict) -> int:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0
