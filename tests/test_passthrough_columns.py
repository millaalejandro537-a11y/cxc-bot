"""Algunas rutas reales (Comayagua, Occidente, Santa Cruz, Villanueva, Yoro,
Santa Barbara) traen una columna 'ESTADO' entre nombre y factura que no
tiene fuente de datos real todavia. Este test confirma que el motor:
  - le preserva el estilo/alto en cada fila (tambien en filas NUEVAS), y
  - la deja en blanco en cada mes nuevo (no inventa contenido).
"""

from datetime import date

import openpyxl
import pytest

from cxc_bot.excel_service import generate_route
from cxc_bot.models import RouteConfig, SapClientRecord
from cxc_bot.route_engine import AddClient


@pytest.fixture
def comayagua_style_base(tmp_path, base_file):
    """Reutiliza el archivo real de Cortes pero le inserta una columna
    ESTADO entre Nombre y Factura, como en el layout real de Comayagua."""
    wb = openpyxl.load_workbook(base_file)
    ws = wb["CORTES"]
    ws.insert_cols(4)  # nueva columna D; Factura/Saldo/Observacion se corren a E/F/G
    for row in range(7, 40):
        ws[f"D{row}"].value = "ACTIVO" if row >= 8 else "ESTADO"
    path = tmp_path / "COMAYAGUA_style_base.xlsx"
    wb.save(path)
    return path


@pytest.fixture
def comayagua_config():
    return RouteConfig(
        route_id="COMAYAGUA_TEST",
        vendor="José Carpio",
        main_sheet="CORTES",
        data_sheet="Hoja2",
        fecha_cell="B5",
        header_row=7,
        data_start_row=8,
        num_col="A",
        code_col="B",
        name_col="C",
        factura_col="E",
        saldo_col="F",
        obs_col="G",
        total_label_col="E",
        total_value_col="F",
        filename_prefix="COMAYAGUA",
        passthrough_cols=["D"],
    )


def test_passthrough_column_style_preserved_on_new_rows(comayagua_style_base, comayagua_config, baseline_sap_records, tmp_path):
    wb_before = openpyxl.load_workbook(comayagua_style_base)
    template_font = wb_before["CORTES"]["D8"].font

    sap = list(baseline_sap_records) + [
        SapClientRecord(codigo="C99777", nombre="CLIENTE ESTADO TEST", saldo=100.0)
    ]
    output = tmp_path / "COMAYAGUA_out.xlsx"
    generate_route(
        route_config=comayagua_config,
        base_path=comayagua_style_base,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=sap,
        operations=[AddClient(codigo="C99777", after_codigo="C10621")],
    )

    wb_after = openpyxl.load_workbook(output, data_only=False)
    ws_after = wb_after["CORTES"]

    # fila insertada para el nuevo cliente: la columna ESTADO (D) debe tener
    # el mismo estilo que las demas filas, pero quedar VACIA.
    new_row = 8 + 5  # C10621 esta en indice 4; C99777 queda en indice 5
    assert ws_after[f"B{new_row}"].value == "C99777"
    assert ws_after[f"D{new_row}"].value is None
    assert ws_after[f"D{new_row}"].font.b == template_font.b
    assert ws_after[f"D{new_row}"].font.sz == template_font.sz

    # una fila que ya existia tambien debe quedar en blanco (no se inventa
    # contenido para un mes nuevo).
    assert ws_after["D8"].value is None
