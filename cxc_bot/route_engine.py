"""Logica pura de edicion de la lista de clientes de una ruta.

No depende de openpyxl ni de ningun formato de archivo: trabaja sobre una
lista ordenada de ClientEntry y aplica operaciones identificadas por codigo
de cliente (nunca por numero de fila). excel_service es quien traduce esta
lista hacia/desde el Excel real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .models import ClientEntry


class RouteEngineError(Exception):
    """Error de negocio al aplicar una operacion sobre la ruta (falla de forma segura)."""


class ClientNotFoundError(RouteEngineError):
    pass


class DuplicateClientError(RouteEngineError):
    pass


class InvalidOperationError(RouteEngineError):
    pass


@dataclass
class AddClient:
    codigo: str
    after_codigo: Optional[str] = None
    at_start: bool = False
    no_factura: Optional[str] = None
    observacion: Optional[str] = None


@dataclass
class RemoveClient:
    codigo: str


@dataclass
class MoveClient:
    codigo: str
    after_codigo: Optional[str] = None
    at_start: bool = False


@dataclass
class ReorderAll:
    """Reemplaza el orden completo por la lista de codigos dada (deben ser los mismos)."""

    codigos: List[str]


Operation = object  # AddClient | RemoveClient | MoveClient | ReorderAll


def _find_index(roster: List[ClientEntry], codigo: str) -> int:
    for i, entry in enumerate(roster):
        if entry.codigo == codigo:
            return i
    raise ClientNotFoundError(f"El cliente '{codigo}' no existe en la ruta actual.")


def _insert_position(roster: List[ClientEntry], after_codigo: Optional[str], at_start: bool) -> int:
    if at_start:
        return 0
    if after_codigo is None:
        return len(roster)
    idx = _find_index(roster, after_codigo)
    return idx + 1


def apply_add(roster: List[ClientEntry], op: AddClient) -> List[ClientEntry]:
    if not op.codigo or not op.codigo.strip():
        raise InvalidOperationError("El codigo de cliente a agregar no puede estar vacio.")
    codigo = op.codigo.strip()
    if not codigo.upper().startswith("C"):
        raise InvalidOperationError(f"Codigo de cliente invalido: '{codigo}' (debe iniciar con 'C').")
    if any(e.codigo == codigo for e in roster):
        raise DuplicateClientError(f"El cliente '{codigo}' ya existe en la ruta; no se puede agregar duplicado.")
    if op.after_codigo and op.at_start:
        raise InvalidOperationError("No se puede indicar 'after_codigo' y 'at_start' al mismo tiempo.")

    pos = _insert_position(roster, op.after_codigo, op.at_start)
    new_roster = list(roster)
    new_roster.insert(pos, ClientEntry(codigo=codigo, no_factura=op.no_factura, observacion=op.observacion))
    return new_roster


def apply_remove(roster: List[ClientEntry], op: RemoveClient) -> List[ClientEntry]:
    idx = _find_index(roster, op.codigo)
    new_roster = list(roster)
    del new_roster[idx]
    return new_roster


def apply_move(roster: List[ClientEntry], op: MoveClient) -> List[ClientEntry]:
    if op.after_codigo and op.at_start:
        raise InvalidOperationError("No se puede indicar 'after_codigo' y 'at_start' al mismo tiempo.")
    if op.after_codigo == op.codigo:
        raise InvalidOperationError(f"'{op.codigo}' no puede moverse despues de si mismo.")

    idx = _find_index(roster, op.codigo)
    new_roster = list(roster)
    entry = new_roster.pop(idx)

    pos = _insert_position(new_roster, op.after_codigo, op.at_start)
    new_roster.insert(pos, entry)
    return new_roster


def apply_reorder(roster: List[ClientEntry], op: ReorderAll) -> List[ClientEntry]:
    current_codes = {e.codigo for e in roster}
    new_codes = op.codigos
    if len(new_codes) != len(set(new_codes)):
        raise InvalidOperationError("La lista de reordenamiento tiene codigos duplicados.")
    if set(new_codes) != current_codes:
        faltantes = current_codes - set(new_codes)
        sobrantes = set(new_codes) - current_codes
        detalle = []
        if faltantes:
            detalle.append(f"faltan: {sorted(faltantes)}")
        if sobrantes:
            detalle.append(f"no pertenecen a la ruta: {sorted(sobrantes)}")
        raise InvalidOperationError(
            "El reordenamiento completo debe incluir exactamente los mismos clientes actuales ("
            + "; ".join(detalle)
            + "). Usa AddClient/RemoveClient para cambiar la composicion de la ruta."
        )
    by_code = {e.codigo: e for e in roster}
    return [by_code[c] for c in new_codes]


def apply_operation(roster: List[ClientEntry], op: Operation) -> List[ClientEntry]:
    if isinstance(op, AddClient):
        return apply_add(roster, op)
    if isinstance(op, RemoveClient):
        return apply_remove(roster, op)
    if isinstance(op, MoveClient):
        return apply_move(roster, op)
    if isinstance(op, ReorderAll):
        return apply_reorder(roster, op)
    raise InvalidOperationError(f"Tipo de operacion desconocido: {op!r}")


def apply_operations(roster: List[ClientEntry], operations: List[Operation]) -> List[ClientEntry]:
    """Aplica una lista de operaciones en orden. Si alguna falla, no se aplica ninguna
    (se opera sobre copias y solo se confirma el resultado final)."""

    current = list(roster)
    for op in operations:
        current = apply_operation(current, op)
    validate_roster(current)
    return current


def validate_roster(roster: List[ClientEntry]) -> None:
    codes = [e.codigo for e in roster]
    if len(codes) != len(set(codes)):
        dupes = {c for c in codes if codes.count(c) > 1}
        raise DuplicateClientError(f"La ruta resultante tiene codigos duplicados: {sorted(dupes)}")
    for c in codes:
        if not c or not c.upper().startswith("C"):
            raise InvalidOperationError(f"Codigo de cliente invalido en la ruta: '{c}'")
