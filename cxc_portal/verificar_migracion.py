"""Verificacion de SOLO LECTURA de una ruta migrada: BD (copia aislada o real) contra el JSON de organizacion fisica aprobado, el documento de hojas de
visita y, opcionalmente, el semaforo vigente comparado con el motor unico. No escribe nada.

    python -m cxc_portal.verificar_migracion --estado DIR --ruta OCCIDENTE --json v3.json [--hojas HOJAS.xlsx] [--semaforo SEMAFORO.xlsx --datos DIR_SNAPSHOT]

Las clasificaciones del snapshot son datos HISTORICOS del corte indicado: sirven para verificar reglas, no como saldos actuales.
Codigo de salida: 0 = todo coincide; 1 = hay diferencias (se listan; nada se corrige).
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List

from .estado import Estado

MAPA = {"VERDE": "A", "AZUL": "B", "AMARILLA": "C", "BLANCA": "EN_PRUEBA", "ROJO": "SOLO_CONTADO", "ROJA": "SOLO_CONTADO"}


def verificar(estado: Estado, route_id: str, archivo_json: Path, hojas: Path = None, semaforo: Path = None, datos: Path = None,
              salida: Callable[[str], None] = print) -> Dict[str, List[str]]:
    resultado = {"ok": [], "fallas": [], "informativas": []}

    def chk(nombre, cond, extra=""):
        (resultado["ok"] if cond else resultado["fallas"]).append(nombre + ("" if cond or not extra else f"  [{extra}]"))
        salida(("PASS " if cond else "FAIL ") + nombre + ("" if cond or not extra else f"  [{extra}]"))

    J = json.loads(Path(archivo_json).read_text(encoding="utf-8"))
    r = estado.obtener_ruta(route_id)
    cl = r["clientes"]
    chk(f"{len(J['customers'])} clientes únicos con folders únicos", len(cl) == len(J["customers"]) and len({c["codigo"] for c in cl}) == len(cl) == len({c["folder"] for c in cl}))
    chk("folders, zonas y orden físico == JSON", [(c["codigo"], c["folder"], c["zona"], c["orden"]) for c in cl] == [(c["sap_code"], c["folder_label"], c["zone_prefix"], c["physical_order"]) for c in J["customers"]])
    chk(f"{len(J['zones'])} zonas con los nombres del JSON", {z["prefijo"]: z["nombre"] for z in r["zonas"]} == {z["prefix"]: z["name"] for z in J["zones"]})
    chk("foránea / 60 días", (r["tipo_ruta"], r["plazo_dias"]) == ("FORANEA", 60))
    hwm = estado.folders_usados(route_id)
    chk("marcas de agua de folders cubren todos los usados", all(hwm.get(c["zona"], 0) >= int(c["folder"].split()[0]) for c in cl))
    for ren in J.get("zone_renames", []):
        chk(f"prefijo {ren['from']} retirado conserva su marca de agua", ren["from"] in hwm and ren["from"] not in {z["prefijo"] for z in r["zonas"]})
    with estado.tx() as c:
        chk("integridad SQLite", c.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
        chk("ruta NO operativa mientras gerencia no la apruebe", c.execute("SELECT implantacion FROM rutas WHERE route_id = ?", (route_id,)).fetchone()[0] != "OPERATIVA")
        chk("sin decisiones de gerencia sobre la ruta", c.execute("SELECT COUNT(*) FROM decisiones WHERE route_id = ?", (route_id,)).fetchone()[0] == 0)
    chk("documentos y archivos coinciden con sus hashes", estado.verificar_documentos() == [], "; ".join(estado.verificar_documentos()[:3]))
    docs = estado.listar_documentos(route_id, "HOJA_VISITA")
    if hojas:
        vig = next((d for d in docs if d["estado"] == "VIGENTE"), None)
        det = estado.obtener_documento(vig["id"]) if vig else None
        orden_doc = [c["codigo"] for h in (det["contenido"]["hojas"] if det else []) for c in h["clientes"]]
        chk("hojas de visita vigentes: mismo conjunto y orden que la ruta", orden_doc == [c["codigo"] for c in cl], f"{len(orden_doc)} vs {len(cl)}")
        chk("hojas de visita sin advertencias", bool(det) and not det["contenido"].get("advertencias"), str(det["contenido"].get("advertencias") if det else "sin documento"))
        chk("versiones anteriores conservadas como históricas", all(d["estado"] == "HISTORICA" for d in docs if d is not vig))
    if semaforo and datos:
        import openpyxl

        from cxc_bot import payment_semaphore as ps

        from .fuentes import FuenteSnapshotSAP
        codigos = [c["codigo"] for c in cl]
        res = ps.evaluar_ruta(codigos, FuenteSnapshotSAP(Path(datos)), r["plazo_dias"])
        ws = openpyxl.load_workbook(semaforo, data_only=True)["1 - Lista"]
        cab = next(i for i in range(1, 30) if ws.cell(i, 1).value == "ORDEN")
        doc: Dict[str, str] = {}
        for i in range(cab + 1, ws.max_row + 1):
            cod, txt = ws.cell(i, 4).value, str(ws.cell(i, 10).value or "").upper()
            if isinstance(cod, str) and re.fullmatch(r"C\d+", cod):
                doc[cod] = next((MAPA[k] for k in MAPA if txt.startswith(k)), "?")
        chk("el semáforo vigente trae los mismos clientes que la ruta", list(doc) == codigos, f"{len(doc)} vs {len(codigos)}")
        dif = [(c, doc.get(c), res[c].final) for c in codigos if doc.get(c) != res[c].final]
        chk("motor único == semáforo vigente en todos los clientes", not dif, str(dif[:5]))
        for c, esperado, obtenido in dif:
            resultado["informativas"].append(f"{c}: el documento dice {esperado}; el motor (reglas vigentes) dice {obtenido}")
        salida("   distribución del motor: " + str(dict(collections.Counter(res[c].final for c in codigos))))
    return resultado


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--estado", required=True)
    ap.add_argument("--ruta", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--hojas")
    ap.add_argument("--semaforo")
    ap.add_argument("--datos")
    a = ap.parse_args(argv)
    r = verificar(Estado(Path(a.estado) / "portal.sqlite3", solo_lectura=True), a.ruta.upper(), Path(a.json), Path(a.hojas) if a.hojas else None,
                  Path(a.semaforo) if a.semaforo else None, Path(a.datos) if a.datos else None)
    print(f"\n{len(r['ok'])} PASS / {len(r['fallas'])} FAIL")
    for x in r["informativas"]:
        print("  INFO:", x)
    return 1 if r["fallas"] else 0


if __name__ == "__main__":
    sys.exit(main())
