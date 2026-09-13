"""Catalogo oficial de vendedores y sus rutas (config/vendor_catalog.json).

El objetivo es no confiar unicamente en texto heredado del Excel: cualquier
entrada de programacion se valida contra este catalogo antes de generar
nada. La comparacion es normalizada (sin acentos, sin importar mayusculas)
porque el mismo vendedor/ruta puede llegar escrito de formas distintas desde
el Excel, una carpeta en disco, o (en el futuro) InSitu.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .text_utils import normalize_key

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "vendor_catalog.json"


class CatalogError(Exception):
    pass


class VendorNotFoundError(CatalogError):
    pass


class RouteNotAssignedError(CatalogError):
    """La ruta existe en el catalogo pero no pertenece a ese vendedor (o no
    existe en absoluto)."""


@dataclass(frozen=True)
class VendorCatalog:
    # nombre normalizado de vendedor -> nombre de vendedor tal como se muestra
    vendor_display: Dict[str, str]
    # nombre normalizado de vendedor -> {ruta normalizada: ruta tal como se muestra}
    routes_by_vendor: Dict[str, Dict[str, str]]

    @classmethod
    def load(cls, path: Path = DEFAULT_CATALOG_PATH) -> "VendorCatalog":
        if not path.exists():
            raise FileNotFoundError(f"No se encontro el catalogo de vendedores: {path}")
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        vendor_display: Dict[str, str] = {}
        routes_by_vendor: Dict[str, Dict[str, str]] = {}
        for vendor, routes in raw.items():
            vkey = normalize_key(vendor)
            vendor_display[vkey] = vendor
            routes_by_vendor[vkey] = {normalize_key(r): r for r in routes}
        return cls(vendor_display=vendor_display, routes_by_vendor=routes_by_vendor)

    def validate(self, vendor: str, route: str) -> None:
        """Lanza CatalogError si el vendedor no existe o la ruta no le
        pertenece. No devuelve nada si es valido."""

        vkey = normalize_key(vendor)
        if vkey not in self.vendor_display:
            disponibles = ", ".join(sorted(self.vendor_display.values()))
            raise VendorNotFoundError(f"Vendedor '{vendor}' no esta en el catalogo. Vendedores: {disponibles}")

        rkey = normalize_key(route)
        routes = self.routes_by_vendor[vkey]
        if rkey not in routes:
            disponibles = ", ".join(sorted(routes.values()))
            raise RouteNotAssignedError(
                f"La ruta '{route}' no pertenece a '{self.vendor_display[vkey]}' segun el catalogo "
                f"(o no existe). Rutas de {self.vendor_display[vkey]}: {disponibles}"
            )

    def canonical_vendor(self, vendor: str) -> str:
        vkey = normalize_key(vendor)
        if vkey not in self.vendor_display:
            raise VendorNotFoundError(f"Vendedor '{vendor}' no esta en el catalogo.")
        return self.vendor_display[vkey]

    def canonical_route(self, vendor: str, route: str) -> str:
        self.validate(vendor, route)
        return self.routes_by_vendor[normalize_key(vendor)][normalize_key(route)]

    def all_vendors(self) -> List[str]:
        return sorted(self.vendor_display.values())
