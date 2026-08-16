"""Aplica el esquema base y las migraciones SQL pendientes."""
from __future__ import annotations

from pathlib import Path
import sys

import psycopg

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.config import settings


DB_DIR = Path(__file__).resolve().parent


def migrate() -> None:
    sql_files = sorted(DB_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    if not sql_files:
        raise RuntimeError("No se encontraron archivos SQL de esquema.")

    with psycopg.connect(settings.dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version TEXT PRIMARY KEY,
                  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("SELECT to_regclass('public.proyectos')")
            has_base_schema = cur.fetchone()[0] is not None

            for sql_file in sql_files:
                version = sql_file.stem
                if version.startswith("001_") and has_base_schema:
                    cur.execute(
                        """
                        INSERT INTO schema_migrations (version)
                        VALUES (%s)
                        ON CONFLICT (version) DO NOTHING
                        """,
                        (version,),
                    )
                    continue

                cur.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s",
                    (version,),
                )
                if cur.fetchone():
                    continue

                print(f"Aplicando {sql_file.name}...")
                cur.execute(sql_file.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )

    print("Esquema de base de datos actualizado.")


if __name__ == "__main__":
    migrate()
