"""Localiza automaticamente la carpeta y el archivo base de una ruta bajo:

    <routes_root>/<Vendedor>/<Ruta>/[RUTA] [mes].xlsx

Regla de oro: si hay cualquier ambiguedad, NO ADIVINAR. Se aborta con un
error especifico y una lista clara de candidatos para que una persona
decida. Nunca se elige "el primero" ni se ordena alfabeticamente (agosto
quedaria antes que enero, que es incorrecto cronologicamente).

Estrategia: la senal PRINCIPAL es el mes interpretado desde el nombre del
archivo (nunca la fecha de modificacion como criterio principal, porque
abrir o volver a guardar un Excel viejo le cambia el mtime sin que eso
signifique que ahora es el mas reciente). Como el nombre solo trae el mes
("[RUTA] septiembre.xlsx", sin año), el año de cada candidato se infiere
contra una fecha de referencia (la fecha de salida de la ruta que se esta
preparando): el mes se interpreta como su ocurrencia mas reciente que no
sea posterior a esa fecha. Asi, "diciembre" visto en octubre de 2026 se
entiende como diciembre de 2025 (el unico diciembre que ya paso), y no se
confunde con un archivo futuro. Con esto, dos archivos con nombres de mes
DISTINTOS siempre quedan ordenados sin ambiguedad por (año inferido, mes).

La fecha de modificacion (mtime) solo entra en juego como desempate, y
unicamente en el caso genuinamente ambiguo de que dos archivos tengan el
MISMO nombre de mes (p.ej. una copia duplicada): si sus mtimes se
distinguen con margen claro, gana el mas reciente; si no, se aborta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import RouteConfig
from .text_utils import normalize_key

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# Dos archivos del MISMO mes/año inferido con mtimes mas cerca que esto se
# consideran "empatados" y se abortan como ambiguos en vez de adivinar.
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
    month_name: str
    month_index: int  # 1=enero ... 12=diciembre
    inferred_year: int
    mtime: float

    @property
    def sort_key(self) -> Tuple[int, int]:
        return (self.inferred_year, self.month_index)


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


def _month_from_filename(filename: str, prefix: str) -> Optional[Tuple[str, int]]:
    """Devuelve (nombre_de_mes, indice 1-12) si el archivo sigue el patron
    '<prefijo> <mes>.xlsx' con un mes valido en español; None si no aplica
    (nombre invalido/no relacionado -- se ignora, no se fuerza a encajar)."""

    stem = Path(filename).stem  # sin ".xlsx"
    prefix_key = normalize_key(prefix)
    stem_key = normalize_key(stem)
    if not stem_key.startswith(prefix_key):
        return None
    remainder = stem_key[len(prefix_key):].strip()
    for idx, mes in enumerate(MESES_ES, start=1):
        if normalize_key(mes) == remainder:
            return mes, idx
    return None


def _infer_year_for_month(month_index: int, reference_date: date) -> int:
    """La ocurrencia MAS RECIENTE de ese mes que no sea posterior a
    reference_date. Si el mes (numerico) ya paso o es el actual dentro del
    año de referencia, es ese año; si todavia no llega, fue el año anterior."""

    if month_index <= reference_date.month:
        return reference_date.year
    return reference_date.year - 1


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


def list_base_file_candidates(
    route_dir: Path, config: RouteConfig, reference_date: date
) -> List[BaseFileCandidate]:
    candidates = []
    for child in route_dir.iterdir():
        if not child.is_file() or child.suffix.lower() != ".xlsx":
            continue
        if child.name.startswith("~$"):  # archivo temporal de Excel abierto
            continue
        parsed = _month_from_filename(child.name, config.filename_prefix)
        if parsed is None:  # nombre invalido/no reconocido: se ignora, no se adivina
            continue
        month_name, month_index = parsed
        candidates.append(
            BaseFileCandidate(
                path=child,
                month_name=month_name,
                month_index=month_index,
                inferred_year=_infer_year_for_month(month_index, reference_date),
                mtime=child.stat().st_mtime,
            )
        )
    return candidates


def resolve_base_file(
    routes_root: Path, vendor: str, route: str, config: RouteConfig, reference_date: date
) -> Path:
    """Devuelve el archivo base mas reciente para esa ruta, o lanza un
    FileResolutionError especifico (nunca adivina).

    `reference_date` es la fecha de la ruta que se esta preparando (la
    salida): se usa para interpretar a que año pertenece cada nombre de mes.
    """

    route_dir = find_vendor_route_dir(routes_root, vendor, route)
    candidates = list_base_file_candidates(route_dir, config, reference_date)

    if not candidates:
        raise NoBaseFileFoundError(
            f"No hay ningun archivo base para la ruta '{route}' de '{vendor}' en {route_dir} "
            f"(se buscaron archivos '{config.filename_prefix} <mes>.xlsx' con un mes valido en español)."
        )

    # Agrupar por (año inferido, mes): dos nombres de mes DISTINTOS nunca
    # caen en el mismo grupo (por construccion), asi que solo hay algo que
    # desempatar cuando dos archivos comparten literalmente el mismo mes.
    groups: Dict[Tuple[int, int], List[BaseFileCandidate]] = {}
    for c in candidates:
        groups.setdefault(c.sort_key, []).append(c)

    resolved: Dict[Tuple[int, int], BaseFileCandidate] = {}
    for key, group in groups.items():
        if len(group) == 1:
            resolved[key] = group[0]
            continue

        group_sorted = sorted(group, key=lambda c: c.mtime, reverse=True)
        newest, second = group_sorted[0], group_sorted[1]
        if (newest.mtime - second.mtime) < _MTIME_TIE_THRESHOLD_SECONDS:
            raise AmbiguousBaseFileError(
                f"Hay mas de un archivo para '{newest.month_name} {newest.inferred_year}' en {route_dir} "
                f"({[c.path.name for c in group]}) y sus fechas de modificacion no permiten desempatar con "
                "certeza. No se adivina; elimina o renombra el duplicado."
            )
        # Desempate por metadata (mtime) SOLO entre archivos del mismo mes:
        # aqui si es razonable asumir que el modificado mas recientemente es
        # la version vigente de ese mes.
        resolved[key] = newest

    best_key = max(resolved.keys())
    return resolved[best_key].path
