import pytest

from cxc_bot.route_engine import (
    AddClient,
    ClientNotFoundError,
    DuplicateClientError,
    InvalidOperationError,
    MoveClient,
    RemoveClient,
    ReorderAll,
    apply_operations,
)
from cxc_bot.models import ClientEntry


def roster(*codes):
    return [ClientEntry(codigo=c) for c in codes]


def test_add_after():
    r = roster("C1", "C2", "C3")
    result = apply_operations(r, [AddClient(codigo="C99", after_codigo="C2")])
    assert [e.codigo for e in result] == ["C1", "C2", "C99", "C3"]


def test_add_at_start():
    r = roster("C1", "C2")
    result = apply_operations(r, [AddClient(codigo="C99", at_start=True)])
    assert [e.codigo for e in result] == ["C99", "C1", "C2"]


def test_add_default_appends_at_end():
    r = roster("C1", "C2")
    result = apply_operations(r, [AddClient(codigo="C99")])
    assert [e.codigo for e in result] == ["C1", "C2", "C99"]


def test_add_duplicate_fails_safely():
    r = roster("C1", "C2")
    with pytest.raises(DuplicateClientError):
        apply_operations(r, [AddClient(codigo="C1")])


def test_add_invalid_code_fails_safely():
    r = roster("C1")
    with pytest.raises(InvalidOperationError):
        apply_operations(r, [AddClient(codigo="X999")])


def test_remove():
    r = roster("C1", "C2", "C3")
    result = apply_operations(r, [RemoveClient(codigo="C2")])
    assert [e.codigo for e in result] == ["C1", "C3"]


def test_remove_nonexistent_fails_safely():
    r = roster("C1", "C2")
    with pytest.raises(ClientNotFoundError):
        apply_operations(r, [RemoveClient(codigo="C999")])


def test_move_after():
    r = roster("C1", "C2", "C3", "C4")
    result = apply_operations(r, [MoveClient(codigo="C1", after_codigo="C3")])
    assert [e.codigo for e in result] == ["C2", "C3", "C1", "C4"]


def test_move_to_start():
    r = roster("C1", "C2", "C3")
    result = apply_operations(r, [MoveClient(codigo="C3", at_start=True)])
    assert [e.codigo for e in result] == ["C3", "C1", "C2"]


def test_move_nonexistent_fails_safely():
    r = roster("C1", "C2")
    with pytest.raises(ClientNotFoundError):
        apply_operations(r, [MoveClient(codigo="C999", after_codigo="C1")])


def test_move_after_itself_fails_safely():
    r = roster("C1", "C2")
    with pytest.raises(InvalidOperationError):
        apply_operations(r, [MoveClient(codigo="C1", after_codigo="C1")])


def test_reorder_all():
    r = roster("C1", "C2", "C3")
    result = apply_operations(r, [ReorderAll(codigos=["C3", "C1", "C2"])])
    assert [e.codigo for e in result] == ["C3", "C1", "C2"]


def test_reorder_missing_client_fails_safely():
    r = roster("C1", "C2", "C3")
    with pytest.raises(InvalidOperationError):
        apply_operations(r, [ReorderAll(codigos=["C1", "C2"])])


def test_reorder_extra_client_fails_safely():
    r = roster("C1", "C2")
    with pytest.raises(InvalidOperationError):
        apply_operations(r, [ReorderAll(codigos=["C1", "C2", "C999"])])


def test_add_remove_move_combo():
    r = roster("C1", "C2", "C3", "C4")
    ops = [
        AddClient(codigo="C99", after_codigo="C2"),
        RemoveClient(codigo="C4"),
        MoveClient(codigo="C1", after_codigo="C3"),
    ]
    result = apply_operations(r, ops)
    assert [e.codigo for e in result] == ["C2", "C99", "C3", "C1"]


def test_failed_operation_in_batch_does_not_partially_apply():
    r = roster("C1", "C2", "C3")
    ops = [AddClient(codigo="C99", after_codigo="C2"), RemoveClient(codigo="DOES_NOT_EXIST")]
    with pytest.raises(ClientNotFoundError):
        apply_operations(r, ops)
    # el roster original (fuera de la funcion) nunca fue mutado
    assert [e.codigo for e in r] == ["C1", "C2", "C3"]
