"""Cubre las secciones 24-28 del encargo: snapshots de negocio, inmutables,
separados del historial tecnico, con el candado DEV/TEST/PROD."""

from datetime import date

import pytest

from cxc_bot.business_history import (
    BusinessHistoryStore,
    DuplicateVigenteSnapshotError,
    SnapshotClientInput,
)


@pytest.fixture
def store(tmp_path):
    return BusinessHistoryStore(tmp_path / "business.sqlite3")


def make_clients(n=3):
    return [
        SnapshotClientInput(codigo=f"C{i:05d}", nombre=f"Cliente {i}", posicion=i, saldo=float(i * 100))
        for i in range(1, n + 1)
    ]


def test_create_snapshot_basic(store):
    result = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=make_clients(3), fuente_datos_es_mock=True,
    )
    assert result.environment == "dev"
    assert result.downgraded_from_prod is False

    row = store.get_snapshot(result.snapshot_id)
    assert row["vendor"] == "Erick Brizuela"
    assert row["cantidad_clientes"] == 3
    assert row["saldo_total"] == 600.0  # 100+200+300
    assert row["estado"] == "vigente"

    clients = store.get_snapshot_clients(result.snapshot_id)
    assert [c["codigo"] for c in clients] == ["C00001", "C00002", "C00003"]


def test_prod_snapshot_with_mock_data_is_downgraded_to_dev(store):
    """Candado tecnico: nunca un snapshot oficial de PROD con datos mock,
    aunque el llamador pida environment='prod' explicitamente."""
    result = store.create_snapshot(
        environment="prod", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=make_clients(2), fuente_datos_es_mock=True,
    )
    assert result.environment == "dev"
    assert result.downgraded_from_prod is True

    row = store.get_snapshot(result.snapshot_id)
    assert row["environment"] == "dev"


def test_prod_snapshot_with_real_data_is_accepted(store):
    result = store.create_snapshot(
        environment="prod", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=make_clients(2), fuente_datos_es_mock=False,
    )
    assert result.environment == "prod"
    assert result.downgraded_from_prod is False


def test_only_one_vigente_snapshot_per_salida(store):
    store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=make_clients(1),
    )
    with pytest.raises(DuplicateVigenteSnapshotError):
        store.create_snapshot(
            environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
            clients=make_clients(1),
        )


def test_replace_snapshot_creates_new_vigente_and_marks_old_as_sustituido(store):
    """El indice unico exige que solo haya un 'vigente' por salida, pero el
    id del snapshot nuevo no existe todavia cuando hay que liberar el slot
    del viejo -- replace_snapshot() resuelve ese orden en una transaccion."""
    first = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=make_clients(1),
    )

    second = store.replace_snapshot(
        first.snapshot_id,
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=make_clients(2),
    )

    old_row = store.get_snapshot(first.snapshot_id)
    assert old_row["estado"] == "sustituido"
    assert old_row["sustituido_por"] == second.snapshot_id
    # el snapshot viejo NUNCA se borra ni cambia su contenido
    assert old_row["cantidad_clientes"] == 1

    new_row = store.get_snapshot(second.snapshot_id)
    assert new_row["estado"] == "vigente"

    vigente = store.find_vigente("Erick Brizuela", "Cortés", date(2026, 10, 13))
    assert vigente["snapshot_id"] == second.snapshot_id


def test_cancel_snapshot(store):
    result = store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
        clients=make_clients(1),
    )
    store.cancel_snapshot(result.snapshot_id, motivo="salida cancelada por el vendedor")
    row = store.get_snapshot(result.snapshot_id)
    assert row["estado"] == "cancelado"
    assert row["cancelado_motivo"] == "salida cancelada por el vendedor"
    assert store.find_vigente("Erick Brizuela", "Cortés", date(2026, 10, 13)) is None


def test_snapshots_are_immutable_no_update_api_exists(store):
    """No existe ningun metodo para EDITAR los datos de un snapshot ya
    creado: los unicos metodos de escritura crean uno nuevo (create/replace)
    o cambian solo el 'estado' de uno existente (supersede/cancel) -- nunca
    sus datos (saldo, clientes, archivo, etc)."""
    write_methods = {
        name for name in dir(store)
        if not name.startswith("_") and callable(getattr(store, name))
        and any(name.startswith(p) for p in ("create", "supersede", "cancel", "replace"))
    }
    assert write_methods == {"create_snapshot", "supersede_snapshot", "cancel_snapshot", "replace_snapshot"}
    assert not any("update" in name.lower() for name in dir(store) if not name.startswith("_"))


def test_component_traceability_is_stored_per_client(store):
    clients = [
        SnapshotClientInput(codigo="C10822", nombre="Cliente Comayagua", posicion=1, saldo=100.0, componente="Comayagua"),
        SnapshotClientInput(codigo="C10572", nombre="Cliente Santa Cruz", posicion=2, saldo=200.0, componente="Santa Cruz de Yojoa"),
    ]
    result = store.create_snapshot(
        environment="dev", vendor="José Carpio", route="Comayagua", fecha_salida=date(2026, 10, 15), clients=clients,
    )
    rows = store.get_snapshot_clients(result.snapshot_id)
    componentes = {r["codigo"]: r["componente"] for r in rows}
    assert componentes["C10822"] == "Comayagua"
    assert componentes["C10572"] == "Santa Cruz de Yojoa"


def test_list_by_route_filters_by_environment_and_vigente(store):
    store.create_snapshot(
        environment="dev", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 9, 13), clients=make_clients(1)
    )
    store.create_snapshot(
        environment="test", vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13), clients=make_clients(1)
    )
    dev_only = store.list_by_route("Cortés", environment="dev")
    assert len(dev_only) == 1
    assert dev_only[0]["fecha_salida"] == "2026-09-13"

    all_routes = store.list_by_route("Cortés")
    assert len(all_routes) == 2


def test_invalid_environment_rejected(store):
    with pytest.raises(Exception):
        store.create_snapshot(
            environment="produccion",  # no es un valor valido
            vendor="Erick Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13), clients=make_clients(1),
        )
