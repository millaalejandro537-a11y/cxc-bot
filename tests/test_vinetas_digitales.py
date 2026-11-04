"""Viñetas digitales (Issue #2): el color sale del MOTOR UNICO (`payment_semaphore.color_final`); solo es DEFINITIVO con fuente validada y clase
determinada; el Excel se pinta unicamente por opt-in y es identico salvo el relleno de la celda de viñeta. Datos 100 % sinteticos."""

import io
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from cxc_bot import payment_semaphore as ps
from cxc_bot import route_markers as rm
from cxc_bot import validators
from cxc_bot.config import load_route_config
from cxc_bot.excel_service import generate_route
from cxc_bot.models import ClientEntry, SapClientRecord
from cxc_portal import demo
from cxc_portal.flujo import FlujoOperativo
from tests.portal_helpers import ACTOR, CLAVE_GER, FuenteApta, Servidor, cliente_con_login, crear_entorno

FIX = Path("tests/fixtures/rutas_reales")


# --------------------------------------------------------------------------------------------- 1. traduccion motor -> viñeta (pura)
@pytest.fixture(scope="module")
def resultados_demo():
    from cxc_portal.demo import FuenteSintetica
    f = FuenteSintetica()
    cods = [c for c, *_ in demo.SPEC]
    return _evaluar(f, cods)


def _evaluar(fuente, codigos):
    ev = fuente.facturas_por_cliente(codigos)
    return [ps.evaluar_cliente(c, ev[c], demo.CORTE, ps.PLAZO_FORANEA, None) for c in codigos]


def test_el_color_definitivo_es_exactamente_el_del_motor(resultados_demo):
    plan = rm.plan_vinetas(resultados_demo, fuente_apta=True)
    vistos = set()
    for r in resultados_demo:
        v = plan[r.codigo]
        if v.estado == rm.DEFINITIVA:
            assert v.color == r.color_final and v.hex == rm.HEX[r.color_final]          # sin reglas propias: mismo color que el motor
            vistos.add(r.color_final)
    assert {"VERDE", "AZUL", "AMARILLO", "ROJO", "BLANCO"} <= vistos                    # las 5 viñetas de la leyenda aparecen con datos sinteticos


def test_contado_es_rojo_automatico_sin_decision_de_gerencia(resultados_demo):
    contado = [r for r in resultados_demo if r.final == ps.SOLO_CONTADO]
    assert contado and all(r.decision_gerencia is None for r in contado)
    assert all(rm.vineta_de(r, True).hex == "DC2626" for r in contado)


def test_pendiente_y_sin_datos_nunca_reciben_color_de_credito(resultados_demo):
    pend = [r for r in resultados_demo if ps.COLOR[r.final] == "PENDIENTE_GERENCIA"]
    assert pend                                                                          # C90008: sin base para la media -> revision humana
    for apta in (True, False):
        for r in pend:
            v = rm.vineta_de(r, apta)
            assert v.estado == rm.REVISION and v.color is None and v.hex is None
    import dataclasses
    sin = rm.vineta_de(dataclasses.replace(resultados_demo[0], final=ps.SIN_DATOS, calculado=ps.SIN_DATOS), True)     # el Portal lo produce cuando no hay datos cargados
    assert sin.estado == rm.SIN_DATOS and sin.color is None and sin.hex is None


def test_fuente_no_validada_no_da_ningun_color_definitivo(resultados_demo):
    plan = rm.plan_vinetas(resultados_demo, fuente_apta=False)
    assert rm.colores_a_pintar(plan) == {}
    assert {v.estado for v in plan.values()} <= {rm.PROVISIONAL, rm.REVISION, rm.SIN_DATOS}
    assert all("no está validada" in v.motivo for v in plan.values() if v.estado == rm.PROVISIONAL)


# --------------------------------------------------------------------------------------------- 2. Excel identico salvo el relleno
def _fill(f):
    return (f.fill_type, repr(f.fgColor), repr(f.bgColor))


def _estilo(c):
    return (repr(c.font), repr(c.border), repr(c.alignment), c.number_format, repr(c.protection))


def _firma(ws):
    ps_, m = ws.page_setup, ws.page_margins
    return (ws.title, ws.dimensions, sorted(str(r) for r in ws.merged_cells.ranges),
            {k: d.width for k, d in ws.column_dimensions.items()}, {k: d.height for k, d in ws.row_dimensions.items() if d.height},
            (ps_.orientation, ps_.paperSize, ps_.fitToWidth, ps_.fitToHeight, ps_.scale, m.left, m.right, m.top, m.bottom, ws.print_title_rows, ws.print_area))


def _sin_relleno_a(a, b, filas_pintadas, col="A"):
    """a y b son hojas: todo igual (valor, fuente, borde, alineacion, formato) y relleno igual salvo en col/filas_pintadas."""
    diffs = []
    for fila_a, fila_b in zip(a.iter_rows(), b.iter_rows()):
        for ca, cb in zip(fila_a, fila_b):
            if ca.value != cb.value or _estilo(ca) != _estilo(cb):
                diffs.append(ca.coordinate)
            if _fill(ca.fill) != _fill(cb.fill) and not (ca.column == openpyxl.utils.column_index_from_string(col) and ca.row in filas_pintadas):
                diffs.append(ca.coordinate + ":relleno")
    return diffs


def _roster(n, zonas):
    cnt = {z: 0 for z in zonas}
    out = []
    for i in range(n):
        z = zonas[i % len(zonas)]
        cnt[z] += 1
        out.append(ClientEntry(codigo=f"C9{i:04d}", folder=f"{cnt[z]} {z}"))
    return out


COLORES = ["34A853", "2563EB", "FACC15", "DC2626", "64748B"]


@pytest.mark.parametrize("ruta,base,n,zonas", [("LAS VEGAS", "LAS VEGAS MAYO.xlsx", 24, ["LV", "PB"]), ("YORO", "YOROABRIL.xlsx", 31, ["SY", "MY", "PY"]),
                                               ("OCCIDENTE", "OCCIDENTE MAYO.xlsx", 119, [f"Z{chr(65 + i)}" for i in range(21)])])
def test_excel_identico_salvo_el_relleno_de_la_viñeta(tmp_path, ruta, base, n, zonas):
    cfg = load_route_config(ruta)
    sap = [SapClientRecord(codigo=f"C9{i:04d}", nombre=f"CLIENTE SINTETICO {i}", saldo=100.0 + i) for i in range(n)]
    roster = _roster(n, zonas)
    markers = {e.codigo: COLORES[i % 5] for i, e in enumerate(roster) if i % 7 != 3}                 # algunos clientes quedan sin color (pendiente/sin datos)
    kw = dict(route_config=cfg, base_path=FIX / base, new_date=date(2026, 11, 3), sap_records=sap, override_roster=roster)
    generate_route(output_path=tmp_path / "sin.xlsx", **kw)
    generate_route(output_path=tmp_path / "con.xlsx", markers=markers, **kw)
    sin, con = openpyxl.load_workbook(tmp_path / "sin.xlsx"), openpyxl.load_workbook(tmp_path / "con.xlsx")
    assert sin.sheetnames == con.sheetnames and len(con.sheetnames) == 2
    for nombre in sin.sheetnames:
        assert _firma(sin[nombre]) == _firma(con[nombre])
    filas = {cfg.data_start_row + i for i, e in enumerate(roster) if e.codigo in markers}
    assert _sin_relleno_a(sin[cfg.main_sheet], con[cfg.main_sheet], filas, cfg.num_col) == []
    assert _sin_relleno_a(sin[cfg.data_sheet], con[cfg.data_sheet], set()) == []
    assert validators.validate_vinetas(tmp_path / "con.xlsx", cfg, markers).ok
    # idempotencia: repetir produce el mismo contenido
    generate_route(output_path=tmp_path / "con2.xlsx", markers=markers, **kw)
    assert _sin_relleno_a(con[cfg.main_sheet], openpyxl.load_workbook(tmp_path / "con2.xlsx")[cfg.main_sheet], set()) == []


def test_sin_markers_el_comportamiento_es_el_anterior(tmp_path):
    cfg = load_route_config("LAS VEGAS")
    sap = [SapClientRecord(codigo=f"C9{i:04d}", nombre=f"C{i}", saldo=1.0) for i in range(5)]
    for kw in ({}, {"markers": None}, {"markers": {}}):
        generate_route(cfg, FIX / "LAS VEGAS MAYO.xlsx", tmp_path / "o.xlsx", date(2026, 11, 3), sap, override_roster=_roster(5, ["LV"]), force=True, **kw)
        ws = openpyxl.load_workbook(tmp_path / "o.xlsx")[cfg.main_sheet]
        base_fill = _fill(openpyxl.load_workbook(FIX / "LAS VEGAS MAYO.xlsx")[cfg.main_sheet].cell(8, 1).fill)
        assert all(_fill(ws.cell(r, 1).fill) == base_fill for r in range(8, 13))


def test_el_validador_detecta_color_equivocado_o_inventado(tmp_path):
    cfg = load_route_config("LAS VEGAS")
    sap = [SapClientRecord(codigo=f"C9{i:04d}", nombre=f"C{i}", saldo=1.0) for i in range(4)]
    roster = _roster(4, ["LV"])
    generate_route(cfg, FIX / "LAS VEGAS MAYO.xlsx", tmp_path / "o.xlsx", date(2026, 11, 3), sap, override_roster=roster,
                   markers={"C90000": "34A853", "C90001": "DC2626"})
    assert validators.validate_vinetas(tmp_path / "o.xlsx", cfg, {"C90000": "34A853", "C90001": "DC2626"}).ok
    mal = validators.validate_vinetas(tmp_path / "o.xlsx", cfg, {"C90000": "2563EB", "C90001": "DC2626"})
    assert not mal.ok and "C90000" in mal.errors[0]
    inventado = validators.validate_vinetas(tmp_path / "o.xlsx", cfg, {"C90000": "34A853"})                 # C90001 quedo pintado sin que el motor lo determine
    assert not inventado.ok and "C90001" in inventado.errors[0]


# --------------------------------------------------------------------------------------------- 3. contraste y legibilidad (incluida impresion simulada)
@pytest.mark.parametrize("nombre,hex_", sorted(rm.HEX.items()))
def test_texto_de_la_celda_de_folder_es_legible_sobre_cada_color(tmp_path, nombre, hex_):
    """Folder real de las plantillas: Arial 16 negrita automatico (texto grande -> minimo 3:1). Se mide sobre cada relleno."""
    cfg = load_route_config("LAS VEGAS")
    sap = [SapClientRecord(codigo="C90000", nombre="X", saldo=1.0)]
    generate_route(cfg, FIX / "LAS VEGAS MAYO.xlsx", tmp_path / "o.xlsx", date(2026, 11, 3), sap, override_roster=[ClientEntry(codigo="C90000", folder="1 LV")], markers={"C90000": hex_})
    c = openpyxl.load_workbook(tmp_path / "o.xlsx")[cfg.main_sheet]["A8"]
    assert c.font.sz >= 14 and c.font.b                                   # la plantilla usa texto grande
    assert rm.contraste(hex_, "000000") >= rm.contraste_minimo(c.font.sz, c.font.b) >= 3.0
    assert validators.validate_vinetas(tmp_path / "o.xlsx", cfg, {"C90000": hex_}).ok


def test_texto_pequeno_sobre_relleno_oscuro_se_rechaza_con_un_mensaje_claro(tmp_path):
    from openpyxl.styles import Font
    cfg = load_route_config("LAS VEGAS")
    sap = [SapClientRecord(codigo="C90000", nombre="X", saldo=1.0)]
    generate_route(cfg, FIX / "LAS VEGAS MAYO.xlsx", tmp_path / "o.xlsx", date(2026, 11, 3), sap, override_roster=[ClientEntry(codigo="C90000", folder="1 LV")],
                   markers={"C90000": "2563EB"})
    wb = openpyxl.load_workbook(tmp_path / "o.xlsx")
    wb[cfg.main_sheet]["A8"].font = Font(name="Arial", sz=11, b=False)    # una plantilla con texto normal (<14 pt): exige 4.5:1
    wb.save(tmp_path / "pequeno.xlsx")
    r = validators.validate_vinetas(tmp_path / "pequeno.xlsx", cfg, {"C90000": "2563EB"})
    assert not r.ok and "contraste insuficiente" in r.errors[0] and "C90000" in r.errors[0]
    wb[cfg.main_sheet]["A8"].font = Font(name="Arial", sz=11, b=False, color="FFFFFFFF")     # texto blanco si alcanza en el azul
    wb.save(tmp_path / "blanco.xlsx")
    assert validators.validate_vinetas(tmp_path / "blanco.xlsx", cfg, {"C90000": "2563EB"}).ok


def test_impresion_en_blanco_y_negro_los_colores_oscuros_no_se_distinguen():
    """Limitacion medida (no se oculta): en escala de grises AZUL, ROJO, BLANCO y VERDE quedan a <= 40 niveles; solo AMARILLO se distingue."""
    grupos = rm.grupos_indistinguibles_en_gris(rm.HEX)
    assert grupos == [["ROJO", "AZUL", "BLANCO", "VERDE"]] or sorted(grupos[0]) == ["AZUL", "BLANCO", "ROJO", "VERDE"]
    assert rm.grupos_indistinguibles_en_gris(["AMARILLO", "VERDE"]) == []
    assert rm.grupos_indistinguibles_en_gris(["VERDE"]) == []
