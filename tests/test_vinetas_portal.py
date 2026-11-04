"""Viñetas digitales en el Portal (Issue #2), por HTTP y con datos 100 % sinteticos: el color del Excel es el del motor unico, solo con opt-in y
fuente validada; pendiente/sin datos no llevan color; las altas/retiros no renumeran; y un cliente fuera de SAP nunca habilita un total parcial."""

import hashlib
import io
import json
import shutil
from datetime import date

import openpyxl
import pytest

from cxc_bot import route_markers as rm
from cxc_portal import demo
from cxc_portal.flujo import FlujoOperativo
from tests.portal_helpers import ACTOR, CLAVE_GER, FuenteApta, Servidor, cliente_con_login, crear_entorno
from tests.test_flujo_occidente_sintetico import BASE, N, ZONAS, FuenteOccidenteSintetica, _base_con_leyenda


class FuenteOccidenteApta(FuenteOccidenteSintetica):
    apta_para_publicar = True            # DOBLE DE PRUEBA: se declara validada; no es una fuente real


def _entorno(tmp_path, fuente):
    ctx = crear_entorno(tmp_path)
    ctx.servicio.fuente = fuente
    clientes, cont = [], {z: 0 for z in ZONAS}
    for i in range(N):
        z = ZONAS[i % len(ZONAS)]
        cont[z] += 1
        clientes.append({"codigo": f"C91{i:03d}", "folder": f"{cont[z]} {z}", "zona": z, "nombre": f"CLIENTE SINTETICO C91{i:03d}"})
    ctx.estado.guardar_ruta(ACTOR, "OCCIDENTE", "Occidente", "Vendedor Demo", "FORANEA", 60, zonas={z: f"Zona {z}" for z in ZONAS}, clientes=clientes,
                            requiere_hojas_visita=True)
    ctx.estado.registrar_documento(ACTOR, "OCCIDENTE", "HOJA_VISITA", "hojas.xlsx", b"PK-hoja-sintetica", {"hojas": [], "total_clientes": N}, tmp_path / "estado" / "documentos")
    (tmp_path / "datos" / "01_Rutas").mkdir(parents=True)
    _base_con_leyenda(tmp_path / "datos" / "01_Rutas" / "base.xlsx")
    hoy = {"d": date(2026, 11, 2)}
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: hoy["d"])
    return ctx, hoy


@pytest.fixture()
def apta(tmp_path):
    ctx, hoy = _entorno(tmp_path, FuenteOccidenteApta())
    s = Servidor(ctx)
    yield s, ctx, hoy
    s.cerrar()


@pytest.fixture()
def no_apta(tmp_path):
    ctx, hoy = _entorno(tmp_path, FuenteOccidenteSintetica())
    s = Servidor(ctx)
    yield s, ctx, hoy
    s.cerrar()


def _excel(ger, hoy, ctx, fecha, prep):
    assert ger.post("/api/salidas/serie", {"ruta": "OCCIDENTE", "fechas": [fecha]})[0] == 201
    sid = [c["id"] for c in ger.get("/api/calendario?ruta=OCCIDENTE")[2]["salidas"] if c["fecha_salida"] == fecha][0]
    hoy["d"] = prep
    ger.post("/api/salidas/ciclo", {})
    st, _, e = ger.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})
    assert st in (200, 201)
    ws = openpyxl.load_workbook(io.BytesIO((ctx.estado.carpeta_excel / e["archivo"]).read_bytes()))["Hoja1"]
    return sid, e, ws


def _adv(e):
    a = e["advertencias"]
    return json.loads(a) if isinstance(a, str) else a


def _rellenos(ws, n):
    out = []
    for r in range(8, 8 + n):
        f = ws.cell(r, 1).fill
        out.append(f.fgColor.rgb[2:] if f.fill_type == "solid" and f.fgColor.type == "rgb" else None)
    return out


def _vinetas_api(ger):
    return [c["semaforo"]["vineta"] for c in ger.get("/api/rutas/OCCIDENTE/clientes")[2]["clientes"]]


def test_la_api_expone_la_vineta_con_trazabilidad_y_marca_provisional_sin_fuente_validada(no_apta):
    s, ctx, hoy = no_apta
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    v = _vinetas_api(ger)
    assert len(v) == N and {x["estado"] for x in v} <= {rm.PROVISIONAL, rm.REVISION, rm.SIN_DATOS}
    assert all(x["hex"] is None for x in v) and any(x["estado"] == rm.PROVISIONAL and x["color"] for x in v)         # color visible solo como provisional
    assert all({"clase_final", "clase_calculada", "decision_aplicada", "motivo"} <= set(x) for x in v)


def test_desactivado_por_defecto_el_excel_no_lleva_viñetas_ni_con_fuente_validada(apta):
    s, ctx, hoy = apta
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    assert ctx.estado.configuracion()["vinetas_excel"] == 0
    _, e, ws = _excel(ger, hoy, ctx, "2026-11-10", date(2026, 11, 6))
    assert not any(r in rm.HEX.values() for r in _rellenos(ws, N))
    assert not any(a.startswith("Viñetas") for a in _adv(e))


def test_con_opt_in_pero_fuente_no_validada_no_se_pinta_ningun_color_definitivo(no_apta):
    s, ctx, hoy = no_apta
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    assert ger.post("/api/admin/configuracion", {"clave": "vinetas_excel", "valor": 1})[0] == 200
    _, e, ws = _excel(ger, hoy, ctx, "2026-11-10", date(2026, 11, 6))
    assert not any(r in rm.HEX.values() for r in _rellenos(ws, N))
    assert any(a.startswith("Viñetas: 0 pintada(s)") and "PROVISIONAL" in a for a in _adv(e))               # queda constancia, sin color


def test_con_opt_in_y_fuente_validada_el_excel_lleva_el_color_del_motor(apta):
    s, ctx, hoy = apta
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    assert ger.post("/api/admin/configuracion", {"clave": "vinetas_excel", "valor": 1})[0] == 200
    sid, e1, ws = _excel(ger, hoy, ctx, "2026-11-10", date(2026, 11, 6))
    api = _vinetas_api(ger)
    esperado = [v["hex"] for v in api]                                  # DEFINITIVA -> hex; REVISION/SIN_DATOS -> None
    assert _rellenos(ws, N) == esperado
    assert {"VERDE", "AZUL", "AMARILLO", "ROJO", "BLANCO"} <= {v["color"] for v in api if v["estado"] == rm.DEFINITIVA}
    assert any(a.startswith("Impresión en blanco y negro") for a in _adv(e1))                                  # limitacion informada, no oculta
    assert all(v["hex"] is None for v in api if v["estado"] in (rm.REVISION, rm.SIN_DATOS))                      # nada de color de credito inventado
    assert all(v["hex"] == "DC2626" for v in api if v["clase_final"] == "SOLO_CONTADO")                         # contado = rojo automatico
    assert ws.cell(8 + N + 3, 1).value == "SIGNIFICADO DE LAS VIÑETAS"                                          # leyenda intacta
    assert [ws.cell(r, 2).value for r in range(8, 8 + N)] == [c["codigo"] for c in ger.get("/api/rutas/OCCIDENTE/clientes")[2]["clientes"]]
    # repetir no crea otra version ni cambia el archivo
    assert ger.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})[0] == 200
    assert hashlib.sha256((ctx.estado.carpeta_excel / e1["archivo"]).read_bytes()).hexdigest() == e1["sha256"]


def test_alta_y_retiro_no_renumeran_y_los_colores_siguen_al_codigo(apta):
    s, ctx, hoy = apta
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    ger.post("/api/admin/configuracion", {"clave": "vinetas_excel", "valor": 1})
    antes = {c["codigo"]: c["folder"] for c in ger.get("/api/rutas/OCCIDENTE/clientes")[2]["clientes"]}
    ctx.estado.quitar_cliente(ACTOR, "OCCIDENTE", "C91005")
    ctx.estado.agregar_cliente(ACTOR, "OCCIDENTE", "C91999", "CLIENTE SINTETICO C91999", "ZA")
    ctx.servicio.fuente._f["C91999"] = demo._facturas(900000, [0, 1, 2, 0, 3])
    ctx.servicio.fuente._n["C91999"] = "CLIENTE SINTETICO C91999"
    ctx.servicio.invalidar()
    _, e, ws = _excel(ger, hoy, ctx, "2026-11-10", date(2026, 11, 6))
    cl = ger.get("/api/rutas/OCCIDENTE/clientes")[2]["clientes"]
    assert len(cl) == N and "C91005" not in {c["codigo"] for c in cl} and cl[-1]["codigo"] == "C91999"
    assert all(antes[c["codigo"]] == c["folder"] for c in cl if c["codigo"] in antes)                             # nadie se renumera
    assert _rellenos(ws, N) == [c["semaforo"]["vineta"]["hex"] for c in cl]
    assert _rellenos(ws, N)[-1] is not None                                                                       # el cliente nuevo tiene su propio color


# ---------------------------------------------------------------------------- IFERROR: un total parcial no habilita ni publica nada
def test_cliente_sin_sap_en_plantilla_iferror_no_produce_un_excel_con_total_parcial(tmp_path):
    ctx = crear_entorno(tmp_path)
    ctx.servicio.fuente = FuenteApta()
    wb = openpyxl.load_workbook("tests/fixtures/rutas_reales/YOROABRIL.xlsx")
    wb["Hoja1"].title, wb["Hoja2"].title = "YORO", "Hoja1"                               # estructura de la plantilla YORO FOLDERS
    (tmp_path / "datos" / "01_Rutas").mkdir(parents=True)
    wb.save(tmp_path / "datos" / "01_Rutas" / "base.xlsx")
    ctx.estado.guardar_ruta(ACTOR, "YORO_T", "Yoro prueba", "Vendedor Demo", "FORANEA", 60, zonas={"SY": "Sy"}, plantilla_excel="YORO FOLDERS",
                            clientes=[{"codigo": "C90001", "folder": "1 SY", "nombre": "SALON DEMO EXCELENTE (A)"},
                                      {"codigo": "C90777", "folder": "2 SY", "nombre": "CLIENTE FUERA DE SAP"},
                                      {"codigo": "C90002", "folder": "3 SY", "nombre": "SALON DEMO BUENO (B)"}])
    ctx.servicio.invalidar()
    with pytest.raises(Exception) as ex:
        ctx.servicio.generar_excel(ACTOR, "YORO_T", "base.xlsx", "2026-11-10")
    assert "Fila 9 (C90777)" in str(ex.value) and "REVISAR CÓDIGO SAP" in str(ex.value) and getattr(ex.value, "status", 409) == 409
    assert not (tmp_path / "estado" / "salidas").exists() or not list((tmp_path / "estado" / "salidas").glob("YORO_T_*"))        # nada quedo guardado
    ctx.estado.quitar_cliente(ACTOR, "YORO_T", "C90777")                                                                      # control positivo: sin el cliente ausente si genera
    ctx.servicio.invalidar()
    datos, nombre, _ = ctx.servicio.generar_excel(ACTOR, "YORO_T", "base.xlsx", "2026-11-10")
    assert datos and nombre.startswith("YORO_T_2026-11-10_BORRADOR")
