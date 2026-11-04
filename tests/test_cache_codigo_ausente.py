"""Un cliente de la ruta cuyo codigo aun no esta en la hoja de datos SAP no debe romper la generacion del Excel (datos 100 % sinteticos).

Antes, con nombre propio de ruta (celda de nombre literal) la inyeccion de valores calculados abortaba con "La celda C.. deberia contener
una formula"; y con plantillas IFERROR (YORO FOLDERS) se cacheaba #N/A donde Excel muestra el texto de reserva y un total que ignora la fila."""

from datetime import date

import openpyxl

from cxc_bot.config import load_route_config
from cxc_bot.excel_service import _iferror_fallback, compute_cached_values, generate_route
from cxc_bot.models import ClientEntry, SapClientRecord

SAP = {"C90001": ("UNO", 100.0), "C90002": ("DOS", 50.0)}


def test_fallback_de_iferror():
    assert _iferror_fallback('=IFERROR(VLOOKUP(B8,Hoja1!$A$2:$C$1400,2,FALSE),"REVISAR CÓDIGO SAP")') == "REVISAR CÓDIGO SAP"
    assert _iferror_fallback("=VLOOKUP(B8,Hoja2!A:C,2,0)") is None
    assert _iferror_fallback(None) is None


def test_plantilla_iferror_cachea_texto_y_el_total_ignora_la_fila():
    cfg = load_route_config("YORO FOLDERS")
    roster = [ClientEntry(codigo="C90001", folder="1 A"), ClientEntry(codigo="C99999", folder="2 A"), ClientEntry(codigo="C90002", folder="3 A")]
    v = compute_cached_values(cfg, roster, 11, SAP)
    assert v["C9"] == ("str", "REVISAR CÓDIGO SAP") and v["F9"] == ("str", "REVISAR CÓDIGO SAP")
    assert v["F11"] == ("n", 150.0)                      # SUM ignora el texto, igual que Excel


def test_plantilla_sin_iferror_mantiene_na_y_total_na():
    cfg = load_route_config("CORTES")
    roster = [ClientEntry(codigo="C90001"), ClientEntry(codigo="C99999")]
    v = compute_cached_values(cfg, roster, 10, SAP)
    assert v["C9"][0] == "e" and v["E9"][0] == "e" and v["E10"][0] == "e"


def test_nombre_literal_con_codigo_ausente_no_rompe_la_generacion(base_file, cortes_config, baseline_sap_records, tmp_path):
    codigos = [r.codigo for r in baseline_sap_records[:3]]
    roster = [ClientEntry(codigo=codigos[0]), ClientEntry(codigo="C99888", nombre_ruta="CLIENTE NUEVO AUN SIN SAP"), ClientEntry(codigo=codigos[1])]
    salida = tmp_path / "salida.xlsx"
    generate_route(route_config=cortes_config, base_path=base_file, output_path=salida, new_date=date(2026, 11, 3),
                   sap_records=baseline_sap_records, override_roster=roster)
    ws = openpyxl.load_workbook(salida, data_only=True)[cortes_config.main_sheet]
    fila = cortes_config.data_start_row + 1
    assert ws[f"{cortes_config.name_col}{fila}"].value == "CLIENTE NUEVO AUN SIN SAP"      # nombre propio de la ruta conservado
    assert ws[f"{cortes_config.saldo_col}{fila}"].value == "#N/A"                          # codigo ausente de SAP: visible, no inventado
