"""Lector de la LISTA de una ruta desde un Excel operativo (solo lectura): folder/numero, codigo, nombre.

Sirve para dar de alta rutas nuevas (p. ej. Occidente) sin tocar codigo. Si el Excel no trae la columna Folder (archivos
historicos con '#'), los folders se numeran por zona deducida del nombre ENTRE PARENTESIS solo si se indica un mapa;
si no, quedan como '<n> GEN' para que el administrador los ajuste: nunca se inventan zonas.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Dict, List

import openpyxl


def _n(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def leer_lista_ruta(path: Path) -> List[Dict[str, str]]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    cols: Dict[str, int] = {}
    out: List[Dict[str, str]] = []
    n = 0
    for row in ws.iter_rows(values_only=True):
        if not cols:
            heads = [_n(c) for c in row]
            if any(h.startswith("COD") for h in heads) and any(h.startswith("NOMBRE") for h in heads):
                for i, h in enumerate(heads):
                    if h.startswith("COD"):
                        cols["codigo"] = i
                    elif h.startswith("NOMBRE"):
                        cols["nombre"] = i
                    elif h in ("FOLDER", "#"):
                        cols["folder"] = i
            continue
        code = str(row[cols["codigo"]] or "").strip().upper()
        if not re.fullmatch(r"C\d{3,8}", code):
            if out:
                break
            continue
        n += 1
        folder = row[cols["folder"]] if "folder" in cols and row[cols["folder"]] is not None else n
        folder = str(folder).strip()
        if re.fullmatch(r"\d+", folder):
            folder = f"{folder} GEN"
        out.append({"codigo": code, "folder": folder, "nombre": str(row[cols["nombre"]] or "").strip()})
    return out
