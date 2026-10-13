"""Integracion del motor unico `payment_semaphore` con el generador real de rutas (runner), sin alterar su funcionamiento."""

import dataclasses
from datetime import date, timedelta

import pytest

from cxc_bot import payment_semaphore as ps
from cxc_bot import runner
from cxc_bot.config import load_all_routes
from cxc_bot.runner import process_due_routes
from tests.test_runner import FakeDataSource, FakeScheduleSource, calendar, catalog, history, make_entry, routes_root, sap_source  # noqa: F401

CORTE = date(2026, 10, 8)


def _serie(moras):
    out = []
    for i, m in enumerate(moras):
        e = date(2025, 1, 1) + timedelta(days=20 * i)
        out.append(ps.FacturaSAP(str(i + 1), str(i + 1), e, e + timedelta(days=30), 1000.0, 1000.0, "C",
                                 (ps.Recibo(f"R{i}", e + timedelta(days=60 + m), 1000.0),)))
    return out


class Evidencia:
    fecha_corte = CORTE
    etiqueta = "evidencia de prueba"

    def __init__(self, moras=(0, 0, 0, 0), error=None):
        self.moras, self.error, self.llamadas = moras, error, []

    def facturas_por_cliente(self, codigos):
        self.llamadas.append(list(codigos))
        if self.error:
            raise self.error
        return {c: _serie(self.moras) for c in codigos}


def _correr(routes_root, sap_source, history, catalog, calendar, **kw):
    return process_due_routes(
        schedule_source=FakeScheduleSource([make_entry()]), catalog=catalog, calendar=calendar, lead_business_days=3,
        routes_root=routes_root, sap_data_source=sap_source, sap_source_label="fake", history=history, today=date(2026, 10, 9), **kw)


@pytest.fixture
def con_plazo(monkeypatch):
    """CORTES con plazo de credito definido (por defecto solo Las Vegas y Yoro lo traen)."""
    real = runner.load_route_config
    monkeypatch.setattr(runner, "load_route_config", lambda rid, *a, **k: dataclasses.replace(real(rid, *a, **k), credit_days=60))


def test_sin_evidencia_el_resultado_no_cambia(routes_root, sap_source, history, catalog, calendar):
    res = _correr(routes_root, sap_source, history, catalog, calendar)
    r = res.routes[0]
    assert r.status == "success" and r.semaphore is None and r.semaphore_error is None
    assert "semaphore" not in r.to_dict() and "semaphore_error" not in r.to_dict()


def test_con_evidencia_y_plazo_agrega_el_semaforo_de_cada_cliente_de_la_ruta(routes_root, sap_source, history, catalog, calendar, con_plazo):
    ev = Evidencia()
    r = _correr(routes_root, sap_source, history, catalog, calendar, payment_evidence=ev).routes[0]
    assert r.status == "success" and r.semaphore_error is None
    s = r.semaphore
    assert s["fecha_corte"] == "2026-10-08" and s["plazo_dias"] == 60 and s["fuente"] == "evidencia de prueba"
    assert len(s["clientes"]) == r.client_count and [c["posicion"] for c in s["clientes"]] == list(range(1, r.client_count + 1))
    assert s["clasificacion"] == {"A": r.client_count}
    assert ev.llamadas and len(ev.llamadas[0]) == r.client_count
    assert r.to_dict()["semaphore"] == s


def test_las_decisiones_de_gerencia_se_aplican_sin_borrar_el_calculo(routes_root, sap_source, history, catalog, calendar, con_plazo):
    from cxc_bot import excel_service
    from cxc_bot.config import load_route_config
    base = next(routes_root.rglob("CORTES septiembre.xlsx"))
    cod = excel_service.peek_roster(base, load_route_config("CORTES"))[0].codigo
    dec = {cod: ps.DecisionGerencia(ps.SOLO_CONTADO, "aprobado", "Gerencia", "2026-10-10")}
    r = _correr(routes_root, sap_source, history, catalog, calendar, payment_evidence=Evidencia((90,) * 5), payment_decisions=dec).routes[0]
    por_cod = {c["codigo"]: c for c in r.semaphore["clientes"]}
    assert por_cod[cod]["calculado"] == ps.SOLO_CONTADO and por_cod[cod]["final"] == ps.SOLO_CONTADO
    assert por_cod[cod]["color_final"] == "ROJO" and por_cod[cod]["decision_gerencia"]
    otros = [c for k, c in por_cod.items() if k != cod]
    assert otros and all(c["final"] == ps.SOLO_CONTADO and not c["decision_gerencia"] for c in otros)       # contado = rojo automatico, sin decision


def test_ruta_sin_plazo_de_credito_no_calcula_semaforo_ni_adivina(routes_root, sap_source, history, catalog, calendar):
    assert load_all_routes()["CORTES"].credit_days is None
    ev = Evidencia()
    r = _correr(routes_root, sap_source, history, catalog, calendar, payment_evidence=ev).routes[0]
    assert r.status == "success" and r.semaphore is None and r.semaphore_error is None and ev.llamadas == []


def test_un_fallo_de_la_fuente_de_evidencia_no_tumba_la_generacion(routes_root, sap_source, history, catalog, calendar, con_plazo):
    r = _correr(routes_root, sap_source, history, catalog, calendar, payment_evidence=Evidencia(error=RuntimeError("fuente caida"))).routes[0]
    assert r.status == "success" and r.output_file and r.semaphore is None and "fuente caida" in r.semaphore_error


def test_las_vegas_y_yoro_tienen_plazo_foraneo_de_60_dias_y_las_demas_no():
    rutas = load_all_routes()
    assert rutas["LAS VEGAS"].credit_days == 60 and rutas["YORO"].credit_days == 60
    assert all(c.credit_days is None for k, c in rutas.items() if k not in ("LAS VEGAS", "YORO", "YORO FOLDERS"))
