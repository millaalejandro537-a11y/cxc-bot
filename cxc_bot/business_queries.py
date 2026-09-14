"""Consultas historicas basicas sobre snapshots de negocio (seccion 30 del
encargo). Deliberadamente simples: solo numeros objetivos (saldo total,
variacion absoluta/porcentual, quien subio/bajo). Ninguna funcion aqui
etiqueta un resultado como "mejoro"/"empeoro" -- esa es una decision de
negocio que no esta definida todavia (seccion 31: no confundir saldo con
mora, no inventar reglas de interpretacion sin aprobar).
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from .business_history import BusinessHistoryStore


@dataclass
class SnapshotSummary:
    snapshot_id: str
    fecha_salida: date
    saldo_total: float
    cantidad_clientes: int


@dataclass
class ClientBalanceChange:
    codigo: str
    nombre: Optional[str]
    saldo_anterior: Optional[float]
    saldo_actual: Optional[float]
    variacion_absoluta: Optional[float]
    variacion_porcentual: Optional[float]


def route_balance_history(store: BusinessHistoryStore, route: str, environment: Optional[str] = None) -> List[SnapshotSummary]:
    """Evolucion del saldo total de una ruta, salida por salida, en orden
    cronologico. Solo snapshots 'vigentes' (una sustitucion no cuenta dos
    veces la misma salida)."""

    rows = store.list_by_route(route, environment=environment, only_vigente=True)
    return [
        SnapshotSummary(
            snapshot_id=r["snapshot_id"],
            fecha_salida=date.fromisoformat(r["fecha_salida"]),
            saldo_total=r["saldo_total"] or 0.0,
            cantidad_clientes=r["cantidad_clientes"] or 0,
        )
        for r in rows
    ]


def compare_snapshots(store: BusinessHistoryStore, snapshot_id_before: str, snapshot_id_after: str) -> Dict:
    """Salida vs salida anterior (o mes vs mes, si se le pasan snapshots de
    meses distintos): variacion absoluta y porcentual del total, y detalle
    por cliente."""

    before = store.get_snapshot(snapshot_id_before)
    after = store.get_snapshot(snapshot_id_after)
    if before is None or after is None:
        raise ValueError("Alguno de los dos snapshots no existe.")

    total_before = before["saldo_total"] or 0.0
    total_after = after["saldo_total"] or 0.0
    variacion_absoluta = total_after - total_before
    variacion_porcentual = (variacion_absoluta / total_before * 100.0) if total_before else None

    clients_before = {c["codigo"]: c for c in store.get_snapshot_clients(snapshot_id_before)}
    clients_after = {c["codigo"]: c for c in store.get_snapshot_clients(snapshot_id_after)}

    all_codes = set(clients_before) | set(clients_after)
    per_client: List[ClientBalanceChange] = []
    for codigo in sorted(all_codes):
        c_before = clients_before.get(codigo)
        c_after = clients_after.get(codigo)
        saldo_before = c_before["saldo"] if c_before else None
        saldo_after = c_after["saldo"] if c_after else None
        abs_change = None
        pct_change = None
        if saldo_before is not None and saldo_after is not None:
            abs_change = saldo_after - saldo_before
            pct_change = (abs_change / saldo_before * 100.0) if saldo_before else None
        per_client.append(
            ClientBalanceChange(
                codigo=codigo,
                nombre=(c_after or c_before)["nombre"],
                saldo_anterior=saldo_before,
                saldo_actual=saldo_after,
                variacion_absoluta=abs_change,
                variacion_porcentual=pct_change,
            )
        )

    return {
        "saldo_total_anterior": total_before,
        "saldo_total_actual": total_after,
        "variacion_absoluta": variacion_absoluta,
        "variacion_porcentual": variacion_porcentual,
        "clientes": per_client,
    }


def clients_with_increased_balance(comparison: Dict) -> List[ClientBalanceChange]:
    return [c for c in comparison["clientes"] if c.variacion_absoluta is not None and c.variacion_absoluta > 0]


def clients_with_decreased_balance(comparison: Dict) -> List[ClientBalanceChange]:
    return [c for c in comparison["clientes"] if c.variacion_absoluta is not None and c.variacion_absoluta < 0]


def clients_with_persistent_balance(comparison: Dict) -> List[ClientBalanceChange]:
    """Clientes con saldo > 0 en ambos snapshots (no aparecieron ni
    desaparecieron, y siguen debiendo)."""

    return [
        c for c in comparison["clientes"]
        if c.saldo_anterior not in (None, 0) and c.saldo_actual not in (None, 0)
    ]


def count_clients_with_balance(store: BusinessHistoryStore, snapshot_id: str) -> int:
    rows = store.get_snapshot_clients(snapshot_id)
    return sum(1 for r in rows if (r["saldo"] or 0) > 0)


def client_history(store: BusinessHistoryStore, codigo: str, route: Optional[str] = None) -> List[Dict]:
    """Historial de un cliente especifico a traves de todos los snapshots
    vigentes (opcionalmente filtrado por ruta)."""

    with closing(store.connect()) as conn:
        query = (
            "SELECT s.snapshot_id, s.route, s.fecha_salida, s.estado, sc.saldo, sc.nombre, sc.componente "
            "FROM snapshot_clients sc JOIN snapshots s ON s.snapshot_id = sc.snapshot_id "
            "WHERE sc.codigo = ? AND s.estado = 'vigente'"
        )
        params: list = [codigo]
        if route:
            query += " AND s.route = ?"
            params.append(route)
        query += " ORDER BY s.fecha_salida"
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def component_breakdown(store: BusinessHistoryStore, snapshot_id: str) -> Dict[str, Dict]:
    """Para una ruta compuesta: saldo total y cantidad de clientes por
    componente dentro de UN snapshot (p.ej. Comayagua vs Santa Cruz de Yojoa
    dentro de la misma salida de Comayagua)."""

    rows = store.get_snapshot_clients(snapshot_id)
    breakdown: Dict[str, Dict] = {}
    for r in rows:
        key = r["componente"] or "(sin componente)"
        entry = breakdown.setdefault(key, {"cantidad_clientes": 0, "saldo_total": 0.0})
        entry["cantidad_clientes"] += 1
        entry["saldo_total"] += r["saldo"] or 0.0
    return breakdown


def multiple_departures_in_month(store: BusinessHistoryStore, route: str, year: int, month: int) -> List[SnapshotSummary]:
    """Todas las salidas vigentes de una ruta dentro de un mes especifico
    (soporta el caso de la misma ruta saliendo mas de una vez en el mes)."""

    all_snapshots = route_balance_history(store, route)
    return [s for s in all_snapshots if s.fecha_salida.year == year and s.fecha_salida.month == month]
