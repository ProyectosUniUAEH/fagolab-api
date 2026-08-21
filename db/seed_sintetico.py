"""Genera un laboratorio completo con datos INVENTADOS pero verosímiles.

Para qué existe: los datos reales del laboratorio pertenecen a la investigación de otra
institución y no pueden mostrarse en una demostración pública ni en un video. Este script
produce un conjunto con la misma forma, la misma cadena de trazabilidad y rangos
biológicamente plausibles — pero donde ni un solo pez, lote o resultado corresponde a algo
real.

    py -3 db/seed_sintetico.py            # limpia y siembra datos sintéticos
    py -3 db/seed_sintetico.py --guardar  # además lo deja como semilla oficial

Antes de limpiar hace un respaldo automático, así que la operación es reversible desde el
panel de datos de la aplicación.

Se apoya en las mismas funciones de escritura que usa la interfaz (`repo`, `repo_mol`), no
en INSERT sueltos: así los códigos, las etiquetas y los objetos de laboratorio quedan
exactamente como los genera el sistema en uso normal.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import random
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app import admin, repo, repo_mol  # noqa: E402
from app.db import get_conn  # noqa: E402

# Semilla fija: dos ejecuciones producen el mismo laboratorio ficticio. Importa para poder
# repetir una demostración y que los códigos coincidan con lo que se narró.
SEMILLA = 20260101
random.seed(SEMILLA)

# --- Vocabulario inventado -------------------------------------------------------------
# Nombres de granja deliberadamente genéricos: no corresponden a ninguna unidad real.
GRANJAS = [
    "Unidad Demostrativa Norte",
    "Unidad Demostrativa Sur",
    "Estanque Piloto A",
    "Estanque Piloto B",
]
RESPONSABLES = ["Equipo de demostración"]
ORGANOS = ["Hígado", "Riñón", "Branquia", "Bazo", "Intestino"]
MEDIOS = ["TSA", "Agar sangre", "MacConkey"]

MORFOLOGIAS = ["circular lisa", "irregular rugosa", "puntiforme", "filamentosa"]
COLORES = ["crema", "blanquecina", "amarillenta", "beige"]
FORMAS = ["convexa", "plana", "umbonada"]
BORDES = ["entero", "ondulado", "lobulado"]
HEMOLISIS = ["beta", "alfa", "gamma"]

SIGNOS = [
    "Nado errático y letargo",
    "Lesiones cutáneas superficiales",
    "Branquias pálidas",
    "Distensión abdominal leve",
    "Sin signos externos evidentes",
]

# Escala del laboratorio ficticio.
N_LOTES = 4
PECES_POR_LOTE = (3, 5)
FECHA_BASE = date(2026, 3, 2)


def _fecha(dias: int) -> str:
    return (FECHA_BASE + timedelta(days=dias)).isoformat()


def _limpiar() -> None:
    print("Respaldando los datos actuales antes de limpiar...")
    respaldo = admin.crear_respaldo("antes-de-seed-sintetico")
    print(f"  respaldo: {respaldo['archivo']} ({respaldo['bytes']:,} bytes)")
    print("Limpiando datos experimentales (seguridad y auditoría no se tocan)...")
    resultado = admin.limpiar()
    print(f"  {resultado}")


def _sembrar() -> dict:
    resumen = {"lotes": 0, "peces": 0, "cajas": 0, "subcultivos": 0, "viales": 0,
               "pcr": 0, "geles": 0}
    dia = 0

    for numero_lote in range(1, N_LOTES + 1):
        granja = GRANJAS[(numero_lote - 1) % len(GRANJAS)]
        n_peces = random.randint(*PECES_POR_LOTE)
        recepcion = repo.crear_recepcion({
            "fecha": _fecha(dia),
            "responsable": RESPONSABLES[0],
            "cantidadPeces": n_peces,
            "especie": "Tilapia del Nilo",
            "cientifico": "Oreochromis niloticus",
            "origen": granja,
            "motivo": "Mortalidad elevada en el estanque (caso ficticio de demostración)",
        })
        resumen["lotes"] += 1
        print(f"\n[{recepcion['codigo']}] {granja} · {n_peces} peces")

        for _ in range(n_peces):
            organos = random.sample(ORGANOS, random.randint(2, 3))
            medios = random.sample(MEDIOS, random.randint(1, 2))
            # Matriz órgano × medio: entre una y dos cajas por combinación.
            matriz = {o: {m: random.randint(1, 2) for m in medios} for o in organos}

            aislamiento = repo.registrar_aislamiento({
                "idRecepcion": recepcion["id"],
                "fechaSiembra": _fecha(dia),
                "especie": "Tilapia del Nilo",
                "estadoClinico": random.choice(["enfermo", "moribundo", "recién muerto"]),
                "pesoG": round(random.uniform(120, 420), 1),
                "longitudCm": round(random.uniform(14, 26), 1),
                "diagnostico": random.choice(SIGNOS),
                "organos": organos,
                "medios": medios,
                "matriz": matriz,
            })
            resumen["peces"] += 1
            cajas = aislamiento.get("cajas") or []
            resumen["cajas"] += len(cajas)

            for caja in cajas:
                # Alrededor de dos de cada tres cajas muestran crecimiento.
                crecio = random.random() < 0.68
                repo.registrar_observacion(caja["id"], {
                    "fecha": _fecha(dia + 2),
                    "hayCrecimiento": crecio,
                    "numeroMorfotipos": random.randint(1, 3) if crecio else 0,
                    "morfologia": random.choice(MORFOLOGIAS) if crecio else None,
                    "color": random.choice(COLORES) if crecio else None,
                    "forma": random.choice(FORMAS) if crecio else None,
                    "borde": random.choice(BORDES) if crecio else None,
                    "hemolisis": random.choice(HEMOLISIS) if crecio else None,
                    "observaciones": "Registro sintético de demostración.",
                })

                # Solo una parte de las cajas con crecimiento avanza a subcultivo.
                if not crecio or random.random() > 0.55:
                    continue

                sub = repo_mol.crear_subcultivos(caja["id"], {
                    "medio": caja["medio"],
                    "fechaSiembra": _fecha(dia + 3),
                    "subcultivos": [{
                        "morfotipo": random.choice("ABC"),
                        "color": random.choice(COLORES),
                        "forma": random.choice(FORMAS),
                        "resultadoPureza": "puro",
                        "aptoExtraccion": True,
                    }],
                })
                for creado in sub["creados"]:
                    resumen["subcultivos"] += 1
                    extraccion = repo_mol.crear_extraccion(creado["id"], {
                        "fecha": _fecha(dia + 5),
                        "metodo": "CTAB / fenol-cloroformo",
                        "concentracionNgUl": round(random.uniform(35, 190), 1),
                    })
                    resumen["viales"] += 1

                    # NanoDrop: la mayoría dentro de rango aceptable, algunas dudosas.
                    if random.random() < 0.75:
                        r280 = round(random.uniform(1.80, 1.99), 2)
                        r230 = round(random.uniform(2.00, 2.35), 2)
                    else:
                        r280 = round(random.uniform(1.55, 1.79), 2)
                        r230 = round(random.uniform(1.20, 1.95), 2)
                    repo_mol.crear_nanodrop(extraccion["vial"]["id"], {
                        "fecha": _fecha(dia + 6),
                        "ratio260_280": r280,
                        "ratio260_230": r230,
                        "concentracionNgUl": round(random.uniform(40, 210), 1),
                    })

                    if r280 >= 1.70:
                        resultado = "positivo" if random.random() < 0.7 else "negativo"
                        repo_mol.crear_pcr(extraccion["vial"]["id"], {
                            "fecha": _fecha(dia + 8),
                            "resultado": resultado,
                            "calidad": random.choice(["buena", "aceptable"]),
                        })
                        resumen["pcr"] += 1
        dia += 12

    resumen["geles"] = _sembrar_geles(resumen)
    return resumen


def _sembrar_geles(resumen: dict) -> int:
    """Un gel por tanda de reacciones, con sus carriles y algunos positivos.

    Los carriles positivos son los que después aparecen como candidatas a secuenciación,
    así que sin esto la demostración del flujo posterior se queda sin cola de entrada.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select pr.id_pcr_reaccion::text as id, pr.codigo_reaccion as codigo,
                   coalesce(pr.resultado_pcr,'pendiente') as resultado,
                   to_char(pr.fecha_pcr,'YYYY-MM-DD') as fecha
            from pcr_reacciones pr
            where pr.tipo_reaccion = 'muestra' or pr.tipo_reaccion is null
            order by pr.fecha_pcr, pr.created_at
            """
        )
        reacciones = cur.fetchall()

    if not reacciones:
        return 0

    geles = 0
    for inicio in range(0, len(reacciones), 8):
        tanda = reacciones[inicio:inicio + 8]
        carriles = [
            {"numero": 1, "tipo": "blanco", "codigoVisible": "Blanco / control −", "estado": "negativo"},
            {"numero": 2, "tipo": "positivo", "codigoVisible": "Control +", "estado": "positivo"},
        ]
        for posicion, reaccion in enumerate(tanda, start=3):
            positivo = reaccion["resultado"] == "positivo"
            carriles.append({
                "numero": posicion,
                "tipo": "muestra",
                "idPcr": reaccion["id"],
                "codigoVisible": reaccion["codigo"],
                "estado": "positivo" if positivo else "negativo",
                "tamanoPb": 1500 if positivo else None,
            })
        repo_mol.crear_gel({
            "fecha": tanda[0]["fecha"],
            "responsable": RESPONSABLES[0],
            "agarosaPct": 1.2,
            "voltaje": 90,
            "marcador": "1 Kb Plus",
            "observaciones": "Gel sintético de demostración.",
            "carriles": carriles,
        })
        geles += 1
    return geles


def main() -> None:
    print("=" * 68)
    print("  SIEMBRA SINTÉTICA — laboratorio ficticio para demostración")
    print("  Ningún dato de este conjunto corresponde a una muestra real.")
    print("=" * 68)

    _limpiar()
    print("\nGenerando laboratorio ficticio...")
    resumen = _sembrar()

    print("\n" + "=" * 68)
    for clave, valor in resumen.items():
        print(f"  {clave:14} {valor}")
    print("=" * 68)

    if "--guardar" in sys.argv:
        resultado = admin.guardar_semilla()
        print(f"\nGuardado como semilla oficial: {resultado}")

    print("\nListo. Los datos reales están respaldados en _backups/ y se restauran")
    print("desde el panel de datos de la aplicación.")


if __name__ == "__main__":
    main()
