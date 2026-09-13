"""Carga de configuracion de rutas desde config/routes.json.

Cortes es el piloto pero el sistema debe soportar mas rutas/vendedores sin
tocar el motor. Cada ruta es una entrada en el JSON con los mismos campos
que RouteConfig.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .models import RouteConfig

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "routes.json"


class RouteNotFoundError(Exception):
    pass


def load_all_routes(config_path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, RouteConfig]:
    if not config_path.exists():
        raise FileNotFoundError(f"No se encontro el archivo de configuracion de rutas: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    routes: Dict[str, RouteConfig] = {}
    for route_id, fields in raw.items():
        fields = dict(fields)
        fields.setdefault("route_id", route_id)
        routes[route_id] = RouteConfig(**fields)
    return routes


def load_route_config(route_id: str, config_path: Path = DEFAULT_CONFIG_PATH) -> RouteConfig:
    routes = load_all_routes(config_path)
    key = route_id.upper()
    if key not in routes:
        disponibles = ", ".join(sorted(routes.keys())) or "(ninguna)"
        raise RouteNotFoundError(
            f"Ruta '{route_id}' no esta configurada en {config_path}. Rutas disponibles: {disponibles}"
        )
    return routes[key]
