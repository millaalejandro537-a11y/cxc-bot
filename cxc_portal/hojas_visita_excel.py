"""Lector del documento aprobado de HOJAS DE VISITA (Excel de varias hojas 'Hoja 01'..'Hoja NN'). Solo lectura.

Extrae, por hoja, los clientes en el orden del documento con su color TAL COMO figura. Los colores de Occidente (VERDE, AMARILLA,
AZUL, BLANCA, CANDIDATO, REVISAR) son PRELIMINARES: 'CANDIDATO' (rojo claro) es un candidato a solo contado, NO una decision de gerencia.
No se inventan zonas ni folders: el documento no los trae.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

import openpyxl

COLORES = {"VERDE", "AMARILLA", "AZUL", "BLANCA", "CANDIDATO", "REVISAR", "ROJA", "ROJO"}   # ROJO = solo contado (resultado del sistema, regla vigente desde 2026-10-12)
_CODIGO = re.compile(r"^C\d{3,8}$")


def _n(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def _zona_del_nombre(nombre: str) -> str:
    m = re.search(r"[\(\[]([^\)\]]+)[\)\]]?\s*$", nombre or "")
    return m.group(1).strip() if m else ""


def leer_hojas_visita(path: Path) -> Dict[str, Any]:
    wb = openpyxl.load_workbook(path, data_only=True)
    hojas: List[dict] = []
    advertencias: List[str] = []
    vistos: Dict[str, str] = {}
    orden = 0
    for ws in wb.worksheets:
        filas = list(ws.iter_rows(values_only=True))
        cab = next((i for i, r in enumerate(filas) if r and _n(r[0]) == "COLOR" and len(r) > 2 and "CODIGO" in _n(r[1])), None)
        if cab is None:
            advertencias.append(f"{ws.title}: sin encabezado COLOR / CÓDIGO SAP; se ignora.")
            continue
        titulo = " ".join(str(c) for c in (filas[1][:1] if len(filas) > 1 else []) if c)
        clientes = []
        for r in filas[cab + 1:]:
            if not r or r[0] is None:
                continue
            color, codigo = _n(r[0]), str(r[1] or "").strip().upper()
            if not _CODIGO.match(codigo):
                break  # pie del documento (resumen, firmas)
            if color not in COLORES:   # un color nuevo NUNCA corta la lectura en silencio: el cliente se conserva y se advierte
                advertencias.append(f"{codigo} ({ws.title}): color '{color}' no reconocido; se conserva tal como figura.")
            orden += 1
            nombre = str(r[2] or "").strip()
            if codigo in vistos:
                advertencias.append(f"{codigo} aparece en {vistos[codigo]} y en {ws.title}.")
            vistos[codigo] = ws.title
            clientes.append({"orden": orden, "codigo": codigo, "nombre": nombre, "color_documento": color, "zona_sugerida": _zona_del_nombre(nombre)})
        hojas.append({"hoja": ws.title, "titulo": titulo, "clientes": clientes})
    total = sum(len(h["clientes"]) for h in hojas)
    colores: Dict[str, int] = {}
    for h in hojas:
        for c in h["clientes"]:
            colores[c["color_documento"]] = colores.get(c["color_documento"], 0) + 1
    return {"hojas": hojas, "total_clientes": total, "colores": colores, "advertencias": advertencias,
            "nota_colores": "Colores PRELIMINARES del documento (clasificación sobre datos históricos del corte, no saldos vigentes). 'ROJO' = solo contado según el resultado del sistema (regla vigente); 'CANDIDATO' (versiones anteriores) = candidato pendiente de gerencia."}
