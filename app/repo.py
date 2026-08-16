"""Repositorio de datos: SQL hacia PostgreSQL.

Las consultas usan ALIAS en camelCase ("idRecepcion", "pesoG", ...) para devolver
exactamente la forma que ya consume el frontend Vue, evitando una capa de mapeo extra.

Las escrituras se hacen en TRANSACCIÓN: cada objeto rastreable se inserta primero en
objetos_laboratorio (identidad + código único) y luego en su tabla específica.
"""
from __future__ import annotations

import json
from typing import Any

from .db import get_conn

# --------------------------------------------------------------------------------------
# Helpers de códigos secuenciales
# --------------------------------------------------------------------------------------

def _count(cur, table: str) -> int:
    cur.execute(f"select count(*) as n from {table}")
    return int(cur.fetchone()["n"])


def _siguiente(cur, entidad: str) -> int:
    """Siguiente número de un contador que solo sube (no reutiliza tras borrar)."""
    cur.execute(
        """
        insert into contadores (entidad, valor) values (%s, 1)
        on conflict (entidad) do update set valor = contadores.valor + 1
        returning valor
        """,
        (entidad,),
    )
    return int(cur.fetchone()["valor"])


def _pad(n: int, w: int = 3) -> str:
    return str(n).zfill(w)


def _nuevo_objeto(cur, tipo: str, codigo: str, nombre: str | None = None) -> str:
    cur.execute(
        "insert into objetos_laboratorio (tipo_objeto, codigo, nombre) "
        "values (%s, %s, %s) returning id_objeto::text as id",
        (tipo, codigo, nombre),
    )
    return cur.fetchone()["id"]


def _etiqueta(cur, id_objeto: str, codigo: str, tipo: str) -> None:
    import json
    valor_qr = json.dumps({"codigo": codigo, "tipo": tipo})
    cur.execute(
        "insert into etiquetas_fisicas (id_objeto, codigo_visible, valor_qr, valor_barcode) "
        "values (%s, %s, %s, %s) on conflict do nothing",
        (id_objeto, codigo, valor_qr, codigo),
    )


def _lote_medio_default(cur, medio: str) -> str:
    """Devuelve (creando si hace falta) el lote de medio por defecto para un medio."""
    cur.execute(
        "select l.id_lote_medio::text as id from lotes_medio_cultivo l "
        "join medios_cultivo m on m.id_medio_cultivo = l.id_medio_cultivo "
        "where m.nombre_medio = %s and l.codigo_lote_medio like 'LM-%%-DEFAULT' limit 1",
        (medio,),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    # crea medio + lote si no existían
    cur.execute(
        "insert into medios_cultivo (nombre_medio) values (%s) "
        "on conflict (nombre_medio) do update set nombre_medio = excluded.nombre_medio "
        "returning id_medio_cultivo::text as id",
        (medio,),
    )
    id_medio = cur.fetchone()["id"]
    cur.execute(
        "insert into lotes_medio_cultivo (id_medio_cultivo, codigo_lote_medio, estado_lote) "
        "values (%s, %s, 'disponible') returning id_lote_medio::text as id",
        (id_medio, f"LM-{medio}-DEFAULT"),
    )
    return cur.fetchone()["id"]


def _proyecto_id(cur) -> str:
    cur.execute("select id_proyecto::text as id from proyectos order by created_at limit 1")
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(
        "insert into proyectos (codigo_proyecto, nombre, especie_objetivo, responsable_principal) "
        "values ('FAGO-ACUA-2026', 'Fagoterapia acuícola', 'Oreochromis niloticus', 'Pamela') "
        "returning id_proyecto::text as id"
    )
    return cur.fetchone()["id"]


# --------------------------------------------------------------------------------------
# CATÁLOGOS (órganos y medios de cultivo, ampliables desde la app)
# --------------------------------------------------------------------------------------

def list_organos() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select codigo, nombre from organos where activo order by orden, nombre"
        )
        return cur.fetchall()


def crear_organo(p: dict) -> dict:
    """Alta de un órgano nuevo. El código entra en el código de la muestra biológica."""
    nombre = (p.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("El nombre del órgano es obligatorio")
    codigo = (p.get("codigo") or nombre[:2]).strip()
    if not codigo:
        raise ValueError("El código del órgano es obligatorio")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select codigo, nombre from organos where lower(codigo)=lower(%s)", (codigo,))
            existente = cur.fetchone()
            if existente:
                raise ValueError(f"Ya existe un órgano con el código {existente['codigo']}")
            cur.execute(
                "insert into organos (codigo, nombre) values (%s,%s) returning codigo, nombre",
                (codigo, nombre),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def list_medios() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select nombre_medio as nombre, coalesce(nombre_completo,'') as completo,
                   coalesce(tipo_medio,'nutritivo') as tipo, coalesce(color,'#6b7d92') as color
            from medios_cultivo where activo order by orden, nombre_medio
            """
        )
        return cur.fetchall()


def crear_medio(p: dict) -> dict:
    """Alta de un medio de cultivo + su lote por defecto (el backend siembra contra el lote)."""
    nombre = (p.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("El nombre del medio es obligatorio")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select nombre_medio from medios_cultivo where lower(nombre_medio)=lower(%s)", (nombre,))
            if cur.fetchone():
                raise ValueError(f"Ya existe un medio llamado {nombre}")
            cur.execute(
                """
                insert into medios_cultivo (nombre_medio, nombre_completo, tipo_medio, color)
                values (%s,%s,%s,%s)
                returning nombre_medio as nombre, coalesce(nombre_completo,'') as completo,
                          coalesce(tipo_medio,'nutritivo') as tipo, coalesce(color,'#6b7d92') as color
                """,
                (nombre, p.get("completo") or nombre, p.get("tipo") or "nutritivo",
                 p.get("color") or "#6b7d92"),
            )
            row = cur.fetchone()
            # Lote por defecto, igual que hace 002_excel_core.sql para los medios base.
            cur.execute(
                """
                insert into lotes_medio_cultivo (id_medio_cultivo, codigo_lote_medio, estado_lote)
                select id_medio_cultivo, 'LM-' || nombre_medio || '-DEFAULT', 'disponible'
                from medios_cultivo where nombre_medio=%s
                on conflict (codigo_lote_medio) do nothing
                """,
                (nombre,),
            )
        conn.commit()
    return row


def list_preferencias_siembra() -> dict:
    """Cajas por combinación órgano × medio: {organo: {medio: n}}."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select organo, medio, cajas from preferencias_siembra")
        out: dict[str, dict[str, int]] = {}
        for r in cur.fetchall():
            out.setdefault(r["organo"], {})[r["medio"]] = int(r["cajas"])
        return out


def guardar_preferencias_siembra(matriz: dict) -> dict:
    """Recuerda las cantidades usadas para que sean el punto de partida del siguiente pez."""
    if not isinstance(matriz, dict):
        raise ValueError("Formato de matriz no válido")
    n = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for organo, medios in matriz.items():
                if not isinstance(medios, dict):
                    continue
                for medio, cajas in medios.items():
                    try:
                        valor = max(0, min(99, int(cajas)))
                    except (TypeError, ValueError):
                        continue
                    cur.execute(
                        """
                        insert into preferencias_siembra (organo, medio, cajas)
                        values (%s,%s,%s)
                        on conflict (organo, medio)
                        do update set cajas = excluded.cajas, updated_at = now()
                        """,
                        (organo, medio, valor),
                    )
                    n += 1
        conn.commit()
    return {"ok": True, "guardadas": n}


# --------------------------------------------------------------------------------------
# LECTURAS (forma camelCase para el frontend)
# --------------------------------------------------------------------------------------

def get_proyecto() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select codigo_proyecto as codigo, nombre, especie_objetivo as especie, "
            "responsable_principal as responsable, institucion "
            "from proyectos order by created_at limit 1"
        )
        return cur.fetchone() or {
            "codigo": "FAGO-ACUA-2026",
            "nombre": "Proyecto Tilapia 2026",
            "especie": "Oreochromis niloticus",
            "responsable": "Pamela",
            "institucion": "Laboratorio de Acuicultura",
        }


def list_recepciones() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id_recepcion::text as id, codigo_recepcion as codigo,
                   to_char(fecha_recepcion,'YYYY-MM-DD') as fecha,
                   coalesce(origen_nombre,'') as origen,
                   coalesce(especie_reportada,'') as especie,
                   coalesce(nombre_cientifico_reportado,'') as cientifico,
                   coalesce(cantidad_peces_declarada,0) as "cantidadPeces",
                   coalesce(motivo_envio,'') as motivo,
                   case when estado_registro='cerrado' then 'cerrado' else 'abierto' end as estado
            from recepciones_lote order by fecha_recepcion desc, created_at desc
            """
        )
        return cur.fetchall()


def list_peces() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select p.id_pez::text as id, p.codigo_pez as codigo,
                   p.id_recepcion::text as "idRecepcion",
                   p.numero_pez_en_lote as "numeroEnLote",
                   coalesce(p.peso_g,0)::float as "pesoG",
                   coalesce(p.longitud_total_cm,0)::float as "longitudCm",
                   coalesce(p.estado_al_recibir,'enfermo') as "estadoClinico",
                   coalesce(p.lesiones_externas,'') as lesiones,
                   m.storage_uri as "fotoUrl"
            from peces p
            left join lateral (
                select ma.storage_uri from media_vinculos mv
                join media_archivos ma on ma.id_media = mv.id_media
                where mv.id_objeto = p.id_objeto order by mv.es_principal desc, mv.created_at desc limit 1
            ) m on true
            order by p.numero_pez_en_lote
            """
        )
        return cur.fetchall()


def list_muestras() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select mb.id_muestra_biologica::text as id, mb.codigo_muestra_biologica as codigo,
                   mb.id_pez::text as "idPez", mb.organo_tejido as organo,
                   coalesce(mb.replica,'') as replica,
                   coalesce(mb.metodo_toma,'asa estéril') as "metodoToma"
            from muestras_biologicas mb order by mb.created_at
            """
        )
        return cur.fetchall()


def list_cajas() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select c.id_caja_petri::text as id, c.codigo_caja_petri as codigo,
                   c.id_muestra_biologica::text as "idMuestra",
                   med.nombre_medio as medio, c.numero_caja as numero,
                   to_char(c.fecha_siembra,'YYYY-MM-DD') as "fechaSiembra",
                   coalesce(c.estado_caja,'incubando') as estado,
                   obs.morfologia_colonial as morfologia,
                   obs.color_colonias as "colorColonia",
                   c.trazabilidad as trazabilidad,
                   m.storage_uri as "fotoUrl",
                   coalesce(m.n, 0)::int as "nFotos"
            from cajas_petri c
            join lotes_medio_cultivo l on l.id_lote_medio = c.id_lote_medio
            join medios_cultivo med on med.id_medio_cultivo = l.id_medio_cultivo
            left join lateral (
                select morfologia_colonial, color_colonias from observaciones_caja_petri o
                where o.id_caja_petri = c.id_caja_petri
                order by o.fecha_observacion desc, o.created_at desc limit 1
            ) obs on true
            left join lateral (
                select ma.storage_uri, count(*) over () as n
                from media_vinculos mv
                join media_archivos ma on ma.id_media = mv.id_media
                where mv.id_objeto = c.id_objeto
                order by mv.es_principal desc, mv.created_at desc limit 1
            ) m on true
            order by c.created_at
            """
        )
        return cur.fetchall()


def list_subcultivos() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select s.id_subcultivo::text as id, s.codigo_subcultivo as codigo,
                   col.id_caja_petri::text as "idCaja",
                   col.morfotipo, coalesce(col.color,'') as color,
                   coalesce(col.forma,'') as forma, coalesce(col.textura,'') as textura,
                   s.numero_subcultivo as numero,
                   to_char(s.fecha_siembra,'YYYY-MM-DD') as "fechaSiembra",
                   coalesce(s.resultado_pureza,'incubando') as estado,
                   s.apto_para_extraccion as "aptoExtraccion",
                   s.trazabilidad as trazabilidad,
                   m.storage_uri as "fotoUrl",
                   coalesce(m.n, 0)::int as "nFotos"
            from subcultivos_petri s
            join colonias_seleccionadas col on col.id_colonia = s.id_colonia
            left join lateral (
                select ma.storage_uri, count(*) over () as n
                from media_vinculos mv
                join media_archivos ma on ma.id_media = mv.id_media
                where mv.id_objeto = s.id_objeto
                order by mv.es_principal desc, mv.created_at desc limit 1
            ) m on true
            order by s.created_at
            """
        )
        return cur.fetchall()


def update_subcultivo(id_sub: str, p: dict) -> dict:
    """Actualiza la ficha de un subcultivo.

    color/forma/textura viven en colonias_seleccionadas (vía id_colonia);
    pureza (resultado_pureza), apto_para_extraccion y trazabilidad (JSONB)
    viven en subcultivos_petri.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id_colonia from subcultivos_petri where id_subcultivo=%s", (id_sub,))
        row = cur.fetchone()
        if not row:
            raise ValueError("subcultivo no encontrado")
        id_colonia = row["id_colonia"]

        col_sets, col_vals = [], []
        for campo in ("color", "forma", "textura"):
            if campo in p:
                col_sets.append(f"{campo}=%s")
                col_vals.append(p[campo] or None)
        if col_sets:
            col_vals.append(id_colonia)
            cur.execute(
                f"update colonias_seleccionadas set {', '.join(col_sets)} where id_colonia=%s",
                tuple(col_vals),
            )

        sub_sets, sub_vals = [], []
        if "estado" in p:
            sub_sets.append("resultado_pureza=%s")
            sub_vals.append(p["estado"])
        if "aptoExtraccion" in p:
            sub_sets.append("apto_para_extraccion=%s")
            sub_vals.append(bool(p["aptoExtraccion"]))
        if "trazabilidad" in p:
            sub_sets.append("trazabilidad=%s::jsonb")
            sub_vals.append(json.dumps(p["trazabilidad"]))
        if sub_sets:
            sub_sets.append("updated_at=now()")
            sub_vals.append(id_sub)
            cur.execute(
                f"update subcultivos_petri set {', '.join(sub_sets)} where id_subcultivo=%s",
                tuple(sub_vals),
            )
        conn.commit()
    return {"id": id_sub}


def update_caja_trazabilidad(id_caja: str, traza: Any) -> dict:
    """Guarda la trazabilidad editable (registrada/estado) de una caja en JSONB."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update cajas_petri set trazabilidad=%s::jsonb, updated_at=now() where id_caja_petri=%s",
            (json.dumps(traza), id_caja),
        )
        if cur.rowcount == 0:
            raise ValueError("caja no encontrada")
        conn.commit()
    return {"id": id_caja}


def clasificar_nanodrop(r280: float, r230: float) -> tuple[str, str]:
    """Regla de pureza del ADN. Misma que aplica al crear la lectura."""
    if 1.8 <= r280 <= 2.0 and r230 >= 2.0:
        return "optima", "Continuar a PCR"
    if r280 >= 1.7:
        return "aceptable", "Continuar a PCR"
    return "baja", "Repetir extracción"


# --------------------------------------------------------------------------------------
# BORRADO SEGURO
# --------------------------------------------------------------------------------------
# Cada registro puede tener "hijos" (una caja → subcultivos, un subcultivo → extracción…).
# Para no dejar datos huérfanos, solo se permite borrar si NO hay hijos; si los hay, se
# avisa con un mensaje claro para que la científica borre primero lo de más abajo.

def _borrar_objeto(cur, id_objeto: str) -> None:
    """Borra las etiquetas y el objeto central. Las media_vinculos caen en cascada."""
    cur.execute("delete from etiquetas_fisicas where id_objeto=%s", (id_objeto,))
    cur.execute("delete from objetos_laboratorio where id_objeto=%s", (id_objeto,))


def eliminar_media(id_media: str) -> dict:
    """Borra una foto: su vínculo, su registro y el archivo del disco."""
    import os
    from .config import settings
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select storage_uri from media_archivos where id_media=%s", (id_media,))
            row = cur.fetchone()
            if not row:
                raise ValueError("La foto no existe")
            uri = row["storage_uri"]
            cur.execute("delete from media_vinculos where id_media=%s", (id_media,))
            cur.execute("delete from media_archivos where id_media=%s", (id_media,))
        conn.commit()
    # Borra el archivo físico (si el nombre es seguro y está en MEDIA_DIR).
    try:
        nombre = os.path.basename(uri or "")
        ruta = os.path.join(settings.MEDIA_DIR, nombre)
        if nombre and os.path.isfile(ruta):
            os.remove(ruta)
    except OSError:
        pass
    return {"ok": True}


def eliminar_caja(id_caja: str) -> dict:
    """Borra una caja Petri. No se permite si ya tiene subcultivos derivados."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id_objeto::text as obj, codigo_caja_petri as cod from cajas_petri where id_caja_petri=%s", (id_caja,))
            caja = cur.fetchone()
            if not caja:
                raise ValueError("La caja no existe")
            cur.execute(
                "select count(*) as n from colonias_seleccionadas where id_caja_petri=%s", (id_caja,)
            )
            if int(cur.fetchone()["n"]) > 0:
                raise ValueError(
                    f"La caja {caja['cod']} tiene subcultivos. Borra primero sus subcultivos."
                )
            cur.execute("delete from observaciones_caja_petri where id_caja_petri=%s", (id_caja,))
            cur.execute("delete from cajas_petri where id_caja_petri=%s", (id_caja,))
            _borrar_objeto(cur, caja["obj"])
        conn.commit()
    return {"ok": True}


def eliminar_subcultivo(id_sub: str) -> dict:
    """Borra un subcultivo y su colonia. No se permite si ya tiene extracción de ADN."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select s.id_objeto::text as obj, s.codigo_subcultivo as cod, "
                "s.id_colonia::text as col, o.id_objeto::text as colobj "
                "from subcultivos_petri s join colonias_seleccionadas o on o.id_colonia=s.id_colonia "
                "where s.id_subcultivo=%s",
                (id_sub,),
            )
            sub = cur.fetchone()
            if not sub:
                raise ValueError("El subcultivo no existe")
            cur.execute("select count(*) as n from extracciones_adn where id_subcultivo=%s", (id_sub,))
            if int(cur.fetchone()["n"]) > 0:
                raise ValueError(
                    f"El subcultivo {sub['cod']} ya tiene una extracción de ADN. Bórrala primero."
                )
            cur.execute("delete from subcultivos_petri where id_subcultivo=%s", (id_sub,))
            _borrar_objeto(cur, sub["obj"])
            # La colonia solo existe para este subcultivo: se borra con él.
            cur.execute("delete from colonias_seleccionadas where id_colonia=%s", (sub["col"],))
            _borrar_objeto(cur, sub["colobj"])
        conn.commit()
    return {"ok": True}


def update_nanodrop(id_lectura: str, p: dict) -> dict:
    """Actualiza una lectura NanoDrop. Si cambian los ratios, recalcula calidad y acción."""
    sets, vals = [], []
    if "observaciones" in p:
        sets.append("observaciones=%s")
        vals.append(p["observaciones"] or None)
    if "decision" in p:
        sets.append("decision=%s::jsonb")
        vals.append(json.dumps(p["decision"]) if p["decision"] is not None else None)
    if "trazabilidad" in p:
        sets.append("trazabilidad=%s::jsonb")
        vals.append(json.dumps(p["trazabilidad"]) if p["trazabilidad"] is not None else None)

    # Edición de los números medidos: recalcula el veredicto de pureza.
    cambia_ratios = "ratio260_280" in p or "ratio260_230" in p or "concentracionNgUl" in p
    if cambia_ratios:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select ratio_260_280, ratio_260_230, concentracion_ng_ul from lecturas_nanodrop "
                "where id_lectura_nanodrop=%s",
                (id_lectura,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("lectura nanodrop no encontrada")
        r280 = float(p.get("ratio260_280") if p.get("ratio260_280") not in (None, "") else row["ratio_260_280"] or 0)
        r230 = float(p.get("ratio260_230") if p.get("ratio260_230") not in (None, "") else row["ratio_260_230"] or 0)
        conc = p.get("concentracionNgUl") if "concentracionNgUl" in p else row["concentracion_ng_ul"]
        calidad, accion = clasificar_nanodrop(r280, r230)
        sets += ["ratio_260_280=%s", "ratio_260_230=%s", "concentracion_ng_ul=%s",
                 "estado_calidad=%s", "accion_recomendada=%s"]
        vals += [r280, r230, conc if conc not in ("",) else None, calidad, accion]

    if not sets:
        return {"id": id_lectura}
    vals.append(id_lectura)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"update lecturas_nanodrop set {', '.join(sets)} where id_lectura_nanodrop=%s",
            tuple(vals),
        )
        if cur.rowcount == 0:
            raise ValueError("lectura nanodrop no encontrada")
        conn.commit()
    return {"id": id_lectura}


def list_extracciones() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select e.id_extraccion_adn::text as id, e.codigo_extraccion as codigo,
                   e.id_subcultivo::text as "idSubcultivo",
                   coalesce(e.metodo_extraccion,'CTAB') as metodo,
                   to_char(e.fecha_extraccion,'YYYY-MM-DD') as fecha,
                   'listo' as estado
            from extracciones_adn e order by e.created_at
            """
        )
        return cur.fetchall()


def list_viales() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select v.id_vial_adn::text as id, v.codigo_vial as codigo,
                   v.id_extraccion_adn::text as "idExtraccion", v.numero_vial as numero,
                   v.concentracion_ng_ul_estimada::float as "concentracionNgUl",
                   'listo' as estado
            from viales_adn v order by v.created_at
            """
        )
        return cur.fetchall()


def list_nanodrop() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select n.id_lectura_nanodrop::text as id, n.codigo_lectura as codigo,
                   n.id_vial_adn::text as "idVial",
                   to_char(n.fecha_hora_lectura,'YYYY-MM-DD') as fecha,
                   n.ratio_260_280::float as "ratio260_280",
                   n.ratio_260_230::float as "ratio260_230",
                   n.concentracion_ng_ul::float as "concentracionNgUl",
                   coalesce(n.estado_calidad,'aceptable') as calidad,
                   coalesce(n.accion_recomendada,'') as accion,
                   coalesce(n.observaciones,'') as observaciones,
                   n.decision as decision,
                   n.trazabilidad as trazabilidad
            from lecturas_nanodrop n order by n.created_at, n.codigo_lectura
            """
        )
        return cur.fetchall()


def list_pcr() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select pr.id_pcr_reaccion::text as id, pr.codigo_reaccion as codigo,
                   pr.id_vial_adn::text as "idVial",
                   coalesce(en.gen_objetivo,'16S rRNA') as "genObjetivo",
                   to_char(pr.fecha_pcr,'YYYY-MM-DD') as fecha,
                   case when pr.resultado_pcr='positivo' then 'positivo' else 'negativo' end as resultado,
                   pr.tamano_banda_observado_pb as "tamanoBandaPb"
            from pcr_reacciones pr
            join pcr_ensayos en on en.id_pcr_ensayo = pr.id_pcr_ensayo
            order by pr.created_at
            """
        )
        return cur.fetchall()


def list_viales_pendientes_pcr() -> list[dict]:
    """Viales autorizados desde NanoDrop que aún no pertenecen a una corrida.

    Esta cola es la fuente de la pantalla PCR; no depende del reporte histórico.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select v.codigo_vial as codigo,
                   row_number() over (order by n.fecha_hora_lectura, v.codigo_vial)::int as num,
                   p.codigo_pez as muestra,
                   mb.organo_tejido as organo,
                   coalesce(n.concentracion_ng_ul, v.concentracion_ng_ul_estimada)::float as ngul,
                   coalesce(r.origen_nombre, '') as lote,
                   exists (
                     select 1 from pcr_reacciones pr
                     where pr.id_vial_adn = v.id_vial_adn and pr.tipo_reaccion = 'muestra'
                   ) as "yaCorrida"
            from lecturas_nanodrop n
            join viales_adn v on v.id_vial_adn = n.id_vial_adn
            join extracciones_adn e on e.id_extraccion_adn = v.id_extraccion_adn
            join subcultivos_petri s on s.id_subcultivo = e.id_subcultivo
            join colonias_seleccionadas col on col.id_colonia = s.id_colonia
            join cajas_petri c on c.id_caja_petri = col.id_caja_petri
            join muestras_biologicas mb on mb.id_muestra_biologica = c.id_muestra_biologica
            join peces p on p.id_pez = mb.id_pez
            join recepciones_lote r on r.id_recepcion = p.id_recepcion
            where n.decision->>'estado' = 'enviada_pcr'
            order by n.fecha_hora_lectura, v.codigo_vial
            """
        )
        return cur.fetchall()


def list_geles() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select g.id_gel::text as id, g.codigo_gel as codigo,
                   to_char(g.fecha_corrida,'YYYY-MM-DD') as fecha,
                   coalesce(g.concentracion_agarosa_pct,1.2)::float as "agarosaPct",
                   coalesce(g.voltaje_v,90)::float as voltaje,
                   coalesce(g.marcador_peso_molecular,'1 Kb Plus') as marcador,
                   m.storage_uri as "imagenUrl"
            from geles_electroforesis g
            left join lateral (
                select ma.storage_uri from media_vinculos mv
                join media_archivos ma on ma.id_media = mv.id_media
                where mv.id_objeto = g.id_objeto order by mv.es_principal desc, mv.created_at desc limit 1
            ) m on true
            order by g.created_at desc
            """
        )
        geles = cur.fetchall()
        for g in geles:
            cur.execute(
                """
                select numero_carril as numero, id_pcr_reaccion::text as "idPcr",
                       tipo_carril as tipo, coalesce(codigo_muestra_visible,'') as "codigoVisible",
                       coalesce(banda_detectada,false) as banda,
                       coalesce(estado_resultado,'pendiente') as estado,
                       tamano_estimado_pb as "tamanoPb"
                from carriles_gel where id_gel = %s order by numero_carril
                """,
                (g["id"],),
            )
            g["carriles"] = cur.fetchall()
        return geles


def list_etiquetas() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select codigo, tipo_objeto as tipo, coalesce(nombre, tipo_objeto) as titulo
            from objetos_laboratorio order by created_at
            """
        )
        return cur.fetchall()


def reporte_rows() -> list[dict]:
    """Reconstruye la tabla 'Resumen' del Excel (una fila por caja/frasco) desde la BD.

    Forma de columnas espejo del Excel de Pamela:
      lote | frasco (Nº F) | muestra | organo | medio | descripcion | r280 | r230 | ngul
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select
              coalesce(r.origen_nombre, '') as lote,
              p.codigo_pez as muestra,
              p.numero_pez_en_lote as "numeroPez",
              trim(mb.organo_tejido || ' ' || coalesce(mb.replica,'')) as organo,
              med.nombre_medio as medio,
              coalesce(obs.observaciones, obs.morfologia_colonial, '') as descripcion,
              v.codigo_vial as frasco,
              nd.ratio_260_280::float as "r280",
              nd.ratio_260_230::float as "r230",
              coalesce(nd.concentracion_ng_ul, v.concentracion_ng_ul_estimada)::float as "ngul",
              r.motivo_envio as muestreo
            from cajas_petri c
            join muestras_biologicas mb on mb.id_muestra_biologica = c.id_muestra_biologica
            join peces p on p.id_pez = mb.id_pez
            join recepciones_lote r on r.id_recepcion = p.id_recepcion
            join lotes_medio_cultivo l on l.id_lote_medio = c.id_lote_medio
            join medios_cultivo med on med.id_medio_cultivo = l.id_medio_cultivo
            left join lateral (
                select observaciones, morfologia_colonial from observaciones_caja_petri o
                where o.id_caja_petri = c.id_caja_petri
                order by o.fecha_observacion desc, o.created_at desc limit 1
            ) obs on true
            left join colonias_seleccionadas col on col.id_caja_petri = c.id_caja_petri
            left join subcultivos_petri s on s.id_colonia = col.id_colonia
            left join extracciones_adn e on e.id_subcultivo = s.id_subcultivo
            left join viales_adn v on v.id_extraccion_adn = e.id_extraccion_adn
            left join lateral (
                select ratio_260_280, ratio_260_230, concentracion_ng_ul from lecturas_nanodrop n
                where n.id_vial_adn = v.id_vial_adn order by n.fecha_hora_lectura desc limit 1
            ) nd on true
            order by r.created_at, p.numero_pez_en_lote, c.created_at
            """
        )
        return cur.fetchall()


def dashboard() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        def n(sql):
            cur.execute(sql)
            return int(cur.fetchone()["n"])
        kpis = {
            "peces": n("select count(*) n from peces"),
            "cajas": n("select count(*) n from cajas_petri"),
            "conCrecimiento": n("select count(*) n from cajas_petri where estado_caja='con-crecimiento'"),
            "subcultivos": n("select count(*) n from subcultivos_petri"),
            "viales": n("select count(*) n from viales_adn"),
            "nanodrop": n("select count(*) n from lecturas_nanodrop"),
            "pcrPositivos": n("select count(*) n from pcr_reacciones where resultado_pcr='positivo'"),
        }
        cur.execute(
            """
            select med.nombre_medio as medio, count(*) as total
            from cajas_petri c join lotes_medio_cultivo l on l.id_lote_medio=c.id_lote_medio
            join medios_cultivo med on med.id_medio_cultivo=l.id_medio_cultivo
            group by med.nombre_medio order by total desc
            """
        )
        cajas_por_medio = cur.fetchall()
        return {"kpis": kpis, "cajasPorMedio": cajas_por_medio}


# --------------------------------------------------------------------------------------
# ESCRITURAS
# --------------------------------------------------------------------------------------

def crear_recepcion(p: dict) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            pid = _proyecto_id(cur)
            n = _siguiente(cur, "recepcion")
            fecha = p.get("fecha")
            codigo = f"REC-{(fecha or '2026-01-01').replace('-', '')}-{_pad(n, 2)}"
            id_obj = _nuevo_objeto(cur, "recepcion_lote", codigo, "Recepción de lote")
            cur.execute(
                """
                insert into recepciones_lote
                  (id_objeto, codigo_recepcion, id_proyecto, fecha_recepcion, responsable_recepcion,
                   cantidad_peces_declarada, especie_reportada, nombre_cientifico_reportado,
                   origen_nombre, motivo_envio)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id_recepcion::text as id
                """,
                (id_obj, codigo, pid, fecha, p.get("responsable", "Pamela"),
                 p.get("cantidadPeces"), p.get("especie"), p.get("cientifico"),
                 p.get("origen"), p.get("motivo")),
            )
            rid = cur.fetchone()["id"]
        conn.commit()
    return {"id": rid, "codigo": codigo}


def actualizar_recepcion(id_recepcion: str, p: dict) -> dict:
    """Corrige los datos de una recepción ya registrada (no toca su código ni su historial)."""
    campos = {
        "fecha": "fecha_recepcion",
        "origen": "origen_nombre",
        "especie": "especie_reportada",
        "cientifico": "nombre_cientifico_reportado",
        "cantidadPeces": "cantidad_peces_declarada",
        "motivo": "motivo_envio",
    }
    sets, valores = [], []
    for clave, columna in campos.items():
        if clave in p:
            sets.append(f"{columna} = %s")
            valores.append(p[clave])
    if not sets:
        raise ValueError("No hay cambios que guardar")

    sets.append("updated_at = now()")
    valores.append(id_recepcion)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update recepciones_lote set {', '.join(sets)} where id_recepcion = %s "
                "returning id_recepcion::text as id, codigo_recepcion as codigo",
                valores,
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Recepción no encontrada")
        conn.commit()
    return row


def registrar_aislamiento(p: dict) -> dict:
    """Crea pez + muestras (por órgano) + cajas Petri en una transacción.

    El número de cajas se toma de `matriz[organo][medio]` cuando viene; si no,
    se usa `cajasPorMuestra` para todas las combinaciones (comportamiento previo).
    """
    organos: list[str] = p.get("organos") or []
    medios: list[str] = p.get("medios") or []
    cajas_por_muestra: int = int(p.get("cajasPorMuestra") or 1)
    matriz: dict = p.get("matriz") or {}
    fecha = p.get("fechaSiembra") or "2026-01-01"

    def cajas_de(org: str, medio: str) -> int:
        if not matriz:
            return cajas_por_muestra
        try:
            return max(0, int((matriz.get(org) or {}).get(medio, 0)))
        except (TypeError, ValueError):
            return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            numero = _siguiente(cur, "pez")
            pez_codigo = f"PEZ-{_pad(numero)}"
            id_obj_pez = _nuevo_objeto(cur, "pez", pez_codigo, f"Pez {pez_codigo}")
            cur.execute(
                """
                insert into peces
                  (id_objeto, codigo_pez, id_recepcion, numero_pez_en_lote, especie_observada,
                   estado_al_recibir, peso_g, longitud_total_cm, lesiones_externas,
                   coloracion_anormal, branquias_observacion, ojos_observacion,
                   piel_observacion, abdomen_observacion, diagnostico_presuntivo, estado_flujo)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'procesado')
                returning id_pez::text as id
                """,
                (id_obj_pez, pez_codigo, p["idRecepcion"], numero, p.get("especie", "Tilapia"),
                 p.get("estadoClinico", "enfermo"), p.get("pesoG"), p.get("longitudCm"),
                 p.get("lesiones"), p.get("coloracion"), p.get("branquias"), p.get("ojos"),
                 p.get("piel"), p.get("abdomen"), p.get("diagnostico")),
            )
            id_pez = cur.fetchone()["id"]
            _etiqueta(cur, id_obj_pez, pez_codigo, "pez")

            nuevas_cajas = []
            n_caja = 0
            replicas = ["a", "b", "c"]
            for oi, org in enumerate(organos):
                # Un órgano sin cajas en ningún medio no llegó a sembrarse: no se registra muestra.
                if sum(cajas_de(org, m) for m in medios) == 0:
                    continue
                mb_codigo = f"MB-{pez_codigo}-{org}"
                id_obj_mb = _nuevo_objeto(cur, "muestra_biologica", mb_codigo, f"Muestra {org}")
                cur.execute(
                    """
                    insert into muestras_biologicas
                      (id_objeto, codigo_muestra_biologica, id_pez, tipo_muestra, organo_tejido,
                       replica, metodo_toma, fecha_toma)
                    values (%s,%s,%s,'organo',%s,%s,'asa estéril',%s)
                    returning id_muestra_biologica::text as id
                    """,
                    (id_obj_mb, mb_codigo, id_pez, org, replicas[oi % 3], fecha),
                )
                id_mb = cur.fetchone()["id"]

                for medio in medios:
                    n_medio = cajas_de(org, medio)
                    if n_medio == 0:
                        continue
                    id_lote = _lote_medio_default(cur, medio)
                    for _ in range(n_medio):
                        n_caja += 1
                        cp_codigo = f"CP-{pez_codigo}-{_pad(n_caja)}"
                        id_obj_cp = _nuevo_objeto(cur, "caja_petri", cp_codigo, f"Caja {cp_codigo}")
                        cur.execute(
                            """
                            insert into cajas_petri
                              (id_objeto, codigo_caja_petri, id_muestra_biologica, id_lote_medio,
                               numero_caja, fecha_siembra, responsable_siembra, metodo_siembra, estado_caja)
                            values (%s,%s,%s,%s,%s,%s,'Pamela','estría por agotamiento','incubando')
                            returning id_caja_petri::text as id
                            """,
                            (id_obj_cp, cp_codigo, id_mb, id_lote, n_caja, fecha),
                        )
                        id_cp = cur.fetchone()["id"]
                        _etiqueta(cur, id_obj_cp, cp_codigo, "caja_petri")
                        nuevas_cajas.append({"id": id_cp, "codigo": cp_codigo, "medio": medio, "organo": org})
        conn.commit()

    # Recuerda las cantidades para el siguiente pez. Si falla, el aislamiento ya está a salvo.
    if matriz:
        try:
            guardar_preferencias_siembra(matriz)
        except Exception:
            pass

    return {"pez": {"id": id_pez, "codigo": pez_codigo}, "cajas": nuevas_cajas}


def registrar_observacion(id_caja: str, p: dict) -> dict:
    """Registra la observación de colonia de una caja (el corazón del Excel)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into observaciones_caja_petri
                  (id_caja_petri, fecha_observacion, responsable_observacion, hay_crecimiento,
                   numero_morfotipos, morfologia_colonial, color_colonias, forma_colonias,
                   borde_colonias, elevacion_colonias, hemolisis, observaciones)
                values (%s,%s,'Pamela',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id_observacion_caja::text as id
                """,
                (id_caja, p.get("fecha", "2026-01-01"), p.get("hayCrecimiento", True),
                 p.get("numeroMorfotipos"), p.get("morfologia"), p.get("color"),
                 p.get("forma"), p.get("borde"), p.get("elevacion"),
                 p.get("hemolisis"), p.get("observaciones")),
            )
            oid = cur.fetchone()["id"]
            estado = "con-crecimiento" if p.get("hayCrecimiento", True) else "sin-crecimiento"
            cur.execute("update cajas_petri set estado_caja=%s where id_caja_petri=%s", (estado, id_caja))
        conn.commit()
    return {"id": oid}
