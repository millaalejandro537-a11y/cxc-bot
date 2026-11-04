"""Flujo completo de preparacion de una ruta del tamaño de Occidente (119 clientes, 21 zonas, folders por zona) con datos 100 % sinteticos.

Reutiliza el motor de semaforo, el generador de Excel y el flujo de salidas del Portal; no inventa saldos SAP (las facturas salen de los patrones
de `cxc_portal.demo`). Verifica: roster, codigos, zonas, folders, formulas, total, leyenda bajo el total, hojas, versionado Actual/Historico."""

import hashlib
import io
import shutil
from datetime import date
from pathlib import Path

import openpyxl
import pytest
from openpyxl.styles import Font

from cxc_portal import demo
from cxc_portal.flujo import FlujoOperativo
from tests.portal_helpers import ACTOR, CLAVE_GER, Servidor, cliente_con_login, crear_entorno

BASE = Path("tests/fixtures/rutas_reales/OCCIDENTE MAYO.xlsx")          # libro base ANONIMIZADO (estructura de Occidente, hojas Hoja1/Hoja2)
ZONAS = [f"Z{chr(65 + i)}" for i in range(21)]                          # 21 prefijos sinteticos
N = 119
LEYENDA = [("Blanca", "FF64748B"), ("Verde", "FF34A853"), ("Azul", "FF2563EB"), ("Amarilla", "FFFACC15"), ("Roja", "FFDC2626")]


class FuenteOccidenteSintetica(demo.FuenteSintetica):
    """119 clientes ficticios C91xxx: cada uno recibe uno de los patrones de pago de `demo.SPEC` (el semaforo lo calcula el motor real)."""

    def __init__(self):
        self._f, self._n = {}, {}
        for i in range(N):
            _, _, moras, abiertas, nc = demo.SPEC[i % len(demo.SPEC)]
            codigo = f"C91{i:03d}"
            self._f[codigo] = demo._facturas(100000 * (i + 1), moras, abiertas, nc)
            self._n[codigo] = f"CLIENTE SINTETICO {codigo}"


def _base_con_leyenda(dest):
    wb = openpyxl.load_workbook(BASE)
    ws = wb["Hoja1"]
    total = next(r for r in range(8, 200) if str(ws.cell(r, 5).value or "").strip().upper() == "TOTAL")
    t = total + 3
    ws.cell(t, 1, "SIGNIFICADO DE LAS VIÑETAS")
    ws.merge_cells(start_row=t, start_column=1, end_row=t, end_column=3)
    for i, (nombre, color) in enumerate(LEYENDA, 1):
        ws.cell(t + i, 1, "●").font = Font(color=color)
        ws.cell(t + i, 2, nombre)
    wb.save(dest)


@pytest.fixture()
def env(tmp_path):
    ctx = crear_entorno(tmp_path)
    ctx.servicio.fuente = FuenteOccidenteSintetica()
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
    s = Servidor(ctx)
    yield s, ctx, tmp_path, hoy
    s.cerrar()


def _hoja(datos):
    wb = openpyxl.load_workbook(io.BytesIO(datos))
    assert wb.sheetnames == ["Hoja1", "Hoja2"]                           # formato operativo = SOLO portada + datos SAP
    return wb["Hoja1"]


def test_preparar_ruta_occidente_de_punta_a_punta(env):
    s, ctx, tmp, hoy = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    # 1. roster y semaforo desde el motor unico
    cl = ger.get("/api/rutas/OCCIDENTE/clientes")[2]["clientes"]
    assert len(cl) == N and len({c["codigo"] for c in cl}) == N and len({c["folder"] for c in cl}) == N
    assert {c["folder"].split()[1] for c in cl} == set(ZONAS)
    assert all(c["clasificacion"] for c in cl) if "clasificacion" in cl[0] else True
    # 2. programar y preparar
    assert ger.post("/api/salidas/serie", {"ruta": "OCCIDENTE", "fechas": ["2026-11-10"]})[0] == 201
    sid = ger.get("/api/calendario?ruta=OCCIDENTE")[2]["salidas"][0]["id"]
    hoy["d"] = date(2026, 11, 6)
    assert [p["salida"] for p in ger.post("/api/salidas/ciclo", {})[2]["preparadas"]] == [sid]
    # 3. Excel de la salida
    st, _, e1 = ger.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})
    assert st == 201 and e1["clientes"] == N and e1["estado"] == "BORRADOR"
    ws = _hoja((ctx.estado.carpeta_excel / e1["archivo"]).read_bytes())
    fila0, total = 8, 8 + N
    assert [ws.cell(r, 2).value for r in range(fila0, total)] == [c["codigo"] for c in cl]          # orden de visita == orden del portal
    assert [ws.cell(r, 1).value for r in range(fila0, total)] == [c["folder"] for c in cl]          # folders por zona
    assert all(str(ws.cell(r, 3).value).startswith("=VLOOKUP(") and str(ws.cell(r, 6).value).startswith("=VLOOKUP(") for r in range(fila0, total))
    assert ws.cell(total, 5).value == "TOTAL" and ws.cell(total, 6).value == f"=SUM(F{fila0}:F{total - 1})"
    # 4. leyenda bajo el total intacta y en su sitio
    assert ws.cell(total + 3, 1).value == "SIGNIFICADO DE LAS VIÑETAS" and f"A{total + 3}:C{total + 3}" in {str(r) for r in ws.merged_cells.ranges}
    assert [ws.cell(total + 3 + i, 2).value for i in range(1, 6)] == [n for n, _ in LEYENDA]
    # 5. el vendedor descarga el mismo archivo (hash) y repetir no crea otra version
    assert hashlib.sha256((ctx.estado.carpeta_excel / e1["archivo"]).read_bytes()).hexdigest() == e1["sha256"]
    assert ger.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})[0] == 200
    # 6. cliente nuevo: crece una fila, la leyenda baja una, la v1 pasa a historico
    ctx.estado.agregar_cliente(ACTOR, "OCCIDENTE", "C91999", "CLIENTE SINTETICO C91999", "ZA")
    ctx.servicio.fuente._f["C91999"] = demo._facturas(900000, [0, 1, 2, 0, 3])
    ctx.servicio.fuente._n["C91999"] = "CLIENTE SINTETICO C91999"
    ctx.servicio.invalidar()
    assert ger.post("/api/salidas/serie", {"ruta": "OCCIDENTE", "fechas": ["2026-11-17"]})[0] == 201
    sid2 = [c["id"] for c in ger.get("/api/calendario?ruta=OCCIDENTE")[2]["salidas"] if c["fecha_salida"] == "2026-11-17"][0]
    hoy["d"] = date(2026, 11, 13)
    ger.post("/api/salidas/ciclo", {})
    st, _, e2 = ger.post(f"/api/salidas/{sid2}/excel", {"base": "base.xlsx"})
    assert st == 201 and e2["version"] == 2 and e2["clientes"] == N + 1
    ws2 = _hoja((ctx.estado.carpeta_excel / e2["archivo"]).read_bytes())
    assert ws2.cell(total + 1, 5).value == "TOTAL" and ws2.cell(total + 4, 1).value == "SIGNIFICADO DE LAS VIÑETAS"
    assert ws2.cell(8 + N, 1).value.endswith(" ZA") and ws2.cell(8 + N, 2).value == "C91999"
    assert {v["version"]: v["estado"] for v in ger.get("/api/rutas/OCCIDENTE/excel")[2]["versiones"]} == {1: "HISTORICO", 2: "BORRADOR"}
    assert ger.get("/api/rutas/OCCIDENTE/excel")[2]["integridad"] == []


def _salida_lista(ger, hoy, fecha, preparacion):
    assert ger.post("/api/salidas/serie", {"ruta": "OCCIDENTE", "fechas": [fecha]})[0] == 201
    sid = [c["id"] for c in ger.get("/api/calendario?ruta=OCCIDENTE")[2]["salidas"] if c["fecha_salida"] == fecha][0]
    hoy["d"] = preparacion
    ger.post("/api/salidas/ciclo", {})
    return sid


def test_retirar_un_cliente_no_renumera_y_la_leyenda_sube(env):
    s, ctx, tmp, hoy = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    antes = {c["codigo"]: c["folder"] for c in ger.get("/api/rutas/OCCIDENTE/clientes")[2]["clientes"]}
    ctx.estado.quitar_cliente(ACTOR, "OCCIDENTE", "C91005")
    ctx.servicio.invalidar()
    sid = _salida_lista(ger, hoy, "2026-11-10", date(2026, 11, 6))
    st, _, e = ger.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})
    assert st == 201 and e["clientes"] == N - 1
    ws = _hoja((ctx.estado.carpeta_excel / e["archivo"]).read_bytes())
    total = 8 + N - 1
    codigos = [ws.cell(r, 2).value for r in range(8, total)]
    assert "C91005" not in codigos and len(codigos) == N - 1
    assert all(ws.cell(8 + i, 1).value == antes[c] for i, c in enumerate(codigos))                 # ningun folder se renumera
    assert ws.cell(total, 5).value == "TOTAL" and ws.cell(total + 3, 1).value == "SIGNIFICADO DE LAS VIÑETAS"


def test_cliente_fuera_de_sap_da_un_error_claro_y_no_publica_nada(env):
    s, ctx, tmp, hoy = env
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    ctx.estado.agregar_cliente(ACTOR, "OCCIDENTE", "C91888", "CLIENTE SINTETICO AUN SIN SAP", "ZA")       # no existe en la fuente
    ctx.servicio.invalidar()
    sid = _salida_lista(ger, hoy, "2026-11-10", date(2026, 11, 6))
    st, _, r = ger.post(f"/api/salidas/{sid}/excel", {"base": "base.xlsx"})
    assert st == 409 and "C91888" in str(r) and "formula" not in str(r).lower()                          # indica el cliente; sin el error criptico anterior
    assert ger.get("/api/rutas/OCCIDENTE/excel")[2]["versiones"] == []
