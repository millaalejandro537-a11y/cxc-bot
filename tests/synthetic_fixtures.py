"""Libro base SINTETICO (ruta CORTES) para las pruebas.

Los libros versionados en `tests/fixtures/rutas_reales/` y `samples/` ya estan anonimizados con `tests/tools/anonimizar_fixtures.py`
(estructura real, datos inventados). Este modulo solo expone el de CORTES y las funciones que describen sus valores inventados."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
from anonimizar_fixtures import nombre_sintetico, saldo_sintetico  # noqa: E402,F401

BASE = Path(__file__).resolve().parent / "fixtures" / "rutas_reales" / "CORTES septiembre.xlsx"


def build_cortes_base(dest: Path) -> Path:
    shutil.copy2(BASE, dest)
    return dest
