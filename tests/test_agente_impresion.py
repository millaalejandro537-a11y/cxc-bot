"""Preparacion de impresion por salida y adaptador del futuro agente (impresora SIMULADA). Nada se imprime."""

import hashlib
import shutil
from datetime import date
from pathlib import Path

import pytest

from cxc_portal import security as sec
from cxc_portal.agente_impresion import AgenteImpresion, ImpresoraSimulada, TransporteHttp
from cxc_portal.flujo import FlujoOperativo
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, Servidor, cliente_con_login, crear_entorno

CONTENIDO = b"PK-hoja-de-visita-sintetica-" + bytes(range(200))
FIXTURE = Path("tests/fixtures/rutas_reales/LAS VEGAS MAYO.xlsx")


@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: date(2026, 10, 12))
    ctx.estado.registrar_documento(ACTOR, "DEMO", "HOJA_VISITA", "demo.xlsx", CONTENIDO, {"hojas": [], "total_clientes": 0}, tmp_path / "estado" / "documentos")
    sid = ctx.estado.crear_salida(ACTOR, "DEMO", "2026-10-19")["id"]
    s = Servidor(ctx)
    yield s, ctx, sid, tmp_path
    s.cerrar()


def _agente_token(s):
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    return adm, adm.post("/api/admin/agentes-impresion", {"nombre": "Agente prueba"})[2]["token"]


def test_preparar_impresion_es_idempotente_y_solo_registra(env):
    _, ctx, sid, _ = env
    r1 = ctx.flujo.preparar_impresion(ACTOR, sid)
    assert len(r1["creadas"]) == 1 and r1["omitidas"][0]["tipo"] == "EXCEL_RUTA"           # sin Excel publicado: se explica por que no
    r2 = ctx.flujo.preparar_impresion(ACTOR, sid)
    assert r2["creadas"] == [] and r2["existentes"] == r1["creadas"] and len(ctx.estado.listar_impresiones()) == 1
    assert ctx.estado.listar_impresiones()[0]["estado"] == "pendiente"                     # nada se imprime


def test_el_agente_descarga_verifica_y_reporta_con_impresora_simulada(env):
    s, ctx, sid, _ = env
    adm, token = _agente_token(s)
    assert adm.post(f"/api/salidas/{sid}/impresion", {})[0] == 200
    imp = ImpresoraSimulada()
    ag = AgenteImpresion(TransporteHttp("127.0.0.1", s.puerto, token), imp)
    res = ag.vaciar_cola()
    assert len(res) == 1 and res[0]["estado"] == "impreso" and "no se imprimió nada" in res[0]["detalle"]
    assert imp.trabajos[0]["sha256"] == hashlib.sha256(CONTENIDO).hexdigest() and imp.trabajos[0]["copias"] == 1
    assert ag.procesar_uno() is None                                                       # cola vacia


def test_fallo_de_impresora_se_reporta_como_error_y_el_agente_sigue(env):
    s, ctx, sid, _ = env
    adm, token = _agente_token(s)
    adm.post(f"/api/salidas/{sid}/impresion", {})
    r = AgenteImpresion(TransporteHttp("127.0.0.1", s.puerto, token), ImpresoraSimulada(fallar=True)).procesar_uno()
    assert r["estado"] == "error" and "atasco" in r["detalle"]


def test_token_ajeno_no_descarga_ni_toma_trabajos_y_el_archivo_exige_asignacion(env):
    s, ctx, sid, _ = env
    adm, token = _agente_token(s)
    adm.post(f"/api/salidas/{sid}/impresion", {})
    mal = TransporteHttp("127.0.0.1", s.puerto, "token-falso-" + "x" * 30)
    with pytest.raises(RuntimeError):
        mal.siguiente()
    bueno = TransporteHttp("127.0.0.1", s.puerto, token)
    iid = ctx.estado.listar_impresiones()[0]["id"]
    with pytest.raises(RuntimeError):
        bueno.archivo(iid)                                                                 # aun no la tomo: no hay archivo para el
    assert bueno.siguiente()["id"] == iid
    assert bueno.archivo(iid)[1] == CONTENIDO


def test_el_archivo_alterado_en_disco_no_se_entrega(env):
    s, ctx, sid, tmp = env
    adm, token = _agente_token(s)
    adm.post(f"/api/salidas/{sid}/impresion", {})
    d = ctx.estado.documento_vigente("DEMO")
    (tmp / "estado" / "documentos" / d["almacenado"]).write_bytes(b"alterado")
    r = AgenteImpresion(TransporteHttp("127.0.0.1", s.puerto, token), ImpresoraSimulada()).procesar_uno()
    assert r["estado"] == "error"


def test_el_agente_rechaza_impresoras_reales_hasta_autorizarlas():
    class Real:
        simulada = False

        def imprimir(self, *a):
            raise AssertionError("no debe llamarse")
    with pytest.raises(RuntimeError, match="SIMULADAS"):
        AgenteImpresion(object(), Real())
    AgenteImpresion(object(), Real(), permitir_reales=True)                                 # decision futura y explicita


def test_el_excel_borrador_no_se_prepara_para_impresion(tmp_path):
    ctx = crear_entorno(tmp_path)
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: date(2026, 10, 12))
    ctx.estado.guardar_ruta(ACTOR, "LAS_VEGAS", "Las Vegas", "Vendedor Demo", "FORANEA", None, zonas={"LV": "Las Vegas"},
                            clientes=[{"codigo": "C90001", "folder": "1 LV", "nombre": "SALON DEMO EXCELENTE (A)"}])
    (tmp_path / "datos" / "01_Rutas").mkdir(parents=True)
    shutil.copy2(FIXTURE, tmp_path / "datos" / "01_Rutas" / "base.xlsx")
    sid = ctx.estado.crear_salida(ACTOR, "LAS_VEGAS", "2026-10-20")["id"]
    ctx.flujo.preparar(ACTOR, sid)
    ctx.estado.revisar_salida(ACTOR, sid, "Revisada para la prueba.")
    e = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert e["borrador"] == 1
    r = ctx.flujo.preparar_impresion(ACTOR, sid)
    assert r["creadas"] == [] and "BORRADOR" in r["omitidas"][-1]["motivo"]
