"""Pruebas especificas del bug reportado: el Excel generado debe mostrar
nombres/saldos/total YA CALCULADOS, sin que nadie tenga que abrir el archivo
y forzar un recalculo. No basta con que la formula exista: se verifica el
valor cacheado que cualquier programa lee directamente del archivo."""

from datetime import date

import openpyxl
import pytest

from cxc_bot import validators
from cxc_bot.excel_service import edit_route, generate_route
from cxc_bot.models import SapClientRecord
from cxc_bot.route_engine import AddClient, RemoveClient


def test_generated_file_has_calculated_names_and_saldos(base_file, cortes_config, baseline_sap_records, tmp_path):
    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
    )

    # data_only=True es exactamente lo que ve cualquier programa que abra el
    # archivo SIN recalcular: si esto es None, el usuario veria celdas vacias.
    wb = openpyxl.load_workbook(output, data_only=True)
    ws = wb["CORTES"]

    assert ws["C8"].value == "CLIENTE SINTETICO C11098"
    assert isinstance(ws["E8"].value, (int, float))
    assert ws["E8"].value == 0

    # la formula debe seguir intacta en paralelo (data_only=False)
    wb_formulas = openpyxl.load_workbook(output, data_only=False)
    assert wb_formulas["CORTES"]["C8"].value == "=VLOOKUP(B8,Hoja2!A:C,2,0)"


def test_all_rows_and_total_have_calculated_values(base_file, cortes_config, baseline_sap_records, tmp_path):
    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
    )

    wb = openpyxl.load_workbook(output, data_only=True)
    ws = wb["CORTES"]

    expected_total = 0.0
    for row in range(8, 40):
        nombre = ws[f"C{row}"].value
        saldo = ws[f"E{row}"].value
        assert nombre not in (None, ""), f"C{row} sin valor calculado"
        assert isinstance(saldo, (int, float)), f"E{row} sin valor calculado numerico"
        expected_total += saldo

    total = ws["E40"].value
    assert isinstance(total, (int, float))
    assert total == pytest.approx(expected_total, abs=0.01)
    assert total == pytest.approx(38301.0, abs=0.01)  # suma real del archivo piloto


def test_formatting_untouched_by_cache_injection(base_file, cortes_config, baseline_sap_records, tmp_path):
    """La inyeccion de valores calculados no debe alterar nada mas del archivo."""
    wb_before = openpyxl.load_workbook(base_file)
    ws_before = wb_before["CORTES"]
    widths_before = {c: d.width for c, d in ws_before.column_dimensions.items()}
    merges_before = set(str(r) for r in ws_before.merged_cells.ranges)

    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
    )

    wb_after = openpyxl.load_workbook(output)
    ws_after = wb_after["CORTES"]
    assert {c: d.width for c, d in ws_after.column_dimensions.items()} == widths_before
    assert set(str(r) for r in ws_after.merged_cells.ranges) == merges_before
    assert ws_after["B8"].font.b is True
    assert ws_after["B8"].font.sz == 16.0
    assert ws_after["C8"].border.top.style == "thin"
    assert "#,##0.00" in ws_after["E8"].number_format
    assert ws_after.page_setup.orientation == "portrait"


def test_validation_fails_when_added_client_has_no_sap_record(base_file, cortes_config, baseline_sap_records, tmp_path):
    """No basta con que la formula exista: si el codigo no esta en Hoja2, el
    valor calculado real es #N/A y la validacion debe marcarlo como ERROR."""
    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,  # NO incluye C99999 a proposito
        operations=[AddClient(codigo="C99999", after_codigo="C10642")],
    )

    report = validators.validate_workbook(output, cortes_config, expected_client_count=33)
    assert not report.ok
    assert any("C99999" in e and ("#N/A" in e or "error" in e.lower()) for e in report.errors)

    wb = openpyxl.load_workbook(output, data_only=True)
    ws = wb["CORTES"]
    new_row = [r for r in range(8, 41) if ws[f"B{r}"].value == "C99999"][0]
    assert ws[f"C{new_row}"].value == "#N/A"
    assert ws[f"E{new_row}"].value == "#N/A"
    # el total se propaga como error, igual que en Excel real
    total_row = 8 + 33
    assert ws[f"E{total_row}"].value == "#N/A"


def test_edit_route_also_has_calculated_values(base_file, cortes_config, baseline_sap_records, tmp_path):
    generated = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=generated,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
    )

    edited = tmp_path / "editada.xlsx"
    edit_route(
        route_config=cortes_config,
        input_path=generated,
        output_path=edited,
        operations=[RemoveClient(codigo="C10981")],
    )

    wb = openpyxl.load_workbook(edited, data_only=True)
    ws = wb["CORTES"]
    assert ws["C8"].value == "CLIENTE SINTETICO C11098"
    assert isinstance(ws["E39"].value, (int, float))  # ultima fila tras eliminar una
    total_row = 8 + 31
    assert isinstance(ws[f"E{total_row}"].value, (int, float))

    report = validators.validate_workbook(edited, cortes_config, expected_client_count=31)
    assert report.ok, report.errors
