"""Repositorio de la cadena molecular y de media (imágenes)."""
from __future__ import annotations

from datetime import date

from .db import get_conn
from .repo import _count, _siguiente, _pad, _nuevo_objeto, _etiqueta, _lote_medio_default


def crear_subcultivo(id_caja: str, p: dict) -> dict:
    """Crea una colonia seleccionada (a partir de la caja) y su subcultivo de purificación."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select codigo_caja_petri as c from cajas_petri where id_caja_petri=%s", (id_caja,))
            row = cur.fetchone()
            if not row:
                raise ValueError("caja no encontrada")
            base = row["c"].replace("CP-", "")
            # colonia
            ncol = _siguiente(cur, "colonia")
            col_codigo = f"COL-{base}-{_pad(ncol)}"
            id_obj_col = _nuevo_objeto(cur, "colonia", col_codigo, "Colonia seleccionada")
            cur.execute(
                """
                insert into colonias_seleccionadas
                  (id_objeto, codigo_colonia, id_caja_petri, numero_colonia, morfotipo, color, forma,
                   borde, elevacion, textura, fecha_seleccion, responsable_seleccion, motivo_seleccion)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Pamela',%s)
                returning id_colonia::text as id
                """,
                (id_obj_col, col_codigo, id_caja, ncol, p.get("morfotipo", "A"), p.get("color"),
                 p.get("forma"), p.get("borde"), p.get("elevacion"), p.get("textura"),
                 p.get("fechaSiembra", "2026-01-01"), p.get("motivo", "Colonia representativa")),
            )
            id_col = cur.fetchone()["id"]
            # subcultivo
            nsub = _siguiente(cur, "subcultivo")
            sub_codigo = f"SUB-{base}-{_pad(nsub)}"
            id_obj_sub = _nuevo_objeto(cur, "subcultivo_petri", sub_codigo, "Subcultivo")
            id_lote = _lote_medio_default(cur, p.get("medio", "TSA"))
            cur.execute(
                """
                insert into subcultivos_petri
                  (id_objeto, codigo_subcultivo, id_colonia, id_lote_medio, numero_subcultivo,
                   fecha_siembra, responsable_siembra, metodo_siembra, estado_subcultivo,
                   resultado_pureza, apto_para_extraccion)
                values (%s,%s,%s,%s,%s,%s,'Pamela','estría por agotamiento','incubando',%s,%s)
                returning id_subcultivo::text as id
                """,
                (id_obj_sub, sub_codigo, id_col, id_lote, nsub, p.get("fechaSiembra", "2026-01-01"),
                 p.get("resultadoPureza", "puro"), bool(p.get("aptoExtraccion", True))),
            )
            id_sub = cur.fetchone()["id"]
            _etiqueta(cur, id_obj_sub, sub_codigo, "subcultivo_petri")
        conn.commit()
    return {"id": id_sub, "codigo": sub_codigo}


def crear_subcultivos(id_caja: str, p: dict) -> dict:
    """Registra varias colonias de una misma caja y su subcultivo, en una sola transacción.

    A diferencia de `crear_subcultivo`, numera las colonias de forma consecutiva DENTRO de
    la caja (1, 2, 3...), que es lo que exige el UNIQUE (id_caja_petri, numero_colonia);
    el contador global anterior daba números sin relación con la caja.
    """
    filas: list[dict] = p.get("subcultivos") or []
    if not filas:
        raise ValueError("No hay subcultivos que registrar")

    medio = p.get("medio") or "TSA"
    fecha = p.get("fechaSiembra") or date.today().isoformat()
    creados: list[dict] = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select codigo_caja_petri as c from cajas_petri where id_caja_petri=%s", (id_caja,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Caja no encontrada")
            base = row["c"].replace("CP-", "", 1)

            # Continúa la numeración de colonias ya existentes en esta caja.
            cur.execute(
                "select coalesce(max(numero_colonia),0) as n from colonias_seleccionadas where id_caja_petri=%s",
                (id_caja,),
            )
            ncol = int(cur.fetchone()["n"])
            id_lote = _lote_medio_default(cur, medio)

            for f in filas:
                ncol += 1
                sufijo = _pad(ncol, 2)
                col_codigo = f"COL-{base}-{sufijo}"
                id_obj_col = _nuevo_objeto(cur, "colonia", col_codigo, "Colonia seleccionada")
                cur.execute(
                    """
                    insert into colonias_seleccionadas
                      (id_objeto, codigo_colonia, id_caja_petri, numero_colonia, morfotipo, color, forma,
                       borde, elevacion, textura, fecha_seleccion, responsable_seleccion, motivo_seleccion)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Pamela',%s)
                    returning id_colonia::text as id
                    """,
                    (id_obj_col, col_codigo, id_caja, ncol, f.get("morfotipo") or "A", f.get("color"),
                     f.get("forma"), f.get("borde"), f.get("elevacion"), f.get("textura"),
                     fecha, f.get("motivo") or "Colonia representativa"),
                )
                id_col = cur.fetchone()["id"]

                sub_codigo = f"SUB-{base}-{sufijo}"
                id_obj_sub = _nuevo_objeto(cur, "subcultivo_petri", sub_codigo, "Subcultivo")
                cur.execute(
                    """
                    insert into subcultivos_petri
                      (id_objeto, codigo_subcultivo, id_colonia, id_lote_medio, numero_subcultivo,
                       fecha_siembra, responsable_siembra, metodo_siembra, estado_subcultivo,
                       resultado_pureza, apto_para_extraccion)
                    values (%s,%s,%s,%s,1,%s,'Pamela','estría por agotamiento','incubando',%s,%s)
                    returning id_subcultivo::text as id
                    """,
                    (id_obj_sub, sub_codigo, id_col, id_lote, fecha,
                     f.get("resultadoPureza") or "puro", bool(f.get("aptoExtraccion", True))),
                )
                id_sub = cur.fetchone()["id"]
                _etiqueta(cur, id_obj_sub, sub_codigo, "subcultivo_petri")
                creados.append({"id": id_sub, "codigo": sub_codigo, "morfotipo": f.get("morfotipo") or "A"})
        conn.commit()
    return {"creados": creados}


def crear_extraccion(id_subcultivo: str, p: dict) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            n = _siguiente(cur, "extraccion")
            codigo = f"EXT-{_pad(n)}"
            id_obj = _nuevo_objeto(cur, "extraccion_adn", codigo, "Extracción de ADN")
            cur.execute(
                """
                insert into extracciones_adn
                  (id_objeto, codigo_extraccion, id_subcultivo, fecha_extraccion, responsable_extraccion,
                   metodo_extraccion, estado_extraccion)
                values (%s,%s,%s,%s,'Pamela',%s,'pendiente_nanodrop')
                returning id_extraccion_adn::text as id
                """,
                (id_obj, codigo, id_subcultivo, p.get("fecha", "2026-01-01"),
                 p.get("metodo", "CTAB / fenol-cloroformo")),
            )
            id_ext = cur.fetchone()["id"]
            # vial asociado (1 por extracción en v1)
            codigo_vial = codigo.replace("EXT-", "VIAL-")
            id_obj_v = _nuevo_objeto(cur, "vial_adn", codigo_vial, "Vial de ADN")
            cur.execute(
                """
                insert into viales_adn
                  (id_objeto, codigo_vial, id_extraccion_adn, numero_vial,
                   concentracion_ng_ul_estimada, estado_vial)
                values (%s,%s,%s,1,%s,'pendiente_nanodrop')
                returning id_vial_adn::text as id
                """,
                (id_obj_v, codigo_vial, id_ext, p.get("concentracionNgUl")),
            )
            id_vial = cur.fetchone()["id"]
            _etiqueta(cur, id_obj_v, codigo_vial, "vial_adn")
        conn.commit()
    return {"extraccion": {"id": id_ext, "codigo": codigo}, "vial": {"id": id_vial, "codigo": codigo_vial}}


def crear_nanodrop(id_vial: str, p: dict) -> dict:
    r280 = float(p.get("ratio260_280") or 0)
    r230 = float(p.get("ratio260_230") or 0)
    if 1.8 <= r280 <= 2.0 and r230 >= 2.0:
        calidad, accion = "optima", "Continuar a PCR"
    elif r280 >= 1.7:
        calidad, accion = "aceptable", "Continuar a PCR"
    else:
        calidad, accion = "baja", "Repetir extracción"
    with get_conn() as conn:
        with conn.cursor() as cur:
            n = _siguiente(cur, "nanodrop")
            codigo = f"ND-{_pad(n)}"
            cur.execute(
                """
                insert into lecturas_nanodrop
                  (codigo_lectura, id_vial_adn, numero_lectura, fecha_hora_lectura, operador,
                   ratio_260_280, ratio_260_230, concentracion_ng_ul, estado_calidad, accion_recomendada)
                values (%s,%s,1,%s,'Pamela',%s,%s,%s,%s,%s)
                returning id_lectura_nanodrop::text as id
                """,
                (codigo, id_vial, (p.get("fecha", "2026-01-01") + " 10:00"), r280, r230,
                 p.get("concentracionNgUl"), calidad, accion),
            )
            nid = cur.fetchone()["id"]
            cur.execute("update viales_adn set estado_vial='leido' where id_vial_adn=%s", (id_vial,))
        conn.commit()
    return {"id": nid, "codigo": codigo, "calidad": calidad, "accion": accion}


def crear_pcr(id_vial: str, p: dict) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id_pcr_ensayo::text as id from pcr_ensayos where codigo_ensayo='PCR-16S'")
            ens = cur.fetchone()
            id_ens = ens["id"] if ens else None
            if id_ens is None:
                cur.execute(
                    "insert into pcr_ensayos (codigo_ensayo, nombre_ensayo, gen_objetivo, tamano_amplicon_esperado_pb) "
                    "values ('PCR-16S','Identificación 16S rRNA','16S rRNA',1500) returning id_pcr_ensayo::text as id"
                )
                id_ens = cur.fetchone()["id"]
            n = _siguiente(cur, "pcr")
            codigo = f"PCR-{_pad(n)}"
            id_obj = _nuevo_objeto(cur, "pcr_reaccion", codigo, "Reacción PCR 16S")
            resultado = p.get("resultado", "positivo")
            cur.execute(
                """
                insert into pcr_reacciones
                  (id_objeto, codigo_reaccion, id_vial_adn, id_pcr_ensayo, fecha_pcr, responsable_pcr,
                   resultado_pcr, tamano_banda_observado_pb, calidad_resultado)
                values (%s,%s,%s,%s,%s,'Pamela',%s,%s,%s)
                returning id_pcr_reaccion::text as id
                """,
                (id_obj, codigo, id_vial, id_ens, p.get("fecha", "2026-01-01"), resultado,
                 p.get("tamanoBandaPb", 1500 if resultado == "positivo" else None),
                 p.get("calidad", "buena")),
            )
            pid = cur.fetchone()["id"]
        conn.commit()
    return {"id": pid, "codigo": codigo}


# Estados de resultado válidos por pozo y los que retroalimentan la reacción.
ESTADOS_POZO = ("pendiente", "positivo", "negativo", "no_claro", "no_revisado")
_NOTA_CARGA_DEFAULT = (
    "Carga sugerida registrada por el usuario: 2 µL de buffer green y 5 µL del tubo con mix y ADN."
)


def generar_pozos_desde_corrida(id_corrida: str) -> dict:
    """Genera la plantilla de pozos de un gel a partir de una corrida de PCR.

    Orden por defecto (sin filas de marcador entre las muestras):
      Pozo 1 = Blanco / control negativo (fila de control, aunque no tenga muestra)
      Pozo 2 = Control positivo (enlazable a la caja de positivos)
      Pozo 3+ = las muestras de la corrida, en orden.
    El marcador queda como metadato del gel, no como pozo.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select co.codigo_corrida as codigo, co.id_control_positivo::text as "idControlPositivo",
                   cp.etiqueta as "controlEtiqueta", coalesce(co.lotes,'') as lotes
            from pcr_corridas co
            left join controles_positivos cp on cp.id_control_positivo = co.id_control_positivo
            where co.id_corrida_pcr = %s
            """,
            (id_corrida,),
        )
        corr = cur.fetchone()
        if not corr:
            raise ValueError("corrida no encontrada")
        cur.execute(
            """
            select id_pcr_reaccion::text as id, numero_tubo, tipo_reaccion as tipo,
                   coalesce(codigo_muestra_visible,'') as frasco, coalesce(lote,'') as lote,
                   coalesce(resultado_pcr,'pendiente') as resultado, tamano_banda_observado_pb as "tamanoPb"
            from pcr_reacciones where id_corrida_pcr=%s order by numero_tubo
            """,
            (id_corrida,),
        )
        reacciones = cur.fetchall()

    blanco = next((r for r in reacciones if r["tipo"] == "blanco"), None)
    positivo = next((r for r in reacciones if r["tipo"] == "positivo"), None)
    muestras = [r for r in reacciones if r["tipo"] == "muestra"]

    def _estado(resultado: str) -> str:
        return resultado if resultado in ESTADOS_POZO else "pendiente"

    pozos: list[dict] = []
    numero = 1
    # 1) Blanco / control negativo (siempre, aunque no esté enlazado a una muestra)
    pozos.append({
        "numero": numero, "tipo": "blanco",
        "idPcr": blanco["id"] if blanco else None,
        "idControlPositivo": None,
        "codigoVisible": "Blanco / control −",
        "estado": _estado(blanco["resultado"]) if blanco else "pendiente",
        "tamanoPb": None,
    })
    numero += 1
    # 2) Control positivo (enlazado a la caja de positivos si la corrida tenía uno)
    pozos.append({
        "numero": numero, "tipo": "positivo",
        "idPcr": positivo["id"] if positivo else None,
        "idControlPositivo": corr.get("idControlPositivo"),
        "codigoVisible": corr.get("controlEtiqueta") or "Control +",
        "estado": _estado(positivo["resultado"]) if positivo else "pendiente",
        "tamanoPb": None,
    })
    numero += 1
    # 3+) Las muestras de la corrida, en orden
    for m in muestras:
        pozos.append({
            "numero": numero, "tipo": "muestra",
            "idPcr": m["id"], "idControlPositivo": None,
            "codigoVisible": m["frasco"] or f"Tubo {m['numero_tubo']}",
            "estado": _estado(m["resultado"]),
            "tamanoPb": m["tamanoPb"],
        })
        numero += 1

    return {
        "idCorrida": id_corrida,
        "codigoCorrida": corr["codigo"],
        "lotes": corr["lotes"],
        "notaSugerida": _NOTA_CARGA_DEFAULT,
        "pozos": pozos,
    }


def crear_gel(p: dict) -> dict:
    """Registra un gel de electroforesis con sus carriles.

    El estado de resultado de cada pozo (positivo / negativo / no_claro / …)
    retroalimenta el resultado de la reacción enlazada: un pozo de muestra
    marcado positivo deja esa muestra positiva en toda la cadena. Solo
    'positivo' y 'negativo' actualizan la reacción; los estados indeterminados
    (no_claro / no_revisado / pendiente) no la sobreescriben.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            n = _siguiente(cur, "gel")
            fecha = p.get("fecha", "2026-01-01")
            codigo = f"GEL-{fecha.replace('-', '-')}-{_pad(n, 2)}"
            id_obj = _nuevo_objeto(cur, "gel_electroforesis", codigo, "Gel de electroforesis")
            cur.execute(
                """
                insert into geles_electroforesis
                  (id_objeto, codigo_gel, id_corrida_pcr, fecha_corrida, responsable_corrida,
                   concentracion_agarosa_pct, voltaje_v, marcador_peso_molecular, estado_gel, observaciones)
                values (%s,%s,%s,%s,%s,%s,%s,%s,'registrado',%s)
                returning id_gel::text as id
                """,
                (id_obj, codigo, p.get("idCorridaPcr"), fecha, p.get("responsable", "Pamela"),
                 p.get("agarosaPct", 1.2), p.get("voltaje", 90),
                 p.get("marcador", "1 Kb Plus"), p.get("observaciones")),
            )
            gid = cur.fetchone()["id"]
            corridas_tocadas: set[str] = set()
            for c in p.get("carriles", []):
                id_pcr = c.get("idPcr")
                estado = c.get("estado", "pendiente")
                if estado not in ESTADOS_POZO:
                    estado = "pendiente"
                banda = estado == "positivo"
                cur.execute(
                    """
                    insert into carriles_gel
                      (id_gel, numero_carril, id_pcr_reaccion, id_control_positivo, tipo_carril,
                       codigo_muestra_visible, banda_detectada, estado_resultado, tamano_estimado_pb, observaciones)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (gid, c["numero"], id_pcr, c.get("idControlPositivo"), c.get("tipo", "muestra"),
                     c.get("codigoVisible"), banda, estado, c.get("tamanoPb"), c.get("observaciones")),
                )
                # Retroalimentación: solo resultados concluyentes actualizan la reacción de muestra.
                if id_pcr and c.get("tipo", "muestra") == "muestra" and estado in ("positivo", "negativo"):
                    cur.execute(
                        "update pcr_reacciones set resultado_pcr=%s, tamano_banda_observado_pb=%s "
                        "where id_pcr_reaccion=%s returning id_corrida_pcr::text as cid",
                        (estado, c.get("tamanoPb") if estado == "positivo" else None, id_pcr),
                    )
                    row = cur.fetchone()
                    if row and row.get("cid"):
                        corridas_tocadas.add(row["cid"])
            # Las corridas con al menos un pozo leído quedan como 'leida'.
            for cid in corridas_tocadas:
                cur.execute("update pcr_corridas set estado_corrida='leida' where id_corrida_pcr=%s", (cid,))
        conn.commit()
    return {"id": gid, "codigo": codigo}


# --------------------------------------------------------------------------------------
# CAJA DE CONTROLES POSITIVOS
# --------------------------------------------------------------------------------------

def list_controles_positivos() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id_control_positivo::text as id, codigo_control as codigo,
                   id_vial_adn::text as "idVial", etiqueta,
                   coalesce(organismo,'') as organismo, coalesce(descripcion,'') as descripcion,
                   concentracion_ng_ul::float as "concentracionNgUl",
                   coalesce(ubicacion,'') as ubicacion, estado_control as estado,
                   veces_usado as "vecesUsado",
                   to_char(fecha_agregado,'YYYY-MM-DD') as "fechaAgregado",
                   to_char(fecha_agotado,'YYYY-MM-DD') as "fechaAgotado",
                   coalesce(observaciones,'') as observaciones
            from controles_positivos
            order by estado_control, etiqueta
            """
        )
        return cur.fetchall()


def crear_control_positivo(p: dict) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            n = _siguiente(cur, "control")
            codigo = f"CPOS-{_pad(n)}"
            id_vial = None
            cod_vial = p.get("codigoVial")
            if cod_vial:
                cur.execute("select id_vial_adn::text as id from viales_adn where codigo_vial=%s", (cod_vial,))
                row = cur.fetchone()
                id_vial = row["id"] if row else None
            cur.execute(
                """
                insert into controles_positivos
                  (codigo_control, id_vial_adn, etiqueta, organismo, descripcion,
                   concentracion_ng_ul, ubicacion, observaciones)
                values (%s,%s,%s,%s,%s,%s,%s,%s)
                returning id_control_positivo::text as id
                """,
                (codigo, id_vial, p.get("etiqueta") or codigo, p.get("organismo"),
                 p.get("descripcion"), p.get("concentracionNgUl"), p.get("ubicacion"),
                 p.get("observaciones")),
            )
            cid = cur.fetchone()["id"]
        conn.commit()
    return {"id": cid, "codigo": codigo}


def actualizar_control_positivo(id_control: str, p: dict) -> dict:
    """Actualiza estado (disponible/agotado) y/o datos del control positivo.

    Marcar 'agotado' fija la fecha_agotado (recordatorio para repetir extracción).
    """
    if "estado" not in p and "observaciones" not in p:
        return {"id": id_control}
    with get_conn() as conn:
        with conn.cursor() as cur:
            if "estado" in p and p["estado"] == "agotado":
                cur.execute(
                    "update controles_positivos set estado_control='agotado', fecha_agotado=current_date "
                    + (", observaciones=%s " if "observaciones" in p else "")
                    + "where id_control_positivo=%s",
                    ((p["observaciones"], id_control) if "observaciones" in p else (id_control,)),
                )
            elif "estado" in p:
                cur.execute(
                    "update controles_positivos set estado_control='disponible', fecha_agotado=null "
                    + (", observaciones=%s " if "observaciones" in p else "")
                    + "where id_control_positivo=%s",
                    ((p["observaciones"], id_control) if "observaciones" in p else (id_control,)),
                )
            else:
                cur.execute(
                    "update controles_positivos set observaciones=%s where id_control_positivo=%s",
                    (p.get("observaciones"), id_control),
                )
        conn.commit()
    return {"id": id_control}


# --------------------------------------------------------------------------------------
# CORRIDAS DE PCR (memoria de qué muestras / lotes ya se amplificaron)
# --------------------------------------------------------------------------------------

def crear_corrida_pcr(p: dict) -> dict:
    """Guarda una preparación de master mix como corrida + una reacción por tubo.

    Tubos reales = muestras + blanco + control positivo (el margen de pipeteo es
    volumen muerto, no un tubo). Cada muestra crea una reacción ligada a su vial.
    """
    muestras: list[dict] = p.get("muestras") or []
    usar_blanco = bool(p.get("usarBlanco", True))
    usar_positivo = bool(p.get("usarPositivo", True))
    margen = int(p.get("margenError") or 0)
    n_reacciones = len(muestras) + (1 if usar_blanco else 0) + (1 if usar_positivo else 0) + margen
    lotes = ", ".join(sorted({m.get("lote") for m in muestras if m.get("lote")}))

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Ensayo por defecto (16S)
            cur.execute("select id_pcr_ensayo::text as id from pcr_ensayos where codigo_ensayo='PCR-16S'")
            ens = cur.fetchone()
            if ens:
                id_ens = ens["id"]
            else:
                cur.execute(
                    "insert into pcr_ensayos (codigo_ensayo, nombre_ensayo, gen_objetivo, tamano_amplicon_esperado_pb) "
                    "values ('PCR-16S','Identificación 16S rRNA','16S rRNA',1500) returning id_pcr_ensayo::text as id"
                )
                id_ens = cur.fetchone()["id"]

            fecha = p.get("fecha", "2026-01-01")
            n = _siguiente(cur, "corrida")
            codigo = f"CORR-{fecha.replace('-', '')}-{_pad(n, 2)}"
            id_obj = _nuevo_objeto(cur, "pcr_reaccion", codigo, "Corrida de PCR")

            import json
            cur.execute(
                """
                insert into pcr_corridas
                  (id_objeto, codigo_corrida, id_pcr_ensayo, id_control_positivo, fecha_corrida,
                   responsable, volumen_final_ul, volumen_molde_ul, n_muestras, n_reacciones,
                   usar_blanco, usar_positivo, margen_error, receta, lotes, observaciones)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id_corrida_pcr::text as id
                """,
                (id_obj, codigo, id_ens, p.get("idControlPositivo"), fecha,
                 p.get("responsable", "Pamela"), p.get("volFinal"), p.get("volMolde"),
                 len(muestras), n_reacciones, usar_blanco, usar_positivo, margen,
                 json.dumps(p.get("receta") or {}), lotes, p.get("observaciones")),
            )
            id_corr = cur.fetchone()["id"]

            tubo = 0

            def _reaccion(tipo: str, frasco: str | None, lote: str | None, id_vial: str | None):
                nonlocal tubo
                tubo += 1
                cod_rxn = f"{codigo}-T{_pad(tubo, 2)}"
                cur.execute(
                    """
                    insert into pcr_reacciones
                      (codigo_reaccion, id_vial_adn, id_pcr_ensayo, id_corrida_pcr, fecha_pcr,
                       responsable_pcr, tipo_reaccion, numero_tubo, codigo_muestra_visible, lote,
                       resultado_pcr)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pendiente')
                    """,
                    (cod_rxn, id_vial, id_ens, id_corr, fecha, p.get("responsable", "Pamela"),
                     tipo, tubo, frasco, lote),
                )

            for m in muestras:
                id_vial = None
                cod_vial = m.get("frasco")
                if cod_vial:
                    cur.execute("select id_vial_adn::text as id from viales_adn where codigo_vial=%s", (cod_vial,))
                    row = cur.fetchone()
                    id_vial = row["id"] if row else None
                _reaccion("muestra", cod_vial, m.get("lote"), id_vial)

            if usar_blanco:
                _reaccion("blanco", "BLANCO", None, None)
            if usar_positivo:
                _reaccion("positivo", "CONTROL +", None, None)
                if p.get("idControlPositivo"):
                    cur.execute(
                        "update controles_positivos set veces_usado = veces_usado + 1 "
                        "where id_control_positivo=%s",
                        (p["idControlPositivo"],),
                    )
        conn.commit()
    return {"id": id_corr, "codigo": codigo, "nReacciones": n_reacciones, "nTubos": tubo}


def list_corridas_pcr() -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select co.id_corrida_pcr::text as id, co.codigo_corrida as codigo,
                   to_char(co.fecha_corrida,'YYYY-MM-DD') as fecha,
                   coalesce(co.responsable,'') as responsable,
                   co.n_muestras as "nMuestras", co.n_reacciones as "nReacciones",
                   co.volumen_final_ul::float as "volFinal",
                   coalesce(co.lotes,'') as lotes, co.estado_corrida as estado,
                   cp.etiqueta as "controlPositivo"
            from pcr_corridas co
            left join controles_positivos cp on cp.id_control_positivo = co.id_control_positivo
            order by co.fecha_corrida desc, co.created_at desc
            """
        )
        corridas = cur.fetchall()
        for c in corridas:
            cur.execute(
                """
                select id_pcr_reaccion::text as id, numero_tubo as numero, tipo_reaccion as tipo,
                       coalesce(codigo_muestra_visible,'') as frasco, coalesce(lote,'') as lote,
                       coalesce(resultado_pcr,'pendiente') as resultado,
                       tamano_banda_observado_pb as "tamanoPb"
                from pcr_reacciones where id_corrida_pcr=%s order by numero_tubo
                """,
                (c["id"],),
            )
            c["tubos"] = cur.fetchall()
        return corridas


# --------------------------------------------------------------------------------------
# MEDIA (imágenes)
# --------------------------------------------------------------------------------------

def guardar_media_vinculo(storage_uri: str, file_name: str, mime: str, size: int,
                          sha256: str, codigo_objeto: str, rol: str = "evidencia",
                          es_principal: bool = False) -> dict:
    """Registra un archivo de media y lo vincula a un objeto por su código."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into media_archivos (storage_uri, file_name, mime_type, media_type, size_bytes, sha256, uploaded_by)
                values (%s,%s,%s,'imagen',%s,%s,'Pamela') returning id_media::text as id
                """,
                (storage_uri, file_name, mime, size, sha256),
            )
            id_media = cur.fetchone()["id"]
            cur.execute("select id_objeto::text as id from objetos_laboratorio where codigo=%s", (codigo_objeto,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"objeto {codigo_objeto} no encontrado")
            cur.execute(
                """
                insert into media_vinculos (id_media, id_objeto, rol, es_principal)
                values (%s,%s,%s,%s) on conflict do nothing
                """,
                (id_media, row["id"], rol, es_principal),
            )
        conn.commit()
    return {"id": id_media, "storageUri": storage_uri}


def list_media_de_objeto(codigo_objeto: str) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select ma.id_media::text as id, ma.storage_uri as "storageUri", ma.file_name as "fileName",
                   mv.rol, mv.es_principal as "esPrincipal"
            from media_vinculos mv
            join media_archivos ma on ma.id_media = mv.id_media
            join objetos_laboratorio o on o.id_objeto = mv.id_objeto
            where o.codigo = %s order by mv.es_principal desc, mv.created_at desc
            """,
            (codigo_objeto,),
        )
        return cur.fetchall()
