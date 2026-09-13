"""Localiza automaticamente la carpeta y el archivo base de una ruta bajo:

    <routes_root>/<Vendedor>/<Ruta>/[RUTA] [mes].xlsx                  (historico)
    <routes_root>/<Vendedor>/<Ruta>/[RUTA] [mes] [año].xlsx            (historico con año)
    <routes_root>/<Vendedor>/<Ruta>/[RUTA] [DD] [mes] [año].xlsx       (formato preferido, nuevo)

El formato con dia es el preferido para archivos nuevos porque una misma
ruta puede salir mas de una vez en el mismo mes (p.ej. "CORTES 08 octubre
2026.xlsx" y "CORTES 22 octubre 2026.xlsx"): sin el dia, dos salidas del
mismo mes colisionarian en un solo nombre de archivo.

Regla de oro: si hay cualquier ambiguedad, NO ADIVINAR. Se aborta con un
error especifico y una lista clara de candidatos para que una persona
decida. Nunca se elige "el primero" ni se ordena alfabeticamente (agosto
quedaria antes que enero, que es incorrecto cronologicamente).

Estrategia: la senal PRINCIPAL es la fecha (dia/mes/año, hasta donde el
nombre la traiga explicita) interpretada desde el NOMBRE del archivo --
nunca la fecha de modificacion, porque abrir o volver a guardar un Excel
viejo le cambia el mtime sin que eso signifique que ahora es el mas
reciente.

- Si el nombre trae año explicito, ESE año manda (no se infiere). Si ademas
  trae dia explicito, ESE dia manda tambien: la fecha explicita completa
  nunca es desplazada por mtime ni por una fecha menos especifica.
- Si el nombre NO trae año (formato historico, solo mes), el año se infiere
  contra una fecha de referencia (la fecha de salida de la ruta que se esta
  preparando): la ocurrencia mas reciente de ese mes que no sea posterior a
  esa fecha. Un archivo sin dia explicito se ordena como si fuera el
  primero de ese mes (dia 0), asi una salida con dia explicito del MISMO
  mes siempre queda despues.

La clave de orden es (año, mes, dia-o-0): dos candidatos con cualquiera de
estos tres valores distinto quedan ordenados sin ambiguedad, sin importar
mtime. Solo cuando la fecha completa coincide EXACTAMENTE entre dos
archivos entra en juego una jerarquia de "especificidad" (fecha con dia
explicito > con año explicito sin dia > totalmente inferida) y, si aun asi
empatan, el mtime como ultimo desempate -- y si ni eso alcanza, se aborta
como ambiguo en vez de adivinar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from .models import RouteConfig
from .text_utils import normalize_key

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# Rango razonable para un año explicito en el nombre; fuera de esto se
# considera un nombre invalido (se ignora, no se fuerza a encajar).
MIN_VALID_YEAR = 2000
MAX_VALID_YEAR = 2100

# Dos archivos con la MISMA fecha completa (año, mes, dia) y el mismo nivel
# de especificidad, con mtimes mas cerca que esto, se consideran "empatados"
# y se abortan como ambiguos en vez de adivinar.
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


class _ParsedName(NamedTuple):
    month_name: str
    month_index: int  # 1=enero ... 12=diciembre
    explicit_year: Optional[int]
    explicit_day: Optional[int]


@dataclass
class BaseFileCandidate:
    path: Path
    month_name: str
    month_index: int
    year: int  # explicito si estaba en el nombre; si no, inferido via reference_date
    explicit_year: Optional[int]  # None si el año se infirio (formato historico)
    explicit_day: Optional[int]  # None si el nombre no trae dia (formato mensual)
    mtime: float

    @property
    def sort_key(self) -> Tuple[int, int, int]:
        return (self.year, self.month_index, self.explicit_day or 0)

    @property
    def has_explicit_year(self) -> bool:
        return self.explicit_year is not None

    @property
    def has_explicit_day(self) -> bool:
        return self.explicit_day is not None

    @property
    def specificity(self) -> int:
        """0 = mes inferido, 1 = año explicito sin dia, 2 = fecha completa
        (dia+mes+año explicitos). Una fecha mas especifica siempre le gana a
        una menos especifica, nunca al reves, sin importar mtime."""

        if self.has_explicit_day:
            return 2
        if self.has_explicit_year:
            return 1
        return 0

    @property
    def display_label(self) -> str:
        if self.has_explicit_day:
            return f"{self.explicit_day:02d} {self.month_name} {self.year}"
        return f"{self.month_name} {self.year}" + ("" if self.has_explicit_year else " (inferido)")


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


def _parse_name(filename: str, prefix: str) -> Optional[_ParsedName]:
    """Interpreta, en orden de preferencia creciente:
        '<prefijo> <mes>.xlsx'
        '<prefijo> <mes> <año>.xlsx'
        '<prefijo> <DD> <mes> <año>.xlsx'   (formato preferido, nuevo)
    Devuelve None si el nombre no aplica o alguna parte explicita (año, dia)
    es invalida (nunca se fuerza a encajar ni se adivina un valor fuera de
    rango o una fecha calendario imposible)."""

    stem = Path(filename).stem  # sin ".xlsx"
    prefix_key = normalize_key(prefix)
    stem_key = normalize_key(stem)
    if not stem_key.startswith(prefix_key):
        return None
    remainder = stem_key[len(prefix_key):].strip()
    if not remainder:
        return None

    tokens = remainder.split(" ")
    day_token = None
    if len(tokens) == 1:
        month_token, year_token = tokens[0], None
    elif len(tokens) == 2:
        month_token, year_token = tokens
    elif len(tokens) == 3:
        day_token, month_token, year_token = tokens
    else:
        return None

    month_index = None
    month_name = None
    for idx, mes in enumerate(MESES_ES, start=1):
        if normalize_key(mes) == month_token:
            month_index, month_name = idx, mes
            break
    if month_index is None:
        return None

    explicit_year = None
    if year_token is not None:
        if not (year_token.isdigit() and len(year_token) == 4):
            return None
        year_val = int(year_token)
        if not (MIN_VALID_YEAR <= year_val <= MAX_VALID_YEAR):
            return None
        explicit_year = year_val

    explicit_day = None
    if day_token is not None:
        if not (day_token.isdigit() and 1 <= len(day_token) <= 2):
            return None
        day_val = int(day_token)
        try:
            date(explicit_year, month_index, day_val)  # valida que la fecha exista de verdad
        except ValueError:
            return None
        explicit_day = day_val

    return _ParsedName(
        month_name=month_name, month_index=month_index, explicit_year=explicit_year, explicit_day=explicit_day
    )


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
        parsed = _parse_name(child.name, config.filename_prefix)
        if parsed is None:  # nombre invalido/no reconocido: se ignora, no se adivina
            continue
        year = parsed.explicit_year if parsed.explicit_year is not None else _infer_year_for_month(
            parsed.month_index, reference_date
        )
        candidates.append(
            BaseFileCandidate(
                path=child,
                month_name=parsed.month_name,
                month_index=parsed.month_index,
                year=year,
                explicit_year=parsed.explicit_year,
                explicit_day=parsed.explicit_day,
                mtime=child.stat().st_mtime,
            )
        )
    return candidates


def _resolve_group_winner(group: List[BaseFileCandidate], route_dir: Path) -> BaseFileCandidate:
    """Dentro de un mismo (año, mes, dia): una fecha mas ESPECIFICA siempre
    le gana a una menos especifica, sin importar mtime. Solo se desempata
    por mtime entre candidatos del mismo nivel de especificidad."""

    ranked = sorted(group, key=lambda c: (c.specificity, c.mtime), reverse=True)
    if len(ranked) == 1:
        return ranked[0]

    top, second = ranked[0], ranked[1]
    if top.specificity > second.specificity:
        return top  # la fecha mas especifica manda, sin desempate por mtime

    # Mismo nivel de especificidad: ahi si desempata mtime.
    same_level = [c for c in ranked if c.specificity == top.specificity]
    same_level.sort(key=lambda c: c.mtime, reverse=True)
    newest, next_best = same_level[0], same_level[1]
    if (newest.mtime - next_best.mtime) < _MTIME_TIE_THRESHOLD_SECONDS:
        raise AmbiguousBaseFileError(
            f"Hay mas de un archivo para '{newest.display_label}' en {route_dir} "
            f"({[c.path.name for c in same_level]}) y sus fechas de modificacion no permiten desempatar "
            "con certeza. No se adivina; elimina o renombra el duplicado."
        )
    return newest


def resolve_base_file(
    routes_root: Path, vendor: str, route: str, config: RouteConfig, reference_date: date
) -> Path:
    """Devuelve el archivo base mas reciente para esa ruta, o lanza un
    FileResolutionError especifico (nunca adivina).

    `reference_date` es la fecha de la ruta que se esta preparando (la
    salida): se usa SOLO para inferir el año de nombres sin año explicito.
    """

    route_dir = find_vendor_route_dir(routes_root, vendor, route)
    candidates = list_base_file_candidates(route_dir, config, reference_date)

    if not candidates:
        raise NoBaseFileFoundError(
            f"No hay ningun archivo base para la ruta '{route}' de '{vendor}' en {route_dir} "
            f"(se buscaron archivos '{config.filename_prefix} <mes>[<año>].xlsx' con un mes valido en español)."
        )

    # Agrupar por (año, mes): dos candidatos con año/mes DISTINTOS nunca caen
    # en el mismo grupo (por construccion), asi que solo hay algo que
    # desempatar cuando dos archivos representan literalmente el mismo mes.
    groups: Dict[Tuple[int, int], List[BaseFileCandidate]] = {}
    for c in candidates:
        groups.setdefault(c.sort_key, []).append(c)

    resolved: Dict[Tuple[int, int], BaseFileCandidate] = {
        key: _resolve_group_winner(group, route_dir) for key, group in groups.items()
    }

    best_key = max(resolved.keys())
    return resolved[best_key].path
