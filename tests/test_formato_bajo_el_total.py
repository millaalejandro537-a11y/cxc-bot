"""Lo que el libro base tenga DEBAJO del total (p. ej. la leyenda de viñetas) debe acompañar al total cuando la ruta crece o se reduce:
valores, estilos, celdas combinadas y alturas de fila. Libro 100 % sintetico (estructura de Las Vegas + leyenda de ejemplo)."""

from datetime import date
from pathlib import Path

import openpyxl
import pytest
from openpyxl.styles import Font

from cxc_bot.config import load_route_config
from cxc_bot.excel_service import generate_route
from cxc_bot.models import ClientEntry, SapClientRecord

FIXTURE = Path("tests/fixtures/rutas_reales/LAS VEGAS MAYO.xlsx")
COLORES = [("Blanca", "FF64748B"), ("Verde", "FF34A853"), ("Azul", "FF2563EB"), ("Amarilla", "FFFACC15"), ("Roja", "FFDC2626")]


def _fila_total(ws, cfg):
    col = openpyxl.utils.column_index_from_string(cfg.total_label_col)
    return next(r for r in range(cfg.data_start_row, 200) if str(ws.cell(r, col).value or "").strip().upper() == cfg.total_label_text.upper())


@pytest.fixture()
def base_con_leyenda(tmp_path):
    cfg = load_route_config("LAS VEGAS")
    wb = openpyxl.load_workbook(FIXTURE)
    ws = wb[cfg.main_sheet]
    t = _fila_total(ws, cfg)
    titulo = t + 3
    ws.cell(titulo, 1, "SIGNIFICADO DE LAS VIÑETAS")
    ws.merge_cells(start_row=titulo, start_column=1, end_row=titulo, end_column=3)
    ws.row_dimensions[titulo].height = 24
    for i, (nombre, color) in enumerate(COLORES, 1):
        ws.cell(titulo + i, 1, "●").font = Font(color=color)
        ws.cell(titulo + i, 2, nombre)
        ws.row_dimensions[titulo + i].height = 18
    ruta = tmp_path / "base.xlsx"
    wb.save(ruta)
    return cfg, ruta, t - cfg.data_start_row


def _generar(cfg, base, n, tmp_path):
    sap = [SapClientRecord(codigo=f"C9{i:04d}", nombre=f"CLIENTE SINTETICO {i}", saldo=100.0 + i) for i in range(n)]
    roster = [ClientEntry(codigo=f"C9{i:04d}", folder=f"{i + 1} LV") for i in range(n)]
    salida = tmp_path / f"salida_{n}.xlsx"
    generate_route(route_config=cfg, base_path=base, output_path=salida, new_date=date(2026, 11, 3), sap_records=sap, override_roster=roster)
    return openpyxl.load_workbook(salida)[cfg.main_sheet]


@pytest.mark.parametrize("cambio", [4, -3, 0])
def test_la_leyenda_acompana_al_total(base_con_leyenda, tmp_path, cambio):
    cfg, base, n0 = base_con_leyenda
    ws = _generar(cfg, base, n0 + cambio, tmp_path)
    titulo = _fila_total(ws, cfg) + 3
    assert ws.cell(titulo, 1).value == "SIGNIFICADO DE LAS VIÑETAS"
    assert f"A{titulo}:C{titulo}" in {str(r) for r in ws.merged_cells.ranges}
    assert {str(r) for r in ws.merged_cells.ranges} == {"A2:C2", "A3:C3", f"A{titulo}:C{titulo}"}      # ninguna combinacion queda en la fila equivocada
    assert ws.row_dimensions[titulo].height == 24
    for i, (nombre, color) in enumerate(COLORES, 1):
        assert ws.cell(titulo + i, 1).value == "●" and ws.cell(titulo + i, 1).font.color.rgb == color
        assert ws.cell(titulo + i, 2).value == nombre
        assert ws.row_dimensions[titulo + i].height == 18


def test_sin_nada_bajo_el_total_el_comportamiento_no_cambia(tmp_path):
    cfg = load_route_config("LAS VEGAS")
    ws = _generar(cfg, FIXTURE, 27, tmp_path)
    assert {str(r) for r in ws.merged_cells.ranges} == {"A2:C2", "A3:C3"}
    assert _fila_total(ws, cfg) == cfg.data_start_row + 27


def test_la_configuracion_de_impresion_del_libro_base_se_conserva(base_con_leyenda, tmp_path):
    """La generacion no inventa ni pierde ajustes de impresion: orientacion, ajuste a pagina, margenes y titulos/area del libro base."""
    cfg, base, n0 = base_con_leyenda
    antes = openpyxl.load_workbook(base)[cfg.main_sheet]
    despues = _generar(cfg, base, n0 + 6, tmp_path)

    def firma(ws):
        ps, m = ws.page_setup, ws.page_margins
        return (ps.orientation, ps.paperSize, ps.fitToWidth, ps.fitToHeight, ps.scale, bool(ws.sheet_properties.pageSetUpPr and ws.sheet_properties.pageSetUpPr.fitToPage),
                m.left, m.right, m.top, m.bottom, ws.print_title_rows, ws.print_title_cols, ws.print_area)
    assert firma(despues) == firma(antes)
