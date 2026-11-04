"""Revision del PR #4. Riesgo 1: un nuevo corte/datos validos de la fuente empresarial NO puede dejar clasificaciones, saldos ni viñetas del corte anterior.
Riesgo 2: la fuente y el corte se revalidan inmediatamente antes de persistir un Excel; un cambio o degradacion durante la generacion impide publicarlo
como vigente. Datos 100 % sinteticos; `TransporteVivoDoble` es un doble de prueba (no una fuente real)."""

import copy
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from cxc_bot import route_markers as rm
from cxc_portal import fuente_empresarial as fe
from cxc_portal.demo import FuenteSintetica, _facturas
from cxc_portal.estado import ErrorNegocio
from cxc_portal.flujo import FlujoOperativo
from tests.portal_helpers import ACTOR, crear_entorno

CORTE_A = date(2026, 10, 8)
CORTE_B = date(2026, 10, 9)
RELOJ = {"d": CORTE_B}                       # ambos cortes son vigentes (<= 2 dias)
GER = {"id": None, "usuario": "ger1", "rol": "gerencia", "nombre": "Gerencia"}


class TransporteVivoDoble(fe.TransporteSimulado):
    simulado = False


def _fuente_b():
    """Mismo universo de clientes pero OTROS datos y OTRO corte: C90003 pasa de regular (C) a excelente (A) y cambia su saldo."""
    b = FuenteSintetica()
    b.fecha_corte = CORTE_B
    b._f["C90003"] = _facturas(3000, [0, 1, 0, 2, 1, 0, 1, 0], [], 0)
    b._f["C90001"] = _facturas(1000, [-3, 0, 2, 0, 1, 4, 0, 2, 3, 1, 0, 2], [9], 0)       # una factura abierta nueva: cambia el saldo
    return b


def _servicio(tmp_path):
    ctx = crear_entorno(tmp_path)
    RELOJ["d"] = CORTE_B
    a = FuenteSintetica()
    t = TransporteVivoDoble(a, hoy=lambda: RELOJ["d"])
    f = fe.FuenteEmpresarial(t, hoy=lambda: RELOJ["d"], max_edad_dias=2)
    f.recargar()
    ctx.servicio.fuente = f
    ctx.servicio.invalidar()
    return ctx, f, t


def _cliente(ctx, codigo):
    return next(c for c in ctx.servicio.ruta("DEMO").clientes if c.codigo == codigo)


# --------------------------------------------------------------------------------------------- riesgo 1
def test_un_nuevo_corte_valido_recalcula_clasificaciones_saldos_y_vinetas_sin_tocar_el_catalogo(tmp_path):
    ctx, f, t = _servicio(tmp_path)
    antes3, antes1 = _cliente(ctx, "C90003"), _cliente(ctx, "C90001")
    assert antes3.resultado.final == "C" and ctx.servicio.ruta("DEMO").fuente["fecha_corte"] == CORTE_A.isoformat()
    t.src = _fuente_b()                                                   # el origen entrega otros datos y otro corte
    assert f.recargar()["estado"] in ("OK", "CON_ADVERTENCIAS") and f.fecha_corte == CORTE_B
    despues3, despues1 = _cliente(ctx, "C90003"), _cliente(ctx, "C90001")   # SIN invalidar(), SIN cambiar el catalogo SQLite
    assert despues3.resultado.final == "A", "la clasificacion sigue siendo la del corte anterior"
    assert despues1.saldo != antes1.saldo, "el saldo sigue siendo el del corte anterior"
    ruta = ctx.servicio.ruta("DEMO")
    assert ruta.fuente["fecha_corte"] == CORTE_B.isoformat()
    assert ruta.cliente_dict(despues3)["semaforo"]["vineta"]["clase_final"] == "A"


def test_el_excel_usa_los_datos_del_nuevo_corte_y_no_los_del_anterior(tmp_path):
    ctx, f, t = _servicio(tmp_path)
    plan_a = rm.plan_vinetas([c.resultado for c in ctx.servicio.ruta("DEMO").clientes], True)
    assert plan_a["C90003"].color == "AMARILLO"
    t.src = _fuente_b()
    f.recargar()
    plan_b = rm.plan_vinetas([c.resultado for c in ctx.servicio.ruta("DEMO").clientes], True)
    assert plan_b["C90003"].color == "VERDE"                              # la viñeta definitiva sale de B, no de A


def test_una_recarga_con_los_mismos_datos_no_cambia_nada(tmp_path):
    ctx, f, t = _servicio(tmp_path)
    r1 = ctx.servicio.ruta("DEMO")
    f.recargar()
    assert {c.codigo: (c.resultado.final, c.saldo) for c in ctx.servicio.ruta("DEMO").clientes} == {c.codigo: (c.resultado.final, c.saldo) for c in r1.clientes}


# --------------------------------------------------------------------------------------------- riesgo 2
def _entorno_excel(tmp_path):
    ctx, f, t = _servicio(tmp_path)
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: date(2026, 11, 6))
    ctx.estado.guardar_ruta(ACTOR, "LAS_VEGAS", "Las Vegas", "Vendedor Demo", "FORANEA", 60, zonas={"LV": "Las Vegas"},
                            clientes=[{"codigo": "C90001", "folder": "1 LV", "nombre": "SALON DEMO EXCELENTE (A)"},
                                      {"codigo": "C90002", "folder": "2 LV", "nombre": "SALON DEMO BUENO (B)"},
                                      {"codigo": "C90003", "folder": "3 LV", "nombre": "SALON DEMO REGULAR (C)"}])
    (tmp_path / "datos" / "01_Rutas").mkdir(parents=True, exist_ok=True)
    shutil.copy2("tests/fixtures/rutas_reales/LAS VEGAS MAYO.xlsx", tmp_path / "datos" / "01_Rutas" / "base.xlsx")
    ctx.servicio.invalidar()
    sid = ctx.estado.crear_salida(ACTOR, "LAS_VEGAS", "2026-11-10")["id"]
    ctx.flujo.preparar(GER, sid)
    return ctx, f, t, sid


def test_control_positivo_con_fuente_estable_se_publica_como_actual(tmp_path):
    ctx, f, t, sid = _entorno_excel(tmp_path)
    e = ctx.flujo.generar_excel_salida(GER, sid, "base.xlsx")
    assert e["publicable"] and e["estado"] == "ACTUAL" and ctx.estado.excel_actual("LAS_VEGAS")


def test_si_la_fuente_se_degrada_durante_la_generacion_el_excel_no_se_publica_como_vigente(tmp_path, monkeypatch):
    ctx, f, t, sid = _entorno_excel(tmp_path)
    real = ctx.servicio.generar_excel

    def generar_y_degradar(*a, **k):
        r = real(*a, **k)
        t.fallas["incompleto"] = True                                     # justo despues de generar, el origen entrega una carga incompleta
        assert f.recargar()["estado"] == "BLOQUEADA" and not f.apta_para_publicar
        return r
    monkeypatch.setattr(ctx.servicio, "generar_excel", generar_y_degradar)
    e = ctx.flujo.generar_excel_salida(GER, sid, "base.xlsx")
    assert not e["publicable"] and e["estado"] != "ACTUAL" and e["borrador"] == 1
    assert any("fuente" in m.lower() for m in e["motivos"])
    assert ctx.estado.excel_actual("LAS_VEGAS") is None                   # nada quedo como vigente


def test_si_la_fuente_envejece_entre_la_evaluacion_y_el_registro_queda_solo_borrador(tmp_path, monkeypatch):
    ctx, f, t, sid = _entorno_excel(tmp_path)
    real = ctx.servicio.generar_excel

    def generar_y_envejecer(*a, **k):
        r = real(*a, **k)
        RELOJ["d"] = CORTE_B + timedelta(days=10)                         # el reloj avanza: el corte ya no es vigente
        return r
    monkeypatch.setattr(ctx.servicio, "generar_excel", generar_y_envejecer)
    e = ctx.flujo.generar_excel_salida(GER, sid, "base.xlsx")
    assert not e["publicable"] and e["estado"] != "ACTUAL" and ctx.estado.excel_actual("LAS_VEGAS") is None


def test_si_cambian_los_datos_o_el_corte_durante_la_generacion_se_aborta_y_no_se_registra_nada(tmp_path):
    ctx, f, t, sid = _entorno_excel(tmp_path)
    real_registros = f.registros_sap

    def registros_y_cambiar():                                            # la fuente se recarga a mitad de la generacion
        r = real_registros()
        t.src = _fuente_b()
        f.recargar()
        return r
    f.registros_sap = registros_y_cambiar
    with pytest.raises(ErrorNegocio) as ex:
        ctx.flujo.generar_excel_salida(GER, sid, "base.xlsx")
    assert "cambió" in str(ex.value).lower() and ex.value.codigo == 409
    assert ctx.estado.listar_excel("LAS_VEGAS") == []


def test_la_preparacion_de_otro_corte_no_habilita_la_publicacion_normal(tmp_path):
    ctx, f, t, sid = _entorno_excel(tmp_path)                             # preparada con el corte A
    t.src = _fuente_b()
    f.recargar()                                                          # llega el corte B (valido y vigente)
    ev = ctx.flujo.evaluar_publicacion(ctx.estado.obtener_salida(sid))
    assert not ev["publicable"] and any("corte" in m.lower() for m in ev["motivos"])


def test_importacion_de_route_markers_no_esta_duplicada():
    fuente = Path("cxc_portal/store.py").read_text(encoding="utf-8")
    assert fuente.count("from cxc_bot import route_markers") == 1
