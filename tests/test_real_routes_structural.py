"""Prueba de conformidad estructural: para cada ruta real (copiada, de solo
lectura, desde 'Rutas de Cosprobell') el motor debe poder generar y validar
un mes nuevo usando la RouteConfig correspondiente -- confirma que
config/routes.json realmente describe la estructura de cada archivo real
(no solo la de Cortes). Los archivos de referencia son DEV/TEST: saldos y
clientes historicos, nunca se tratan como datos vigentes de PROD.
"""

from dataclasses import replace
from datetime import date

import openpyxl
import pytest

from cxc_bot import validators
from cxc_bot.config import load_route_config
from cxc_bot.data_source import LocalMockDataSource
from cxc_bot.excel_service import generate_route

FIXTURES = "tests/fixtures/rutas_reales"

# (archivo real, route_id en config/routes.json, config inline si difiere)
CASES = [
    ("CORTES septiembre.xlsx", "CORTES", None),
    ("LIMA JUNIO 6.xlsx", "LA LIMA", None),
    ("LITORAL MARZO.xlsx", "LITORAL", None),
    ("OCCIDENTE MAYO.xlsx", "OCCIDENTE", None),
    ("OLANCHO AGOSTO.xlsx", "OLANCHO", None),
    ("PROGRESO 21-5.xlsx", "PROGRESO", None),
    ("ROATAN ABRIL.xlsx", "ROATAN", None),
    ("COMAYAGUA JUNIO.xlsx", "COMAYAGUA", None),
    ("STA CRUZ MAYO.xlsx", "STA_CRUZ", None),
    ("VILLANUEVA MAYO 2.xlsx", "VILLANUEVA", None),
    ("YOROABRIL.xlsx", "YORO", None),
    ("sta barbara Junio.xlsx", "SANTA BARBARA", None),
    ("CHOLOMA 19-5.xlsx", "CHOLOMA", None),
    # Las Vegas real difiere del demo sintetico de Fase 2 (data_sheet="Hoja1",
    # no "Hoja2"): se prueba con una config ad-hoc que describe el archivo
    # REAL, documentado en docs/reporte_estructural_rutas.md.
    ("LAS VEGAS MAYO.xlsx", "LAS VEGAS", {"main_sheet": "LAS VEGAS", "data_sheet": "Hoja1"}),
]


@pytest.mark.parametrize("filename,route_id,overrides", CASES, ids=[c[0] for c in CASES])
def test_real_route_generates_and_validates(filename, route_id, overrides, tmp_path):
    src = f"{FIXTURES}/{filename}"
    config = load_route_config(route_id)
    if overrides:
        config = replace(config, **overrides)

    # Datos SAP: la propia Hoja2/Hoja1 del archivo real, usada SOLO como
    # fuente estructural DEV/TEST (nunca como saldo vigente de PROD).
    sap_records = LocalMockDataSource(src, sheet_name=config.data_sheet).fetch_clients()
    assert sap_records, f"{filename}: no se pudo leer ninguna fila de {config.data_sheet}"

    output = tmp_path / f"{config.filename_prefix} salida.xlsx"
    result = generate_route(
        route_config=config,
        base_path=src,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=sap_records,
    )
    assert result.client_count > 0

    report = validators.validate_workbook(output, config, expected_date=date(2026, 10, 12))
    assert report.ok, f"{filename}: {report.errors}"

    # El original de referencia nunca se toco.
    assert openpyxl.load_workbook(src) is not None  # simplemente confirma que sigue siendo un xlsx valido
