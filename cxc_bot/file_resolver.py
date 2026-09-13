"""Localiza automaticamente la carpeta y el archivo base de una ruta bajo:

    <routes_root>/<Vendedor>/<Ruta>/[RUTA] [mes].xlsx

Regla de oro: si hay cualquier ambiguedad, NO ADIVINAR. Se aborta con un
error especifico y una lista clara de candidatos para que una persona
decida. Nunca se elige "el primero" ni se ordena alfabeticamente (agosto
quedaria antes que enero, que es incorrecto cronologicamente).

Estrategia: entre los archivos que siguen el patron de nombre de la ruta
(configurable via RouteConfig.filename_prefix), se usa la fecha de
modificacion del archivo (mtime) como senal de "mas reciente" -- es
exactamente como se generan estos archivos en la practica (se copia el mes
anterior y se le cambia el nombre), asi que el ultimo modificado es el mas
reciente. Si el candidato mas reciente no se distingue con margen claro del
segundo (mismo mtime o casi, o mismo nombre de mes repetido), se considera
ambiguo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .models import RouteConfig
from .text_utils import normalize_key

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# Dos archivos con mtimes mas cerca que esto se consideran "empatados"
# (por ejemplo, una copia masiva que preserva timestamps) y se abortan como
# ambiguos en vez de adivinar cual es realmente el mas reciente.
_MTIME_TIE_THRESHOLD_SECONDS = 2.0


class FileResolutionError(Exception):
    category = "archivo_base"


class VendorFolderNotFoundError(FileResolutionError):
    category = "carpeta_vendedor_faltante"


class RouteFolderNotFoundError(FileResolutionError):
    category = "carpeta_ruta_faltante"


class NoBaseFileFoundError(FileResolutionError):
    category = "archivo_base_faltante"


class AmbiguousBaseFileError(FileResolutionError):
    category = "archivo_base_ambiguo"


@dataclass
class BaseFileCandidate:
    path: Path
    month_name: Optional[str]
    mtime: float


def _find_child_dir(parent: Path, target_name: str) -> Optional[Path]:
    if not parent.is_dir():
        return None
    target_key = normalize_key(target_name)
    matches = [d for d in parent.iterdir() if d.is_dir() and normalize_key(d.name) == target_key]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise FileResolutionError(
            f"Hay mas de una carpeta que coincide con '{target_name}' dentro de {parent}: "
            f"{[str(m) for m in matches]}. No se adivina; renombra para que solo exista una."
        )
    return None


def _month_from_filename(filename: str, prefix: str) -> Optional[str]:
    stem = Path(filename).stem  # sin ".xlsx"
    prefix_key = normalize_key(prefix)
    stem_key = normalize_key(stem)
    if not stem_key.startswith(prefix_key):
        return None
    remainder = stem_key[len(prefix_key):].strip()
    remainder_plain = remainder.lower()
    for mes in MESES_ES:
        if normalize_key(mes) == normalize_key(remainder_plain):
            return mes
    return None


def find_vendor_route_dir(routes_root: Path, vendor: str, route: str) -> Path:
    routes_root = Path(routes_root)
    if not routes_root.is_dir():
        raise VendorFolderNotFoundError(
            f"La raiz de rutas de vendedores no existe o no es una carpeta: {routes_root}"
        )

    vendor_dir = _find_child_dir(routes_root, vendor)
    if vendor_dir is None:
        raise VendorFolderNotFoundError(
            f"No se encontro una carpeta para el vendedor '{vendor}' dentro de {routes_root}."
        )

    route_dir = _find_child_dir(vendor_dir, route)
    if route_dir is None:
        raise RouteFolderNotFoundError(
            f"No se encontro una carpeta para la ruta '{route}' dentro de {vendor_dir}."
        )
    return route_dir


def list_base_file_candidates(route_dir: Path, config: RouteConfig) -> List[BaseFileCandidate]:
    candidates = []
    for child in route_dir.iterdir():
        if not child.is_file() or child.suffix.lower() != ".xlsx":
            continue
        if child.name.startswith("~$"):  # archivo temporal de Excel abierto
            continue
        month = _month_from_filename(child.name, config.filename_prefix)
        if month is None:
            continue
        candidates.append(BaseFileCandidate(path=child, month_name=month, mtime=child.stat().st_mtime))
    return candidates


def resolve_base_file(routes_root: Path, vendor: str, route: str, config: RouteConfig) -> Path:
    """Devuelve el archivo base mas reciente para esa ruta, o lanza un
    FileResolutionError especifico (nunca adivina)."""

    route_dir = find_vendor_route_dir(routes_root, vendor, route)
    candidates = list_base_file_candidates(route_dir, config)

    if not candidates:
        raise NoBaseFileFoundError(
            f"No hay ningun archivo base para la ruta '{route}' de '{vendor}' en {route_dir} "
            f"(se buscaron archivos '{config.filename_prefix} <mes>.xlsx')."
        )

    months_seen = [c.month_name for c in candidates]
    if len(months_seen) != len(set(months_seen)):
        dupes = sorted({m for m in months_seen if months_seen.count(m) > 1})
        raise AmbiguousBaseFileError(
            f"Hay mas de un archivo para el mismo mes ({dupes}) en {route_dir}: "
            f"{[str(c.path) for c in candidates]}. No se adivina cual usar; elimina o renombra el duplicado."
        )

    candidates.sort(key=lambda c: c.mtime, reverse=True)
    newest = candidates[0]

    if len(candidates) > 1:
        second = candidates[1]
        if (newest.mtime - second.mtime) < _MTIME_TIE_THRESHOLD_SECONDS:
            raise AmbiguousBaseFileError(
                f"No se puede determinar con certeza cual archivo es el mas reciente en {route_dir}: "
                f"'{newest.path.name}' y '{second.path.name}' tienen fechas de modificacion practicamente "
                "iguales. No se adivina; verifica manualmente cual es el correcto."
            )

    return newest.path
