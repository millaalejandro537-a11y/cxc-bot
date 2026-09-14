from datetime import date

import pytest

from cxc_bot.business_history import BusinessHistoryStore, SnapshotClientInput
from cxc_bot.business_queries import (
    client_history,
    clients_with_decreased_balance,
    clients_with_increased_balance,
    clients_with_persistent_balance,
    compare_snapshots,
    component_breakdown,
    count_clients_with_balance,
    multiple_departures_in_month,
    route_balance_history,
)


@pytest.fixture
def store(tmp_path):
    return BusinessHistoryStore(tmp_path / "business.sqlite3")


def test_route_balance_history_chronological(store):
    store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 9, 13),
        clients=[SnapshotClientInput(codigo="C1", nombre="A", posicion=1, saldo=100.0)],
    )
    store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=[SnapshotClientInput(codigo="C1", nombre="A", posicion=1, saldo=150.0)],
    )
    history = route_balance_history(store, "Cortés")
    assert [h.saldo_total for h in history] == [100.0, 150.0]
    assert history[0].fecha_salida < history[1].fecha_salida


def test_compare_snapshots_computes_variation(store):
    s1 = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 9, 13),
        clients=[SnapshotClientInput(codigo="C1", nombre="A", posicion=1, saldo=100.0)],
    )
    s2 = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=[SnapshotClientInput(codigo="C1", nombre="A", posicion=1, saldo=150.0)],
    )
    comparison = compare_snapshots(store, s1.snapshot_id, s2.snapshot_id)
    assert comparison["saldo_total_anterior"] == 100.0
    assert comparison["saldo_total_actual"] == 150.0
    assert comparison["variacion_absoluta"] == 50.0
    assert comparison["variacion_porcentual"] == 50.0


def test_clients_increased_decreased_and_new_or_gone(store):
    s1 = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 9, 13),
        clients=[
            SnapshotClientInput(codigo="C1", nombre="Sube", posicion=1, saldo=100.0),
            SnapshotClientInput(codigo="C2", nombre="Baja", posicion=2, saldo=200.0),
            SnapshotClientInput(codigo="C3", nombre="Se va", posicion=3, saldo=50.0),
        ],
    )
    s2 = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=[
            SnapshotClientInput(codigo="C1", nombre="Sube", posicion=1, saldo=300.0),
            SnapshotClientInput(codigo="C2", nombre="Baja", posicion=2, saldo=100.0),
            SnapshotClientInput(codigo="C4", nombre="Nuevo", posicion=3, saldo=75.0),
        ],
    )
    comparison = compare_snapshots(store, s1.snapshot_id, s2.snapshot_id)

    increased = {c.codigo for c in clients_with_increased_balance(comparison)}
    decreased = {c.codigo for c in clients_with_decreased_balance(comparison)}
    assert increased == {"C1"}
    assert decreased == {"C2"}

    # C3 desaparecio (solo tenia saldo antes) y C4 es nuevo (solo despues) --
    # ninguno de los dos tiene ambos lados, asi que no cuentan como
    # incremento/decremento (no se puede comparar contra la nada).
    codes_in_comparison = {c.codigo for c in comparison["clientes"]}
    assert "C3" in codes_in_comparison and "C4" in codes_in_comparison
    assert "C3" not in increased and "C3" not in decreased
    assert "C4" not in increased and "C4" not in decreased


def test_clients_with_persistent_balance(store):
    s1 = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 9, 13),
        clients=[
            SnapshotClientInput(codigo="C1", nombre="Persistente", posicion=1, saldo=100.0),
            SnapshotClientInput(codigo="C2", nombre="Ya pago", posicion=2, saldo=200.0),
        ],
    )
    s2 = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=[
            SnapshotClientInput(codigo="C1", nombre="Persistente", posicion=1, saldo=90.0),
            SnapshotClientInput(codigo="C2", nombre="Ya pago", posicion=2, saldo=0.0),
        ],
    )
    comparison = compare_snapshots(store, s1.snapshot_id, s2.snapshot_id)
    persistent = {c.codigo for c in clients_with_persistent_balance(comparison)}
    assert persistent == {"C1"}


def test_count_clients_with_balance_excludes_zero(store):
    result = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 9, 13),
        clients=[
            SnapshotClientInput(codigo="C1", nombre="Debe", posicion=1, saldo=100.0),
            SnapshotClientInput(codigo="C2", nombre="No debe", posicion=2, saldo=0.0),
        ],
    )
    assert count_clients_with_balance(store, result.snapshot_id) == 1


def test_client_history_across_snapshots(store):
    store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 9, 13),
        clients=[SnapshotClientInput(codigo="C1", nombre="Ana", posicion=1, saldo=100.0)],
    )
    store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=[SnapshotClientInput(codigo="C1", nombre="Ana", posicion=1, saldo=50.0)],
    )
    history = client_history(store, "C1")
    assert [h["saldo"] for h in history] == [100.0, 50.0]


def test_component_breakdown_for_composite_route(store):
    result = store.create_snapshot(
        environment="dev", vendor="José Carpio", route="Comayagua", fecha_salida=date(2026, 10, 15),
        clients=[
            SnapshotClientInput(codigo="C1", nombre="A", posicion=1, saldo=100.0, componente="Comayagua"),
            SnapshotClientInput(codigo="C2", nombre="B", posicion=2, saldo=50.0, componente="Comayagua"),
            SnapshotClientInput(codigo="C3", nombre="C", posicion=3, saldo=200.0, componente="Santa Cruz de Yojoa"),
        ],
    )
    breakdown = component_breakdown(store, result.snapshot_id)
    assert breakdown["Comayagua"]["cantidad_clientes"] == 2
    assert breakdown["Comayagua"]["saldo_total"] == 150.0
    assert breakdown["Santa Cruz de Yojoa"]["cantidad_clientes"] == 1
    assert breakdown["Santa Cruz de Yojoa"]["saldo_total"] == 200.0


def test_multiple_departures_in_month(store):
    store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 8),
        clients=[SnapshotClientInput(codigo="C1", nombre="A", posicion=1, saldo=100.0)],
    )
    store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 22),
        clients=[SnapshotClientInput(codigo="C1", nombre="A", posicion=1, saldo=120.0)],
    )
    store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 11, 5),
        clients=[SnapshotClientInput(codigo="C1", nombre="A", posicion=1, saldo=90.0)],
    )
    october_trips = multiple_departures_in_month(store, "Cortés", 2026, 10)
    assert len(october_trips) == 2
    assert {t.fecha_salida for t in october_trips} == {date(2026, 10, 8), date(2026, 10, 22)}
