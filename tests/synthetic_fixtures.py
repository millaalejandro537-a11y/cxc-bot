"""Libro base SINTETICO equivalente a CORTES septiembre, para que la suite no dependa de datos SAP privados.

Parte de la ESTRUCTURA del libro ya versionado en `samples/CORTES septiembre.xlsx` (portada, formulas, estilos, tabla Hoja2) y
sustituye todo dato de negocio por valores deterministas inventados:
  * Hoja2 nombre   -> "CLIENTE SINTETICO <codigo>"
  * Hoja2 saldo    -> magnitud inventada (0 sigue siendo 0; el signo se conserva)
  * Hoja2 'Activo desde' -> vacio; metadatos del archivo (autor) -> "sintetico"
  * Portada No. Factura -> cada numero se desplaza +100000 (se conserva el formato "a-b")
Los codigos de cliente se conservan (son identificadores que las pruebas citan). Nada de lo generado contiene nombres ni importes reales.
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path

import openpyxl

SOURCE = Path(__file__).resolve().parent.parent / "samples" / "CORTES septiembre.xlsx"


def nombre_sintetico(codigo: str) -> str:
    return f"CLIENTE SINTETICO {codigo}"


def saldo_sintetico(codigo: str, original) -> float:
    if not original:
        return 0
    magnitud = 100 + (zlib.crc32(codigo.encode()) % 900000) / 100
    return round(magnitud if original > 0 else -magnitud, 2)


def _facturas(valor):
    if valor is None:
        return None
    return re.sub(r"\d+", lambda m: str(int(m.group()) + 100000), str(valor))


def build_cortes_base(dest: Path) -> Path:
    wb = openpyxl.load_workbook(SOURCE)
    cover, sap = wb["CORTES"], wb["Hoja2"]
    for row in range(8, cover.max_row + 1):
        if cover.cell(row, 2).value:
            cover.cell(row, 4).value = _facturas(cover.cell(row, 4).value)
    for row in range(2, sap.max_row + 1):
        codigo = sap.cell(row, 1).value
        if not codigo:
            continue
        sap.cell(row, 2).value = nombre_sintetico(codigo)
        sap.cell(row, 3).value = saldo_sintetico(codigo, sap.cell(row, 3).value or 0)
        sap.cell(row, 5).value = None  # "Activo desde": fecha real de alta del cliente
    wb.properties.creator = wb.properties.lastModifiedBy = "sintetico"
    wb.save(dest)
    return dest
