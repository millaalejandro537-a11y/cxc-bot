"""Validaciones automaticas post-generacion (seccion 10 del encargo).

Cada funcion revisa un aspecto puntual y lanza ValidationError con un mensaje
claro si algo no cuadra. `validate_all` corre todas y junta los errores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import openpyxl

from .excel_service import find_block_end, read_roster
from .models import RouteConfig


class ValidationError(Exception):
    pass


@dataclass
class ValidationReport:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_workbook(
    path: Path,
    config: RouteConfig,
    expected_date: Optional[date] = None,
    expected_client_count: Optional[int] = None,
    expect_clear_observaciones: bool = True,
    expected_sap_count: Optional[int] = None,
    folder_mode: bool = False,
    nombres_literales: Optional[dict] = None,
) -> ValidationReport:
    errors: List[str] = []
    warnings: List[str] = []

    wb = openpyxl.load_workbook(path, data_only=False)

    if config.main_sheet not in wb.sheetnames:
        errors.append(f"Falta la hoja principal '{config.main_sheet}'.")
    if config.data_sheet not in wb.sheetnames:
        errors.append(f"Falta la hoja de datos SAP '{config.data_sheet}'.")
    if errors:
        return ValidationReport(ok=False, errors=errors, warnings=warnings)

    ws_main = wb[config.main_sheet]
    ws_sap = wb[config.data_sheet]

    # --- Estructura: fecha ---
    date_val = ws_main[config.fecha_cell].value
    if date_val is None:
        errors.append(f"La celda de fecha {config.fecha_cell} esta vacia.")
    elif expected_date is not None:
        got = date_val.date() if isinstance(date_val, datetime) else date_val
        if got != expected_date:
            errors.append(f"Fecha en {config.fecha_cell} es {got}, se esperaba {expected_date}.")

    # --- Estructura: encabezados de la tabla principal ---
    expected_headers = {
        config.num_col: "Folder" if folder_mode else "#",
        config.code_col: "Codigo",
        config.factura_col: "No. Factura",
        config.obs_col: "Observacion",
    }
    for col, expected_text in expected_headers.items():
        actual = ws_main[f"{col}{config.header_row}"].value
        if actual is None or expected_text.lower() not in str(actual).lower():
            warnings.append(
                f"Encabezado en {col}{config.header_row} es {actual!r}, se esperaba algo como {expected_text!r}."
            )

    # --- Clientes: roster ---
    roster = read_roster(ws_main, config)
    total_row = find_block_end(ws_main, config)

    codes = [c.codigo for c in roster]
    if len(codes) != len(set(codes)):
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        errors.append(f"Codigos de cliente duplicados en la ruta: {dupes}")

    invalid_codes = [c for c in codes if not c or not c.upper().startswith("C")]
    if invalid_codes:
        errors.append(f"Codigos de cliente invalidos (no inician con 'C'): {invalid_codes}")

    if folder_mode:
        # Folder fisico por zona ("<n> <PREFIJO>"): unicos; se permiten huecos (un cliente retirado no se renumera).
        import re as _re
        vistos = set()
        for row in range(config.data_start_row, total_row):
            num_val = ws_main[f"{config.num_col}{row}"].value
            if not isinstance(num_val, str) or not _re.fullmatch(r"\d+ [A-Za-z]{1,4}", num_val.strip()):
                errors.append(f"Folder invalido en fila {row}: {num_val!r} (se esperaba '<n> <zona>', ej. '1 LV').")
            elif num_val.strip().upper() in vistos:
                errors.append(f"Folder duplicado en fila {row}: {num_val!r}.")
            else:
                vistos.add(num_val.strip().upper())
    else:
        for i, row in enumerate(range(config.data_start_row, total_row), start=1):
            num_val = ws_main[f"{config.num_col}{row}"].value
            if num_val != i:
                errors.append(f"Numeracion no consecutiva en fila {row}: es {num_val}, se esperaba {i}.")

    if expected_client_count is not None and len(roster) != expected_client_count:
        errors.append(f"Se esperaban {expected_client_count} clientes y hay {len(roster)}.")

    # --- Formulas ---
    for row in range(config.data_start_row, total_row):
        name_formula = ws_main[f"{config.name_col}{row}"].value
        saldo_formula = ws_main[f"{config.saldo_col}{row}"].value
        literal_ok = (nombres_literales or {}).get(str(ws_main[f"{config.code_col}{row}"].value).strip().upper())
        for label, formula in (("nombre", name_formula), ("saldo", saldo_formula)):
            if label == "nombre" and literal_ok is not None:  # nombre propio de la ruta (distinto al de SAP): debe ser exactamente ese literal
                if formula != literal_ok:
                    errors.append(f"Fila {row}: el nombre propio de la ruta es {formula!r}, se esperaba {literal_ok!r}.")
                continue
            if not isinstance(formula, str) or not formula.startswith("="):
                errors.append(f"Fila {row}: la formula de {label} no existe o no es formula ({formula!r}).")
                continue
            if "#REF!" in formula:
                errors.append(f"Fila {row}: la formula de {label} contiene #REF! -> {formula}")
            if config.data_sheet not in formula:
                errors.append(f"Fila {row}: la formula de {label} no apunta a '{config.data_sheet}' -> {formula}")
            expected_row_ref = f"{config.code_col}{row}"
            if expected_row_ref not in formula:
                errors.append(
                    f"Fila {row}: la formula de {label} no referencia su propia fila ({expected_row_ref}) -> {formula}"
                )

    total_formula = ws_main[f"{config.total_value_col}{total_row}"].value
    if isinstance(total_formula, str) and "#REF!" in total_formula:
        errors.append(f"La formula de totales contiene #REF!: {total_formula}")

    # --- Valores calculados (cache) ---
    # No basta con que la formula exista: hay que comprobar que el archivo
    # trae el resultado ya calculado, que es lo que cualquier programa
    # muestra al abrirlo sin forzar un recalculo.
    wb_values = openpyxl.load_workbook(path, data_only=True)
    ws_main_values = wb_values[config.main_sheet]
    computed_total = 0.0
    total_has_error = False
    for row in range(config.data_start_row, total_row):
        name_val = ws_main_values[f"{config.name_col}{row}"].value
        saldo_val = ws_main_values[f"{config.saldo_col}{row}"].value
        codigo = ws_main[f"{config.code_col}{row}"].value
        if name_val is None or (isinstance(name_val, str) and name_val.strip() == ""):
            errors.append(f"Fila {row} ({codigo}): el nombre no tiene un valor calculado (formula sin resultado).")
        elif isinstance(name_val, str) and name_val.strip().upper() in ("#N/A", "#REF!", "#VALUE!", "#NAME?"):
            errors.append(f"Fila {row} ({codigo}): el nombre calculado es un error ({name_val}).")
        if saldo_val is None:
            errors.append(f"Fila {row} ({codigo}): el saldo no tiene un valor calculado (formula sin resultado).")
        elif isinstance(saldo_val, str):
            errors.append(f"Fila {row} ({codigo}): el saldo calculado es un error o texto ({saldo_val}), se esperaba un numero.")
            total_has_error = True
        elif isinstance(saldo_val, (int, float)):
            computed_total += saldo_val

    total_val = ws_main_values[f"{config.total_value_col}{total_row}"].value
    if len(roster) > 0:
        if total_val is None:
            errors.append("El total de la ruta no tiene un valor calculado (formula sin resultado).")
        elif isinstance(total_val, str):
            errors.append(f"El total calculado de la ruta es un error ({total_val}).")
        elif isinstance(total_val, (int, float)) and not total_has_error:
            if abs(total_val - computed_total) > 0.01:
                errors.append(
                    f"El total calculado ({total_val}) no coincide con la suma de los saldos calculados ({computed_total})."
                )
    wb_values.close()

    # --- Observacion ---
    if expect_clear_observaciones:
        non_empty = [
            (row, ws_main[f"{config.obs_col}{row}"].value)
            for row in range(config.data_start_row, total_row)
            if ws_main[f"{config.obs_col}{row}"].value not in (None, "")
        ]
        if non_empty:
            errors.append(f"Observacion deberia quedar vacia pero tiene datos en: {non_empty}")

    # --- Hoja2 ---
    sap_header = [ws_sap.cell(row=1, column=c).value for c in range(1, 7)]
    if sap_header[0] is None or "digo" not in str(sap_header[0]).lower():
        warnings.append(f"Encabezado de Hoja2 inesperado: {sap_header}")

    sap_row_count = max(ws_sap.max_row - 1, 0)
    if expected_sap_count is not None and sap_row_count != expected_sap_count:
        errors.append(f"Hoja2 deberia tener {expected_sap_count} filas de datos y tiene {sap_row_count}.")

    sap_codes = set()
    for row in range(2, ws_sap.max_row + 1):
        v = ws_sap.cell(row=row, column=1).value
        if v is not None:
            sap_codes.add(str(v).strip().upper())  # VLOOKUP no distingue mayusculas/minusculas

    missing_in_sap = [c for c in codes if c.upper() not in sap_codes]
    if missing_in_sap:
        warnings.append(
            f"Estos codigos de la ruta no existen en Hoja2 (el VLOOKUP dara #N/A): {missing_in_sap}"
        )

    wb.close()
    return ValidationReport(ok=len(errors) == 0, errors=errors, warnings=warnings)


def validate_vinetas(path: Path, config: RouteConfig, esperado: Dict[str, str], col: Optional[str] = None) -> ValidationReport:
    """Comprueba que cada cliente con color esperado ({codigo: hex}) tiene EXACTAMENTE ese relleno en la celda de viñeta y que ningun otro
    cliente quedo con un color de viñeta (nada pintado por error ni color de credito inventado)."""
    from .route_markers import HEX, contraste, contraste_minimo
    errors: List[str] = []
    ws = openpyxl.load_workbook(path, data_only=False)[config.main_sheet]
    col = col or config.num_col
    code_col = config.code_col
    esperado = {str(k).upper(): "FF" + v.upper().lstrip("#") for k, v in esperado.items()}
    colores_vineta = {"FF" + h for h in HEX.values()}
    row = config.data_start_row
    while ws[f"{code_col}{row}"].value not in (None, ""):
        codigo = str(ws[f"{code_col}{row}"].value).strip().upper()
        f = ws[f"{col}{row}"].fill
        actual = f.fgColor.rgb if (f is not None and f.fill_type == "solid" and isinstance(f.fgColor.rgb, str)) else None
        if codigo in esperado:
            if actual != esperado[codigo]:
                errors.append(f"Fila {row} ({codigo}): la viñeta debería ser {esperado[codigo][2:]} y es {actual[2:] if actual else 'sin color'}.")
            else:   # legibilidad del texto de la celda sobre el relleno (sin cambiar la fuente: si no alcanza, se informa)
                c = ws[f"{col}{row}"]
                col_txt = c.font.color.rgb[2:] if (c.font.color is not None and c.font.color.type == "rgb" and isinstance(c.font.color.rgb, str)) else "000000"
                minimo = contraste_minimo(float(c.font.sz or 11), bool(c.font.b))
                if contraste(esperado[codigo][2:], col_txt) < minimo:
                    errors.append(f"Fila {row} ({codigo}): contraste insuficiente del texto {col_txt} sobre {esperado[codigo][2:]} "
                                  f"({contraste(esperado[codigo][2:], col_txt):.1f}:1; mínimo {minimo}:1 para {c.font.sz or 11} pt).")
        elif actual in colores_vineta:
            errors.append(f"Fila {row} ({codigo}): tiene color de viñeta {actual[2:]} sin que el motor lo haya determinado.")
        row += 1
    return ValidationReport(ok=not errors, errors=errors, warnings=[])


def assert_valid(report: ValidationReport) -> None:
    if not report.ok:
        raise ValidationError("Validacion fallida:\n- " + "\n- ".join(report.errors))
