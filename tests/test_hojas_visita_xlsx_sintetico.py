"""Fase 2 (Issue #2): hojas de visita e impresion con libros XLSX sinteticos VALIDOS (no bytes de relleno), por HTTP y sin dispositivos fisicos.

Cubre el sistema EXISTENTE (documentos reutilizables, versiones inmutables, vinculo a salidas, descarga, cola y agente simulado); no lo reconstruye.
`hojas_visita_sintetico` solo cumple el contrato del lector (`hojas_visita_excel.leer_hojas_visita`: hojas 'Hoja NN', encabezado COLOR / CODIGO SAP / NOMBRE,
pie sin codigos); NO pretende reproducir el diseño aprobado de Occidente, que es privado."""

import hashlib
import io
import json
import shutil
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from cxc_portal.agente_impresion import AgenteImpresion, ImpresoraSimulada, TransporteHttp
from cxc_portal.flujo import FlujoOperativo
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, CLAVE_VEN2, Servidor, cliente_con_login, crear_entorno
from tests.test_flujo_occidente_sintetico import N, ZONAS, _base_con_leyenda
from tests.test_vinetas_portal import FuenteOccidenteApta

COLORES_DOC = ["VERDE", "AZUL", "AMARILLA", "BLANCA", "ROJO"]


def hojas_visita_sintetico(destino: Path, codigos, color_de=None, por_hoja: int = 16) -> Path:
    color_de = color_de or (lambda i, c: COLORES_DOC[i % len(COLORES_DOC)])
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for h in range(0, len(codigos), por_hoja):
        ws = wb.create_sheet(f"Hoja {h // por_hoja + 1:02d}")
        ws["A1"] = "DISTRIBUCIONES COSPROBELL"
        ws["A2"] = f"RUTA SINTETICA — HOJA {h // por_hoja + 1:02d}"
        for j, t in enumerate(["COLOR", "CÓDIGO SAP", "SALÓN / CLIENTE", "ENCARGADA / TELÉFONO", "COBRO", "VENTA (L)", "OBSERVACIONES", "FIRMA"], 1):
            ws.cell(4, j, t)
        for k, c in enumerate(codigos[h:h + por_hoja]):
            ws.cell(5 + k, 1, color_de(h + k, c))
            ws.cell(5 + k, 2, c)
            ws.cell(5 + k, 3, f"CLIENTE SINTETICO {c}")
        pie = 5 + len(codigos[h:h + por_hoja]) + 1
        ws.cell(pie, 1, "RESUMEN DE SALIDA: VISITADOS ___ NO VISITADOS ___")
        ws.cell(pie + 1, 1, "FIRMA VENDEDOR: ____________")
    wb.save(destino)
    return destino


CODIGOS = [f"C91{i:03d}" for i in range(N)]


@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    ctx.servicio.fuente = FuenteOccidenteApta()
    clientes, cont = [], {z: 0 for z in ZONAS}
    for i, cod in enumerate(CODIGOS):
        z = ZONAS[i % len(ZONAS)]
        cont[z] += 1
        clientes.append({"codigo": cod, "folder": f"{cont[z]} {z}", "zona": z, "nombre": f"CLIENTE SINTETICO {cod}"})
    ctx.estado.guardar_ruta(ACTOR, "OCCIDENTE", "Occidente", "Vendedor Demo", "FORANEA", 60, zonas={z: f"Zona {z}" for z in ZONAS}, clientes=clientes,
                            requiere_hojas_visita=True)
    rutas = tmp_path / "datos" / "01_Rutas"
    rutas.mkdir(parents=True)
    _base_con_leyenda(rutas / "base.xlsx")
    hojas_visita_sintetico(rutas / "hojas_v1.xlsx", CODIGOS)
    hoy = {"d": date(2026, 11, 2)}
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: hoy["d"])
    s = Servidor(ctx)
    yield s, ctx, tmp_path, hoy
    s.cerrar()


def _importar(adm, archivo, **extra):
    return adm.post("/api/admin/rutas/OCCIDENTE/documentos", {"archivo": archivo, **extra})


def _salida(ger, fecha):
    assert ger.post("/api/salidas/serie", {"ruta": "OCCIDENTE", "fechas": [fecha]})[0] == 201
    return [c["id"] for c in ger.get("/api/calendario?ruta=OCCIDENTE")[2]["salidas"] if c["fecha_salida"] == fecha][0]


# --------------------------------------------------------------------------------------------- importacion, integridad y descarga
def test_importar_un_libro_valido_de_8_hojas_conserva_contenido_hash_y_descarga_identica(env):
    s, ctx, tmp, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    st, _, d = _importar(adm, "hojas_v1.xlsx")
    assert st == 201 and d["version"] == 1 and d["estado"] == "VIGENTE"
    c = d["contenido"]
    assert c["total_clientes"] == N and len(c["hojas"]) == 8 and [len(h["clientes"]) for h in c["hojas"]] == [16] * 7 + [7]
    assert c["advertencias"] == [] and set(c["colores"]) == set(COLORES_DOC)
    original = (tmp / "datos" / "01_Rutas" / "hojas_v1.xlsx").read_bytes()
    assert d["sha256"] == hashlib.sha256(original).hexdigest() and d["bytes"] == len(original)
    # descarga como gerencia y como el vendedor de la ruta: bytes identicos y libro abrible con los mismos clientes
    for usuario, clave in (("ger1", CLAVE_GER), ("ven1", CLAVE_VEN)):
        cli = cliente_con_login(s, usuario, clave)
        st, cab, cuerpo = cli.get(f"/api/documentos/{d['id']}/descargar")
        assert st == 200 and cuerpo == original
        wb = openpyxl.load_workbook(io.BytesIO(cuerpo))
        assert len(wb.sheetnames) == 8 and [r[1].value for ws in wb for r in ws.iter_rows(min_row=5, max_col=2) if r[1].value and str(r[1].value).startswith("C91")] == CODIGOS
    assert cliente_con_login(s, "ven2", CLAVE_VEN2).get(f"/api/documentos/{d['id']}/descargar")[0] in (403, 404)       # otro vendedor: no se revela
    assert sum(1 for a in ctx.estado.listar_auditoria(500) if a["accion"] == "documento.descargar") == 2


def test_documentos_invalidos_se_rechazan_o_se_advierte_sin_adivinar(env):
    s, ctx, tmp, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    rutas = tmp / "datos" / "01_Rutas"
    (rutas / "falso.xlsx").write_bytes(b"PK-no-es-un-libro")                                                          # lo que usaban las pruebas antiguas
    assert _importar(adm, "falso.xlsx")[0] == 400
    wb = openpyxl.Workbook(); wb.active["A1"] = "sin encabezado COLOR / CODIGO"; wb.save(rutas / "vacio.xlsx")
    assert _importar(adm, "vacio.xlsx")[0] == 400
    assert _importar(adm, "../../fuera.xlsx")[0] == 400 and _importar(adm, "hojas_v1.txt")[0] == 400
    hojas_visita_sintetico(rutas / "incompleto.xlsx", CODIGOS[:-3] + ["C99999"], color_de=lambda i, c: "MORADO" if i == 0 else "VERDE")
    st, _, d = _importar(adm, "incompleto.xlsx")
    assert st == 201                                                                                                  # se registra, pero con las diferencias a la vista
    adv = " ".join(d["contenido"]["advertencias"])
    assert "MORADO" in adv and "C99999" in adv and CODIGOS[-1] in adv and "118" in adv or "N" in adv
    assert d["contenido"]["colores"]["MORADO"] == 1                                                                   # el color desconocido NO corta la lectura ni se descarta


# --------------------------------------------------------------------------------------------- versiones inmutables y vinculo a salidas
def test_versiones_inmutables_y_cada_salida_conserva_la_suya(env):
    s, ctx, tmp, hoy = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    d1 = _importar(adm, "hojas_v1.xlsx")[2]
    sid1 = _salida(ger, "2026-11-10")
    assert [x["version"] for x in ger.get(f"/api/salidas/{sid1}/documentos")[2]["documentos"]] == [1]
    rutas = tmp / "datos" / "01_Rutas"
    hojas_visita_sintetico(rutas / "hojas_v2.xlsx", CODIGOS, color_de=lambda i, c: "ROJO" if i == 0 else COLORES_DOC[i % 5])       # cambia un color
    assert _importar(adm, "hojas_v2.xlsx")[0] == 409                                                                  # ya hay vigente: exige nueva version
    assert _importar(adm, "hojas_v2.xlsx", nueva_version=True)[0] == 409                                              # sin confirmar
    assert _importar(adm, "hojas_v2.xlsx", nueva_version=True, confirmar=True, motivo="corto")[0] == 400              # motivo < 10 caracteres (dato invalido)
    st, _, d2 = _importar(adm, "hojas_v2.xlsx", nueva_version=True, confirmar=True, motivo="Cambio de color de un cliente tras revisión")
    assert st == 201 and d2["version"] == 2
    assert _importar(adm, "hojas_v2.xlsx", nueva_version=True, confirmar=True, motivo="El mismo contenido otra vez")[0] == 409      # mismo hash: no duplica
    docs = {x["version"]: x for x in ger.get("/api/rutas/OCCIDENTE/documentos")[2]["documentos"]}
    assert docs[1]["estado"] == "HISTORICA" and docs[2]["estado"] == "VIGENTE"
    # el historico sigue integro y distinto del vigente; ambos descargables
    b1 = ger.get(f"/api/documentos/{d1['id']}/descargar")[2]
    b2 = ger.get(f"/api/documentos/{d2['id']}/descargar")[2]
    assert hashlib.sha256(b1).hexdigest() == d1["sha256"] and hashlib.sha256(b2).hexdigest() == d2["sha256"] and b1 != b2
    assert ctx.estado.verificar_documentos() == []
    # la salida 1 conserva v1; una salida nueva se vincula a v2
    assert [x["version"] for x in ger.get(f"/api/salidas/{sid1}/documentos")[2]["documentos"]] == [1]
    sid2 = _salida(ger, "2026-11-17")
    assert [x["version"] for x in ger.get(f"/api/salidas/{sid2}/documentos")[2]["documentos"]] == [2]


def test_la_ruta_sin_hojas_de_visita_no_acepta_documentos(env):
    s, ctx, tmp, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    assert adm.post("/api/admin/rutas/OTRA/documentos", {"archivo": "hojas_v1.xlsx"})[0] in (201, 409, 400)           # OTRA si usa hojas (crear_entorno); la regla se prueba abajo
    ctx.estado.guardar_ruta(ACTOR, "SIN_HOJAS", "Sin hojas", "Vendedor Demo", "LOCAL", 30, zonas={"SH": "x"}, clientes=[{"codigo": "C90001", "folder": "1 SH", "nombre": "UNO"}])
    assert adm.post("/api/admin/rutas/SIN_HOJAS/documentos", {"archivo": "hojas_v1.xlsx"})[0] == 409


# --------------------------------------------------------------------------------------------- impresion: cola + agente SIMULADO con libros reales
def _agente(s):
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    return adm.post("/api/admin/agentes-impresion", {"nombre": "Agente simulado"})[2]["token"]


def test_cola_de_impresion_con_hoja_y_excel_publicado_los_entrega_integros_al_agente_simulado(env):
    s, ctx, tmp, hoy = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    d = _importar(adm, "hojas_v1.xlsx")[2]
    sid = _salida(ger, "2026-11-10")
    hoy["d"] = date(2026, 11, 6)
    ger.post("/api/salidas/ciclo", {})
    # el Excel aun es BORRADOR (hay excepciones que requieren gerencia): solo se prepara la hoja de visita
    st, _, e = ger.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})
    assert st == 201 and e["estado"] == "BORRADOR"
    r = ger.post(f"/api/salidas/{sid}/impresion", {"copias": 2})[2]
    assert len(r["creadas"]) == 1 and r["omitidas"][-1]["tipo"] == "EXCEL_RUTA" and "BORRADOR" in r["omitidas"][-1]["motivo"]
    # tras la revision de gerencia con fuente validada el Excel pasa a publicado y entra a la cola
    assert ger.post(f"/api/salidas/{sid}/revisar", {"nota": "Revisión de gerencia (prueba sintética)."})[0] == 200
    e2 = ger.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})[2]
    assert e2["publicable"] and e2["estado"] == "ACTUAL"
    r2 = ger.post(f"/api/salidas/{sid}/impresion", {})[2]
    assert len(r2["creadas"]) == 1 and r2["existentes"] == r["creadas"]                                              # idempotente: la hoja no se duplica
    token = _agente(s)
    imp = ImpresoraSimulada()
    res = AgenteImpresion(TransporteHttp("127.0.0.1", s.puerto, token), imp).vaciar_cola()
    assert [x["estado"] for x in res] == ["impreso", "impreso"] and len(imp.trabajos) == 2
    hoja = next(t for t in imp.trabajos if t["sha256"] == d["sha256"])
    assert hoja["copias"] == 2                                                                                         # copias respetadas (antes el endpoint las ignoraba)
    assert ger.post(f"/api/salidas/{sid}/impresion", {"copias": "dos"})[0] == 400 and ger.post(f"/api/salidas/{sid}/impresion", {"copias": 99})[0] == 400
    assert any(t["sha256"] == e2["sha256"] for t in imp.trabajos)                                                      # el Excel entregado es el publicado, byte a byte
    estados = {i["tipo"]: i["estado"] for i in ctx.estado.listar_impresiones()}
    assert estados == {"HOJA_VISITA": "impreso", "EXCEL_RUTA": "impreso"}
    assert all("no se imprimió nada" in x["detalle"] for x in res)                                                     # dispositivos simulados


def test_cancelar_y_permisos_de_la_cola(env):
    s, ctx, tmp, hoy = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    _importar(adm, "hojas_v1.xlsx")
    sid = _salida(ger, "2026-11-10")
    iid = ger.post(f"/api/salidas/{sid}/impresion", {})[2]["creadas"][0]
    ven2 = cliente_con_login(s, "ven2", CLAVE_VEN2)
    assert ven2.post(f"/api/impresion/{iid}/cancelar", {})[0] == 403                                                   # solo gerencia/admin cancelan
    assert not [i for i in ven2.get("/api/impresion")[2]["solicitudes"] if i["id"] == iid]                             # otro vendedor ni la ve
    assert ger.post(f"/api/impresion/{iid}/cancelar", {})[0] == 200
    imp = ImpresoraSimulada()
    assert AgenteImpresion(TransporteHttp("127.0.0.1", s.puerto, _agente(s)), imp).vaciar_cola() == [] and imp.trabajos == []     # lo cancelado no se imprime
