from datetime import date

import openpyxl

from cxc_bot import payment_semaphore as ps
from cxc_bot.sap_exports import cargar_consultas

H1 = ["#", "CodigoCliente", "NombreCliente", "FechaAltaCliente", "IdFactura", "NumeroFactura", "FechaFactura", "FechaVencimiento",
      "MontoFactura", "ImporteAplicado", "Estado", "Moneda"]
H2 = ["#", "IdConciliacion", "FechaConciliacion", "TipoConciliacion", "Linea", "CodigoCliente", "TipoDocumento", "IdDocumento",
      "MontoConciliado", "DebeHaber", "NumeroRecibo", "FechaRecibo"]
H3 = ["#", "CodigoCliente", "IdFactura", "NumeroFactura", "FechaVencimiento", "IdRecibo", "NumeroRecibo", "FechaContableRecibo", "MontoAplicado"]


def _xlsx(path, head, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(head)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _datos(tmp_path):
    d = tmp_path / "02_SAP_08Oct2026"
    d.mkdir()
    _xlsx(d / "consulta 1.xlsx", H1, [
        [1, "99999", "RECIBOS NULOS", "12/08/2022", None, None, None, None, 0.0, 0.0],
        [2, "C1", "CLIENTE UNO", "01/01/2024", 9101, 3000001, "13/01/2024", "12/03/2024", 900.0, 900.0, "C", "LPS"],
        [3, "C1", "CLIENTE UNO", "01/01/2024", 9102, 3000002, "04/03/2024", "27/04/2024", 700.0, 700.0, "C", "LPS"],
        [4, "C1", "CLIENTE UNO", "01/01/2024", 9103, 3000003, "06/11/2024", "06/11/2024", 1500.0, 1500.0, "C", "LPS"],
        [5, "C1", "CLIENTE UNO", "01/01/2024", 9104, 3000004, "01/02/2026", "03/03/2026", 600.0, 0.0, "O", "LPS"],
        [6, "C2", "OTRO", "01/01/2024", 9000, 1, "01/01/2024", "01/02/2024", 100.0, 100.0, "C", "LPS"],
    ])
    _xlsx(d / "consulta 3.xlsx", H3, [
        [1, "C1", 9101, 3000001, "12/03/2024", 8101, 8101, "20/01/2024", 400.0],
        [2, "C1", 9101, 3000001, "12/03/2024", 8102, 8102, "04/03/2024", 250.0],
        [3, "C1", 9102, 3000002, "27/04/2024", 8104, 8104, "27/03/2024", 700.0],
    ])
    _xlsx(d / "consulta 2.xlsx", H2, [
        [1, 9139, "04/03/2024", "0", None, "C1", "13", 9101, 250.0, "D"],
        [2, 9139, "04/03/2024", "0", 1, "C1", "24", 8103, 250.0, "C", 8103, "02/02/2024"],   # recibo que consulta 3 no lista
        [3, 9140, "04/03/2024", "3", None, "C1", "13", 9101, 250.0, "D"],
        [4, 9140, "04/03/2024", "3", 1, "C1", "24", 8102, 250.0, "C", 8102, "04/03/2024"],   # duplicado de consulta 3
        [5, 9557, "27/03/2024", "3", None, "C1", "13", 9102, 700.0, "D"],
        [6, 9557, "27/03/2024", "3", 1, "C1", "24", 8104, 700.0, "C", 8104, "27/03/2024"],
        [7, 5, "06/11/2024", "5", None, "C1", "13", 9103, 1500.0, "D"],
        [8, 5, "06/11/2024", "5", 1, "C1", "14", 123, 1500.0, "C"],                           # nota de credito: NO es recibo
    ])
    return tmp_path


def test_carga_recibos_de_consulta_3_y_conciliaciones_de_consulta_2_sin_duplicar(tmp_path):
    datos = cargar_consultas(_datos(tmp_path), {"C1"})
    assert "C2" not in datos.facturas and "99999" not in datos.facturas
    f = {x.numero: x for x in datos.facturas["C1"]}
    rec = {r.numero: r for r in f["3000001"].recibos}
    assert set(rec) == {"8101", "8102", "8103"} and sum(r.monto for r in rec.values()) == 900.0
    assert rec["8103"].via == "consulta 2" and rec["8102"].via == "consulta 3"


def test_saldo_abierto_y_nombre_vienen_de_consulta_1(tmp_path):
    datos = cargar_consultas(_datos(tmp_path), {"C1"})
    assert datos.saldo_abierto == {"C1": 600.0} and datos.nombres["C1"] == "CLIENTE UNO"


def test_nota_de_credito_no_cuenta_como_pago_y_el_motor_la_excluye_de_la_media(tmp_path):
    datos = cargar_consultas(_datos(tmp_path), {"C1"})
    r = ps.evaluar_cliente("C1", datos.facturas["C1"], date(2026, 10, 8), 60)
    est = {f.numero: f for f in r.facturas}
    assert est["3000003"].estado == ps.CERRADA_NO_ACREDITADA and not est["3000003"].recibos
    assert est["3000001"].estado == ps.PAGADA and est["3000001"].ultimo_recibo == date(2024, 3, 4)
    assert est["3000004"].estado == ps.ABIERTA and est["3000004"].mora == 189 and r.alertas
