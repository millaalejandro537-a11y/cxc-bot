"""Anonimiza IN SITU los libros Excel versionados para que el repositorio no contenga datos de negocio reales.

Conserva la ESTRUCTURA que necesitan las pruebas (hojas, formulas, estilos, filas, codigos de cliente) y sustituye todo dato de
negocio por valores deterministas inventados. Es idempotente: ejecutarlo dos veces produce el mismo resultado.
    python tests/tools/anonimizar_fixtures.py [--comprobar] ARCHIVO.xlsx ...
Reglas:
  * Hoja de datos SAP (la de mas filas): nombre -> "CLIENTE SINTETICO <codigo>"; saldo -> magnitud inventada (0 sigue en 0 y se
    conserva el signo); "Activo desde" y direccion -> vacio.
  * Filas auxiliares de la hoja SAP (sin codigo en A): textos -> "TEXTO SINTETICO", numeros y fechas -> valores inventados.
  * Demas hojas: numeros > 1000 sin formula (facturas/saldos literales) se desplazan; textos libres (observaciones) -> "TEXTO SINTETICO".
    Se conservan las etiquetas estructurales, los codigos C##### y los textos que son nombres de ruta/vendedor de config/.
  * Metadatos: autor y ultimo editor -> "sintetico"; comentarios y propiedades personalizadas fuera.
Los codigos de cliente se conservan (identificadores que las pruebas citan)."""

from __future__ import annotations

import json
import re
import sys
import zlib
from datetime import datetime
from pathlib import Path

import openpyxl

RAIZ = Path(__file__).resolve().parents[2]
CODIGO = re.compile(r"C\d{4,6}", re.IGNORECASE)
SOLO_NUMEROS = re.compile(r"[\d\-\s,/y]+")
ESTRUCTURALES = {"", "#", "Codigo", "Credito", "ESTADO", "FECHA:", "NOMBRE DEL CLIENTE", "No. Factura", "Observacion",
                 "RUTA Y MOVIMIENTO DE FACTURAS", "Saldo", "TOTAL", "Total Saldo Ruta", "VENDEDOR", "DISTRIBUCIONES COSPROBELL"}
DESPLAZAMIENTO = 100000


def nombre_sintetico(codigo: str) -> str:
    return f"CLIENTE SINTETICO {codigo}"


def saldo_sintetico(codigo: str, original) -> float:
    if not original:
        return 0
    magnitud = 100 + (zlib.crc32(codigo.encode()) % 900000) / 100
    return round(magnitud if original > 0 else -magnitud, 2)


def _etiquetas_config() -> set:
    etiquetas: set = set()

    def recorrer(o):
        if isinstance(o, dict):
            for v in o.values():
                recorrer(v)
        elif isinstance(o, list):
            for v in o:
                recorrer(v)
        elif isinstance(o, str):
            etiquetas.add(o.strip().upper())
    for nombre in ("routes.json", "vendor_catalog.json"):
        recorrer(json.loads((RAIZ / "config" / nombre).read_text(encoding="utf-8")))
    return etiquetas


def _desplazar(texto: str) -> str:
    return re.sub(r"\d+", lambda m: str(int(m.group()) + DESPLAZAMIENTO) if int(m.group()) < DESPLAZAMIENTO else m.group(), texto)


def _auxiliar(c, v):
    if isinstance(v, bool):
        return v
    if isinstance(v, datetime):
        return datetime(2026, 1, 1)
    if isinstance(v, (int, float)):
        return 100 + zlib.crc32(f"{c.row}:{c.column}".encode()) % 9000
    if isinstance(v, str):
        return v if v.strip() in ESTRUCTURALES else "TEXTO SINTETICO"
    return v


def anonimizar(wb: "openpyxl.Workbook") -> None:
    etiquetas = _etiquetas_config()
    sap = max(wb.worksheets, key=lambda w: w.max_row)
    for ws in wb.worksheets:
        for fila in ws.iter_rows():
            for c in fila:
                v = c.value
                if c.comment is not None:
                    c.comment = None
                if v is None or (isinstance(v, str) and v.startswith("=")):
                    continue
                if ws is sap:
                    codigo = ws.cell(c.row, 1).value
                    if not (isinstance(codigo, str) and CODIGO.fullmatch(codigo)):
                        if c.row > 1:  # filas auxiliares de la hoja SAP (detalle sin codigo en A): nada real se conserva
                            c.value = _auxiliar(c, v)
                        continue
                    if c.column == 2:
                        c.value = nombre_sintetico(codigo)
                    elif c.column == 3 and isinstance(v, (int, float)):
                        c.value = saldo_sintetico(codigo, v)
                    elif c.column in (4, 5):
                        c.value = None
                elif isinstance(v, str):
                    s = v.strip()
                    if s in ESTRUCTURALES or CODIGO.fullmatch(s) or s.upper() in etiquetas:
                        continue
                    c.value = _desplazar(v) if SOLO_NUMEROS.fullmatch(s) else "TEXTO SINTETICO"
                elif isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) > 1000 and abs(v) < DESPLAZAMIENTO:
                    c.value = v + DESPLAZAMIENTO
    wb.properties.creator = wb.properties.lastModifiedBy = "sintetico"
    wb.properties.title = wb.properties.subject = wb.properties.description = wb.properties.keywords = wb.properties.category = None
    wb.properties.modified = datetime(2026, 1, 1)
    wb.properties.created = datetime(2026, 1, 1)


def main(argv) -> int:
    comprobar = "--comprobar" in argv
    archivos = [Path(a) for a in argv if not a.startswith("--")]
    for ruta in archivos:
        wb = openpyxl.load_workbook(ruta)
        anonimizar(wb)
        if not comprobar:
            wb.save(ruta)
        print(f"{'revisado' if comprobar else 'anonimizado'}: {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
