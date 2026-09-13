"""Modelos de datos usados por todo el motor: clientes SAP, clientes de ruta y config de ruta."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class SapClientRecord:
    """Un registro de cliente tal como lo entrega (o simula) la fuente SAP."""

    codigo: str
    nombre: str
    saldo: float
    direccion_factura: Optional[str] = None
    activo_desde: Optional[str] = None
    contabilizacion_automatica: Optional[str] = None


@dataclass
class ClientEntry:
    """Un cliente dentro de la lista/ruta de un vendedor (hoja principal)."""

    codigo: str
    no_factura: Optional[str] = None
    observacion: Optional[str] = None

    def clone(self) -> "ClientEntry":
        return ClientEntry(codigo=self.codigo, no_factura=self.no_factura, observacion=self.observacion)


@dataclass
class RouteConfig:
    """Configuracion de una ruta/vendedor especifico. Cortes es el piloto, no el unico."""

    route_id: str
    vendor: str
    main_sheet: str = "CORTES"
    data_sheet: str = "Hoja2"
    fecha_cell: str = "B5"
    header_row: int = 7
    data_start_row: int = 8
    num_col: str = "A"
    code_col: str = "B"
    name_col: str = "C"
    factura_col: str = "D"
    saldo_col: str = "E"
    obs_col: str = "F"
    total_label_col: str = "D"
    total_value_col: str = "E"
    total_label_text: str = "Total Saldo Ruta"
    sap_code_col: str = "A"
    sap_name_col: str = "B"
    sap_saldo_col: str = "C"
    sap_dir_col: str = "D"
    sap_activo_col: str = "E"
    sap_contab_col: str = "F"
    name_formula: str = "=VLOOKUP({code_ref},{data_sheet}!A:C,2,0)"
    saldo_formula: str = "=VLOOKUP({code_ref},{data_sheet}!A:C,3,0)"
    total_formula: str = "=SUM({first_ref}:{last_ref})"
    filename_prefix: str = "CORTES"

    def name_formula_for(self, row: int) -> str:
        code_ref = f"{self.code_col}{row}"
        return self.name_formula.format(code_ref=code_ref, data_sheet=self.data_sheet)

    def saldo_formula_for(self, row: int) -> str:
        code_ref = f"{self.code_col}{row}"
        return self.saldo_formula.format(code_ref=code_ref, data_sheet=self.data_sheet)


@dataclass
class GenerationResult:
    """Resultado de una operacion de generacion/edicion, listo para loguear o reportar."""

    source_file: str
    output_file: str
    route_id: str
    new_date: Optional[date]
    client_count: int
    operations_applied: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class ScheduleEntry:
    """Una salida programada: 'Fulano tiene la ruta X el dia Y'.

    Viene de un ScheduleSource (hoy: LocalScheduleSource; manana: InSitu).
    `natural_key` identifica la salida por sus datos de negocio (vendedor +
    ruta + fecha_salida), no por el id interno de la fuente, para que la
    idempotencia sobreviva un cambio de fuente de programacion."""

    id: str
    vendor: str
    route: str
    fecha_salida: date
    fecha_regreso: Optional[date] = None
    observacion: Optional[str] = None
    estado: str = "programada"
    aprobado: bool = False

    @property
    def natural_key(self) -> str:
        from .text_utils import normalize_key

        return f"{self.fecha_salida.isoformat()}::{normalize_key(self.vendor)}::{normalize_key(self.route)}"
