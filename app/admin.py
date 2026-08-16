"""Control de datos para pruebas y backups (solo entorno local).

Cuatro operaciones, todas en SQL puro sobre la conexión existente (sin depender
de pg_dump / psql instalados en el host):

  1. limpiar()        -> vacía solo datos experimentales; conserva seguridad y auditoría.
  2. exportar_sql()   -> respaldo restaurable de datos experimentales.
  3. sembrar()        -> restaura el seed guardado (datos del Excel de la maestra).
  4. importar_sql()   -> ejecuta un .sql exportado por (2) = restore de un backup.

El restore usa `session_replication_role = replica` para desactivar los triggers
de llave foránea durante la carga, así el orden de inserción no importa. Requiere
un rol con privilegios (el backend se conecta como superusuario en local).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from psycopg import ClientCursor
from psycopg.types.json import Jsonb

from .db import get_conn, get_pool

# Semilla "oficial": snapshot de los datos buenos del Excel. Se versiona junto al repo.
SEED_PATH = Path(__file__).resolve().parent.parent / "db" / "seed_data.sql"

# Carpeta donde se guardan las copias de seguridad con fecha (fuera de git).
BACKUP_DIR = Path(__file__).resolve().parent.parent / "_backups"
# Cuántas copias conservar (las más viejas se borran solas).
MAX_BACKUPS = 30

# Nunca se incluyen en limpiezas, semillas ni restores desde la interfaz. Así una
# prueba "desde cero" no vuelve a borrar cuentas, permisos, sesiones ni trazabilidad.
SECURITY_TABLES = frozenset(
    {
        "eventos_auditoria",
        "grupos_acceso",
        "grupos_miembros",
        "grupos_permisos",
        "grupos_roles",
        "permisos_acceso",
        "roles_acceso",
        "roles_permisos",
        "roles_permisos_semilla",
        "schema_migrations",
        "sesiones_usuario",
        "usuarios_laboratorio",
        "usuarios_permisos",
        "usuarios_roles",
    }
)

# Datos de colaboración/configuración que nunca forman parte de una limpieza,
# semilla o restore experimental. Incluye la configuración IA cifrada.
COLABORACION_TABLES = frozenset(
    {
        "conversaciones", "conversacion_miembros", "mensajes_conversacion", "mensaje_reacciones",
        "tareas_flujos", "tareas_estados", "tareas_transiciones", "tareas_reglas_transicion",
        "tareas_tipos",
        "tareas_espacios", "tareas", "tareas_observadores", "tareas_comentarios", "tareas_adjuntos",
        "tareas_actividad", "tareas_campos", "tareas_valores_campo", "tareas_esquemas_permisos",
        "tareas_esquema_reglas", "ia_configuracion", "ia_politicas", "ia_conectores",
        "ia_conversaciones", "ia_mensajes", "ia_ejecuciones", "ia_llamadas_herramienta",
        "ia_ejecuciones_shell",
    }
)


def _tablas(cur) -> list[str]:
    """Todas las tablas base del esquema public (orden alfabético estable)."""
    cur.execute(
        """
        select table_name from information_schema.tables
        where table_schema = 'public' and table_type = 'BASE TABLE'
        order by table_name
        """
    )
    return [r["table_name"] for r in cur.fetchall()]


def _tablas_dominio(cur) -> list[str]:
    return [table for table in _tablas(cur) if table not in (SECURITY_TABLES | COLABORACION_TABLES)]


def _jsonb_cols(cur, tabla: str) -> set[str]:
    cur.execute(
        """
        select column_name from information_schema.columns
        where table_schema = 'public' and table_name = %s and data_type = 'jsonb'
        """,
        (tabla,),
    )
    return {r["column_name"] for r in cur.fetchall()}


def estado() -> dict:
    """Resumen experimental; las tablas de seguridad se reportan por separado."""
    with get_conn() as conn, conn.cursor() as cur:
        tablas = _tablas_dominio(cur)
        total = 0
        detalle: list[dict] = []
        for t in tablas:
            cur.execute(f'select count(*) as n from "{t}"')
            n = int(cur.fetchone()["n"])
            total += n
            if n:
                detalle.append({"tabla": t, "filas": n})
        detalle.sort(key=lambda d: d["filas"], reverse=True)
        return {
            "tablas": len(tablas),
            "filas": total,
            "detalle": detalle,
            "seedDisponible": SEED_PATH.exists(),
            "seguridadProtegida": True,
        }


def limpiar() -> dict:
    """Vacía el experimento y conserva cuentas, ACL, sesiones y auditoría."""
    with get_conn() as conn, conn.cursor() as cur:
        tablas = _tablas_dominio(cur)
        lista = ", ".join(f'"{t}"' for t in tablas)
        cur.execute(f"TRUNCATE {lista} RESTART IDENTITY CASCADE")
        conn.commit()
    return {"ok": True, "tablas": len(tablas), "seguridadConservada": True}


def exportar_sql() -> str:
    """Genera un respaldo restaurable sin contraseñas ni configuración de acceso."""
    ts = dt.datetime.now().isoformat(timespec="seconds")
    out: list[str] = [
        f"-- FagoLab · respaldo de datos · {ts}",
        "-- Restaurar: Modelo de datos -> Importar SQL, o  psql -f este_archivo.sql",
        "SET session_replication_role = replica;",
        "BEGIN;",
    ]
    # ClientCursor: hace el binding del lado cliente y expone mogrify() -> str.
    with get_conn() as conn, ClientCursor(conn) as cur:
        tablas = _tablas_dominio(cur)
        lista = ", ".join(f'"{t}"' for t in tablas)
        out.append(f"TRUNCATE {lista} RESTART IDENTITY CASCADE;")
        for t in tablas:
            cur.execute(f'select * from "{t}"')
            rows = cur.fetchall()
            if not rows:
                continue
            jb = _jsonb_cols(cur, t)
            cols = list(rows[0].keys())
            collist = ", ".join(f'"{c}"' for c in cols)
            placeholders = ", ".join(["%s"] * len(cols))
            tmpl = f'INSERT INTO "{t}" ({collist}) VALUES ({placeholders});'
            out.append(f"-- {t}: {len(rows)} fila(s)")
            for row in rows:
                vals = [
                    Jsonb(row[c]) if (c in jb and row[c] is not None) else row[c]
                    for c in cols
                ]
                out.append(cur.mogrify(tmpl, vals))
    out += ["COMMIT;", "SET session_replication_role = DEFAULT;", ""]
    return "\n".join(out)


def importar_sql(sql: str) -> dict:
    """Ejecuta un script SQL (restore). Atómico: el propio script trae BEGIN/COMMIT."""
    if not sql or not sql.strip():
        raise ValueError("El archivo SQL está vacío.")
    # Conexión en autocommit para que el BEGIN/COMMIT del script controlen la transacción.
    with get_pool().connection() as conn:
        prev = conn.autocommit
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
        finally:
            conn.autocommit = prev
    return {"ok": True, "bytes": len(sql)}


def guardar_semilla() -> dict:
    """Congela el estado actual como semilla oficial (db/seed_data.sql)."""
    sql = exportar_sql()
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_PATH.write_text(sql, encoding="utf-8")
    return {"ok": True, "ruta": str(SEED_PATH), "bytes": len(sql)}


def sembrar() -> dict:
    """Restaura la BD desde la semilla guardada (datos del Excel de la maestra)."""
    if not SEED_PATH.exists():
        raise FileNotFoundError(
            "No hay semilla guardada (db/seed_data.sql). Genera una con 'Guardar semilla'."
        )
    sql = SEED_PATH.read_text(encoding="utf-8")
    return importar_sql(sql)


# --------------------------------------------------------------------------------------
# COPIAS DE SEGURIDAD (respaldos con fecha)
# --------------------------------------------------------------------------------------

def _limpiar_viejos() -> None:
    """Conserva solo los MAX_BACKUPS respaldos más recientes."""
    copias = sorted(BACKUP_DIR.glob("respaldo_*.sql"), reverse=True)
    for viejo in copias[MAX_BACKUPS:]:
        try:
            viejo.unlink()
        except OSError:
            pass


def crear_respaldo(motivo: str = "manual") -> dict:
    """Guarda una copia de seguridad con fecha en la carpeta _backups."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    sql = exportar_sql()
    ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    ruta = BACKUP_DIR / f"respaldo_{ts}.sql"
    # Si ya hay uno de este mismo minuto, no lo dupliques.
    if not ruta.exists():
        ruta.write_text(sql, encoding="utf-8")
    _limpiar_viejos()
    return {"ok": True, "archivo": ruta.name, "bytes": len(sql), "motivo": motivo}


def listar_respaldos() -> list[dict]:
    """Lista las copias guardadas, de la más reciente a la más antigua."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(BACKUP_DIR.glob("respaldo_*.sql"), reverse=True):
        st = f.stat()
        out.append({
            "archivo": f.name,
            "bytes": st.st_size,
            "fecha": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes"),
        })
    return out


def restaurar_respaldo(archivo: str) -> dict:
    """Restaura la BD desde una copia de seguridad concreta."""
    # Seguridad: solo nombres de archivo dentro de la carpeta de respaldos.
    ruta = (BACKUP_DIR / Path(archivo).name)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el respaldo {archivo}")
    return importar_sql(ruta.read_text(encoding="utf-8"))


def respaldo_automatico_diario() -> dict | None:
    """Crea un respaldo si hoy aún no hay ninguno. Se llama al arrancar el backend."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    hoy = dt.datetime.now().strftime("%Y-%m-%d")
    ya_hay = any(f.name.startswith(f"respaldo_{hoy}") for f in BACKUP_DIR.glob("respaldo_*.sql"))
    if ya_hay:
        return None
    try:
        return crear_respaldo(motivo="automatico")
    except Exception:
        return None
