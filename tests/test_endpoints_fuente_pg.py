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
    assert ctx.servicio.fuente.facturas_por_cliente(["C10001"])["C10001"]                  # sigue sirviendo la carga anterior


def test_conciliacion_muestra_el_desfase_de_cortes_sin_validar_contablemente(env):
    s, ctx, _ = env
    st, _, d = cliente_con_login(s, "admin1", CLAVE_ADMIN).get("/api/fuente/conciliacion")
    assert st == 200 and d["sin_corte"]["difieren"] == 1
    assert d["corte_por_transid"]["minimas_diferencias"] == 0 and d["corte_por_transid"]["transid_limite_desde"] == 2
    assert d["diferencias_sin_corte"] == [{"codigo": "C10001", "control": "150.00", "libro": "50.00", "diferencia": "-100.00"}] and d["diferencias_con_corte_transid"] == []
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
    assert cli["C10001"][1]["vendedor_codigo"] == "7" and cli["C10001"][1]["zona"] == "04" and cli["C10002"][1]["zona"] is None


def _ruta_pg(ctx):
    from tests.portal_helpers import ACTOR
    ctx.estado.crear_vendedor(ACTOR, "Vendedor Siete")
    ctx.estado.guardar_ruta(ACTOR, "PGT", "Ruta PG", "Vendedor Siete", "FORANEA", 60, zonas={"PG": "Zona PG"}, requiere_hojas_visita=False,
                            clientes=[{"codigo": "C10001", "folder": "1 PG", "zona": "PG", "nombre": "NOMBRE DEL CATALOGO 1"}, {"codigo": "C10002", "folder": "2 PG", "zona": "PG", "nombre": "NOMBRE DEL CATALOGO 2"},
                                      {"codigo": "C10009", "folder": "3 PG", "zona": "PG", "nombre": "NO ESTA EN LA FUENTE"}])
    ctx.servicio.invalidar()


def test_discrepancias_informa_sin_modificar_rutas_y_solo_gerencia_admin(env):
    s, ctx, _ = env
    _ruta_pg(ctx)
    antes = [(r["route_id"], len(r["clientes"])) for r in ctx.estado.listar_rutas(solo_activas=False)]
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    st, _, d = ger.get("/api/fuente/discrepancias")
    r = next(x for x in d["rutas"] if x["route_id"] == "PGT")
    assert st == 200 and r["no_en_fuente"] == ["C10009"] and r["vendedor"]["coincide_con_vendedor_de_la_ruta"] is True
    assert r["zonas"][0]["zona_financiera_predominante"] == "04" and [x["codigo"] for x in r["zonas"][0]["discrepantes"]] == ["C10002"]
    assert [(x["route_id"], len(x["clientes"])) for x in ctx.estado.listar_rutas(solo_activas=False)] == antes             # rutas operativas intactas
    assert any(e["accion"] == "fuente.discrepancias" for e in ctx.estado.listar_auditoria())
    assert cliente_con_login(s, "ven1", CLAVE_VEN).get("/api/fuente/discrepancias")[0] == 403


def test_nombres_reales_llegan_por_los_permisos_de_la_ruta(env):
    s, ctx, _ = env
    _ruta_pg(ctx)
    st, _, d = cliente_con_login(s, "ger1", CLAVE_GER).get("/api/rutas/PGT/clientes")
    nombres = {c["codigo"]: c["nombre"] for c in d["clientes"]}
    assert st == 200 and nombres["C10001"] == "CLIENTE UNO SA" and nombres["C10009"] == "NO ESTA EN LA FUENTE"              # sin dato financiero: conserva el nombre del catálogo
    from tests.portal_helpers import CLAVE_VEN2
    assert cliente_con_login(s, "ven2", CLAVE_VEN2).get("/api/rutas/PGT/clientes")[0] == 404                       # un vendedor ajeno a la ruta no ve nombres
