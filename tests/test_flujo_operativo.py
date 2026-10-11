"""Flujo operativo de una salida con datos SINTETICOS: calendario, preparacion automatica idempotente, alertas, notificaciones simuladas,
Excel versionado Actual/Historico, seguimiento de visitas/cobros/ventas, comparacion entre salidas y dashboard."""

import hashlib
import shutil
import stat
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from cxc_portal.estado import ErrorNegocio
from cxc_portal.flujo import FlujoOperativo, NotificadorSimulado, huella_excel
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, CLAVE_VEN2, FuenteApta, Servidor, cliente_con_login, crear_entorno

FIXTURE = Path("tests/fixtures/rutas_reales/LAS VEGAS MAYO.xlsx")
HOY = {"d": date(2026, 10, 12)}


def _hoy():
    return HOY["d"]


@pytest.fixture()
def env(tmp_path):
    HOY["d"] = date(2026, 10, 12)
    ctx = crear_entorno(tmp_path)
    ctx.estado.guardar_ruta(ACTOR, "LAS_VEGAS", "Las Vegas", "Vendedor Demo", "FORANEA", None, zonas={"LV": "Las Vegas", "PB": "Peña Blanca"},
                            clientes=[{"codigo": "C90001", "folder": "1 LV", "nombre": "SALON DEMO EXCELENTE (A)"},
                                      {"codigo": "C90002", "folder": "2 LV", "nombre": "SALON DEMO BUENO (B)"},
                                      {"codigo": "C90003", "folder": "1 PB", "nombre": "SALON DEMO REGULAR (C)"}], requiere_hojas_visita=True)
    rutas = tmp_path / "datos" / "01_Rutas"
    rutas.mkdir(parents=True)
    shutil.copy2(FIXTURE, rutas / "base.xlsx")
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=_hoy)
    s = Servidor(ctx)
    yield s, ctx, tmp_path
    s.cerrar()


# ------------------------------------------------------------------ calendario
def test_preparacion_tres_dias_habiles_antes_lunes_a_sabado_con_feriados(env):
    _, ctx, _ = env
    f = ctx.flujo
    assert f.fecha_preparacion("2026-10-19") == date(2026, 10, 15)         # lun 19 -> sab 17, vie 16, jue 15 (el sabado SI cuenta)
    assert f.fecha_preparacion("2026-10-17") == date(2026, 10, 14)         # sab 17 -> vie 16, jue 15, mie 14
    ctx.estado.agregar_feriado(ACTOR, "2026-10-16", "Feriado de prueba")
    assert f.fecha_preparacion("2026-10-19") == date(2026, 10, 14)         # con feriado el viernes: sab 17, jue 15, mie 14
    with pytest.raises(ErrorNegocio, match="domingo"):
        f.validar_fecha_salida("2026-10-18")
    with pytest.raises(ErrorNegocio, match="feriado"):
        f.validar_fecha_salida("2026-10-16")
    with pytest.raises(ErrorNegocio, match="ya está registrado"):
        ctx.estado.agregar_feriado(ACTOR, "2026-10-16", "Repetido")


def test_serie_de_salidas_es_idempotente_y_rechaza_dias_no_habiles(env):
    _, ctx, _ = env
    r = ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-19", "2026-10-18", "2026-10-26"])
    assert [c["fecha_salida"] for c in r["creadas"]] == ["2026-10-19", "2026-10-26"] and r["rechazadas"][0]["fecha"] == "2026-10-18"
    r2 = ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-19", "2026-10-26"])
    assert r2["creadas"] == [] and r2["existentes"] == ["2026-10-19", "2026-10-26"] and len(ctx.estado.listar_salidas("DEMO")) == 2


# ------------------------------------------------------------------ ciclo diario: automatico, idempotente, con alertas y notificaciones simuladas
def test_ciclo_diario_prepara_en_la_ventana_y_repetirlo_no_duplica_nada(env):
    _, ctx, _ = env
    ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-19"])
    r = ctx.flujo.ciclo_diario(hoy="2026-10-14")
    assert r["futuras"] == 1 and r["preparadas"] == [] and ctx.estado.listar_salidas("DEMO")[0]["estado"] == "programada"
    r = ctx.flujo.ciclo_diario(hoy="2026-10-15")                                # llega el dia: 3 dias habiles antes
    assert len(r["preparadas"]) == 1 and ctx.estado.listar_salidas("DEMO")[0]["estado"] == "preparada"
    alertas = ctx.estado.listar_alertas(["DEMO"])
    notifs = ctx.estado.listar_notificaciones()
    assert alertas and notifs and all(n["estado"] == "simulado" and "SIMULADO" in n["detalle"] for n in notifs)
    r2 = ctx.flujo.ciclo_diario(hoy="2026-10-15")                                # repeticion
    assert r2["preparadas"] == [] and r2["ya_preparadas"] == 1
    assert len(ctx.estado.listar_alertas(["DEMO"])) == len(alertas) and len(ctx.estado.listar_notificaciones()) == len(notifs)
    assert len(ctx.estado.listar_salidas("DEMO")) == 1


def test_salida_pasada_sin_preparar_genera_aviso_alto_una_sola_vez(env):
    _, ctx, _ = env
    ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-13"])
    for _ in range(2):
        r = ctx.flujo.ciclo_diario(hoy="2026-10-20")
        assert len(r["atrasadas"]) == 1
    av = [a for a in ctx.estado.listar_alertas(["DEMO"]) if a["tipo"] == "PREPARACION_ATRASADA"]
    assert len(av) == 1 and av[0]["severidad"] == "alta"


def test_ruta_no_operativa_no_se_prepara_y_se_avisa(env):
    _, ctx, _ = env
    ctx.estado.guardar_ruta(ACTOR, "VACIA", "Ruta vacía", "Vendedor Demo", "LOCAL", None, implantacion="PENDIENTE_INFORMACION")
    with pytest.raises(ErrorNegocio, match="operativa"):
        ctx.estado.crear_salida(ACTOR, "VACIA", "2026-10-19")


def test_los_candidatos_a_solo_contado_nunca_se_convierten_en_decision_automatica(env):
    _, ctx, _ = env
    ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-19"])
    ctx.flujo.ciclo_diario(hoy="2026-10-15")
    assert ctx.estado.decisiones_vigentes() == {}                                  # ninguna decision creada por el proceso
    pend = [a for a in ctx.estado.listar_alertas(["DEMO"]) if a["tipo"] == "CANDIDATO_PENDIENTE_GERENCIA"]
    assert {"C90004", "C90005"} <= {a["codigo"] for a in pend} and all(a["severidad"] == "alta" for a in pend)
    assert ctx.servicio.ruta("DEMO").clientes[3].resultado.final == "CANDIDATO_SOLO_CONTADO"


# ------------------------------------------------------------------ evaluaciones por salida, comparacion y alertas de cambio
def test_cambios_entre_salidas_generan_alertas_y_se_pueden_comparar(env):
    _, ctx, _ = env
    ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-19", "2026-10-26"])
    ctx.flujo.ciclo_diario(hoy="2026-10-15")
    ger = {"id": None, "usuario": "ger1", "rol": "gerencia", "nombre": "Gerente"}
    ctx.servicio.registrar_decision(ger, "C90004", "SOLO_CONTADO", "Decisión de gerencia de prueba para el cambio.")
    ctx.flujo.ciclo_diario(hoy="2026-10-22")
    s1, s2 = sorted(ctx.estado.listar_salidas("DEMO"), key=lambda s: s["fecha_salida"])
    cmp = ctx.estado.comparar_salidas(s1["id"], s2["id"])
    cam = {c["codigo"]: c for c in cmp["cambios"]}
    assert cam["C90004"]["antes"] == "CANDIDATO_SOLO_CONTADO" and cam["C90004"]["ahora"] == "SOLO_CONTADO" and cmp["sin_cambio"] >= 7
    a = [x for x in ctx.estado.listar_alertas(["DEMO"], salida_id=s2["id"]) if x["tipo"] == "CAMBIO_CATEGORIA"]
    assert len(a) == 1 and a[0]["codigo"] == "C90004"
    with pytest.raises(ErrorNegocio, match="misma ruta"):
        ctx.estado.comparar_salidas(s1["id"], ctx.estado.crear_salida(ACTOR, "LAS_VEGAS", "2026-10-20")["id"])


# ------------------------------------------------------------------ Excel de ruta: bloqueos, versiones Actual/Historico, inmutabilidad
def _preparar_lv(ctx, fecha="2026-10-20"):
    ctx.flujo.programar_salidas(ACTOR, "LAS_VEGAS", [fecha])
    sid = next(s["id"] for s in ctx.estado.listar_salidas("LAS_VEGAS") if s["fecha_salida"] == fecha)
    ctx.flujo.preparar(ACTOR, sid)
    return sid


def test_excel_versionado_borrador_actual_historico_idempotente_e_inmutable(env):
    _, ctx, tmp = env
    ctx.servicio.fuente = FuenteApta()           # fuente validada (doble de prueba): sin excepciones que decidan, se publica normalmente
    ctx.servicio.invalidar()
    sid = _preparar_lv(ctx)
    e1 = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert e1["creada"] and e1["version"] == 1 and e1["estado"] == "ACTUAL" and e1["borrador"] == 0 and "/Actual/" in e1["archivo"] and e1["publicable"]
    wb = openpyxl.load_workbook(ctx.estado.carpeta_excel / e1["archivo"])
    assert len(wb.sheetnames) == 2                                                  # portada + datos SAP: solo dos hojas
    e1b = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")                   # repeticion con el mismo contenido
    assert not e1b["creada"] and e1b["id"] == e1["id"] and len(ctx.estado.listar_excel("LAS_VEGAS")) == 1
    # cambia el roster (cliente nuevo confirmado) -> version 2; la 1 pasa a Historico sin tocarse
    ctx.estado.agregar_cliente(ACTOR, "LAS_VEGAS", "C90006", "SALON DEMO EN PRUEBA", "LV")
    e2 = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert e2["creada"] and e2["version"] == 2 and e2["clientes"] == 4
    versiones = {v["version"]: v for v in ctx.estado.listar_excel("LAS_VEGAS")}
    assert versiones[1]["estado"] == "HISTORICO" and "/Historico/" in versiones[1]["archivo"] and versiones[2]["estado"] == "ACTUAL"
    f1 = ctx.estado.carpeta_excel / versiones[1]["archivo"]
    assert f1.is_file() and hashlib.sha256(f1.read_bytes()).hexdigest() == versiones[1]["sha256"]       # el historico conserva su contenido exacto
    assert not list((ctx.estado.carpeta_excel / "LAS_VEGAS" / "Actual").glob("v0001*"))
    assert ctx.estado.verificar_excel() == []
    f1.chmod(0o666)
    f1.write_bytes(b"alterado")                                                      # si alguien altera un historico, la verificacion lo detecta
    assert "hash no coincide" in ctx.estado.verificar_excel()[0]


def test_huella_es_estable_entre_guardados_y_cambia_con_el_contenido(env):
    _, ctx, tmp = env
    wb = openpyxl.load_workbook(FIXTURE)
    a, b = tmp / "a.xlsx", tmp / "b.xlsx"
    wb.save(a)
    wb.save(b)
    assert huella_excel(a.read_bytes()) == huella_excel(b.read_bytes())
    wb.worksheets[0]["Z60"] = "otro valor"
    wb.save(b)
    assert huella_excel(a.read_bytes()) != huella_excel(b.read_bytes())


# ------------------------------------------------------------------ seguimiento de visitas, cobros y ventas
def test_visitas_con_cobro_y_venta_cumplimiento_y_omisiones_consecutivas(env):
    _, ctx, _ = env
    ven = {"id": ctx.estado.listar_usuarios()[2]["id"], "usuario": "ven1", "rol": "vendedor", "nombre": "V"}
    ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-05", "2026-10-12", "2026-10-19"])
    sal = sorted(ctx.estado.listar_salidas("DEMO"), key=lambda s: s["fecha_salida"])
    for s in sal:
        ctx.estado.cambiar_estado_salida(ACTOR, s["id"], "cerrada")
        ctx.estado.registrar_visita(ven, "DEMO", s["fecha_salida"], "C90001", "visitada_firmada", cobro=1500.5, venta=3200)
        ctx.estado.registrar_visita(ven, "DEMO", s["fecha_salida"], "C90002", "no_visitada", "Local cerrado")        # 3 veces seguidas
        ctx.estado.registrar_visita(ven, "DEMO", s["fecha_salida"], "C90003", "visitada_sin_firma", "Sin lapicero")     # sin firma != no visita
    seg = ctx.estado.seguimiento_salida(sal[2]["id"])
    assert seg["totales"]["cobro_declarado"] == 1500.5 and seg["totales"]["venta_declarada"] == 3200 and seg["totales"]["visitados"] == 2
    om = ctx.estado.omisiones_consecutivas("DEMO", 3)
    assert [c["codigo"] for c in om["omitidas_confirmadas"]] == ["C90002"]                  # C90003 (sin firma) NO es omision
    with pytest.raises(ErrorNegocio, match="no realizada"):
        ctx.estado.registrar_visita(ven, "DEMO", "2026-10-19", "C90004", "no_visitada", "x", cobro=10)
    with pytest.raises(ErrorNegocio, match="monto"):
        ctx.estado.registrar_visita(ven, "DEMO", "2026-10-19", "C90004", "visitada_firmada", cobro=-5)
    ctx.estado.crear_salida(ACTOR, "DEMO", "2026-10-26")
    sid = ctx.estado.listar_salidas("DEMO")[0]["id"]
    ctx.flujo.preparar(ACTOR, sid)
    a = [x for x in ctx.estado.listar_alertas(["DEMO"]) if x["tipo"] == "OMISION_3_SALIDAS"]
    assert len(a) == 1 and a[0]["codigo"] == "C90002" and not [x for x in ctx.estado.listar_alertas(["DEMO"]) if x["codigo"] == "C90003" and x["tipo"].startswith("OMISION")]


# ------------------------------------------------------------------ API: permisos, calendario, dashboard
def test_api_calendario_ciclo_avisos_notificaciones_y_permisos(env):
    s, ctx, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    ven2 = cliente_con_login(s, "ven2", CLAVE_VEN2)
    st, _, js = ger.post("/api/salidas/serie", {"ruta": "DEMO", "fechas": ["2026-10-19", "2026-10-18"]})
    assert st == 201 and len(js["creadas"]) == 1 and js["rechazadas"][0]["fecha"] == "2026-10-18"
    assert ven.post("/api/salidas/serie", {"ruta": "DEMO", "fechas": ["2026-10-26"]})[0] == 403
    assert ven.post("/api/salidas/ciclo", {})[0] == 403
    cal = ven.get("/api/calendario?periodo=2026-10")[2]
    assert [x["ruta"] for x in cal["salidas"]] == ["DEMO"] and cal["salidas"][0]["fecha_preparacion"] == "2026-10-15" and cal["salidas"][0]["preparacion"] == "futura"
    assert ven2.get("/api/calendario")[2]["salidas"] == []                                      # no ve rutas ajenas
    st, _, r = adm.post("/api/salidas/ciclo", {"hoy": "2026-10-15"})
    assert st == 200 and len(r["preparadas"]) == 1
    assert adm.post("/api/salidas/ciclo", {"hoy": "2026-10-15"})[2]["preparadas"] == []
    av = ven.get("/api/avisos")[2]["avisos"]
    assert av and all(a["route_id"] == "DEMO" for a in av) and ven2.get("/api/avisos")[2]["avisos"] == []
    assert ven.post(f"/api/avisos/{av[0]['id']}/atender", {"estado": "vista"})[0] == 403          # solo admin/gerencia atienden
    assert ger.post(f"/api/avisos/{av[0]['id']}/atender", {"estado": "cerrada"})[0] == 400        # exige nota
    assert ger.post(f"/api/avisos/{av[0]['id']}/atender", {"estado": "cerrada", "nota": "Revisado por gerencia"})[0] == 200
    nv = ven.get("/api/notificaciones")[2]["notificaciones"]
    assert nv and all(n["destinatario"] == "ven1" for n in nv)                                     # cada quien ve solo las suyas
    assert {n["destinatario"] for n in ger.get("/api/notificaciones")[2]["notificaciones"]} >= {"ven1", "ger1", "admin1"}
    assert ven.get("/api/dashboard")[0] == 403 and ven.get("/api/dashboard/mio")[0] == 200
    d = ger.get("/api/dashboard")[2]
    assert d["por_ruta"] and d["top_saldos"] and d["alertas"]["abiertas"] > 0 and "core" in d and d["aviso"]
    assert {x["ruta"] for x in ven.get("/api/dashboard/mio")[2]["por_ruta"]} == {"DEMO", "LAS_VEGAS"}
    assert "core" not in ven.get("/api/dashboard/mio")[2]                                           # el vendedor no recibe cartera global


def test_oportunidades_clientes_a_con_compras_esporadicas(env):
    from cxc_portal import dashboard as dash
    _, ctx, _ = env
    op = dash.oportunidades(ctx.servicio, {"DEMO": ctx.servicio.ruta("DEMO")})
    assert "aprobada" in op["criterio"] and "solo informativa" in op["criterio"]
    assert op["informativa"] is True and all(o["codigo"] and o["facturas_ventana"] <= 2 for o in op["oportunidades"])


def test_el_notificador_simulado_no_envia_nada_y_el_candado_esta_cerrado():
    import cxc_portal.flujo as fl
    assert fl.ENVIO_EXTERNO_HABILITADO is False
    assert "SIMULADO" in NotificadorSimulado().enviar({"destinatario": "x"})


# ------------------------------------------------------------------ permisos de los archivos versionados y herramientas de operacion
def test_excel_versionado_respeta_la_visibilidad_por_ruta_y_exige_csrf(env):
    s, ctx, _ = env
    sid = _preparar_lv(ctx)
    e = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    ven, ven2, ger = (cliente_con_login(s, u, c) for u, c in (("ven1", CLAVE_VEN), ("ven2", CLAVE_VEN2), ("ger1", CLAVE_GER)))
    assert ven.get(f"/api/excel/{e['id']}/descargar")[0] == 200
    assert ven2.get(f"/api/excel/{e['id']}/descargar")[0] == 404                       # vendedor de otra ruta: ni se revela que existe
    assert ven2.get("/api/rutas/LAS_VEGAS/excel")[0] == 404
    assert ven.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})[0] == 403        # el vendedor no publica
    assert ven.post(f"/api/salidas/{sid}/impresion", {})[0] == 403
    tok, ger.csrf = ger.csrf, None
    assert ger.post("/api/salidas/serie", {"ruta": "DEMO", "fechas": ["2026-10-26"]})[0] == 403   # sin CSRF
    ger.csrf = tok
    assert ger.post("/api/admin/feriados", {"fecha": "no-es-fecha", "nombre": "x"})[0] == 400


def test_cli_ciclo_diario_prepara_e_imprime_resumen(tmp_path, capsys):
    from cxc_portal import admin
    ctx = crear_entorno(tmp_path)
    FlujoOperativo(ctx.servicio).programar_salidas(ACTOR, "DEMO", ["2026-10-19"])
    assert admin.main(["ciclo-diario", "--estado", str(tmp_path / "estado"), "--demo", "--fecha", "2026-10-15"]) == 0
    assert "preparadas 1" in capsys.readouterr().out
    assert admin.main(["ciclo-diario", "--estado", str(tmp_path / "estado"), "--demo", "--fecha", "2026-10-15"]) == 0
    assert "preparadas 0" in capsys.readouterr().out and ctx.estado.listar_salidas("DEMO")[0]["estado"] == "preparada"


def test_dashboard_usa_cxc_reporting_core_para_la_cartera_cuando_esta_disponible(tmp_path, monkeypatch):
    import os

    from cxc_portal import dashboard as dash
    from cxc_portal.reporting_bridge import ReportingNoDisponible, ReportingService
    src = Path(r"C:\Users\LTP\Documents\Trabajo-Sync\Cosprobell\cxc-reporting-core\src")
    if not (src / "cxc_reporting").is_dir():
        pytest.skip("cxc-reporting-core no está disponible en este equipo")
    monkeypatch.setenv("CXC_REPORTING_SRC", str(src))
    ctx = crear_entorno(tmp_path)
    try:
        rep = ReportingService(ctx.servicio.fuente, tmp_path / "estado")
    except ReportingNoDisponible:
        pytest.skip("cxc-reporting-core no es importable")
    d = dash.construir(ctx.servicio, ctx.estado, ctx.servicio.rutas(), rep)
    assert d["core"]["disponible"] is False and "corte" in d["core"]["motivo"].lower()      # sin corte importado: se dice, no se inventa
    rep.importar_corte(ctx.estado.listar_rutas())
    d = dash.construir(ctx.servicio, ctx.estado, ctx.servicio.rutas(), rep)
    c = d["core"]
    assert c["disponible"] and "NO_VALIDADO" in c["estado_validacion"] and c["cartera"]["n_documentos_abiertos"] == 3 and c["aging"] and c["por_vendedor"]
    assert c["clientes_prioritarios"] and "NO VALIDADO" in c["aviso"]


def test_el_aviso_de_preparacion_atrasada_se_cierra_solo_al_preparar_la_salida(env):
    _, ctx, _ = env
    ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-13"])
    ctx.flujo.ciclo_diario(hoy="2026-10-20")
    sid = ctx.estado.listar_salidas("DEMO")[0]["id"]
    assert [a["estado"] for a in ctx.estado.listar_alertas(["DEMO"], tipo="PREPARACION_ATRASADA")] == ["nueva"]
    ctx.flujo.preparar(ACTOR, sid)
    a = ctx.estado.listar_alertas(["DEMO"], tipo="PREPARACION_ATRASADA")[0]
    assert a["estado"] == "cerrada" and a["atendida_por"] == "sistema"


def test_cumplimiento_no_lista_salidas_futuras_sin_registros(env):
    from cxc_portal import dashboard as dash
    _, ctx, _ = env
    ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-19", "2026-10-26"])
    assert dash.cumplimiento(ctx.estado, ctx.servicio.rutas()) == []
