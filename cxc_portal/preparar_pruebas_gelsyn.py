"""Prepara un ESTADO AISLADO del Portal para probar la fuente `gelsyn-pg-pruebas`: crea las 3 rutas aprobadas (Las Vegas, Yoro, Occidente) SOLO con codigos de cliente
(tomados de los Excel aprobados; no se leen nombres) y un usuario de pruebas. No toca datos de negocio reales ni escribe fuera de la carpeta de estado indicada.

  python -m cxc_portal.preparar_pruebas_gelsyn --estado ~/Developer/portal-gelsyn-pruebas [--aprobados ~/Downloads]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence

from . import corrida_en_seco_gelsyn as ces
from .estado import Estado

ACTOR = {"id": None, "usuario": "preparar_pruebas_gelsyn", "rol": "admin"}
ZONA = "GP"
VENDEDOR = "Vendedor Pruebas"


def sembrar(estado: Estado, codigos_por_ruta: Dict[str, Sequence[str]]) -> Dict[str, int]:
    existentes = {r["route_id"] for r in estado.listar_rutas(solo_activas=False)}
    creadas: Dict[str, int] = {}
    for ruta, codigos in codigos_por_ruta.items():
        if ruta in existentes:
            continue
        cods = sorted(set(codigos))
        estado.guardar_ruta(ACTOR, ruta, f"{ruta.replace('_', ' ').title()} (pruebas Gelsyn)", VENDEDOR, "FORANEA", ces.PLAZO, zonas={ZONA: "Zona pruebas"},
                            clientes=[{"codigo": c, "folder": f"{i} {ZONA}", "zona": ZONA, "nombre": c} for i, c in enumerate(cods, 1)],
                            estado_fisico="n/a (pruebas)", notas="Ruta de PRUEBAS con codigos de los Excel aprobados; sin nombres. No es una ruta operativa.",
                            requiere_hojas_visita=False)
        creadas[ruta] = len(cods)
    return creadas


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    def opt(n, d=None):
        return args[args.index(n) + 1] if n in args and args.index(n) + 1 < len(args) else d
    est = opt("--estado")
    if not est:
        print("Falta --estado (carpeta aislada, p. ej. ~/Developer/portal-gelsyn-pruebas).", file=sys.stderr)
        return 2
    carpeta = Path(est).expanduser()
    carpeta.mkdir(parents=True, exist_ok=True)
    os.chmod(carpeta, 0o700)
    aprob = ces.leer_aprobados(Path(opt("--aprobados", os.path.expanduser("~/Downloads"))))
    creadas = sembrar(Estado(carpeta / "portal.sqlite3"), {r: list(d) for r, d in aprob.items()})
    print(f"Rutas creadas: {creadas or 'ninguna (ya existian)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
