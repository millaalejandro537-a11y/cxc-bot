"""Orquestador: "que rutas debo preparar ahora" -> ejecutarlas con el motor
de Fase 1, una por una, sin que el fallo de una detenga a las demas.

Flujo por cada entrada de programacion que este due:

    programacion valida -> config de ruta -> archivo base -> datos SAP
    -> generar (motor Fase 1, sin cambios) -> validar -> registrar -> notificar

Idempotencia: la fuente de verdad es HistoryStore (natural_key = vendedor +
ruta + fecha_salida), no el estado que devuelva el ScheduleSource. Asi que
funciona igual si InSitu, en el futuro, no permite escribir de vuelta el
estado de una entrada.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

from . import excel_service, validators
from .business_calendar import BusinessCalendar
from .catalog import CatalogError, VendorCatalog
from .config import RouteNotFoundError, load_route_config
from .data_source import SapDataSource
from .file_resolver import FileResolutionError, resolve_base_file
from .history_store import ExecutionRecord, HistoryStore
from .models import ScheduleEntry
from .notifier import Notifier, default_notifier
from .schedule_source import ScheduleSource

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def spanish_month_name(d: date) -> str:
    return MESES_ES[d.month - 1]


@dataclass
class RouteJobResult:
    vendor: str
    route: str
    fecha_salida: date
    natural_key: str
    status: str  # 'success' | 'error' | 'skipped'
    schedule_entry_id: Optional[str] = None
    output_file: Optional[str] = None
    source_file: Optional[str] = None
    client_count: Optional[int] = None
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "vendor": self.vendor,
            "route": self.route,
            "fecha_salida": self.fecha_salida.isoformat(),
            "natural_key": self.natural_key,
            "status": self.status,
            "schedule_entry_id": self.schedule_entry_id,
            "output_file": self.output_file,
            "source_file": self.source_file,
            "client_count": self.client_count,
            "error_category": self.error_category,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
        }


@dataclass
class ProcessRunResult:
    run_id: str
    routes: List[RouteJobResult] = field(default_factory=list)

    @property
    def processed(self) -> int:
        return len(self.routes)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.routes if r.status == "success")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.routes if r.status == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.routes if r.status == "error")

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": "error" if self.failed and not self.succeeded and not self.skipped else (
                "partial" if self.failed else "success"
            ),
            "processed": self.processed,
            "succeeded": self.succeeded,
            "skipped": self.skipped,
            "failed": self.failed,
            "routes": [r.to_dict() for r in self.routes],
        }


def compute_due_entries(
    entries: List[ScheduleEntry], today: date, lead_business_days: int, calendar: BusinessCalendar
) -> List[ScheduleEntry]:
    """Solo las 'programada' + aprobadas, y solo si ya llegamos a su fecha de
    preparacion (N dias habiles antes de la salida)."""

    due = []
    for entry in entries:
        if entry.estado != "programada" or not entry.aprobado:
            continue
        due_date = calendar.subtract_business_days(entry.fecha_salida, lead_business_days)
        if today >= due_date:
            due.append(entry)
    return due


def _classify_validation_errors(errors: List[str]) -> str:
    joined = " ".join(errors)
    if "#N/A" in joined:
        return "cliente_inexistente"
    if "#REF!" in joined or "#VALUE!" in joined or "#NAME?" in joined:
        return "formula_invalida"
    return "validacion_fallida"


def process_one_entry(
    entry: ScheduleEntry,
    *,
    catalog: VendorCatalog,
    routes_root: Path,
    sap_records: list,
    sap_source_label: str,
    history: HistoryStore,
    schedule_source: ScheduleSource,
    run_id: str,
) -> RouteJobResult:
    natural_key = entry.natural_key
    started = time.monotonic()

    def _finish(status: str, **kwargs) -> RouteJobResult:
        duration_ms = int((time.monotonic() - started) * 1000)
        result = RouteJobResult(
            vendor=entry.vendor,
            route=entry.route,
            fecha_salida=entry.fecha_salida,
            natural_key=natural_key,
            status=status,
            schedule_entry_id=entry.id,
            duration_ms=duration_ms,
            **kwargs,
        )
        history.record(
            ExecutionRecord(
                run_id=run_id,
                natural_key=natural_key,
                schedule_entry_id=entry.id,
                vendor=entry.vendor,
                route=entry.route,
                fecha_salida=entry.fecha_salida,
                estado={"success": "exito", "error": "error", "skipped": "omitida"}[status],
                archivo_origen=result.source_file,
                archivo_generado=result.output_file,
                fuente_datos=sap_source_label,
                cantidad_clientes=result.client_count,
                validaciones_ok=(status == "success") or None,
                error_categoria=result.error_category,
                error_mensaje=result.error_message,
                duracion_ms=duration_ms,
            )
        )
        return result

    # 0. Idempotencia: si ya existe una ejecucion EXITOSA para esta salida,
    # no se repite el trabajo ni se toca nada.
    already = history.find_successful(natural_key)
    if already is not None:
        return _finish(
            "skipped",
            output_file=already["archivo_generado"],
            source_file=already["archivo_origen"],
            client_count=already["cantidad_clientes"],
            error_message=f"Ya se habia preparado exitosamente el {already['timestamp']} (run {already['run_id']}).",
        )

    # 1. Validar programacion contra el catalogo oficial.
    try:
        canonical_vendor = catalog.canonical_vendor(entry.vendor)
        canonical_route = catalog.canonical_route(entry.vendor, entry.route)
    except CatalogError as e:
        return _finish("error", error_category="programacion_invalida", error_message=str(e))

    # 2. Config de ruta.
    try:
        route_config = load_route_config(canonical_route)
    except RouteNotFoundError as e:
        return _finish("error", error_category="configuracion_ruta_faltante", error_message=str(e))

    # 3. Archivo base mas reciente.
    try:
        base_path = resolve_base_file(
            routes_root, canonical_vendor, canonical_route, route_config, reference_date=entry.fecha_salida
        )
    except FileResolutionError as e:
        return _finish("error", error_category=e.category, error_message=str(e))

    # 4. Datos SAP: ya vienen resueltos (se piden UNA vez por corrida, no por
    # ruta, porque SAP entrega el catalogo completo de clientes).

    # 5-6. Generar + validar con el motor existente (sin modificarlo).
    # Formato preferido para archivos nuevos: "[RUTA] [mes] [año].xlsx"
    # (file_resolver.py sigue reconociendo el formato historico sin año).
    output_filename = (
        f"{route_config.filename_prefix} {spanish_month_name(entry.fecha_salida)} {entry.fecha_salida.year}.xlsx"
    )
    output_path = base_path.parent / output_filename
    try:
        result = excel_service.generate_route(
            route_config=route_config,
            base_path=base_path,
            output_path=output_path,
            new_date=entry.fecha_salida,
            sap_records=sap_records,
            force=False,
        )
    except excel_service.OutputExistsError as e:
        return _finish(
            "error",
            source_file=str(base_path),
            error_category="archivo_destino_ya_existente",
            error_message=str(e),
        )
    except (OSError, PermissionError) as e:
        return _finish(
            "error", source_file=str(base_path), error_category="error_escritura", error_message=str(e)
        )
    except excel_service.ExcelServiceError as e:
        return _finish(
            "error", source_file=str(base_path), error_category="error_inesperado", error_message=str(e)
        )

    report = validators.validate_workbook(output_path, route_config, expected_date=entry.fecha_salida)
    if not report.ok:
        # No dejar un archivo invalido en la carpeta del vendedor.
        try:
            os.remove(output_path)
        except OSError:
            pass
        return _finish(
            "error",
            source_file=str(base_path),
            error_category=_classify_validation_errors(report.errors),
            error_message="; ".join(report.errors),
        )

    # 7. El archivo ya quedo guardado en la carpeta correcta (junto al base).
    # 8-9. Registrar y solo AHORA marcar como preparada.
    try:
        schedule_source.mark_prepared(entry.id)
    except Exception:
        pass  # best-effort; HistoryStore ya es la fuente de verdad de idempotencia

    return _finish(
        "success",
        output_file=str(output_path),
        source_file=str(base_path),
        client_count=result.client_count,
    )


def process_due_routes(
    *,
    schedule_source: ScheduleSource,
    catalog: VendorCatalog,
    calendar: BusinessCalendar,
    lead_business_days: int,
    routes_root: Path,
    sap_data_source: SapDataSource,
    sap_source_label: str,
    history: Optional[HistoryStore] = None,
    notifier: Optional[Notifier] = None,
    today: Optional[date] = None,
    run_id: Optional[str] = None,
) -> ProcessRunResult:
    history = history or HistoryStore()
    notifier = notifier or default_notifier()
    today = today or date.today()
    run_id = run_id or f"{today.isoformat()}-{uuid.uuid4().hex[:8]}"

    entries = schedule_source.list_entries()
    due = compute_due_entries(entries, today, lead_business_days, calendar)

    run_result = ProcessRunResult(run_id=run_id)
    if not due:
        return run_result

    # Se pide UNA sola vez por corrida: SAP entrega el catalogo completo de
    # clientes, no uno distinto por ruta. Si falla, se registra como fallo
    # individual de CADA ruta due (nunca se deja de registrar en silencio).
    try:
        sap_records = sap_data_source.fetch_clients()
    except Exception as e:
        for entry in due:
            job_result = RouteJobResult(
                vendor=entry.vendor,
                route=entry.route,
                fecha_salida=entry.fecha_salida,
                natural_key=entry.natural_key,
                status="error",
                schedule_entry_id=entry.id,
                error_category="fuente_datos_invalida",
                error_message=f"No se pudo obtener la tabla de clientes ({sap_source_label}): {e}",
            )
            history.record(
                ExecutionRecord(
                    run_id=run_id,
                    natural_key=entry.natural_key,
                    schedule_entry_id=entry.id,
                    vendor=entry.vendor,
                    route=entry.route,
                    fecha_salida=entry.fecha_salida,
                    estado="error",
                    fuente_datos=sap_source_label,
                    error_categoria="fuente_datos_invalida",
                    error_mensaje=job_result.error_message,
                )
            )
            run_result.routes.append(job_result)
            notifier.notify_error(
                f"No se pudo generar {entry.route} {spanish_month_name(entry.fecha_salida)}: "
                f"{job_result.error_message}",
                job_result.to_dict(),
            )
        return run_result

    for entry in due:
        try:
            job_result = process_one_entry(
                entry,
                catalog=catalog,
                routes_root=routes_root,
                sap_records=sap_records,
                sap_source_label=sap_source_label,
                history=history,
                schedule_source=schedule_source,
                run_id=run_id,
            )
        except Exception as e:  # noqa: BLE001 - un fallo aqui no debe tumbar el resto del lote
            job_result = RouteJobResult(
                vendor=entry.vendor,
                route=entry.route,
                fecha_salida=entry.fecha_salida,
                natural_key=entry.natural_key,
                status="error",
                schedule_entry_id=entry.id,
                error_category="error_inesperado",
                error_message=str(e),
            )
            history.record(
                ExecutionRecord(
                    run_id=run_id,
                    natural_key=entry.natural_key,
                    schedule_entry_id=entry.id,
                    vendor=entry.vendor,
                    route=entry.route,
                    fecha_salida=entry.fecha_salida,
                    estado="error",
                    error_categoria="error_inesperado",
                    error_mensaje=str(e),
                )
            )

        run_result.routes.append(job_result)

        if job_result.status == "success":
            notifier.notify_success(
                f"{job_result.route} {spanish_month_name(job_result.fecha_salida)} fue generada y "
                "validada correctamente.",
                job_result.to_dict(),
            )
        elif job_result.status == "error":
            notifier.notify_error(
                f"No se pudo generar {job_result.route} {spanish_month_name(job_result.fecha_salida)}: "
                f"{job_result.error_message}",
                job_result.to_dict(),
            )
        # 'skipped' no genera notificacion (ya se aviso la primera vez que tuvo exito).

    return run_result
