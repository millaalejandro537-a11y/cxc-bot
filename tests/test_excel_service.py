import hashlib
from datetime import date

import openpyxl
import pytest

from cxc_bot import excel_service, validators
from cxc_bot.excel_service import (
    ExcelServiceError,
    OutputExistsError,
    edit_route,
    generate_route,
    read_roster,
)
from cxc_bot.models import SapClientRecord
from cxc_bot.route_engine import (
    AddClient,
    ClientNotFoundError,
    DuplicateClientError,
    MoveClient,
    RemoveClient,
)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_sap_record(records, codigo, nombre="CLIENTE NUEVO", saldo=1000.0):
    return list(records) + [SapClientRecord(codigo=codigo, nombre=nombre, saldo=saldo)]


# ---------------------------------------------------------------------------
# Test 1: crear nueva ruta sin modificaciones de clientes
# ---------------------------------------------------------------------------
def test_generate_route_no_client_changes(base_file, cortes_config, baseline_sap_records, tmp_path):
    output = tmp_path / "out" / "CORTES octubre.xlsx"
    result = generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
    )

    assert output.exists()
    assert result.client_count == 32

    wb = openpyxl.load_workbook(output)
    ws = wb["CORTES"]
    b5 = ws["B5"].value
    assert (b5.date() if hasattr(b5, "date") else b5) == date(2026, 10, 12)
    roster = read_roster(ws, cortes_config)
    assert [c.codigo for c in roster] == [
        "C11098", "C10641", "C10642", "C10615", "C10621", "C10755", "C10714", "C11069",
        "C10623", "C10616", "C11112", "C10614", "C10343", "C10620", "C11122", "C10644",
        "C11136", "C10896", "C11083", "C10943", "C10210", "C11035", "C10875", "C10761",
        "C10695", "C10692", "C11121", "C11120", "C11113", "C11024", "C10932", "C10981",
    ]

    report = validators.validate_workbook(
        output, cortes_config, expected_date=date(2026, 10, 12), expected_client_count=32
    )
    assert report.ok, report.errors


# ---------------------------------------------------------------------------
# Test 2: agregar un cliente
# ---------------------------------------------------------------------------
def test_generate_route_add_client(base_file, cortes_config, baseline_sap_records, tmp_path):
    sap = add_sap_record(baseline_sap_records, "C99001", "CLIENTE NUEVO SALON")
    output = tmp_path / "CORTES octubre.xlsx"
    result = generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=sap,
        operations=[AddClient(codigo="C99001", after_codigo="C10642")],
    )
    assert result.client_count == 33

    wb = openpyxl.load_workbook(output)
    ws = wb["CORTES"]
    roster = read_roster(ws, cortes_config)
    codes = [c.codigo for c in roster]
    assert codes.index("C99001") == codes.index("C10642") + 1
    # numeracion consecutiva
    for i, row in enumerate(range(cortes_config.data_start_row, cortes_config.data_start_row + 33), start=1):
        assert ws[f"A{row}"].value == i
    # formula de la nueva fila referencia su propia fila
    new_row = cortes_config.data_start_row + codes.index("C99001")
    assert ws[f"C{new_row}"].value == f"=VLOOKUP(B{new_row},Hoja2!A:C,2,0)"
    assert ws[f"E{new_row}"].value == f"=VLOOKUP(B{new_row},Hoja2!A:C,3,0)"

    report = validators.validate_workbook(output, cortes_config, expected_client_count=33)
    assert report.ok, report.errors


# ---------------------------------------------------------------------------
# Test 3: eliminar un cliente
# ---------------------------------------------------------------------------
def test_generate_route_remove_client(base_file, cortes_config, baseline_sap_records, tmp_path):
    output = tmp_path / "CORTES octubre.xlsx"
    result = generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
        operations=[RemoveClient(codigo="C11098")],
    )
    assert result.client_count == 31

    wb = openpyxl.load_workbook(output)
    ws = wb["CORTES"]
    roster = read_roster(ws, cortes_config)
    assert "C11098" not in [c.codigo for c in roster]

    total_row = cortes_config.data_start_row + 31
    assert ws[f"E{total_row}"].value == f"=SUM(E8:E{total_row - 1})"
    assert ws[f"D{total_row}"].value == "Total Saldo Ruta"

    report = validators.validate_workbook(output, cortes_config, expected_client_count=31)
    assert report.ok, report.errors


# ---------------------------------------------------------------------------
# Test 4: mover un cliente
# ---------------------------------------------------------------------------
def test_generate_route_move_client(base_file, cortes_config, baseline_sap_records, tmp_path):
    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
        operations=[MoveClient(codigo="C10755", after_codigo="C10621")],
    )

    wb = openpyxl.load_workbook(output)
    ws = wb["CORTES"]
    roster = read_roster(ws, cortes_config)
    codes = [c.codigo for c in roster]
    assert codes.index("C10755") == codes.index("C10621") + 1
    assert len(codes) == 32  # el conteo no cambia al mover

    report = validators.validate_workbook(output, cortes_config, expected_client_count=32)
    assert report.ok, report.errors


# ---------------------------------------------------------------------------
# Test 5: agregar + eliminar + mover en una sola operacion
# ---------------------------------------------------------------------------
def test_generate_route_combined_operations(base_file, cortes_config, baseline_sap_records, tmp_path):
    sap = add_sap_record(baseline_sap_records, "C99002", "CLIENTE COMBINADO")
    output = tmp_path / "CORTES octubre.xlsx"
    result = generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=sap,
        operations=[
            AddClient(codigo="C99002", after_codigo="C10641"),
            RemoveClient(codigo="C10932"),
            MoveClient(codigo="C11098", after_codigo="C10343"),
        ],
    )
    assert result.client_count == 32  # +1 -1

    wb = openpyxl.load_workbook(output)
    ws = wb["CORTES"]
    roster = read_roster(ws, cortes_config)
    codes = [c.codigo for c in roster]
    assert "C99002" in codes
    assert "C10932" not in codes
    assert codes.index("C11098") == codes.index("C10343") + 1

    report = validators.validate_workbook(output, cortes_config, expected_client_count=32)
    assert report.ok, report.errors


# ---------------------------------------------------------------------------
# Test 6: tabla SAP con MAS clientes
# ---------------------------------------------------------------------------
def test_generate_route_sap_table_grows(base_file, cortes_config, baseline_sap_records, tmp_path):
    bigger_sap = list(baseline_sap_records) + [
        SapClientRecord(codigo=f"C9{i:04d}", nombre=f"CLIENTE EXTRA {i}", saldo=float(i))
        for i in range(50)
    ]
    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=bigger_sap,
    )

    wb = openpyxl.load_workbook(output)
    ws_sap = wb["Hoja2"]
    assert ws_sap.max_row - 1 == len(bigger_sap)
    assert ws_sap["A1"].value == "Código SN"

    report = validators.validate_workbook(output, cortes_config, expected_sap_count=len(bigger_sap))
    assert report.ok, report.errors


# ---------------------------------------------------------------------------
# Test 7: tabla SAP con MENOS clientes
# ---------------------------------------------------------------------------
def test_generate_route_sap_table_shrinks(base_file, cortes_config, baseline_sap_records, tmp_path):
    # Se conservan solo los clientes que la ruta necesita + unos pocos extra,
    # para que la reduccion sea real pero el VLOOKUP siga resolviendo.
    needed_codes = {
        "C11098", "C10641", "C10642", "C10615", "C10621", "C10755", "C10714", "C11069",
        "C10623", "C10616", "C11112", "C10614", "C10343", "C10620", "C11122", "C10644",
        "C11136", "C10896", "C11083", "C10943", "C10210", "C11035", "C10875", "C10761",
        "C10695", "C10692", "C11121", "C11120", "C11113", "C11024", "C10932", "C10981",
    }
    smaller_sap = [r for r in baseline_sap_records if r.codigo in needed_codes]
    assert len(smaller_sap) < len(baseline_sap_records)

    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=smaller_sap,
    )

    wb = openpyxl.load_workbook(output)
    ws_sap = wb["Hoja2"]
    assert ws_sap.max_row - 1 == len(smaller_sap)

    report = validators.validate_workbook(output, cortes_config, expected_sap_count=len(smaller_sap))
    assert report.ok, report.errors
    assert not report.warnings  # todos los codigos de la ruta siguen en Hoja2


# ---------------------------------------------------------------------------
# Test 8: Observacion vacia en un nuevo mes
# ---------------------------------------------------------------------------
def test_observacion_is_cleared_on_new_month(base_file, cortes_config, baseline_sap_records, tmp_path):
    # Se simula que el mes anterior SI tenia observaciones cargadas.
    wb = openpyxl.load_workbook(base_file)
    ws = wb["CORTES"]
    ws["F8"] = "Cliente no encontrado en local"
    ws["F10"] = "Pago parcial"
    wb.save(base_file)

    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
    )

    wb2 = openpyxl.load_workbook(output)
    ws2 = wb2["CORTES"]
    for row in range(8, 40):
        assert ws2[f"F{row}"].value in (None, ""), f"F{row} deberia estar vacio"

    report = validators.validate_workbook(output, cortes_config, expect_clear_observaciones=True)
    assert report.ok, report.errors


def test_edit_route_preserves_observaciones_of_untouched_clients(base_file, cortes_config, tmp_path):
    wb = openpyxl.load_workbook(base_file)
    ws = wb["CORTES"]
    ws["F8"] = "Revisar saldo"
    wb.save(base_file)

    output = tmp_path / "CORTES_editada.xlsx"
    edit_route(
        route_config=cortes_config,
        input_path=base_file,
        output_path=output,
        operations=[RemoveClient(codigo="C10981")],  # cliente al final, no afecta fila 8
    )

    wb2 = openpyxl.load_workbook(output)
    ws2 = wb2["CORTES"]
    assert ws2["F8"].value == "Revisar saldo"


# ---------------------------------------------------------------------------
# Test 9: el original nunca se modifica
# ---------------------------------------------------------------------------
def test_original_file_never_modified(base_file, cortes_config, baseline_sap_records, tmp_path):
    original_hash = file_hash(base_file)
    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
    )
    assert file_hash(base_file) == original_hash


def test_original_file_never_modified_even_on_edit(base_file, cortes_config, tmp_path):
    original_hash = file_hash(base_file)
    output = tmp_path / "editada.xlsx"
    edit_route(
        route_config=cortes_config,
        input_path=base_file,
        output_path=output,
        operations=[RemoveClient(codigo="C10981")],
    )
    assert file_hash(base_file) == original_hash


# ---------------------------------------------------------------------------
# Test 10: operacion invalida debe fallar de forma segura (sin dejar archivos)
# ---------------------------------------------------------------------------
def test_invalid_operation_fails_safely_and_leaves_no_output(base_file, cortes_config, baseline_sap_records, tmp_path):
    output = tmp_path / "CORTES octubre.xlsx"
    with pytest.raises(ClientNotFoundError):
        generate_route(
            route_config=cortes_config,
            base_path=base_file,
            output_path=output,
            new_date=date(2026, 10, 12),
            sap_records=baseline_sap_records,
            operations=[RemoveClient(codigo="C99999999")],
        )
    assert not output.exists()


def test_duplicate_add_fails_safely_and_leaves_no_output(base_file, cortes_config, baseline_sap_records, tmp_path):
    output = tmp_path / "CORTES octubre.xlsx"
    with pytest.raises(DuplicateClientError):
        generate_route(
            route_config=cortes_config,
            base_path=base_file,
            output_path=output,
            new_date=date(2026, 10, 12),
            sap_records=baseline_sap_records,
            operations=[AddClient(codigo="C10641")],  # ya existe
        )
    assert not output.exists()


def test_generate_route_refuses_to_overwrite_existing_output(base_file, cortes_config, baseline_sap_records, tmp_path):
    output = tmp_path / "CORTES octubre.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("archivo previo que no debe perderse")

    with pytest.raises(OutputExistsError):
        generate_route(
            route_config=cortes_config,
            base_path=base_file,
            output_path=output,
            new_date=date(2026, 10, 12),
            sap_records=baseline_sap_records,
        )
    assert output.read_text() == "archivo previo que no debe perderse"


def test_generate_route_missing_base_file_fails_safely(cortes_config, baseline_sap_records, tmp_path):
    with pytest.raises(FileNotFoundError):
        generate_route(
            route_config=cortes_config,
            base_path=tmp_path / "no_existe.xlsx",
            output_path=tmp_path / "out.xlsx",
            new_date=date(2026, 10, 12),
            sap_records=baseline_sap_records,
        )


# ---------------------------------------------------------------------------
# Extras: preservacion de formato/estructura
# ---------------------------------------------------------------------------
def test_formatting_is_preserved(base_file, cortes_config, baseline_sap_records, tmp_path):
    wb_before = openpyxl.load_workbook(base_file)
    ws_before = wb_before["CORTES"]
    col_widths_before = {c: d.width for c, d in ws_before.column_dimensions.items()}
    merges_before = set(str(r) for r in ws_before.merged_cells.ranges)

    sap = add_sap_record(baseline_sap_records, "C99010", "CLIENTE FORMATO NUEVO")
    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=sap,
        operations=[AddClient(codigo="C99010", after_codigo="C10621")],
    )

    wb_after = openpyxl.load_workbook(output)
    ws_after = wb_after["CORTES"]
    col_widths_after = {c: d.width for c, d in ws_after.column_dimensions.items()}
    merges_after = set(str(r) for r in ws_after.merged_cells.ranges)

    assert col_widths_after == col_widths_before
    assert merges_after == merges_before
    assert ws_after["A2"].value == "DISTRIBUCIONES COSPROBELL"
    assert ws_after["E3"].value == "DARWING "  # vendedor no se toca
    # numero de formato de moneda se mantiene en la columna de saldo de la fila nueva
    new_row = cortes_config.data_start_row + 5  # C10621 esta en indice 4; C99010 queda en indice 5
    assert ws_after[f"B{new_row}"].value == "C99010"
    assert "#,##0.00" in ws_after[f"E{new_row}"].number_format
    assert ws_after.row_dimensions[new_row].height == pytest.approx(21.0)


def test_growing_route_shifts_and_rewrites_totals_correctly(base_file, cortes_config, baseline_sap_records, tmp_path):
    sap = baseline_sap_records
    # C10001 ya existe en Hoja2 (viene del archivo real) pero no en la ruta.
    ops = [AddClient(codigo="C10001", at_start=True)]
    output = tmp_path / "CORTES octubre.xlsx"
    result = generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=sap,
        operations=ops,
    )
    assert result.client_count == 33
    wb = openpyxl.load_workbook(output)
    ws = wb["CORTES"]
    total_row = cortes_config.data_start_row + 33
    assert ws[f"D{total_row}"].value == "Total Saldo Ruta"
    assert ws[f"E{total_row}"].value == f"=SUM(E8:E{total_row - 1})"
    # no debe quedar basura en la vieja posicion del total
    old_total_row = cortes_config.data_start_row + 32
    assert ws[f"C{old_total_row}"].value != "Total Saldo Ruta"
