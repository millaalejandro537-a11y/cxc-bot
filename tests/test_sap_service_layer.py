"""Contrato de la fuente Service Layer contra un transport FALSO (sin red, sin SAP)."""

import pytest

from cxc_bot.data_source import InMemoryDataSource, LocalMockDataSource
from cxc_bot.sap_fakes import FakeServiceLayerTransport, make_business_partner
from cxc_bot.sap_service_layer import (
    BusinessPartnerFieldMap,
    SapAuthError,
    SapDataQualityError,
    SapPermissionError,
    SapSchemaError,
    SapServiceLayerDataSource,
    SapSessionExpiredError,
    SapSourceError,
    SapUnavailableError,
    UnconfiguredServiceLayerTransport,
)


def source(items, **kw):
    t = FakeServiceLayerTransport(items, **kw)
    return SapServiceLayerDataSource(transport=t), t


def test_maps_and_paginates_all_pages():
    ds, t = source([make_business_partner(i, saldo=i * 10.5) for i in range(1, 6)], page_size=2)
    recs = ds.fetch_clients()
    assert [r.codigo for r in recs] == [f"C{i:05d}" for i in range(1, 6)]
    assert recs[1].saldo == 21.0 and recs[0].nombre == "CLIENTE SINTETICO 1"
    assert recs[0].direccion_factura == "DIRECCION 1" and recs[0].contabilizacion_automatica is None
    assert len(t.calls) == 3
    assert "startswith(CardCode,'C')" in t.calls[0][1]["$filter"]
    assert t.calls[0][1]["$select"].startswith("CardCode,CardName,CurrentAccountBalance")


def test_login_and_logout_always_paired_even_on_failure():
    ds, t = source([make_business_partner(1)], fail_on_get={1: SapUnavailableError("503")})
    with pytest.raises(SapUnavailableError):
        ds.fetch_clients()
    assert t.login_count == 1 and t.logout_count == 1


def test_session_expired_relogs_once_and_succeeds():
    ds, t = source([make_business_partner(1), make_business_partner(2)], page_size=1,
                   fail_on_get={2: SapSessionExpiredError("401")})
    assert len(ds.fetch_clients()) == 2
    assert t.login_count == 2


def test_session_expired_twice_gives_up():
    ds, t = source([make_business_partner(1)], fail_on_get={1: SapSessionExpiredError("401"),
                                                             2: SapSessionExpiredError("401")})
    with pytest.raises(SapAuthError):
        ds.fetch_clients()


@pytest.mark.parametrize("exc", [SapAuthError("x"), SapPermissionError("x"), SapUnavailableError("x")])
def test_transport_errors_propagate_as_sap_source_error(exc):
    ds, _ = source([make_business_partner(1)], fail_login=exc)
    with pytest.raises(SapSourceError):
        ds.fetch_clients()


def test_empty_result_is_rejected():
    ds, _ = source([])
    with pytest.raises(SapDataQualityError):
        ds.fetch_clients()


def test_duplicate_codes_rejected_case_insensitive():
    ds, _ = source([make_business_partner(1), make_business_partner(1, CardCode="c00001")])
    with pytest.raises(SapDataQualityError):
        ds.fetch_clients()


def test_non_numeric_balance_is_error_not_zero():
    ds, _ = source([make_business_partner(1, CurrentAccountBalance="abc")])
    with pytest.raises(SapSchemaError):
        ds.fetch_clients()


def test_missing_code_is_schema_error():
    ds, _ = source([make_business_partner(1, CardCode=None)])
    with pytest.raises(SapSchemaError):
        ds.fetch_clients()


def test_non_c_code_rejected_despite_filter():
    ds, _ = source([make_business_partner(1, CardCode="V00001")])
    with pytest.raises(SapDataQualityError):
        ds.fetch_clients()


def test_response_without_value_list_is_schema_error():
    class Bad(FakeServiceLayerTransport):
        def get(self, path, params=None):
            return {"oops": 1}

    with pytest.raises(SapSchemaError):
        SapServiceLayerDataSource(transport=Bad([])).fetch_clients()


def test_runaway_pagination_aborts():
    class Loop(FakeServiceLayerTransport):
        def get(self, path, params=None):
            return {"value": [make_business_partner(1)], "odata.nextLink": "BusinessPartners?$skip=1"}

    ds = SapServiceLayerDataSource(transport=Loop([]), max_pages=3)
    with pytest.raises(SapDataQualityError):
        ds.fetch_clients()


def test_custom_field_map():
    fm = BusinessPartnerFieldMap(saldo="Balance", direccion_factura=None, activo_desde=None)
    item = {"CardCode": "C1", "CardName": "N", "Balance": 5}
    t = FakeServiceLayerTransport([item])
    rec = SapServiceLayerDataSource(transport=t, field_map=fm).fetch_clients()[0]
    assert rec.saldo == 5.0 and rec.direccion_factura is None


def test_default_transport_fails_loudly_without_network():
    with pytest.raises(NotImplementedError):
        SapServiceLayerDataSource().fetch_clients()
    with pytest.raises(NotImplementedError):
        UnconfiguredServiceLayerTransport().get("x")


def test_is_mock_flags():
    assert SapServiceLayerDataSource().is_mock is False
    assert InMemoryDataSource([]).is_mock is True
    assert LocalMockDataSource.is_mock.fget(object()) is True


# ---- integracion con el runner (todo sintetico, sin red) -------------------
from datetime import date  # noqa: E402

from cxc_bot.business_calendar import BusinessCalendar  # noqa: E402
from cxc_bot.business_history import BusinessHistoryStore  # noqa: E402
from cxc_bot.catalog import VendorCatalog  # noqa: E402
from cxc_bot.history_store import HistoryStore  # noqa: E402
from cxc_bot.models import ScheduleEntry  # noqa: E402
from cxc_bot.runner import process_due_routes  # noqa: E402
from cxc_bot.schedule_source import ScheduleSource  # noqa: E402


class _Sched(ScheduleSource):
    def list_entries(self):
        return [ScheduleEntry(id="e1", vendor="Erick Brizuela", route="Cortés",
                              fecha_salida=date(2026, 10, 13), aprobado=True)]


def _run(tmp_path, ds, **kw):
    from synthetic_fixtures import build_cortes_base
    root = tmp_path / "RUTA VENDEDORES" / "Erick Brizuela" / "Cortes"
    root.mkdir(parents=True)
    build_cortes_base(root / "CORTES septiembre.xlsx")
    bh = BusinessHistoryStore(tmp_path / "b.sqlite3")
    res = process_due_routes(
        schedule_source=_Sched(), catalog=VendorCatalog.load(), calendar=BusinessCalendar(),
        lead_business_days=3, routes_root=tmp_path / "RUTA VENDEDORES", sap_data_source=ds,
        sap_source_label="service-layer-fake", history=HistoryStore(tmp_path / "h.sqlite3"),
        today=date(2026, 10, 9), business_history=bh, **kw,
    )
    return res, bh


def test_runner_sap_failure_is_fuente_datos_invalida(tmp_path):
    ds, _ = source([make_business_partner(1)], fail_on_get={1: SapUnavailableError("503")})
    res, _ = _run(tmp_path, ds)
    assert res.failed == 1 and res.routes[0].error_category == "fuente_datos_invalida"


def test_runner_real_source_is_not_downgraded_but_mock_is(tmp_path, baseline_sap_records):
    items = [make_business_partner(0, CardCode=r.codigo, CardName=r.nombre, CurrentAccountBalance=r.saldo)
             for r in baseline_sap_records]
    ds, _ = source(items, page_size=50)
    res, bh = _run(tmp_path, ds, environment="prod")  # sap_is_mock se deriva de la fuente
    assert res.succeeded == 1
    snap = bh.find_vigente("Erick Brizuela", "Cortés", date(2026, 10, 13))
    assert snap["environment"] == "prod" and not snap["fuente_datos_es_mock"]
