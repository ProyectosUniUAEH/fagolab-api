"""Acceso a PostgreSQL con un pool de conexiones (psycopg 3)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

# Pool perezoso: se abre en el primer uso. open=False evita fallar al importar
# si la BD aún no está disponible (p.ej. arranque del contenedor).
_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.dsn,
            min_size=1,
            max_size=8,
            open=True,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def close_pool() -> None:
    """Cierra limpiamente los hilos y conexiones del pool."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_conn() -> Iterator[Any]:
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: tuple | dict | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()


def ping() -> bool:
    try:
        row = fetch_one("select 1 as ok")
        return bool(row and row.get("ok") == 1)
    except Exception:
        return False
