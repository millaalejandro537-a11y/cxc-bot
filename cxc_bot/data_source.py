"""Fuente de datos de clientes (hoy: mock local, manana: SAP Business One Service Layer).

El resto del motor (route_engine, excel_service) nunca importa openpyxl para leer
SAP ni sabe de donde vienen los SapClientRecord: solo conoce esta interfaz.
Sustituir LocalMockDataSource por SapServiceLayerDataSource (sap_service_layer.py)
no debe requerir tocar route_engine ni excel_service.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

import openpyxl

from .models import SapClientRecord


class SapDataSource(ABC):
    """Interfaz que debe cumplir cualquier fuente de datos de clientes."""

    @property
    def is_mock(self) -> bool:
        """True si los datos NO vienen de SAP real. Es lo que alimenta el candado
        prod+mock del historial de negocio; por defecto una fuente es mock
        (falla hacia el lado seguro) y solo la fuente real lo sobreescribe."""
        return True

    @abstractmethod
    def fetch_clients(self) -> List[SapClientRecord]:
        """Devuelve la tabla completa de clientes, equivalente a filtrar Codigo por 'C' en SAP."""
        raise NotImplementedError


class LocalMockDataSource(SapDataSource):
    """Lee una tabla de clientes desde un .xlsx o .csv local con las 6 columnas de Hoja2.

    Esto simula la extraccion manual de SAP ("filtrar Codigo por C") mientras no
    exista integracion real con el Service Layer. Acepta cualquier cantidad de
    filas: el motor nunca asume un numero fijo.
    """

    EXPECTED_COLUMNS = 6

    def __init__(self, path: str | Path, sheet_name: str | None = None):
        self.path = Path(path)
        self.sheet_name = sheet_name
        if not self.path.exists():
            raise FileNotFoundError(f"No se encontro el archivo de datos SAP simulados: {self.path}")

    def fetch_clients(self) -> List[SapClientRecord]:
        if self.path.suffix.lower() == ".csv":
            rows = self._read_csv()
        else:
            rows = self._read_xlsx()

        records: List[SapClientRecord] = []
        for row in rows:
            codigo = self._clean(row[0]) if len(row) > 0 else None
            if not codigo:
                continue
            if not str(codigo).upper().startswith("C"):
                # En SAP el proceso manual filtra Codigo por 'C' (clientes).
                continue
            nombre = self._clean(row[1]) if len(row) > 1 else ""
            saldo_raw = row[2] if len(row) > 2 else 0
            try:
                saldo = float(saldo_raw) if saldo_raw not in (None, "") else 0.0
            except (TypeError, ValueError):
                saldo = 0.0
            direccion = self._clean(row[3]) if len(row) > 3 else None
            activo_desde = self._clean(row[4]) if len(row) > 4 else None
            contab = self._clean(row[5]) if len(row) > 5 else None
            records.append(
                SapClientRecord(
                    codigo=str(codigo).strip(),
                    nombre=nombre or "",
                    saldo=saldo,
                    direccion_factura=direccion,
                    activo_desde=activo_desde,
                    contabilizacion_automatica=contab,
                )
            )
        return records

    @staticmethod
    def _clean(value):
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value if value != "" else None
        return value

    def _read_xlsx(self):
        wb = openpyxl.load_workbook(self.path, data_only=True, read_only=True)
        ws = wb[self.sheet_name] if self.sheet_name else wb[wb.sheetnames[0]]
        rows = []
        first = True
        for row in ws.iter_rows(values_only=True):
            if first:
                # Se salta la fila de encabezados (Codigo SN, Nombre SN, ...).
                first = False
                continue
            if row and row[0] is not None:
                rows.append(row)
        wb.close()
        return rows

    def _read_csv(self):
        rows = []
        with open(self.path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            first = True
            for row in reader:
                if first:
                    first = False
                    continue
                if row and row[0].strip():
                    rows.append(row)
        return rows


class InMemoryDataSource(SapDataSource):
    """Fuente de datos en memoria, util para pruebas automatizadas."""

    def __init__(self, records: List[SapClientRecord]):
        self._records = records

    def fetch_clients(self) -> List[SapClientRecord]:
        return list(self._records)
