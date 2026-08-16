"""Siembra idempotente de datos derivados del Excel de muestreo de Pamela.

Uso (desde fago-api/):  py -3 db/seed_excel.py
- Crea el proyecto, una recepción de lote, 3 peces y, por pez, muestras por órgano
  y cajas por medio. Añade observaciones de colonia (el corazón del Excel) a varias cajas.
- Si ya hay peces registrados, NO duplica (sale sin tocar nada).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import repo, repo_mol  # noqa: E402
from app.db import get_conn  # noqa: E402

# Núcleo del Excel: cada caja observada = muestra(órgano) + medio + descripción de colonia.
PECES = [
    {"pesoG": 125.4, "longitudCm": 18.7, "lesiones": "Hemorragias en hígado, riñón pálido"},
    {"pesoG": 182.4, "longitudCm": 21.6, "lesiones": "Lesión lateral ulcerativa"},
    {"pesoG": 98.7, "longitudCm": 15.2, "lesiones": "Branquias con moco abundante"},
]
ORGANOS = ["Hg", "Rñ", "Br", "In"]
MEDIOS = ["TSA", "TCBS", "Sangre"]
# Descripciones de colonia tipo Excel (texto libre) para algunas cajas.
COLONIAS = [
    {"morfologia": "Colonias iridiscentes circulares", "color": "iridiscente", "forma": "circular"},
    {"morfologia": "Colonias amarillas convexas", "color": "amarilla", "forma": "redonda"},
    {"morfologia": "Colonias blancas aterciopeladas", "color": "blanca", "forma": "circular"},
    {"morfologia": "Colonias beige irregulares", "color": "beige", "forma": "irregular"},
]


def ya_sembrado() -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from peces")
        return int(cur.fetchone()["n"]) > 0


def main() -> None:
    if ya_sembrado():
        print("Ya hay peces en la BD — seed omitido (idempotente).")
        return

    rec = repo.crear_recepcion({
        "fecha": "2026-05-20",
        "origen": "Unidad acuícola piloto",
        "especie": "Tilapia",
        "cientifico": "Oreochromis niloticus",
        "cantidadPeces": 6,
        "motivo": "Mortalidad y lesiones externas",
    })
    print("Recepción:", rec["codigo"])

    cajas_creadas: list[dict] = []
    for pez in PECES:
        res = repo.registrar_aislamiento({
            "idRecepcion": rec["id"],
            "pesoG": pez["pesoG"],
            "longitudCm": pez["longitudCm"],
            "estadoClinico": "enfermo",
            "lesiones": pez["lesiones"],
            "organos": ORGANOS,
            "medios": MEDIOS,
            "cajasPorMuestra": 1,
            "fechaSiembra": "2026-05-20",
        })
        print(f"  {res['pez']['codigo']}: {len(res['cajas'])} cajas")
        cajas_creadas.extend(res["cajas"])

    # Observaciones de colonia (Excel) en ~70% de las cajas; algunas sin crecimiento.
    for i, caja in enumerate(cajas_creadas):
        if i % 7 == 6:
            repo.registrar_observacion(caja["id"], {"fecha": "2026-05-21", "hayCrecimiento": False,
                                                     "observaciones": "Sin crecimiento a las 24 h"})
            continue
        col = COLONIAS[i % len(COLONIAS)]
        repo.registrar_observacion(caja["id"], {
            "fecha": "2026-05-21", "hayCrecimiento": True, "numeroMorfotipos": 1 + (i % 3),
            "morfologia": col["morfologia"], "color": col["color"], "forma": col["forma"],
            "borde": "entero", "elevacion": "convexa",
        })

    # Cadena molecular de demostración sobre la primera caja con crecimiento del pez 1.
    primera = cajas_creadas[0]
    sub = repo_mol.crear_subcultivo(primera["id"], {"morfotipo": "A", "color": "iridiscente",
                                                    "forma": "circular", "textura": "cremosa",
                                                    "medio": "TSA", "fechaSiembra": "2026-05-22"})
    ev = repo_mol.crear_extraccion(sub["id"], {"fecha": "2026-05-24", "concentracionNgUl": 145.2})
    nd = repo_mol.crear_nanodrop(ev["vial"]["id"], {"fecha": "2026-05-25", "ratio260_280": 1.92,
                                                    "ratio260_230": 2.3, "concentracionNgUl": 145.2})
    pcr = repo_mol.crear_pcr(ev["vial"]["id"], {"fecha": "2026-05-26", "resultado": "positivo",
                                                "tamanoBandaPb": 1500})
    repo_mol.crear_gel({
        "fecha": "2026-05-26", "agarosaPct": 1.2, "voltaje": 90, "marcador": "1 Kb Plus",
        "carriles": [
            {"numero": 1, "tipo": "marcador", "codigoVisible": "MWM 1Kb", "banda": True},
            {"numero": 2, "tipo": "muestra", "idPcr": pcr["id"], "codigoVisible": pcr["codigo"],
             "banda": True, "tamanoPb": 1500},
            {"numero": 3, "tipo": "control", "codigoVisible": "Control -", "banda": False},
        ],
    })
    print(f"  Cadena molecular: {sub['codigo']} -> {ev['vial']['codigo']} -> {nd['codigo']} -> {pcr['codigo']} -> gel")
    print("Seed completado.")


if __name__ == "__main__":
    main()
