"""Endpoints de la fuente financiera (copia PG de pruebas): detalle, recarga, conciliacion. Conexion falsa; la persistencia operativa sigue siendo SQLite."""
import os
from datetime import date

import pytest

from cxc_portal import fuente_pg_pruebas as fp
from tests.portal_helpers import CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, Servidor, cliente_con_login, crear_entorno, ruta_reporting_core
from tests.test_fuente_pg_pruebas import ENV_OK, ConexionFalsa


@pytest.fixture(autouse=True)
def _core():
    from cxc_portal import gelsyn_config as gc
    if not (ruta_reporting_core() / "cxc_reporting").is_dir():
        pytest.skip("cxc-reporting-core no disponible")
    os.environ.setdefault("CXC_REPORTING_SRC", str(ruta_reporting_core()))
    gc._importar_core()


@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    conn = ConexionFalsa()
    estado_pg = {"conn": conn, "falla": False}

    def conectar(url):
        if estado_pg["falla"]:
            raise RuntimeError("fallo con " + url)
        estado_pg["conn"] = ConexionFalsa()
        return estado_pg["conn"]
    ctx.servicio.fuente = fp.crear_fuente_pg_pruebas(ENV_OK, conectar=conectar, hoy=lambda: date(2026, 11, 5))
    s = Servidor(ctx)
    yield s, ctx, estado_pg
    s.cerrar()


def test_sin_sesion_y_vendedor_no_acceden(env):
    s, _, _ = env
    from tests.portal_helpers import Cliente
    anon = Cliente(s.puerto)
    assert anon.get("/api/fuente/conciliacion")[0] == 401 and anon.post("/api/fuente/recargar")[0] in (401, 403)
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    assert ven.get("/api/fuente/conciliacion")[0] == 403 and ven.post("/api/fuente/recargar")[0] == 403
    assert "detalle" not in ven.get("/api/fuente")[2]


def test_fuente_expone_detalle_a_gerencia_sin_permitir_publicar(env):
    s, _, _ = env
    d = cliente_con_login(s, "ger1", CLAVE_GER).get("/api/fuente")[2]
    det = d["detalle"]
    assert d["fuente"]["tipo"] == "EMPRESARIAL_PRUEBAS" and d["fuente"]["en_vivo"] is False
    assert det["apta_para_publicar"] is False and "conciliaciones" in det["motivo_no_apta"] and det["calidad"] == "OK"
    assert det["cortes_por_entidad"]["facturas"] == "2026-11-04" and det["lecturas_pg"] == 1


def test_recarga_relee_en_solo_lectura_invalida_cache_y_se_audita(env):
    s, ctx, pg = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    v0 = ctx.servicio.fuente.version_datos
    st, _, d = ger.post("/api/fuente/recargar")
    assert st == 200 and d["estado"] in ("OK", "CON_ADVERTENCIAS") and d["datos_reemplazados"] is True and d["detalle"]["lecturas_pg"] == 2
    assert ctx.servicio.fuente.version_datos == v0 + 1
    assert all(q.lstrip().upper().startswith("SELECT") for q in pg["conn"].ejecutadas) and pg["conn"].cerrada
    assert any(e["accion"] == "fuente.recargar" for e in ctx.estado.listar_auditoria())


def test_recarga_fallida_conserva_la_ultima_carga_buena_y_no_filtra_la_url(env):
    s, ctx, pg = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    pg["falla"] = True
    st, _, d = ger.post("/api/fuente/recargar")
    assert st == 200 and d["estado"] == "DEGRADADA" and d["datos_reemplazados"] is False
    assert "SECRETO-NO-REAL" not in str(d) and "postgresql://" not in str(d)
    assert ctx.servicio.fuente.facturas_por_cliente(["C1"])["C1"]                  # sigue sirviendo la carga anterior


def test_conciliacion_muestra_el_desfase_de_cortes_sin_validar_contablemente(env):
    s, ctx, _ = env
    st, _, d = cliente_con_login(s, "admin1", CLAVE_ADMIN).get("/api/fuente/conciliacion")
    assert st == 200 and d["sin_corte"]["difieren"] == 1
    assert d["corte_por_transid"]["minimas_diferencias"] == 0 and d["corte_por_transid"]["transid_limite_desde"] == 2
    assert d["diferencias_sin_corte"] == [{"codigo": "C1", "control": "150.00", "libro": "50.00", "diferencia": "-100.00"}] and d["diferencias_con_corte_transid"] == []
    assert d["validado_contablemente"] is False and "SAP B1" in d["nota"]
    assert any(e["accion"] == "fuente.conciliacion" for e in ctx.estado.listar_auditoria())


def test_fuente_sin_recarga_ni_conciliacion_responde_409(tmp_path):
    ctx = crear_entorno(tmp_path)
    s = Servidor(ctx)
    try:
        ger = cliente_con_login(s, "ger1", CLAVE_GER)
        assert ger.post("/api/fuente/recargar")[0] == 409 and ger.get("/api/fuente/conciliacion")[0] == 409
        assert ger.get("/api/fuente")[2]["detalle"] is None
    finally:
        s.cerrar()


def test_vendedor_y_zona_financieros_llegan_a_la_fuente(env):
    _, ctx, _ = env
    cli = {r[0]: r for r in ctx.servicio.fuente._datos()["clientes"].items()}
    assert cli["C1"][1]["vendedor_codigo"] == "7" and cli["C1"][1]["zona"] == "04" and cli["C2"][1]["zona"] is None
