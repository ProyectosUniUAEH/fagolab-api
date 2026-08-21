"""Lectura del contexto del laboratorio para el análisis.

Es lo que permite que la ficha no hable de una secuencia en el vacío, sino de *esta*
muestra: de qué pez salió, de qué órgano, en qué medio creció, qué dio el NanoDrop y qué
banda mostró el gel. Sin esta herramienta la interpretación sería genérica.

Solo lee. No hay ninguna herramienta que escriba en la cadena experimental: el agente
nunca modifica un dato del laboratorio.
"""
from __future__ import annotations

from ..db import get_conn
from .base import ToolError, registrar


@registrar(
    "trazabilidad_secuenciacion",
    "Devuelve la cadena experimental completa de una secuenciación: pez, órgano, medio, "
    "observación de colonia, NanoDrop, PCR y gel. Solo lectura.",
    parametros={
        "type": "object",
        "properties": {
            "idSecuenciacion": {"type": "string", "description": "Identificador de la secuenciación."},
        },
        "required": ["idSecuenciacion"],
    },
    devuelve="Objeto con la procedencia del dato y la cadena de laboratorio si la tiene.",
    permiso="secuenciacion.records.view",
)
def trazabilidad_secuenciacion(entrada: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select sq.codigo_secuenciacion as "codigoSecuenciacion",
                   sq.origen_dato as "origenDato",
                   coalesce(sq.organismo_declarado,'') as "organismoDeclarado",
                   coalesce(sq.fuente_externa,'') as "fuenteExterna",
                   coalesce(sq.accession_externo,'') as accession,
                   coalesce(sq.plataforma,'') as plataforma,
                   coalesce(sq.tecnologia,'') as tecnologia,
                   coalesce(sq.metodo_secuenciacion,'') as "tipoSecuenciacion",
                   coalesce(g.codigo_gel,'') as "codigoGel",
                   cg.numero_carril as carril,
                   cg.tamano_estimado_pb as "bandaPb",
                   coalesce(v.codigo_vial,'') as "codigoVial",
                   nd.ratio_260_280::float as "ratio260280",
                   nd.ratio_260_230::float as "ratio260230",
                   nd.concentracion_ng_ul::float as "concentracionNgUl",
                   coalesce(nd.estado_calidad,'') as "calidadAdn",
                   coalesce(sub.codigo_subcultivo,'') as "codigoSubcultivo",
                   coalesce(sub.resultado_pureza,'') as pureza,
                   coalesce(m.nombre_medio,'') as medio,
                   obs.hay_crecimiento as "hayCrecimiento",
                   coalesce(obs.morfologia_colonial,'') as "morfologiaColonial",
                   coalesce(obs.color_colonias,'') as "colorColonias",
                   coalesce(obs.forma_colonias,'') as "formaColonias",
                   coalesce(obs.hemolisis,'') as hemolisis,
                   coalesce(obs.calidad_aislamiento,'') as "calidadAislamiento",
                   coalesce(mb.organo_tejido,'') as organo,
                   coalesce(p.codigo_pez,'') as pez,
                   coalesce(p.especie_observada, r.especie_reportada, '') as especie,
                   coalesce(p.signos_clinicos_resumen,'') as "signosClinicos",
                   coalesce(p.diagnostico_presuntivo,'') as "diagnosticoPresuntivo",
                   coalesce(r.origen_nombre,'') as lote,
                   to_char(r.fecha_recepcion,'YYYY-MM-DD') as "fechaRecepcion"
            from secuenciaciones sq
            left join geles_electroforesis g on g.id_gel = sq.id_gel
            left join carriles_gel cg on cg.id_carril_gel = sq.id_carril_gel
            left join viales_adn v on v.id_vial_adn = sq.id_vial_adn
            left join lateral (
                select * from lecturas_nanodrop l
                where l.id_vial_adn = v.id_vial_adn
                order by l.fecha_hora_lectura desc limit 1
            ) nd on true
            left join extracciones_adn e on e.id_extraccion_adn = v.id_extraccion_adn
            left join subcultivos_petri sub on sub.id_subcultivo = e.id_subcultivo
            left join colonias_seleccionadas col on col.id_colonia = sub.id_colonia
            left join cajas_petri c on c.id_caja_petri = col.id_caja_petri
            left join lotes_medio_cultivo lm on lm.id_lote_medio = c.id_lote_medio
            left join medios_cultivo m on m.id_medio_cultivo = lm.id_medio_cultivo
            left join lateral (
                select * from observaciones_caja_petri o
                where o.id_caja_petri = c.id_caja_petri
                order by o.created_at desc limit 1
            ) obs on true
            left join muestras_biologicas mb on mb.id_muestra_biologica = c.id_muestra_biologica
            left join peces p on p.id_pez = mb.id_pez
            left join recepciones_lote r on r.id_recepcion = p.id_recepcion
            where sq.id_secuenciacion = %s
            """,
            (entrada["idSecuenciacion"],),
        )
        fila = cur.fetchone()
        if not fila:
            raise ToolError("No existe esa secuenciación.")

    # La observación de colonia es el dato central del Excel de la doctora; se arma en una
    # sola frase legible porque así es como la va a leer el modelo (y una persona).
    partes = [
        fila.get("morfologiaColonial"), fila.get("colorColonias"), fila.get("formaColonias"),
    ]
    descripcion = ", ".join(p for p in partes if p)
    if fila.get("hemolisis"):
        descripcion += f" · hemólisis {fila['hemolisis']}"

    # Una secuenciación de dataset público no tiene cadena experimental, y decirlo
    # explícitamente evita que el modelo invente una procedencia de laboratorio.
    tiene_cadena = bool(fila.get("pez") or fila.get("codigoVial"))
    return {
        **fila,
        "descripcionColonia": descripcion,
        "tieneCadenaExperimental": tiene_cadena,
        "nota": (
            "Esta secuencia proviene del laboratorio y conserva su cadena completa."
            if tiene_cadena and fila["origenDato"] == "experimental"
            else "Esta secuencia no fue generada por el laboratorio: no tiene cadena experimental propia."
        ),
    }


@registrar(
    "resumen_laboratorio",
    "Cifras generales del laboratorio: peces recibidos, cajas sembradas, aislamientos, "
    "extracciones, PCR y geles. Da contexto de escala al análisis.",
    parametros={"type": "object", "properties": {}},
    devuelve="Objeto con los conteos principales del proyecto.",
    permiso="dashboard.main.view",
)
def resumen_laboratorio(_entrada: dict) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select (select count(*) from peces) as peces,
                   (select count(*) from muestras_biologicas) as muestras,
                   (select count(*) from cajas_petri) as cajas,
                   (select count(*) from subcultivos_petri) as subcultivos,
                   (select count(*) from extracciones_adn) as extracciones,
                   (select count(*) from pcr_reacciones where tipo_reaccion='muestra') as "reaccionesPcr",
                   (select count(*) from geles_electroforesis) as geles,
                   (select count(*) from secuenciaciones) as secuenciaciones
            """
        )
        return cur.fetchone()
