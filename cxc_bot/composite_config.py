"""Configuracion de rutas COMPUESTAS (config/route_composition.json).

Una ruta compuesta agrupa varios "componentes", cada uno con su propio
archivo base independiente (en su propia subcarpeta de vendedor/ruta) y su
propio orden. El motor los concatena en ese orden para producir UNA sola
ruta operativa -- ver composite_route_service.py.

Esto es deliberadamente generico: si en el futuro otra ruta se compone de
dos (o mas) zonas, se agrega aqui sin tocar el motor ni el runner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_COMPOSITION_PATH = Path(__file__).resolve().parent.parent / "config" / "route_composition.json"


class CompositeConfigError(Exception):
    pass


@dataclass(frozen=True)
class RouteComponent:
    name: str  # nombre de negocio del componente, p.ej. "Santa Cruz de Yojoa"
    route_id: str  # clave en config/routes.json con la estructura de ESTE componente
    folder_name: str  # subcarpeta bajo <routes_root>/<vendedor>/ donde vive su archivo base
    order: int


@dataclass(frozen=True)
class CompositeRouteConfig:
    composite_id: str  # p.ej. "COMAYAGUA" -- el nombre de la ruta OPERATIVA
    vendor: str
    template_route_id: str  # que RouteConfig (routes.json) define el layout/estilo del resultado
    components: List[RouteComponent]
    vigente_desde: Optional[date]  # None = no definida todavia (no se inventa)

    @property
    def sorted_components(self) -> List[RouteComponent]:
        return sorted(self.components, key=lambda c: c.order)


def load_all_composite_routes(path: Path = DEFAULT_COMPOSITION_PATH) -> Dict[str, CompositeRouteConfig]:
    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    result: Dict[str, CompositeRouteConfig] = {}
    for composite_id, fields in raw.items():
        components = [
            RouteComponent(
                name=c["name"], route_id=c["route_id"], folder_name=c["folder_name"], order=c["order"]
            )
            for c in fields["components"]
        ]
        vigente_desde_raw = fields.get("vigente_desde")
        vigente_desde = datetime.strptime(vigente_desde_raw, "%Y-%m-%d").date() if vigente_desde_raw else None
        result[composite_id.upper()] = CompositeRouteConfig(
            composite_id=composite_id,
            vendor=fields["vendor"],
            template_route_id=fields["template_route_id"],
            components=components,
            vigente_desde=vigente_desde,
        )
    return result


def load_composite_route(composite_id: str, path: Path = DEFAULT_COMPOSITION_PATH) -> Optional[CompositeRouteConfig]:
    """Devuelve la config compuesta para ese id, o None si esa ruta NO es
    compuesta (caso normal: la inmensa mayoria de las rutas son simples)."""

    return load_all_composite_routes(path).get(composite_id.upper())
