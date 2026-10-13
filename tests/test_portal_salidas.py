"""Occidente (piloto), salidas, hojas de visita imprimibles, seguimiento/omisiones, candidatos con confirmacion humana y folders permanentes."""

import pytest

from cxc_portal.estado import Estado
from cxc_portal.sembrar import sembrar
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, CLAVE_VEN2, Servidor, cliente_con_login, crear_entorno


@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    s = Servidor(ctx)
    yield s, ctx, tmp_path
    s.cerrar()


def _salida(adm, fecha="2026-10-13", ruta="DEMO"):
    st, _, js = adm.post("/api/salidas", {"ruta": ruta, "fecha_salida": fecha})
    assert st == 201, js
    return js["id"]


# ----------------------------------------------------------------------------- Occidente: ruta piloto vacia, sin inventar nada
def test_occidente_se_siembra_vacia_pendiente_y_con_hojas_de_visita(tmp_path):
    est = Estado(tmp_path / "p.sqlite3")
    datos = tmp_path / "datos"
    (datos / "01_Rutas").mkdir(parents=True)
    # sembrar solo necesita los Excel de Las Vegas/Yoro: se prueba la logica de Occidente directamente
    from cxc_portal import sembrar as sm
    c = sm.RUTA_OCCIDENTE
    est.guardar_ruta(ACTOR, c["route_id"], c["nombre"], c["vendedor"], c["tipo"], 60, estado_fisico=c["estado_fisico"], notas=c["notas"],
                     requiere_hojas_visita=True, implantacion="PENDIENTE_INFORMACION")
    r = est.obtener_ruta("OCCIDENTE")
    assert r["vendedor"] == "José Carpio" and r["clientes"] == [] and r["zonas"] == []
    assert r["implantacion"] == "PENDIENTE_INFORMACION" and r["requiere_hojas_visita"] == 1
    assert "PROVISIONAL" in r["notas"] and "PENDIENTE" in r["estado_fisico"]


def test_ruta_vacia_no_permite_salidas_hojas_ni_marcarse_operativa(env):
    s, ctx, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    st, _, r = adm.post("/api/admin/rutas", {"route_id": "OCC", "nombre": "Occidente", "vendedor": "José Carpio", "tipo_ruta": "FORANEA", "requiere_hojas_visita": True})
    assert st == 201 and r["implantacion"] == "PENDIENTE_INFORMACION" and r["clientes"] == []
    st, _, js = adm.post("/api/salidas", {"ruta": "OCC", "fecha_salida": "2026-10-13"})
    assert st == 409 and "no está operativa" in js["error"]
    st, _, js = adm.post("/api/admin/rutas/OCC/implantacion", {"estado": "OPERATIVA"})
    assert st == 409 and "sin clientes" in js["error"]


# ----------------------------------------------------------------------------- candidatos y folders permanentes
def test_flujo_de_incorporacion_con_confirmacion_humana_y_folders_permanentes(env):
    s, ctx, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    adm.post("/api/admin/rutas", {"route_id": "OCC", "nombre": "Occidente", "vendedor": "José Carpio", "tipo_ruta": "FORANEA", "requiere_hojas_visita": True,
                                  "zonas": {"SR": "Santa Rosa"}})
    # busqueda en el snapshot: SOLO sugiere (no modifica la ruta)
    sug = adm.post("/api/admin/rutas/OCC/candidatos/buscar", {"palabras": ["salon demo excelente", "bueno"]})[2]["sugerencias"]
    assert {x["codigo"] for x in sug} == {"C90001", "C90002"} and all(x["ya_en_ruta"] == "DEMO" for x in sug)
    assert ctx.estado.obtener_ruta("OCC")["clientes"] == []
    # propuesta -> pendiente de confirmar
    st, _, js = adm.post("/api/admin/rutas/OCC/candidatos", {"clientes": [{"codigo": "C90001", "nombre": "UNO"}, {"codigo": "C90002", "nombre": "DOS"}, {"codigo": "C90003", "nombre": "TRES"}]})
    assert st == 201 and len(js["propuestos"]) == 3 and ctx.estado.obtener_ruta("OCC")["implantacion"] == "EN_CONFIRMACION"
    ids = {c["codigo"]: c["id"] for c in adm.get("/api/admin/rutas/OCC/candidatos")[2]["candidatos"]}
    assert adm.post("/api/admin/rutas/OCC/implantacion", {"estado": "OPERATIVA"})[0] == 409            # hay propuestas sin resolver
    assert adm.post(f"/api/admin/rutas/OCC/candidatos/{ids['C90001']}/confirmar", {"zona": "XX"})[0] == 409   # zona no definida: no se inventa
    assert adm.post(f"/api/admin/rutas/OCC/candidatos/{ids['C90001']}/confirmar", {"zona": "SR"})[2]["folder"] == "1 SR"
    assert adm.post(f"/api/admin/rutas/OCC/candidatos/{ids['C90002']}/confirmar", {"zona": "SR"})[2]["folder"] == "2 SR"
    assert adm.post(f"/api/admin/rutas/OCC/candidatos/{ids['C90003']}/rechazar", {"motivo": "no"})[0] == 400      # motivo corto
    assert adm.post(f"/api/admin/rutas/OCC/candidatos/{ids['C90003']}/rechazar", {"motivo": "No pertenece a la ruta"})[0] == 200
    assert adm.post(f"/api/admin/rutas/OCC/candidatos/{ids['C90002']}/confirmar", {"zona": "SR"})[0] == 409     # ya resuelto
    assert adm.post("/api/admin/rutas/OCC/implantacion", {"estado": "OPERATIVA"})[0] == 200
    # numeracion PERMANENTE: retirar el ultimo folder NO libera su numero
    assert adm.pedir("DELETE", "/api/admin/rutas/OCC/clientes/C90002")[0] == 200
    assert adm.post("/api/admin/rutas/OCC/clientes", {"codigo": "C90004", "nombre": "CUATRO", "zona": "SR"})[2]["folder"] == "3 SR"
    assert ctx.estado.folders_usados("OCC") == {"SR": 3}
    # folder explicito bajo la marca de agua: rechazado
    ctx.estado.proponer_candidatos(ACTOR, "OCC", [{"codigo": "C90005", "nombre": "CINCO"}])
    cid = next(c["id"] for c in ctx.estado.listar_candidatos("OCC", "propuesta"))
    assert adm.post(f"/api/admin/rutas/OCC/candidatos/{cid}/confirmar", {"zona": "SR", "folder": "2 SR"})[0] == 409
    assert adm.post(f"/api/admin/rutas/OCC/candidatos/{cid}/confirmar", {"zona": "SR", "folder": "7 SR"})[2]["folder"] == "7 SR"
    assert any(e["accion"] == "candidato.confirmar" for e in ctx.estado.listar_auditoria())


def test_rbac_de_incorporacion_y_salidas(env):
    s, _, _ = env
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    for m, r, b in (("POST", "/api/admin/rutas/DEMO/candidatos/buscar", {"palabras": ["x"]}), ("POST", "/api/admin/rutas/DEMO/candidatos", {"clientes": [{"codigo": "C90001"}]}),
                    ("POST", "/api/admin/rutas/DEMO/implantacion", {"estado": "OPERATIVA"}), ("POST", "/api/salidas", {"ruta": "DEMO", "fecha_salida": "2026-10-13"}),
                    ("GET", "/api/admin/rutas/DEMO/candidatos", None)):
        assert ven.pedir(m, r, b)[0] == 403, r
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    assert ger.post("/api/admin/rutas/DEMO/implantacion", {"estado": "OPERATIVA"})[0] == 403    # solo admin


# ----------------------------------------------------------------------------- salidas y hojas de visita
# ----------------------------------------------------------------------------- preparar salida: actualizacion y excepciones
def test_preparar_salida_reporta_excepciones_y_cambios_entre_salidas(env):
    s, ctx, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    s1 = _salida(adm, "2026-10-13")
    r1 = adm.post(f"/api/salidas/{s1}/preparar")[2]
    ex = r1["reporte_excepciones"]
    assert r1["estado"] == "preparada" and ex["requiere_revision"] and ex["total_excepciones"] > 0
    assert {c["codigo"] for c in ex["pendientes_gerencia"]} == {"C90008"}          # solo la revision humana: el contado ya es rojo automatico
    assert any(a["codigo"] == "C90009" and a["saldo_pendiente"] > 0 for a in ex["alertas_cobro"])
    assert ex["cambios_de_clasificacion"] == [] and any("snapshot" in a.lower() for a in ex["advertencias"])
    assert adm.post(f"/api/salidas/{s1}/revisar", {"nota": "Revisado sin novedades"})[0] == 200
    # entre salidas: gerencia decide un caso y se retira/agrega un cliente
    assert ger.post("/api/decisiones", {"codigo": "C90008", "clasificacion_final": "C", "motivo": "Gerencia resuelve la falta de evidencia (prueba)."})[0] == 201
    ctx.estado.quitar_cliente(ACTOR, "DEMO", "C90006")
    s2 = _salida(adm, "2026-10-20")
    ex2 = adm.post(f"/api/salidas/{s2}/preparar")[2]["reporte_excepciones"]
    assert any(c["codigo"] == "C90008" and c["antes"] == "REVISION_HUMANA" and c["ahora"] == "C" for c in ex2["cambios_de_clasificacion"])
    assert [c["codigo"] for c in ex2["clientes_retirados"]] == ["C90006"]
    assert "C90008" not in {c["codigo"] for c in ex2["pendientes_gerencia"]}
    assert ger.post(f"/api/salidas/{s2}/revisar", {"nota": "Revisadas las excepciones de la salida."})[2]["revisada_por"] == "ger1"
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    assert ven.post(f"/api/salidas/{s2}/preparar")[0] == 403 and ven.post(f"/api/salidas/{s2}/revisar", {"nota": "xxx"})[0] == 403
    assert adm.post(f"/api/salidas/{s2}/revisar", {"nota": "x"})[0] == 400


# ----------------------------------------------------------------------------- seguimiento y omisiones consecutivas
def test_seguimiento_pendientes_y_omisiones_consecutivas_sin_confundir_falta_de_firma(env):
    s, ctx, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    fechas = ["2026-10-13", "2026-10-20", "2026-10-27"]
    sids = [_salida(adm, f) for f in fechas]
    for sid in sids:
        adm.post(f"/api/salidas/{sid}/estado", {"estado": "en_curso"})
        ven.post("/api/visitas", {"ruta": "DEMO", "fecha_salida": ctx.estado.obtener_salida(sid)["fecha_salida"], "codigo": "C90001", "estado": "visitada_firmada"})
        ven.post("/api/visitas", {"ruta": "DEMO", "fecha_salida": ctx.estado.obtener_salida(sid)["fecha_salida"], "codigo": "C90002", "estado": "visitada_sin_firma", "motivo": "sin firma"})
        ven.post("/api/visitas", {"ruta": "DEMO", "fecha_salida": ctx.estado.obtener_salida(sid)["fecha_salida"], "codigo": "C90003", "estado": "no_visitada", "motivo": "cerrado"})
    sg = ven.get(f"/api/salidas/{sids[0]}")[2]["seguimiento"]
    assert sg["resumen"]["visitada_firmada"] == 1 and sg["resumen"]["visitada_sin_firma"] == 1 and sg["resumen"]["no_visitada"] == 1 and sg["resumen"]["pendiente"] == 6
    om = ven.get("/api/rutas/DEMO/omisiones?n=3")[2]
    assert [c["codigo"] for c in om["omitidas_confirmadas"]] == ["C90003"]                   # evidencia concluyente
    assert "C90002" not in {c["codigo"] for c in om["omitidas_confirmadas"]}                 # falta de firma no es no visita
    # un cliente con no_visitada en 2 salidas y SIN registro en la tercera requiere revision, no se declara omitido
    ctx.estado.registrar_visita(ACTOR | {"id": 1}, "DEMO", "2026-10-13", "C90005", "no_visitada", "cerrado 1")
    ctx.estado.registrar_visita(ACTOR | {"id": 1}, "DEMO", "2026-10-20", "C90005", "no_visitada", "cerrado 2")
    om = ven.get("/api/rutas/DEMO/omisiones?n=3")[2]
    assert "C90005" in {c["codigo"] for c in om["sin_registro_en_salidas"]} and "C90005" not in {c["codigo"] for c in om["omitidas_confirmadas"]}
    assert cliente_con_login(s, "ven2", CLAVE_VEN2).get("/api/rutas/DEMO/omisiones")[0] == 404
