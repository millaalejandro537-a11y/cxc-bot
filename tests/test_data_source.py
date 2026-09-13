import csv

import openpyxl
import pytest

from cxc_bot.data_source import LocalMockDataSource


def test_local_mock_data_source_reads_xlsx_and_filters_by_c(tmp_path):
    path = tmp_path / "sap_mock.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Código SN", "Nombre SN", "Saldo de cuenta", "Dirección de factura GTS", "Activo desde", "Contabilización automática"])
    ws.append(["C10001", "CLIENTE UNO", 100.5, None, None, "No"])
    ws.append(["C10002", "CLIENTE DOS", 0, None, None, "No"])
    ws.append(["X99999", "PROVEEDOR (no es cliente)", 500, None, None, "Si"])
    ws.append([None, None, None, None, None, None])  # fila vacia, debe ignorarse
    wb.save(path)

    records = LocalMockDataSource(path).fetch_clients()
    assert [r.codigo for r in records] == ["C10001", "C10002"]
    assert records[0].nombre == "CLIENTE UNO"
    assert records[0].saldo == 100.5


def test_local_mock_data_source_reads_csv(tmp_path):
    path = tmp_path / "sap_mock.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Codigo SN", "Nombre SN", "Saldo de cuenta", "Direccion", "Activo desde", "Contab"])
        writer.writerow(["C20001", "CLIENTE CSV", "250.75", "", "", ""])

    records = LocalMockDataSource(path).fetch_clients()
    assert len(records) == 1
    assert records[0].codigo == "C20001"
    assert records[0].saldo == 250.75


def test_local_mock_data_source_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        LocalMockDataSource(tmp_path / "no_existe.xlsx")


def test_local_mock_data_source_handles_variable_row_count(tmp_path):
    """El motor nunca debe asumir una cantidad fija de filas."""
    path = tmp_path / "sap_mock_big.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Codigo", "Nombre", "Saldo", "Dir", "Activo", "Contab"])
    for i in range(3000):
        ws.append([f"C{i:05d}", f"CLIENTE {i}", float(i), None, None, None])
    wb.save(path)

    records = LocalMockDataSource(path).fetch_clients()
    assert len(records) == 3000
