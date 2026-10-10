from datetime import date

import openpyxl

from cxc_bot import payment_semaphore as ps
from cxc_portal.sap_loader import cargar_consultas

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
        [2, "C1", "CLIENTE UNO", "01/01/2024", 5572, 1007127, "13/01/2024", "12/03/2024", 2055.0, 2055.0, "C", "LPS"],
        [3, "C1", "CLIENTE UNO", "01/01/2024", 6027, 1007566, "04/03/2024", "27/04/2024", 630.0, 630.0, "C", "LPS"],
        [4, "C1", "CLIENTE UNO", "01/01/2024", 7000, 1010747, "06/11/2024", "06/11/2024", 5750.0, 5750.0, "C", "LPS"],
        [5, "C1", "CLIENTE UNO", "01/01/2024", 8000, 1015529, "20/12/2025", "19/01/2026", 930.0, 0.0, "O", "LPS"],
        [6, "C2", "OTRO", "01/01/2024", 9000, 1, "01/01/2024", "01/02/2024", 100.0, 100.0, "C", "LPS"],
    ])
    _xlsx(d / "consulta 3.xlsx", H3, [
        [1, "C1", 5572, 1007127, "12/03/2024", 6374, 6374, "20/01/2024", 1055.0],
        [2, "C1", 5572, 1007127, "12/03/2024", 6850, 6850, "04/03/2024", 500.0],
        [3, "C1", 6027, 1007566, "27/04/2024", 7067, 7067, "27/03/2024", 630.0],
    ])
    _xlsx(d / "consulta 2.xlsx", H2, [
        [1, 9139, "04/03/2024", "0", None, "C1", "13", 5572, 500.0, "D"],
        [2, 9139, "04/03/2024", "0", 1, "C1", "24", 6509, 500.0, "C", 6509, "02/02/2024"],   # recibo que consulta 3 no lista
        [3, 9140, "04/03/2024", "3", None, "C1", "13", 5572, 500.0, "D"],
        [4, 9140, "04/03/2024", "3", 1, "C1", "24", 6850, 500.0, "C", 6850, "04/03/2024"],   # duplicado de consulta 3
        [5, 9557, "27/03/2024", "3", None, "C1", "13", 6027, 630.0, "D"],
        [6, 9557, "27/03/2024", "3", 1, "C1", "24", 7067, 630.0, "C", 7067, "27/03/2024"],
        [7, 5, "06/11/2024", "5", None, "C1", "13", 7000, 5750.0, "D"],
        [8, 5, "06/11/2024", "5", 1, "C1", "14", 123, 5750.0, "C"],                           # nota de credito: NO es recibo
    ])
    return tmp_path


def test_carga_recibos_de_consulta_3_y_conciliaciones_de_consulta_2_sin_duplicar(tmp_path):
    datos = cargar_consultas(_datos(tmp_path), {"C1"})
    assert "C2" not in datos.facturas and "99999" not in datos.facturas
    f = {x.numero: x for x in datos.facturas["C1"]}
    rec = {r.numero: r for r in f["1007127"].recibos}
    assert set(rec) == {"6374", "6850", "6509"} and sum(r.monto for r in rec.values()) == 2055.0
    assert rec["6509"].via == "consulta 2" and rec["6850"].via == "consulta 3"


def test_saldo_abierto_y_nombre_vienen_de_consulta_1(tmp_path):
    datos = cargar_consultas(_datos(tmp_path), {"C1"})
    assert datos.saldo_abierto == {"C1": 930.0} and datos.nombres["C1"] == "CLIENTE UNO"


def test_nota_de_credito_no_cuenta_como_pago_y_el_motor_la_excluye_de_la_media(tmp_path):
    datos = cargar_consultas(_datos(tmp_path), {"C1"})
    r = ps.evaluar_cliente("C1", datos.facturas["C1"], date(2026, 10, 8), 60)
    est = {f.numero: f for f in r.facturas}
    assert est["1010747"].estado == ps.CERRADA_NO_ACREDITADA and not est["1010747"].recibos
    assert est["1007127"].estado == ps.PAGADA and est["1007127"].ultimo_recibo == date(2024, 3, 4)
    assert est["1015529"].estado == ps.ABIERTA and est["1015529"].mora == 232 and r.alertas
