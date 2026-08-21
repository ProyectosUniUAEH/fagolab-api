"""Repositorio de secuenciación: candidatos, secuenciaciones y archivos FASTQ/FASTA.

El flujo real del laboratorio llega hoy hasta electroforesis. De ahí en adelante:

    carril de gel con banda  ->  candidato a secuenciación
    candidato                ->  secuenciación (plataforma, tecnología, layout)
    secuenciación            ->  archivos FASTQ (crudo) / FASTA (procesado)
    archivo                  ->  validación + QC determinista (app/seq_qc.py)

Toda secuenciación declara su `origen_dato`: experimental, publico_ncbi o sintetico.
Es la garantía de que una demostración con datos públicos nunca se lea como un
resultado del laboratorio.
"""
from __future__ import annotations

from datetime import date
import json

from .db import get_conn
from .repo import _siguiente, _pad, _nuevo_objeto, _etiqueta

ORIGENES = ("experimental", "publico_ncbi", "sintetico")
ESTADOS = ("pendiente", "enviada", "secuenciada", "analizada", "fallida")
ROLES_ARCHIVO = ("R1", "R2", "consenso", "contigs", "referencia", "otro")

# Etiqueta legible de la procedencia, para que la UI y la ficha usen el mismo lenguaje.
ETIQUETA_ORIGEN = {
    "experimental": "Experimental (este laboratorio)",
    "publico_ncbi": "Dataset público NCBI",
    "sintetico": "Sintético / demostración",
}


def _valida_origen(origen: str) -> str:
    if origen not in ORIGENES:
        raise ValueError(f"Origen de dato inválido: {origen}")
    return origen


# --------------------------------------------------------------------------------------
# Candidatos: qué puede pasar a secuenciación
# --------------------------------------------------------------------------------------

def list_candidatos() -> list[dict]:
    """Carriles de gel con banda positiva que todavía no se enviaron a secuenciar.

    Es la continuación natural de la vista de electroforesis: la doctora ya decidió
    qué muestras amplificaron bien; esta cola es la que se manda al secuenciador.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select cg.id_carril_gel::text as "idCarril",
                   g.id_gel::text as "idGel",
                   g.codigo_gel as "codigoGel",
                   to_char(g.fecha_corrida,'YYYY-MM-DD') as "fechaGel",
                   cg.numero_carril as carril,
                   coalesce(cg.codigo_muestra_visible,'') as "codigoVisible",
                   cg.tamano_estimado_pb as "tamanoPb",
                   pr.id_pcr_reaccion::text as "idPcr",
                   pr.codigo_reaccion as "codigoPcr",
                   v.id_vial_adn::text as "idVial",
                   v.codigo_vial as "codigoVial",
                   p.codigo_pez as pez,
                   mb.organo_tejido as organo,
                   coalesce(r.origen_nombre,'') as lote
            from carriles_gel cg
            join geles_electroforesis g on g.id_gel = cg.id_gel
            join pcr_reacciones pr on pr.id_pcr_reaccion = cg.id_pcr_reaccion
            left join viales_adn v on v.id_vial_adn = pr.id_vial_adn
            left join extracciones_adn e on e.id_extraccion_adn = v.id_extraccion_adn
            left join subcultivos_petri s on s.id_subcultivo = e.id_subcultivo
            left join colonias_seleccionadas col on col.id_colonia = s.id_colonia
            left join cajas_petri c on c.id_caja_petri = col.id_caja_petri
            left join muestras_biologicas mb on mb.id_muestra_biologica = c.id_muestra_biologica
            left join peces p on p.id_pez = mb.id_pez
            left join recepciones_lote r on r.id_recepcion = p.id_recepcion
            where cg.tipo_carril = 'muestra'
              and coalesce(cg.estado_resultado, case when cg.banda_detectada then 'positivo' else 'pendiente' end) = 'positivo'
              and not exists (
                    select 1 from secuenciaciones sq
                    where sq.id_carril_gel = cg.id_carril_gel
                       or (sq.id_pcr_reaccion is not null and sq.id_pcr_reaccion = pr.id_pcr_reaccion)
              )
            order by g.fecha_corrida desc, cg.numero_carril
            """
        )
        return cur.fetchall()


# --------------------------------------------------------------------------------------
# Secuenciaciones
# --------------------------------------------------------------------------------------

_SELECT_SEC = """
    select sq.id_secuenciacion::text as id,
           sq.codigo_secuenciacion as codigo,
           sq.origen_dato as "origenDato",
           coalesce(sq.fuente_externa,'') as "fuenteExterna",
           coalesce(sq.accession_externo,'') as accession,
           coalesce(sq.organismo_declarado,'') as "organismoDeclarado",
           coalesce(sq.plataforma,'') as plataforma,
           coalesce(sq.tecnologia,'') as tecnologia,
           coalesce(sq.metodo_secuenciacion,'') as "tipoSecuenciacion",
           coalesce(sq.layout,'') as layout,
           coalesce(sq.laboratorio,'') as laboratorio,
           coalesce(sq.proveedor,'') as proveedor,
           to_char(sq.fecha_secuenciacion,'YYYY-MM-DD') as "fechaSecuenciacion",
           to_char(sq.fecha_envio,'YYYY-MM-DD') as "fechaEnvio",
           sq.estado_secuenciacion as estado,
           coalesce(sq.notas_procedencia,'') as "notasProcedencia",
           coalesce(sq.observaciones,'') as observaciones,
           sq.id_gel::text as "idGel",
           sq.id_carril_gel::text as "idCarril",
           sq.id_pcr_reaccion::text as "idPcr",
           sq.id_vial_adn::text as "idVial",
           coalesce(g.codigo_gel,'') as "codigoGel",
           cg.numero_carril as carril,
           coalesce(v.codigo_vial,'') as "codigoVial",
           coalesce(p.codigo_pez,'') as pez,
           coalesce(mb.organo_tejido,'') as organo
    from secuenciaciones sq
    left join geles_electroforesis g on g.id_gel = sq.id_gel
    left join carriles_gel cg on cg.id_carril_gel = sq.id_carril_gel
    left join viales_adn v on v.id_vial_adn = sq.id_vial_adn
    left join extracciones_adn e on e.id_extraccion_adn = v.id_extraccion_adn
    left join subcultivos_petri s on s.id_subcultivo = e.id_subcultivo
    left join colonias_seleccionadas col on col.id_colonia = s.id_colonia
    left join cajas_petri c on c.id_caja_petri = col.id_caja_petri
    left join muestras_biologicas mb on mb.id_muestra_biologica = c.id_muestra_biologica
    left join peces p on p.id_pez = mb.id_pez
"""

_SELECT_ARCHIVOS = """
    select id_archivo_secuencia::text as id, formato, rol,
           nombre_archivo as "nombreArchivo", storage_uri as "storageUri",
           comprimido, size_bytes as "sizeBytes", sha256,
           estado_validacion as "estadoValidacion", semaforo_qc as "semaforo",
           metricas, hallazgos, origen_dato as "origenDato",
           coalesce(subido_por,'') as "subidoPor",
           to_char(created_at,'YYYY-MM-DD HH24:MI') as "creadoEn"
    from archivos_secuencia
    where id_secuenciacion = %s
    order by created_at
"""


def list_secuenciaciones() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_SELECT_SEC + " order by sq.created_at desc")
        secuenciaciones = cur.fetchall()
        for sec in secuenciaciones:
            cur.execute(_SELECT_ARCHIVOS, (sec["id"],))
            sec["archivos"] = cur.fetchall()
            sec["origenEtiqueta"] = ETIQUETA_ORIGEN.get(sec["origenDato"], sec["origenDato"])
        return secuenciaciones


def get_secuenciacion(id_secuenciacion: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_SELECT_SEC + " where sq.id_secuenciacion = %s", (id_secuenciacion,))
        sec = cur.fetchone()
        if not sec:
            raise ValueError("Secuenciación no encontrada")
        cur.execute(_SELECT_ARCHIVOS, (id_secuenciacion,))
        sec["archivos"] = cur.fetchall()
        sec["origenEtiqueta"] = ETIQUETA_ORIGEN.get(sec["origenDato"], sec["origenDato"])
        return sec


def crear_secuenciacion(p: dict) -> dict:
    """Registra una secuenciación.

    Puede venir de un carril de gel candidato (`idCarril`) o ser una entrada
    independiente para datos públicos/sintéticos. Cuando el origen no es experimental
    se exige declarar la fuente: sin eso, el registro perdería su trazabilidad.
    """
    origen = _valida_origen(p.get("origenDato") or "experimental")
    if origen != "experimental" and not (p.get("fuenteExterna") or "").strip():
        raise ValueError("Un dato público o sintético debe declarar su fuente.")

    estado = p.get("estado") or "pendiente"
    if estado not in ESTADOS:
        raise ValueError(f"Estado de secuenciación inválido: {estado}")

    id_carril = p.get("idCarril") or None
    id_pcr = p.get("idPcr") or None
    id_gel = p.get("idGel") or None
    id_vial = p.get("idVial") or None

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Si viene de un carril, se completa la cadena hacia atrás desde la BD:
            # el frontend no tiene por qué conocer los ids intermedios.
            if id_carril:
                cur.execute(
                    """
                    select cg.id_gel::text as "idGel", cg.id_pcr_reaccion::text as "idPcr",
                           pr.id_vial_adn::text as "idVial"
                    from carriles_gel cg
                    left join pcr_reacciones pr on pr.id_pcr_reaccion = cg.id_pcr_reaccion
                    where cg.id_carril_gel = %s
                    """,
                    (id_carril,),
                )
                fila = cur.fetchone()
                if not fila:
                    raise ValueError("El carril de gel indicado no existe.")
                id_gel = id_gel or fila["idGel"]
                id_pcr = id_pcr or fila["idPcr"]
                id_vial = id_vial or fila["idVial"]

            n = _siguiente(cur, "secuenciacion")
            prefijo = {"experimental": "SEQ", "publico_ncbi": "SEQ-PUB", "sintetico": "SEQ-DEMO"}[origen]
            codigo = f"{prefijo}-{_pad(n)}"
            id_obj = _nuevo_objeto(cur, "secuenciacion", codigo, "Secuenciación")

            cur.execute(
                """
                insert into secuenciaciones
                  (id_objeto, codigo_secuenciacion, id_pcr_reaccion, id_gel, id_carril_gel,
                   id_vial_adn, origen_dato, fuente_externa, accession_externo,
                   organismo_declarado, plataforma, tecnologia, metodo_secuenciacion, layout,
                   laboratorio, proveedor, fecha_secuenciacion, fecha_envio,
                   estado_secuenciacion, notas_procedencia, observaciones)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id_secuenciacion::text as id
                """,
                (
                    id_obj, codigo, id_pcr, id_gel, id_carril, id_vial, origen,
                    (p.get("fuenteExterna") or "").strip() or None,
                    (p.get("accession") or "").strip() or None,
                    (p.get("organismoDeclarado") or "").strip() or None,
                    (p.get("plataforma") or "").strip() or None,
                    (p.get("tecnologia") or "").strip() or None,
                    (p.get("tipoSecuenciacion") or "").strip() or None,
                    (p.get("layout") or "").strip() or None,
                    (p.get("laboratorio") or "").strip() or None,
                    (p.get("proveedor") or "").strip() or None,
                    p.get("fechaSecuenciacion") or date.today().isoformat(),
                    p.get("fechaEnvio") or None,
                    estado,
                    (p.get("notasProcedencia") or "").strip() or None,
                    (p.get("observaciones") or "").strip() or None,
                ),
            )
            id_sec = cur.fetchone()["id"]
            _etiqueta(cur, id_obj, codigo, "secuenciacion")
        conn.commit()
    return {"id": id_sec, "codigo": codigo, "origenDato": origen}


def actualizar_secuenciacion(id_secuenciacion: str, p: dict) -> dict:
    """Actualiza los campos editables. Solo toca lo que venga en el payload."""
    campos = {
        "plataforma": "plataforma",
        "tecnologia": "tecnologia",
        "tipoSecuenciacion": "metodo_secuenciacion",
        "layout": "layout",
        "laboratorio": "laboratorio",
        "proveedor": "proveedor",
        "organismoDeclarado": "organismo_declarado",
        "accession": "accession_externo",
        "fuenteExterna": "fuente_externa",
        "notasProcedencia": "notas_procedencia",
        "observaciones": "observaciones",
        "fechaSecuenciacion": "fecha_secuenciacion",
        "fechaEnvio": "fecha_envio",
    }
    sets: list[str] = []
    valores: list = []
    for clave, columna in campos.items():
        if clave in p:
            sets.append(f"{columna} = %s")
            valores.append((p[clave] or None) if isinstance(p[clave], str) else p[clave])
    if "estado" in p:
        if p["estado"] not in ESTADOS:
            raise ValueError(f"Estado de secuenciación inválido: {p['estado']}")
        sets.append("estado_secuenciacion = %s")
        valores.append(p["estado"])
    if "origenDato" in p:
        sets.append("origen_dato = %s")
        valores.append(_valida_origen(p["origenDato"]))
    if not sets:
        return {"id": id_secuenciacion}

    valores.append(id_secuenciacion)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update secuenciaciones set {', '.join(sets)} where id_secuenciacion = %s",
                tuple(valores),
            )
            if cur.rowcount == 0:
                raise ValueError("Secuenciación no encontrada")
        conn.commit()
    return {"id": id_secuenciacion}


def eliminar_secuenciacion(id_secuenciacion: str) -> dict:
    """Borra la secuenciación, sus archivos y su evidencia externa (cascada en BD)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id_objeto::text as id from secuenciaciones where id_secuenciacion=%s",
                (id_secuenciacion,),
            )
            fila = cur.fetchone()
            if not fila:
                raise ValueError("Secuenciación no encontrada")
            cur.execute("delete from secuenciaciones where id_secuenciacion=%s", (id_secuenciacion,))
            if fila["id"]:
                cur.execute("delete from etiquetas_fisicas where id_objeto=%s", (fila["id"],))
                cur.execute("delete from objetos_laboratorio where id_objeto=%s", (fila["id"],))
        conn.commit()
    return {"ok": True}


# --------------------------------------------------------------------------------------
# Archivos de secuencia
# --------------------------------------------------------------------------------------

def guardar_archivo(id_secuenciacion: str, datos: dict) -> dict:
    """Registra un archivo ya validado por `seq_qc.validar`.

    El resultado de la validación se guarda tal cual (métricas + hallazgos): es la
    evidencia objetiva que después leerá el modelo generativo para redactar la ficha.
    """
    rol = datos.get("rol") or "consenso"
    if rol not in ROLES_ARCHIVO:
        raise ValueError(f"Rol de archivo inválido: {rol}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select origen_dato as origen from secuenciaciones where id_secuenciacion=%s",
                (id_secuenciacion,),
            )
            sec = cur.fetchone()
            if not sec:
                raise ValueError("Secuenciación no encontrada")

            cur.execute(
                """
                insert into archivos_secuencia
                  (id_secuenciacion, formato, rol, nombre_archivo, storage_uri, comprimido,
                   size_bytes, sha256, estado_validacion, semaforo_qc, metricas, hallazgos,
                   origen_dato, subido_por)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (id_secuenciacion, rol, nombre_archivo) do update set
                  storage_uri = excluded.storage_uri,
                  size_bytes = excluded.size_bytes,
                  sha256 = excluded.sha256,
                  comprimido = excluded.comprimido,
                  formato = excluded.formato,
                  estado_validacion = excluded.estado_validacion,
                  semaforo_qc = excluded.semaforo_qc,
                  metricas = excluded.metricas,
                  hallazgos = excluded.hallazgos,
                  created_at = now()
                returning id_archivo_secuencia::text as id
                """,
                (
                    id_secuenciacion,
                    datos.get("formato") or "fasta",
                    rol,
                    datos["nombreArchivo"],
                    datos["storageUri"],
                    bool(datos.get("comprimido")),
                    datos.get("sizeBytes"),
                    datos.get("sha256"),
                    "valido" if datos.get("valido") else "invalido",
                    datos.get("semaforo"),
                    json.dumps(datos.get("metricas") or {}),
                    json.dumps(datos.get("hallazgos") or []),
                    sec["origen"],
                    datos.get("subidoPor") or "",
                ),
            )
            id_archivo = cur.fetchone()["id"]

            # Un archivo válido deja la secuenciación lista para analizar; además se
            # copian a la cabecera las métricas de consenso, que es lo que se consulta
            # desde el resto del sistema (reportes, ficha, trazabilidad).
            metricas = datos.get("metricas") or {}
            if datos.get("valido"):
                cur.execute(
                    """
                    update secuenciaciones
                       set estado_secuenciacion = case
                             when estado_secuenciacion in ('pendiente','enviada') then 'secuenciada'
                             else estado_secuenciacion end,
                           longitud_consenso_pb = coalesce(%s, longitud_consenso_pb),
                           calidad_promedio = coalesce(%s, calidad_promedio),
                           archivo_fasta_url = case when %s = 'fasta' then %s else archivo_fasta_url end
                     where id_secuenciacion = %s
                    """,
                    (
                        metricas.get("longitudTotalPb"),
                        metricas.get("calidadPromedio"),
                        datos.get("formato"),
                        datos["storageUri"],
                        id_secuenciacion,
                    ),
                )
        conn.commit()
    return {"id": id_archivo, "semaforo": datos.get("semaforo"), "valido": bool(datos.get("valido"))}


def eliminar_archivo(id_archivo: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from archivos_secuencia where id_archivo_secuencia=%s "
                "returning storage_uri as uri",
                (id_archivo,),
            )
            fila = cur.fetchone()
            if not fila:
                raise ValueError("Archivo no encontrado")
        conn.commit()
    return {"ok": True, "storageUri": fila["uri"]}


def contenido_archivo(id_archivo: str) -> dict:
    """Metadatos necesarios para leer el archivo del disco (BLAST, descarga, vista previa)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select a.id_archivo_secuencia::text as id, a.formato, a.rol,
                   a.nombre_archivo as "nombreArchivo", a.storage_uri as "storageUri",
                   a.comprimido, a.origen_dato as "origenDato",
                   a.id_secuenciacion::text as "idSecuenciacion",
                   s.codigo_secuenciacion as "codigoSecuenciacion"
            from archivos_secuencia a
            join secuenciaciones s on s.id_secuenciacion = a.id_secuenciacion
            where a.id_archivo_secuencia = %s
            """,
            (id_archivo,),
        )
        fila = cur.fetchone()
        if not fila:
            raise ValueError("Archivo no encontrado")
        return fila
