"""Reglas aprobadas por Alejandro (2026-10-11):
1. Aviso cuando la mora promedio sube 5 dias o mas entre salidas; umbral configurable (y auditado).
2. Oportunidad comercial (cliente A con <= 2 facturas en 180 dias): SOLO informativa.
3. Gestion por excepcion: el borrador siempre se prepara; si los datos estan validados y no hay excepciones que requieran decision se publica normalmente;
   se pide revision de gerencia unicamente ante excepciones que requieren decision antes de publicar.
Datos 100 % sinteticos. `FuenteApta` es un DOBLE DE PRUEBA que se declara validado; no es una fuente real."""

import shutil
from datetime import date
from pathlib import Path

import pytest

from cxc_portal.estado import ErrorNegocio
from cxc_portal.flujo import FlujoOperativo
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, FuenteApta, Servidor, cliente_con_login, crear_entorno

FIXTURE = Path("tests/fixtures/rutas_reales/LAS VEGAS MAYO.xlsx")
GER = {"id": None, "usuario": "ger1", "rol": "gerencia", "nombre": "Gerente Uno"}
ADM = {"id": None, "usuario": "admin1", "rol": "admin", "nombre": "Admin"}


@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    ctx.estado.guardar_ruta(ACTOR, "LAS_VEGAS", "Las Vegas", "Vendedor Demo", "FORANEA", None, zonas={"LV": "Las Vegas", "PB": "Peña Blanca"},
                            clientes=[{"codigo": "C90001", "folder": "1 LV", "nombre": "SALON DEMO EXCELENTE (A)"},
                                      {"codigo": "C90002", "folder": "2 LV", "nombre": "SALON DEMO BUENO (B)"},
                                      {"codigo": "C90003", "folder": "1 PB", "nombre": "SALON DEMO REGULAR (C)"}], requiere_hojas_visita=True)
    (tmp_path / "datos" / "01_Rutas").mkdir(parents=True)
    shutil.copy2(FIXTURE, tmp_path / "datos" / "01_Rutas" / "base.xlsx")
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: date(2026, 10, 12))
    s = Servidor(ctx)
    yield s, ctx, tmp_path
    s.cerrar()


def _validar_fuente(ctx):
    ctx.servicio.fuente = FuenteApta()
    ctx.servicio.invalidar()


def _salida(ctx, fecha="2026-10-20"):
    return ctx.estado.crear_salida(ACTOR, "LAS_VEGAS", fecha)["id"]


# ------------------------------------------------------------------ 3. gestion por excepcion
def test_sin_excepciones_y_con_fuente_validada_se_publica_normal_sin_pedir_revision(env):
    s, ctx, _ = env
    _validar_fuente(ctx)
    ctx.estado.agregar_cliente(ACTOR, "LAS_VEGAS", "C90009", "SALON DEMO CON ALERTA > 60 DIAS ABIERTA", "LV")   # alerta de cobro: INFORMATIVA, no bloquea
    sid = _salida(ctx)
    sal = ctx.flujo.preparar(ACTOR, sid)
    b = sal["reporte_excepciones"]["bloqueantes"]
    assert b == {"duras": 0, "requieren_revision_gerencia": 0} and sal["reporte_excepciones"]["requiere_revision"] is False
    assert sal["reporte_excepciones"]["alertas_cobro"]                                         # hay excepciones informativas
    e = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert e["estado"] == "ACTUAL" and e["borrador"] == 0 and e["publicable"] and e["motivos"] == [] and not e["requiere_revision_gerencia"]
    tipos = {a["tipo"] for a in ctx.estado.listar_alertas(["LAS_VEGAS"])}
    assert "REVISION_GERENCIA_REQUERIDA" not in tipos and "COBRO_MORA_ABIERTA" in tipos
    assert not [n for n in ctx.estado.listar_notificaciones() if n["tipo"] == "REVISION_GERENCIA"]    # a nadie se le pide revisar


def test_con_excepciones_que_requieren_decision_queda_borrador_y_se_pide_revision_solo_a_gerencia(env):
    s, ctx, _ = env
    _validar_fuente(ctx)
    ctx.estado.agregar_cliente(ACTOR, "LAS_VEGAS", "C90004", "SALON DEMO CANDIDATO A SOLO CONTADO", "LV")      # candidato pendiente de decision
    sid = _salida(ctx)
    sal = ctx.flujo.preparar(ACTOR, sid)
    assert sal["reporte_excepciones"]["bloqueantes"]["requieren_revision_gerencia"] == 1
    avisos = [a for a in ctx.estado.listar_alertas(["LAS_VEGAS"]) if a["tipo"] == "REVISION_GERENCIA_REQUERIDA"]
    assert len(avisos) == 1 and avisos[0]["severidad"] == "alta"
    pedidos = ctx.estado.listar_notificaciones() and [n for n in ctx.estado.listar_notificaciones() if n["tipo"] == "REVISION_GERENCIA"]
    assert {n["destinatario"] for n in pedidos} == {"ger1"}                                                    # solo gerencia: ni admin ni vendedor
    e = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert e["estado"] == "BORRADOR" and not e["publicable"] and e["requiere_revision_gerencia"] and any("revisión de gerencia" in m for m in e["motivos"])
    ctx.flujo.preparar(ACTOR, sid)                                                                             # repetir no duplica aviso ni pedido
    assert len([a for a in ctx.estado.listar_alertas(["LAS_VEGAS"]) if a["tipo"] == "REVISION_GERENCIA_REQUERIDA"]) == 1
    assert len([n for n in ctx.estado.listar_notificaciones() if n["tipo"] == "REVISION_GERENCIA"]) == 1


def test_la_revision_de_gerencia_promueve_el_mismo_borrador_a_actual_sin_crear_version(env):
    s, ctx, _ = env
    _validar_fuente(ctx)
    ctx.estado.agregar_cliente(ACTOR, "LAS_VEGAS", "C90004", "SALON DEMO CANDIDATO A SOLO CONTADO", "LV")
    sid = _salida(ctx)
    ctx.flujo.preparar(ACTOR, sid)
    borrador = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    ctx.estado.revisar_salida(ADM, sid, "Revisión de un administrador (no es gerencia).")
    assert ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")["estado"] == "BORRADOR"                   # el administrador NO sustituye a gerencia
    ctx.estado.revisar_salida(GER, sid, "Revisión de gerencia: se puede publicar.")
    e = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert e["estado"] == "ACTUAL" and e["id"] == borrador["id"] and e["promovida"] and not e["creada"] and e["borrador"] == 0 and "/Actual/" in e["archivo"]
    assert len(ctx.estado.listar_excel("LAS_VEGAS")) == 1 and ctx.estado.verificar_excel() == [] and ctx.estado.excel_borrador("LAS_VEGAS") is None


def test_clientes_propuestos_sin_confirmar_no_se_resuelven_con_una_revision(env):
    s, ctx, _ = env
    _validar_fuente(ctx)
    ctx.estado.proponer_candidatos(ACTOR, "LAS_VEGAS", [{"codigo": "C90006", "nombre": "SALON DEMO EN PRUEBA"}])
    sid = _salida(ctx)
    ctx.flujo.preparar(ACTOR, sid)
    ctx.estado.revisar_salida(GER, sid, "Revisión de gerencia con un cliente aún sin confirmar.")
    e = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert e["estado"] == "BORRADOR" and any("sin confirmar" in m for m in e["motivos"])
    cid = ctx.estado.listar_candidatos("LAS_VEGAS", "propuesta")[0]["id"]
    ctx.estado.rechazar_candidato(GER, "LAS_VEGAS", cid, "No pertenece a la ruta (prueba).")                   # se resuelve decidiendo, no revisando
    ctx.flujo.preparar(ACTOR, sid)
    ctx.estado.revisar_salida(GER, sid, "Revisión de gerencia tras resolver el cliente propuesto.")
    assert ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")["estado"] == "ACTUAL"


def test_sin_fuente_validada_siempre_es_borrador_y_no_desplaza_al_publicado(env):
    s, ctx, _ = env
    _validar_fuente(ctx)
    sid = _salida(ctx)
    publicado = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")                                          # prepara solo si hace falta
    assert publicado["estado"] == "ACTUAL"
    ctx.servicio.fuente = __import__("tests.portal_helpers", fromlist=["FuenteSintetica"]).FuenteSintetica()      # la fuente deja de estar validada
    ctx.servicio.invalidar()
    ctx.estado.agregar_cliente(ACTOR, "LAS_VEGAS", "C90006", "SALON DEMO EN PRUEBA", "LV")
    b = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert b["estado"] == "BORRADOR" and any("fuente" in m for m in b["motivos"])
    assert ctx.estado.excel_actual("LAS_VEGAS")["id"] == publicado["id"]                                          # la publicada sigue vigente
    b2 = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert not b2["creada"] and b2["id"] == b["id"] and len(ctx.estado.listar_excel("LAS_VEGAS")) == 2          # idempotente
    assert ctx.estado.verificar_excel() == []


def test_el_borrador_se_prepara_solo_aunque_la_salida_no_estuviera_preparada(env):
    s, ctx, _ = env
    sid = _salida(ctx)
    assert ctx.estado.obtener_salida(sid)["preparada_en"] is None
    e = ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert ctx.estado.obtener_salida(sid)["preparada_en"] and e["estado"] == "BORRADOR"


def test_api_informa_si_se_publico_o_quedo_borrador_y_el_calendario_marca_la_revision(env):
    s, ctx, _ = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    sid = _salida(ctx)
    st, _, e = ger.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})
    assert st == 201 and e["estado"] == "BORRADOR" and e["publicable"] is False and e["motivos"]
    fila = ger.get("/api/calendario?ruta=LAS_VEGAS")[2]["salidas"][0]
    assert "requiere_revision" in fila


# ------------------------------------------------------------------ 1. umbral de mora configurable
def _preparar_con_mora(ctx, mora_antes, mora_ahora):
    """Dos salidas con evaluaciones persistidas controladas; devuelve las alertas AUMENTO_MORA de la segunda."""
    s1 = _salida(ctx, "2026-10-13")
    s2 = _salida(ctx, "2026-10-20")
    fila = lambda m: [{"codigo": "C90001", "folder": "1 LV", "calculado": "A", "final": "A", "promedio_mora": m, "saldo": 0.0}]
    ctx.estado.guardar_preparacion(ACTOR, s1, fila(mora_antes), {"total_excepciones": 0})
    sal2 = ctx.estado.guardar_preparacion(ACTOR, s2, fila(mora_ahora), {"total_excepciones": 0})
    ctx.flujo.generar_alertas(sal2)
    return [a for a in ctx.estado.listar_alertas(["LAS_VEGAS"], tipo="AUMENTO_MORA")]


@pytest.mark.parametrize("antes,ahora,esperada", [(10, 15, True), (10, 14.9, False), (10, 30, True), (10, 5, False), (0, 5, True)])
def test_alerta_cuando_la_mora_promedio_sube_5_dias_o_mas(env, antes, ahora, esperada):
    _, ctx, _ = env
    a = _preparar_con_mora(ctx, antes, ahora)
    assert bool(a) is esperada
    if esperada:
        assert a[0]["detalle"]["umbral_dias"] == 5 and a[0]["detalle"]["ahora"] - a[0]["detalle"]["antes"] >= 5


def test_el_umbral_es_configurable_auditado_y_validado(env):
    s, ctx, _ = env
    assert ctx.estado.configuracion() == {"umbral_aumento_mora_dias": 5, "oportunidad_ventana_dias": 180, "oportunidad_max_facturas": 2}
    ctx.estado.fijar_configuracion(GER, "umbral_aumento_mora_dias", 10)
    assert _preparar_con_mora(ctx, 10, 18) == []                                         # +8 < 10: ya no alerta
    assert any(a["accion"] == "configuracion.fijar" and "10" in a["detalle"] for a in ctx.estado.listar_auditoria(50))
    with pytest.raises(ErrorNegocio, match="rango"):
        ctx.estado.fijar_configuracion(GER, "umbral_aumento_mora_dias", 0)
    with pytest.raises(ErrorNegocio, match="desconocido"):
        ctx.estado.fijar_configuracion(GER, "otra_cosa", 1)
    with pytest.raises(ErrorNegocio, match="entero"):
        ctx.estado.fijar_configuracion(GER, "umbral_aumento_mora_dias", "abc")


def test_api_configuracion_solo_la_cambian_admin_o_gerencia(env):
    s, ctx, _ = env
    ger, ven = cliente_con_login(s, "ger1", CLAVE_GER), cliente_con_login(s, "ven1", CLAVE_VEN)
    assert ven.get("/api/configuracion")[0] == 403 and ven.post("/api/admin/configuracion", {"clave": "umbral_aumento_mora_dias", "valor": 7})[0] == 403
    st, _, js = ger.post("/api/admin/configuracion", {"clave": "umbral_aumento_mora_dias", "valor": 7})
    assert st == 200 and js["valores"]["umbral_aumento_mora_dias"] == 7
    d = ger.get("/api/configuracion")[2]
    assert d["valores"]["umbral_aumento_mora_dias"] == 7 and d["por_defecto"]["umbral_aumento_mora_dias"] == 5
    assert ger.post("/api/admin/configuracion", {"clave": "umbral_aumento_mora_dias", "valor": 500})[0] == 400


# ------------------------------------------------------------------ 2. oportunidad comercial: solo informativa
def test_la_oportunidad_comercial_es_informativa_y_no_genera_avisos_ni_notificaciones_ni_decisiones(env):
    from cxc_portal import dashboard as dash
    s, ctx, _ = env
    antes = (len(ctx.estado.listar_alertas()), len(ctx.estado.listar_notificaciones()), dict(ctx.estado.decisiones_vigentes()))
    op = dash.oportunidades(ctx.servicio, ctx.servicio.rutas())
    assert op["informativa"] is True and op["oportunidades"] and all(ctx.servicio.buscar_cliente(o["codigo"]) for o in op["oportunidades"])
    assert (len(ctx.estado.listar_alertas()), len(ctx.estado.listar_notificaciones()), dict(ctx.estado.decisiones_vigentes())) == antes
    ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-19"])
    ctx.flujo.ciclo_diario(hoy="2026-10-15")
    assert not [a for a in ctx.estado.listar_alertas() if "OPORTUN" in a["tipo"]]                 # ni el ciclo ni el resumen lo convierten en aviso
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    assert ger.get("/api/dashboard")[2]["oportunidades"]["informativa"] is True


def test_los_parametros_de_oportunidad_son_configurables(env):
    from cxc_portal import dashboard as dash
    _, ctx, _ = env
    base = {o["codigo"] for o in dash.oportunidades(ctx.servicio, ctx.servicio.rutas())["oportunidades"]}
    assert "C90001" in base                                                                       # A con 0 facturas en la ventana de 180 dias
    ctx.estado.fijar_configuracion(GER, "oportunidad_ventana_dias", 400)
    amplia = dash.oportunidades(ctx.servicio, ctx.servicio.rutas())["oportunidades"]
    c1 = next((o for o in amplia if o["codigo"] == "C90001"), None)
    assert c1 is None or c1["facturas_ventana"] <= 2
    ctx.estado.fijar_configuracion(GER, "oportunidad_max_facturas", 0)
    assert all(o["facturas_ventana"] == 0 for o in dash.oportunidades(ctx.servicio, ctx.servicio.rutas())["oportunidades"])
