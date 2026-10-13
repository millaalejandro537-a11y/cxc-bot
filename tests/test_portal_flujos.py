"""Flujos operativos del portal: decisiones, visitas, administracion de rutas/clientes, importacion, persistencia y reportes del core."""

import json
from pathlib import Path

import openpyxl
import pytest

from cxc_portal.estado import Estado
from tests.portal_helpers import (ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, Servidor, cliente_con_login, crear_entorno)

CORE_SRC = Path(r"C:\Users\LTP\Documents\Trabajo-Sync\Cosprobell\cxc-reporting-core\src")


@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    s = Servidor(ctx)
    yield s, ctx, tmp_path
    s.cerrar()


def _clientes(c, rid="DEMO"):
    return {x["codigo"]: x for x in c.get(f"/api/rutas/{rid}/clientes")[2]["clientes"]}


# ----------------------------------------------------------------------------- decisiones
def test_decision_de_gerencia_conserva_calculo_y_se_audita(env):
    s, ctx, _ = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    antes = _clientes(ger)["C90008"]["semaforo"]
    assert antes["final"] == "REVISION_HUMANA" and antes["decision_gerencia"] is None and antes["decision_aplicada"] is False       # sin evidencia: revision humana
    st, _, d = ger.post("/api/decisiones", {"codigo": "C90008", "clasificacion_final": "C", "motivo": "Gerencia resuelve la falta de evidencia (prueba)."})
    assert st == 201 and d["calculado_al_decidir"] == "REVISION_HUMANA" and d["actor"] == "Gerente Uno" and d["estado"] == "vigente"
    des = _clientes(ger)["C90008"]["semaforo"]
    assert des["calculado"] == "REVISION_HUMANA" and des["final"] == "C" and des["color_final"] == "AMARILLO" and des["decision_aplicada"] is True
    assert des["promedio_mora"] == antes["promedio_mora"]  # el calculo automatico no cambia
    assert ger.get("/api/clientes/C90008")[2]["historial_decisiones"][0]["motivo"].startswith("Gerencia resuelve")
    assert any(e["accion"] == "decision.registrar" and e["referencia"] == "C90008" for e in ctx.estado.listar_auditoria())


def test_no_se_piden_ni_aceptan_decisiones_sobre_categorias_que_el_sistema_determina(env):
    s, ctx, _ = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    st, _, e = ger.post("/api/decisiones", {"codigo": "C90004", "clasificacion_final": "C", "motivo": "Intento de sustituir el contado automático."})
    assert st == 409 and "sistema ya determina" in e["error"] and "prevalece" in e["error"]
    sem = _clientes(ger)["C90004"]["semaforo"]
    assert sem["final"] == "SOLO_CONTADO" and sem["color_final"] == "ROJO" and sem["decision_gerencia"] is None
    assert ctx.estado.decisiones_vigentes() == {}                                                                       # nada se escribio
    # una decision ANTERIOR (importada) sobre un cliente que el sistema determina se conserva, pero no cambia la clasificacion
    ctx.estado.registrar_decision(ACTOR, "C90004", "C", "Decisión comercial anterior (importada).", route_id="DEMO", origen="archivo", fecha="2026-10-09")
    ctx.servicio.invalidar()
    ant = _clientes(ger)["C90004"]["semaforo"]
    assert ant["final"] == "SOLO_CONTADO" and ant["decision_aplicada"] is False and ant["decision_gerencia"]["fecha"] == "2026-10-09"
    assert any("solo para trazabilidad" in x for x in ant["explicacion"])


def test_nueva_decision_sustituye_y_anular_vuelve_al_automatico(env):
    s, _, _ = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    ger.post("/api/decisiones", {"codigo": "C90008", "clasificacion_final": "C", "motivo": "Primera decisión de prueba."})
    ger.post("/api/decisiones", {"codigo": "C90008", "clasificacion_final": "B", "motivo": "Segunda decisión de prueba."})
    hist = ger.get("/api/decisiones?codigo=C90008")[2]["decisiones"]
    assert [h["estado"] for h in hist] == ["vigente", "sustituida"]  # nunca se borra el historial
    assert ger.post("/api/decisiones/anular", {"codigo": "C90008", "motivo": "corta"})[0] == 400
    assert ger.post("/api/decisiones/anular", {"codigo": "C90008", "motivo": "Se anula por error de captura."})[0] == 200
    assert _clientes(ger)["C90008"]["semaforo"]["final"] == "REVISION_HUMANA"  # vuelve al calculo automatico (revision humana pendiente)
    assert [h["estado"] for h in ger.get("/api/decisiones?codigo=C90008")[2]["decisiones"]] == ["anulada", "sustituida"]


def test_validaciones_de_decision(env):
    s, _, _ = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    assert ger.post("/api/decisiones", {"codigo": "C90008", "clasificacion_final": "ROJO", "motivo": "Motivo suficiente aquí."})[0] == 400
    assert ger.post("/api/decisiones", {"codigo": "C90008", "clasificacion_final": "C", "motivo": "corto"})[0] == 400
    assert ger.post("/api/decisiones", {"codigo": "C00000", "clasificacion_final": "C", "motivo": "Motivo suficiente aquí."})[0] == 404


def test_las_decisiones_se_exportan_para_el_generador_de_rutas(env):
    s, _, tmp = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    ger.post("/api/decisiones", {"codigo": "C90008", "clasificacion_final": "SOLO_CONTADO", "motivo": "Aprobado por gerencia (prueba)."})
    data = json.loads((tmp / "datos" / "decisiones_gerencia.json").read_text(encoding="utf-8"))
    assert data["C90008"]["clasificacion_final"] == "SOLO_CONTADO" and data["C90008"]["actor"] == "Gerente Uno"
    from cxc_bot.sap_exports import cargar_decisiones
    assert cargar_decisiones(tmp / "datos")["C90008"].clasificacion_final == "SOLO_CONTADO"  # lo que lee CXC BOT


def test_persistencia_tras_reiniciar_decisiones_visitas_usuarios_y_rutas(tmp_path):
    ctx = crear_entorno(tmp_path)
    s = Servidor(ctx)
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    ger.post("/api/decisiones", {"codigo": "C90008", "clasificacion_final": "SOLO_CONTADO", "motivo": "Aprobado por gerencia (prueba)."})
    ven.post("/api/visitas", {"ruta": "DEMO", "fecha_salida": "2026-10-13", "codigo": "C90001", "estado": "visitada_firmada"})
    s.cerrar()
    est = Estado(tmp_path / "estado" / "portal.sqlite3")  # "reinicio": todo se reconstruye desde el archivo de la BD propia
    assert est.decisiones_vigentes()["C90008"]["clasificacion_final"] == "SOLO_CONTADO"
    assert est.listar_visitas(route_id="DEMO")[0]["estado"] == "visitada_firmada"
    assert {u["usuario"] for u in est.listar_usuarios()} >= {"admin1", "ger1", "ven1", "ven2"}
    assert "DEMO" in {r["route_id"] for r in est.listar_rutas()}
    assert est.autenticar("ger1", CLAVE_GER) is not None


# ----------------------------------------------------------------------------- visitas
def test_visita_con_trazabilidad_y_reglas(env):
    s, _, _ = env
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    base = {"ruta": "DEMO", "fecha_salida": "2026-10-13", "codigo": "C90001"}
    assert ven.post("/api/visitas", {**base, "estado": "no_visitada"})[0] == 400  # exige motivo
    assert ven.post("/api/visitas", {**base, "estado": "prevista"})[0] == 201
    st, _, v = ven.post("/api/visitas", {**base, "estado": "visitada_sin_firma", "motivo": "El cliente no estaba para firmar"})
    assert st == 201
    ev = ven.get(f"/api/visitas/{v['id']}/eventos")[2]["eventos"]
    assert [(e["estado_anterior"], e["estado_nuevo"]) for e in ev] == [(None, "prevista"), ("prevista", "visitada_sin_firma")]
    assert ven.post("/api/visitas", {**base, "estado": "inventado"})[0] == 400
    assert ven.post("/api/visitas", {**base, "fecha_salida": "13/10/2026", "estado": "prevista"})[0] == 400
    assert ven.post("/api/visitas", {**base, "codigo": "C90101", "estado": "prevista"})[0] == 404  # cliente de otra ruta
    assert ven.post("/api/visitas", {"ruta": "OTRA", "fecha_salida": "2026-10-13", "codigo": "C90101", "estado": "prevista"})[0] == 404  # ruta ajena
    cob = ven.get("/api/visitas/cobertura?ruta=DEMO&fecha=2026-10-13")[2]
    assert cob["visitada_sin_firma"] == 1 and cob["no_visitada"] == 0 and cob["sin_registro"] == cob["clientes_en_ruta"] - 1


def test_falta_de_firma_no_es_no_visita_y_tres_salidas_consecutivas(env):
    s, ctx, _ = env
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    for f in ("2026-10-13", "2026-10-20", "2026-10-27"):
        ven.post("/api/visitas", {"ruta": "DEMO", "fecha_salida": f, "codigo": "C90002", "estado": "visitada_sin_firma", "motivo": "sin firma"})
        ven.post("/api/visitas", {"ruta": "DEMO", "fecha_salida": f, "codigo": "C90003", "estado": "no_visitada", "motivo": "cliente cerrado"})
    assert ctx.estado.salidas_consecutivas_sin_visita("DEMO", "C90003") is True  # evidencia concluyente
    assert ctx.estado.salidas_consecutivas_sin_visita("DEMO", "C90002") is False  # la falta de firma NO cuenta como no visita


# ----------------------------------------------------------------------------- administracion de rutas / clientes
def test_alta_de_ruta_nueva_sin_tocar_codigo_y_orden_de_folders(env):
    s, ctx, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    st, _, r = adm.post("/api/admin/rutas", {
        "route_id": "occidente", "nombre": "Occidente", "vendedor": "Jose Carpio", "tipo_ruta": "FORANEA", "zonas": {"SR": "Santa Rosa", "CP": "Copán"},
        "clientes": [{"codigo": "C90201", "folder": "1 SR", "nombre": "UNO"}, {"codigo": "C90202", "folder": "2 SR", "nombre": "DOS"},
                     {"codigo": "C90203", "folder": "1 CP", "nombre": "TRES"}]})
    assert st == 201 and r["route_id"] == "OCCIDENTE" and r["plazo_dias"] == 60 and [c["folder"] for c in r["clientes"]] == ["1 SR", "2 SR", "1 CP"]
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    assert "OCCIDENTE" in [x["route_id"] for x in ger.get("/api/rutas")[2]["rutas"]]
    assert [c["folder"] for c in ger.get("/api/rutas/OCCIDENTE/clientes")[2]["clientes"]] == ["1 SR", "2 SR", "1 CP"]
    # alta nueva toma el siguiente numero de SU zona y nunca renumera
    assert adm.post("/api/admin/rutas/OCCIDENTE/clientes", {"codigo": "C90204", "nombre": "CUATRO", "zona": "SR"})[2]["folder"] == "3 SR"
    # retirar un cliente NO renumera ni reutiliza su folder
    assert adm.pedir("DELETE", "/api/admin/rutas/OCCIDENTE/clientes/C90202")[0] == 200
    assert adm.post("/api/admin/rutas/OCCIDENTE/clientes", {"codigo": "C90205", "nombre": "CINCO", "zona": "SR"})[2]["folder"] == "4 SR"
    assert [c["folder"] for c in ger.get("/api/rutas/OCCIDENTE/clientes")[2]["clientes"]] == ["1 SR", "1 CP", "3 SR", "4 SR"]
    # un usuario vendedor vinculado al nuevo vendedor ve solo su ruta
    assert adm.post("/api/admin/usuarios", {"usuario": "jose", "nombre": "Jose", "rol": "vendedor", "vendedor": "Jose Carpio", "password": "Clave-Inicial#2026xx"})[0] == 201
    uid = next(u["id"] for u in ctx.estado.listar_usuarios() if u["usuario"] == "jose")
    ctx.estado.cambiar_password(uid, "Clave-Inicial#2026xx", "Clave-Nueva#2026xxy", ACTOR)
    jose = cliente_con_login(s, "jose", "Clave-Nueva#2026xxy")
    assert [x["route_id"] for x in jose.get("/api/rutas")[2]["rutas"]] == ["OCCIDENTE"]


def test_validaciones_de_clientes_de_ruta(env):
    s, _, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    base = {"nombre": "R", "vendedor": "V", "tipo_ruta": "LOCAL"}
    st, _, js = adm.post("/api/admin/rutas", {**base, "route_id": "R1", "clientes": [{"codigo": "XX1", "folder": "1 A"}]})
    assert st == 400 and "Código de cliente inválido" in js["error"]
    st, _, js = adm.post("/api/admin/rutas", {**base, "route_id": "R1", "clientes": [{"codigo": "C90301", "folder": ""}]})
    assert st == 400 and "Falta el folder" in js["error"]
    dup = adm.post("/api/admin/rutas", {**base, "route_id": "R2", "clientes": [{"codigo": "C90301", "folder": "1 A"}, {"codigo": "C90301", "folder": "2 A"}]})
    assert dup[0] == 400 and "duplicado" in dup[2]["error"]
    dupf = adm.post("/api/admin/rutas", {**base, "route_id": "R3", "clientes": [{"codigo": "C90301", "folder": "1 A"}, {"codigo": "C90302", "folder": "1 A"}]})
    assert dupf[0] == 400 and "Folder duplicado" in dupf[2]["error"]
    assert adm.post("/api/admin/rutas", {**base, "route_id": "R4", "tipo_ruta": "XX"})[0] == 400


def test_importar_ruta_desde_excel_solo_dentro_de_01_rutas_y_sin_modificarlo(env):
    s, _, tmp = env
    rutas = tmp / "datos" / "01_Rutas"
    rutas.mkdir(parents=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["DISTRIBUCIONES COSPROBELL"])
    ws.append(["Folder", "Codigo", "NOMBRE DEL CLIENTE", "No. Factura", "Saldo"])
    ws.append(["1 SR", "C90401", "SALON UNO", None, 10])
    ws.append(["2 SR", "C90402", "SALON DOS", None, 20])
    ws.append([None, None, None, "Total Saldo Ruta", 30])
    wb.save(rutas / "lista.xlsx")
    antes = (rutas / "lista.xlsx").read_bytes()
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    cuerpo = {"archivo": "lista.xlsx", "route_id": "NUEVA", "nombre": "Nueva", "vendedor": "Alguien", "tipo_ruta": "LOCAL"}
    st, _, r = adm.post("/api/admin/rutas/importar", cuerpo)
    assert st == 201 and [c["codigo"] for c in r["clientes"]] == ["C90401", "C90402"] and r["plazo_dias"] == 30 and r["clientes"][0]["folder"] == "1 SR"
    assert (rutas / "lista.xlsx").read_bytes() == antes
    for malo in ("../fuera.xlsx", "..\\..\\x.xlsx", "/etc/passwd", "lista.txt", "no_existe.xlsx"):
        assert adm.post("/api/admin/rutas/importar", {**cuerpo, "archivo": malo})[0] == 400, malo


def test_usuarios_vendedores_y_reglas_de_cuentas(env):
    s, ctx, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    assert adm.post("/api/admin/usuarios", {"usuario": "x", "rol": "vendedor", "password": "Clave-Segura#2026x"})[0] == 400
    assert adm.post("/api/admin/usuarios", {"usuario": "nuevo", "rol": "vendedor", "password": "Clave-Segura#2026x"})[0] == 400  # vendedor sin vinculo
    assert adm.post("/api/admin/usuarios", {"usuario": "nuevo", "rol": "gerencia", "password": "debil"})[0] == 400
    assert adm.post("/api/admin/usuarios", {"usuario": "ger1", "rol": "gerencia", "password": "Clave-Segura#2026x"})[0] == 409
    uid = next(u["id"] for u in ctx.estado.listar_usuarios() if u["usuario"] == "admin1")
    assert adm.pedir("PATCH", f"/api/admin/usuarios/{uid}", {"activo": False})[0] == 400  # no puede desactivarse a si mismo
    assert adm.pedir("PATCH", f"/api/admin/usuarios/{uid}", {"rol": "gerencia"})[0] == 400


# ----------------------------------------------------------------------------- reportes gerenciales (cxc-reporting-core)
@pytest.mark.skipif(not CORE_SRC.is_dir(), reason="cxc-reporting-core no esta en esta maquina")
def test_reportes_gerenciales_con_cxc_reporting_core_sobre_la_fuente(tmp_path, monkeypatch):
    monkeypatch.setenv("CXC_REPORTING_SRC", str(CORE_SRC))
    from cxc_portal.demo import FuenteSintetica
    from cxc_portal.reporting_bridge import ReportingService
    ctx = crear_entorno(tmp_path)
    ctx.servicio.reporting = ReportingService(ctx.servicio.fuente, tmp_path / "estado")
    s = Servidor(ctx)
    try:
        ger = cliente_con_login(s, "ger1", CLAVE_GER)
        assert ger.get("/api/reportes/gerencial")[0] == 409  # aun no hay corte
        st, _, res = ger.post("/api/reportes/importar-corte")
        assert st == 200 and res["status"] == "COMPLETADA" and res["estado_validacion"] == "NO_VALIDADO" and res["publicable"] is False
        rep = ger.get("/api/reportes/gerencial")[2]
        assert "NO VALIDADO" in rep["aviso_portal"] and rep["corte"]["estado_validacion"] == "NO_VALIDADO"
        esperado = round(sum(f.total - f.aplicado_sap for fs in FuenteSintetica()._f.values() for f in fs if f.estado_sap == "O"), 2)
        assert float(rep["cartera"]["cartera_positiva"]) == esperado  # el core calcula la cartera sobre los mismos datos
        st, cab, body = ger.get("/api/reportes/gerencial.xlsx")
        assert st == 200 and body[:2] == b"PK" and "NO_VALIDADO" in cab["content-disposition"]
        assert ger.get("/api/reportes/cliente/C90007")[0] == 200
        ven = cliente_con_login(s, "ven1", CLAVE_VEN)
        assert ven.get("/api/reportes/gerencial")[0] == 403 and ven.get("/api/reportes/gerencial.xlsx")[0] == 403
    finally:
        s.cerrar()
