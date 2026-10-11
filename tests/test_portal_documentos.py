"""Documentos reutilizables de hojas de visita (versiones inmutables, almacenamiento privado, vinculo a salidas) y cola de impresion (solo registro)."""

import hashlib

import openpyxl
import pytest

from tests.portal_helpers import ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, CLAVE_VEN2, Cliente, Servidor, cliente_con_login, crear_entorno


@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    s = Servidor(ctx)
    (tmp_path / "datos" / "01_Rutas").mkdir(parents=True, exist_ok=True)
    yield s, ctx, tmp_path
    s.cerrar()


def _xlsx_hojas(path, hojas):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i, clientes in enumerate(hojas, 1):
        ws = wb.create_sheet(f"Hoja {i:02d}")
        ws.append(["DISTRIBUCIONES COSPROBELL"])
        ws.append(["RUTA DEMO · VENDEDOR DEMO"])
        ws.append(["FECHA DE SALIDA: ____", None, None, "N.º DE SALIDA: ___"])
        ws.append(["COLOR", "CÓDIGO SAP", "SALÓN / CLIENTE", "ENCARGADA / TELÉFONO", "COBRO", "VENTA (L)", "OBSERVACIONES", "FIRMA"])
        for color, cod, nombre in clientes:
            ws.append([color, cod, nombre])
        ws.append(["RESUMEN DE SALIDA: VISITADOS ___"])
    wb.save(path)


def _salida(adm, fecha, ruta="DEMO"):
    st, _, js = adm.post("/api/salidas", {"ruta": ruta, "fecha_salida": fecha})
    assert st == 201, js
    return js["id"]


def test_hoja_de_la_salida_es_el_documento_reutilizable_vinculado_y_no_se_regenera(env):
    s, ctx, tmp = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    _xlsx_hojas(tmp / "datos" / "01_Rutas" / "hojas_v1.xlsx",
                [[("VERDE", "C90001", "SALON UNO (ZONA A)"), ("CANDIDATO", "C90002", "SALON DOS (ZONA A)")], [("BLANCA", "C90003", "SALON TRES (ZONA B)")]])
    sid0 = _salida(adm, "2026-10-13")
    assert adm.get(f"/api/salidas/{sid0}/hoja-visita")[0] == 409                      # aun no hay documento: no se genera nada solo
    st, _, d1 = adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": "hojas_v1.xlsx"})
    assert st == 201 and d1["version"] == 1 and d1["estado"] == "VIGENTE" and len(d1["sha256"]) == 64 and d1["contenido"]["total_clientes"] == 3
    assert d1["contenido"]["colores"] == {"VERDE": 1, "CANDIDATO": 1, "BLANCA": 1} and "PRELIMINARES" in d1["contenido"]["nota_colores"]
    assert [c["codigo"] for h in d1["contenido"]["hojas"] for c in h["clientes"]] == ["C90001", "C90002", "C90003"]   # orden del documento
    sid1 = _salida(adm, "2026-10-20")                                                  # una salida NUEVA vincula la version vigente
    h = adm.get(f"/api/salidas/{sid1}/hoja-visita")[2]
    assert h["documento"]["id"] == d1["id"] and h["es_version_vigente"] is True and h["aviso"] is None
    assert adm.get(f"/api/salidas/{sid1}/hoja-visita")[2]["documento"]["id"] == d1["id"]
    assert len(ctx.estado.listar_documentos("DEMO")) == 1                              # consultar no crea nada nuevo
    guardado = tmp / "estado" / "documentos" / d1["almacenado"]                        # almacenamiento privado, con el hash registrado
    assert guardado.is_file() and hashlib.sha256(guardado.read_bytes()).hexdigest() == d1["sha256"]
    st, cab, body = adm.get(f"/api/documentos/{d1['id']}/descargar")
    assert st == 200 and body == guardado.read_bytes() and "attachment" in cab["content-disposition"] and cab["cache-control"] == "no-store"


def test_nueva_version_exige_confirmacion_y_motivo_y_conserva_el_historial_sin_sobrescribir(env):
    s, ctx, tmp = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    _xlsx_hojas(tmp / "datos" / "01_Rutas" / "v1.xlsx", [[("VERDE", "C90001", "UNO")]])
    _xlsx_hojas(tmp / "datos" / "01_Rutas" / "v2.xlsx", [[("VERDE", "C90001", "UNO"), ("AZUL", "C90002", "DOS")]])
    d1 = adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": "v1.xlsx"})[2]
    sid1 = _salida(adm, "2026-10-20")
    assert adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": "v2.xlsx"})[0] == 409                           # ya hay vigente: no se reemplaza solo
    assert adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": "v2.xlsx", "nueva_version": True})[0] == 409    # falta confirmar
    assert adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": "v2.xlsx", "nueva_version": True, "confirmar": True, "motivo": "corto"})[0] == 400
    st, _, d2 = adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": "v2.xlsx", "nueva_version": True, "confirmar": True, "motivo": "Se agregó un cliente nuevo a la zona."})
    assert st == 201 and d2["version"] == 2 and d2["estado"] == "VIGENTE"
    assert [(d["version"], d["estado"]) for d in adm.get("/api/rutas/DEMO/documentos")[2]["documentos"]] == [(2, "VIGENTE"), (1, "HISTORICA")]
    assert (tmp / "estado" / "documentos" / d1["almacenado"]).is_file() and (tmp / "estado" / "documentos" / d2["almacenado"]).is_file()   # el historico sigue
    assert adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": "v2.xlsx", "nueva_version": True, "confirmar": True, "motivo": "Mismo contenido otra vez."})[0] == 409
    h1 = adm.get(f"/api/salidas/{sid1}/hoja-visita")[2]                                  # la salida anterior conserva SU version
    assert h1["documento"]["version"] == 1 and h1["es_version_vigente"] is False and "v2" in h1["aviso"]
    assert adm.get(f"/api/salidas/{_salida(adm, '2026-10-27')}/hoja-visita")[2]["documento"]["version"] == 2
    assert adm.get(f"/api/documentos/{d1['id']}")[2]["salidas"] == [sid1]
    assert any(e["accion"] == "documento.nueva_version" for e in ctx.estado.listar_auditoria())


def test_el_almacenado_nunca_se_sobrescribe(env):
    s, ctx, tmp = env
    _xlsx_hojas(tmp / "datos" / "01_Rutas" / "v1.xlsx", [[("VERDE", "C90001", "UNO")]])
    from cxc_portal.store import PortalServicio  # noqa: F401
    d = ctx.servicio.importar_documento(ACTOR, "DEMO", "v1.xlsx")
    destino = tmp / "estado" / "documentos" / d["almacenado"]
    with pytest.raises(FileExistsError):
        with open(destino, "xb"):
            pass


def test_documentos_solo_en_rutas_con_hojas_rbac_y_rutas_de_archivo_seguras(env):
    s, ctx, tmp = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    rutas = tmp / "datos" / "01_Rutas"
    _xlsx_hojas(rutas / "v1.xlsx", [[("VERDE", "C90001", "UNO")]])
    (rutas / "vacio.xlsx").write_bytes(b"no es un excel")
    ctx.estado.guardar_ruta(ACTOR, "SINHOJA", "Sin hoja", "Vendedor Demo", "LOCAL", None, zonas={"SH": "x"}, clientes=[{"codigo": "C90301", "folder": "1 SH", "nombre": "A"}])
    assert adm.post("/api/admin/rutas/SINHOJA/documentos", {"archivo": "v1.xlsx"})[0] == 409                           # Las Vegas/Yoro/otras sin hojas: no aplica
    for malo in ("../x.xlsx", "..\\x.xlsx", "vacio.xlsx", "no_existe.xlsx", "v1.txt"):
        assert adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": malo})[0] == 400, malo
    d = adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": "v1.xlsx"})[2]
    ven, ven2 = cliente_con_login(s, "ven1", CLAVE_VEN), cliente_con_login(s, "ven2", CLAVE_VEN2)
    assert ven.get(f"/api/documentos/{d['id']}")[0] == 200 and ven.get(f"/api/documentos/{d['id']}/descargar")[0] == 200   # el vendedor ve y descarga SU documento
    assert ven2.get(f"/api/documentos/{d['id']}")[0] == 404 and ven2.get(f"/api/documentos/{d['id']}/descargar")[0] == 404
    assert ven.post("/api/admin/rutas/DEMO/documentos", {"archivo": "v1.xlsx"})[0] == 403
    assert ven.get("/documentos/" + d["almacenado"])[0] in (404, 405)                                                 # no se sirve como estatico


def test_proponer_clientes_desde_el_documento_no_confirma_nada(env):
    s, ctx, tmp = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    adm.post("/api/admin/rutas", {"route_id": "OCC", "nombre": "Occidente", "vendedor": "José Carpio", "tipo_ruta": "FORANEA", "requiere_hojas_visita": True})
    _xlsx_hojas(tmp / "datos" / "01_Rutas" / "occ.xlsx", [[("VERDE", "C90001", "UNO (COPAN)"), ("CANDIDATO", "C90002", "DOS (COPAN)")], [("BLANCA", "C90003", "TRES (OCOTEPEQUE)")]])
    d = adm.post("/api/admin/rutas/OCC/documentos", {"archivo": "occ.xlsx"})[2]
    assert ctx.estado.obtener_ruta("OCC")["implantacion"] == "PENDIENTE_INFORMACION"                                  # registrar el documento no inventa ruta
    prop = adm.post(f"/api/admin/rutas/OCC/documentos/{d['id']}/proponer-clientes")[2]["propuestos"]
    assert [c["codigo"] for c in prop] == ["C90001", "C90002", "C90003"] and all(c["estado"] == "propuesta" for c in prop)
    assert ctx.estado.obtener_ruta("OCC")["clientes"] == [] and ctx.estado.obtener_ruta("OCC")["zonas"] == []        # clientes/zonas/folders siguen sin confirmar
    assert d["contenido"]["hojas"][0]["clientes"][0]["zona_sugerida"] == "COPAN"                                     # solo SUGERENCIA


def test_cola_de_impresion_registra_solicitudes_sin_imprimir_nada(env):
    s, ctx, tmp = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    _xlsx_hojas(tmp / "datos" / "01_Rutas" / "v1.xlsx", [[("VERDE", "C90001", "UNO")]])
    d = adm.post("/api/admin/rutas/DEMO/documentos", {"archivo": "v1.xlsx"})[2]
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    st, _, it = ven.post("/api/impresion", {"tipo": "HOJA_VISITA", "ruta": "DEMO", "documento_id": d["id"], "copias": 2})
    assert st == 201 and it["estado"] == "pendiente" and it["copias"] == 2 and it["solicitado_por"] == "ven1"
    assert adm.post("/api/impresion", {"tipo": "EXCEL_RUTA", "ruta": "DEMO", "referencia": "DEMO_2026-10-13_BORRADOR.xlsx"})[0] == 201
    assert adm.post("/api/impresion", {"tipo": "EXCEL_RUTA", "ruta": "DEMO", "referencia": "../../x.docx"})[0] == 400
    assert adm.post("/api/impresion", {"tipo": "HOJA_VISITA", "ruta": "DEMO", "documento_id": 999})[0] == 400
    assert ven.post("/api/impresion", {"tipo": "HOJA_VISITA", "ruta": "OTRA", "documento_id": d["id"]})[0] == 404          # ruta ajena
    assert ven.post("/api/impresion", {"tipo": "HOJA_VISITA", "ruta": "DEMO", "documento_id": d["id"], "copias": 99})[0] == 400
    assert len(adm.get("/api/impresion")[2]["solicitudes"]) == 2 and len(ven.get("/api/impresion")[2]["solicitudes"]) == 2
    assert cliente_con_login(s, "ven2", CLAVE_VEN2).get("/api/impresion")[2]["solicitudes"] == []
    assert ven.post(f"/api/impresion/{it['id']}/cancelar")[0] == 403 and adm.post(f"/api/impresion/{it['id']}/cancelar")[0] == 200
    assert adm.post(f"/api/impresion/{it['id']}/cancelar")[0] == 409                                                      # solo pendientes


def test_agente_de_impresion_futuro_autentica_con_su_token_y_reporta_resultados(env):
    s, ctx, tmp = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    adm.post("/api/impresion", {"tipo": "EXCEL_RUTA", "ruta": "DEMO", "referencia": "DEMO_2026-10-13_BORRADOR.xlsx"})
    ag = adm.post("/api/admin/agentes-impresion", {"nombre": "Oficina Cosprobell"})[2]
    assert ag["token"] and cliente_con_login(s, "ger1", CLAVE_GER).post("/api/admin/agentes-impresion", {"nombre": "Otro agente"})[0] == 403
    anon = Cliente(s.puerto)
    assert anon.pedir("POST", "/api/agente/siguiente", {}, csrf=False)[0] == 401
    assert anon.pedir("POST", "/api/agente/siguiente", {}, csrf=False, cabeceras={"X-Agent-Token": "falso"})[0] == 401
    cab = {"X-Agent-Token": ag["token"]}
    t = anon.pedir("POST", "/api/agente/siguiente", {}, csrf=False, cabeceras=cab)[2]["trabajo"]
    assert t["estado"] == "tomado" and t["tipo"] == "EXCEL_RUTA"
    assert anon.pedir("POST", "/api/agente/siguiente", {}, csrf=False, cabeceras=cab)[2]["trabajo"] is None
    assert anon.pedir("POST", f"/api/agente/{t['id']}/resultado", {"ok": True, "detalle": "simulado"}, csrf=False, cabeceras=cab)[2]["estado"] == "impreso"
    assert anon.pedir("POST", f"/api/agente/{t['id']}/resultado", {"ok": True}, csrf=False, cabeceras=cab)[0] == 404    # ya reportado
    assert adm.get("/api/impresion?estado=impreso")[2]["solicitudes"][0]["detalle"] == "simulado"
    assert anon.pedir("GET", "/api/rutas", cabeceras=cab)[0] == 401                                                       # el token de agente no abre sesion
