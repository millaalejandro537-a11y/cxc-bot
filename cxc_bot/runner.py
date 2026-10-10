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

import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import excel_service, validators
from .business_calendar import BusinessCalendar
from .business_history import BusinessHistoryStore, client_entries_to_snapshot_inputs
from .catalog import CatalogError, VendorCatalog
from .composite_config import load_composite_route
from .composite_route_service import (
    CompositeRouteError,
    composite_output_folder_name,
    generate_composite_route_local,
)
from .config import RouteNotFoundError, load_route_config
from .config_versioning import ConfigVersionStore
from .data_source import SapDataSource
from .file_resolver import FileResolutionError, resolve_base_file
from .history_store import ExecutionRecord, HistoryStore
from . import payment_semaphore
from .models import ClientEntry, ScheduleEntry
from .notifier import Notifier, default_notifier
from .schedule_source import ScheduleSource
from .storage import LocalRouteStorage, RouteStorage, StorageError

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
    # Semaforo de pago (opcional): resumen por cliente calculado con cxc_bot.payment_semaphore. None si no se
    # pidio, si la ruta no tiene plazo de credito definido o si fallo (en ese caso `semaphore_error`).
    semaphore: Optional[dict] = None
    semaphore_error: Optional[str] = None

    def to_dict(self) -> dict:
        d = self._base_dict()
        if self.semaphore is not None:
            d["semaphore"] = self.semaphore
        if self.semaphore_error is not None:
            d["semaphore_error"] = self.semaphore_error
        return d

    def _base_dict(self) -> dict:
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
    entries: List[ScheduleEntry],
    today: date,
    lead_business_days: int,
    calendar: BusinessCalendar,
    catchup_window_days: Optional[int] = None,
) -> List[ScheduleEntry]:
    """Solo las 'programada' + aprobadas, y solo si ya llegamos a su fecha de
    preparacion (N dias habiles antes de la salida). Si se da
    `catchup_window_days`, una entrada cuya fecha_salida ya quedo mas atras
    que esa ventana NO se procesa aqui (ver compute_expired_entries): la Mac
    pudo estar apagada/dormida varios dias, pero no se re-intenta indefinida
    e implicitamente algo demasiado viejo (seccion 19)."""

    due = []
    for entry in entries:
        if entry.estado != "programada" or not entry.aprobado:
            continue
        due_date = calendar.subtract_business_days(entry.fecha_salida, lead_business_days)
        if today < due_date:
            continue
        if catchup_window_days is not None and (today - entry.fecha_salida).days > catchup_window_days:
            continue
        due.append(entry)
    return due


def compute_expired_entries(
    entries: List[ScheduleEntry],
    today: date,
    lead_business_days: int,
    calendar: BusinessCalendar,
    catchup_window_days: int,
) -> List[ScheduleEntry]:
    """El complemento de compute_due_entries cuando hay ventana de catch-up:
    entradas que ya estaban due pero quedaron fuera de la ventana. Se
    reportan como fallo especifico (categoria 'catchup_expirado') en vez de
    procesarse o de desaparecer en silencio."""

    expired = []
    for entry in entries:
        if entry.estado != "programada" or not entry.aprobado:
            continue
        due_date = calendar.subtract_business_days(entry.fecha_salida, lead_business_days)
        if today < due_date:
            continue
        if (today - entry.fecha_salida).days > catchup_window_days:
            expired.append(entry)
    return expired


def _read_computed_values(path: Path, config) -> dict:
    """Lee nombre/saldo YA CALCULADOS (data_only=True) del roster generado,
    para poblar el snapshot de negocio con los mismos numeros que muestra
    el Excel -- no se recalcula nada por separado."""

    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        ws = wb[config.main_sheet]
        result = {}
        row = config.data_start_row
        while True:
            codigo = ws[f"{config.code_col}{row}"].value
            if not codigo:
                break
            nombre = ws[f"{config.name_col}{row}"].value
            saldo = ws[f"{config.saldo_col}{row}"].value
            result[str(codigo).strip()] = (
                nombre if isinstance(nombre, str) else None,
                saldo if isinstance(saldo, (int, float)) else None,
            )
            row += 1
        return result
    finally:
        wb.close()


def _file_hash_or_none(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        import hashlib

        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _classify_validation_errors(errors: List[str]) -> str:
    joined = " ".join(errors)
    if "#N/A" in joined:
        return "cliente_inexistente"
    if "#REF!" in joined or "#VALUE!" in joined or "#NAME?" in joined:
        return "formula_invalida"
    return "validacion_fallida"


def _evaluate_payment_semaphore(route_config, roster, evidence, decisions, plazo_override=None) -> Tuple[Optional[dict], Optional[str]]:
    """Semaforo de pago de los clientes de la ruta con el motor unico. Nunca debe impedir una publicacion:
    devuelve (resumen, None), (None, None) si no aplica o (None, error)."""
    plazo = plazo_override if plazo_override is not None else getattr(route_config, "credit_days", None)
    if evidence is None or plazo is None or not roster:
        return None, None
    try:
        codigos = [c.codigo for c in roster]
        res = payment_semaphore.evaluar_ruta(codigos, evidence, plazo, decisions)
        clientes = []
        for pos, c in enumerate(roster, 1):
            r = res[c.codigo.strip().upper()]
            clientes.append({
                "posicion": pos, "codigo": c.codigo, "calculado": r.calculado, "final": r.final, "color_final": r.color_final,
                "promedio_mora": round(r.promedio_mora, 2) if r.promedio_mora is not None else None,
                "facturas_historial": r.historial_total, "facturas_evaluables": r.evaluables,
                "alertas_cobro": len(r.alertas_cobro), "alertas_historicas": len(r.alertas_historicas),
                "decision_gerencia": r.decision_gerencia is not None, "requiere_gerencia": r.requiere_gerencia,
            })
        conteo: Dict[str, int] = {}
        for c in clientes:
            conteo[c["final"]] = conteo.get(c["final"], 0) + 1
        return {"fecha_corte": evidence.fecha_corte.isoformat(), "fuente": evidence.etiqueta, "plazo_dias": plazo,
                "clasificacion": conteo, "clientes": clientes}, None
    except Exception as e:  # noqa: BLE001 - el semaforo es informativo para la salida; no tumba la generacion
        return None, f"{type(e).__name__}: {e}"


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
    storage: RouteStorage,
    business_history: Optional[BusinessHistoryStore] = None,
    config_versions: Optional[ConfigVersionStore] = None,
    environment: str = "dev",
    sap_is_mock: bool = True,
    payment_evidence: Optional["payment_semaphore.PaymentEvidenceSource"] = None,
    payment_decisions: Optional[Dict[str, "payment_semaphore.DecisionGerencia"]] = None,
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

    # 2. Config de ruta (simple o compuesta -- ver composite_config.py).
    composite = load_composite_route(canonical_route)
    try:
        route_config = load_route_config(
            composite.template_route_id if composite else canonical_route
        )
    except RouteNotFoundError as e:
        return _finish("error", error_category="configuracion_ruta_faltante", error_message=str(e))

    # Formato preferido para archivos nuevos: "[RUTA] [DD] [mes] [año].xlsx"
    # (incluye el dia porque una misma ruta puede salir mas de una vez en el
    # mismo mes; file_resolver.py sigue reconociendo los formatos historicos
    # sin dia y/o sin año).
    output_filename = (
        f"{route_config.filename_prefix} {entry.fecha_salida.day:02d} "
        f"{spanish_month_name(entry.fecha_salida)} {entry.fecha_salida.year}.xlsx"
    )

    # 5-7: copiar base(s) a una carpeta de trabajo LOCAL, generar y validar
    # ahi (nunca se procesa directamente sobre el storage), y solo si todo
    # sale bien se PUBLICA de forma atomica y verificada. Si la publicacion
    # falla o no se puede verificar, no se marca exito ni se deja nada a
    # medias en el destino (seccion 22 del encargo).
    with tempfile.TemporaryDirectory(prefix="cxc_bot_") as tmp_dir:
        local_work_dir = Path(tmp_dir)
        source_file_label = None
        final_roster: Optional[List[ClientEntry]] = None
        base_hash_source: Optional[Path] = None
        try:
            if composite:
                # 3-4 (compuesta): cada componente resuelve y copia su propio
                # archivo base; el roster combinado se arma aqui, no en el motor.
                output_folder = composite_output_folder_name(composite)
                destination_path = (
                    Path(routes_root) / composite.vendor / output_folder / output_filename
                )
                local_output_path = local_work_dir / f"__salida__{output_filename}"
                comp_result = generate_composite_route_local(
                    composite, routes_root, local_work_dir, local_output_path,
                    entry.fecha_salida, sap_records, storage=storage,
                )
                source_file_label = "; ".join(f"{k}: {v}" for k, v in comp_result.component_base_files.items())
                result = comp_result.generation
                final_roster = comp_result.combined_roster
            else:
                # 3 (simple): un solo archivo base.
                base_path = resolve_base_file(
                    routes_root, canonical_vendor, canonical_route, route_config, reference_date=entry.fecha_salida
                )
                source_file_label = str(base_path)
                destination_path = base_path.parent / output_filename
                staged_base = storage.stage_base_file(base_path, local_work_dir)
                base_hash_source = staged_base
                local_output_path = local_work_dir / output_filename
                result = excel_service.generate_route(
                    route_config=route_config,
                    base_path=staged_base,
                    output_path=local_output_path,
                    new_date=entry.fecha_salida,
                    sap_records=sap_records,
                    force=False,
                )
                final_roster = excel_service.peek_roster(local_output_path, route_config)
        except FileResolutionError as e:
            return _finish("error", source_file=source_file_label, error_category=e.category, error_message=str(e))
        except CompositeRouteError as e:
            return _finish(
                "error", source_file=source_file_label, error_category="configuracion_ruta_faltante", error_message=str(e)
            )
        except StorageError as e:
            return _finish("error", source_file=source_file_label, error_category=e.category, error_message=str(e))
        except (OSError, PermissionError) as e:
            return _finish(
                "error", source_file=source_file_label, error_category="error_escritura", error_message=str(e)
            )
        except excel_service.ExcelServiceError as e:
            return _finish(
                "error", source_file=source_file_label, error_category="error_inesperado", error_message=str(e)
            )

        report = validators.validate_workbook(local_output_path, route_config, expected_date=entry.fecha_salida)
        if not report.ok:
            # El archivo invalido vive solo en el temporal local, que se
            # borra solo al salir del 'with': nunca llega a publicarse.
            return _finish(
                "error",
                source_file=source_file_label,
                error_category=_classify_validation_errors(report.errors),
                error_message="; ".join(report.errors),
            )

        try:
            published_path = storage.publish(local_output_path, destination_path, force=False)
        except StorageError as e:
            return _finish("error", source_file=source_file_label, error_category=e.category, error_message=str(e))

        # Snapshot de negocio (opcional): solo si se paso un BusinessHistoryStore.
        # Se arma ANTES de que se borre el directorio temporal, leyendo los
        # valores YA CALCULADOS del propio archivo generado.
        if business_history is not None and final_roster is not None:
            computed = _read_computed_values(local_output_path, route_config)
            snapshot_clients = client_entries_to_snapshot_inputs(final_roster, computed)
            config_version_id = config_versions.current_version_id() if config_versions else None
            try:
                business_history.create_snapshot(
                    environment=environment,
                    vendor=canonical_vendor,
                    route=canonical_route,
                    fecha_salida=entry.fecha_salida,
                    clients=snapshot_clients,
                    execution_id=run_id,
                    archivo_final=str(published_path),
                    archivo_base=source_file_label,
                    archivo_base_hash=_file_hash_or_none(base_hash_source),
                    config_version_id=config_version_id,
                    fuente_programacion=type(schedule_source).__name__,
                    fuente_datos=sap_source_label,
                    fuente_datos_es_mock=sap_is_mock,
                )
            except Exception:
                pass  # el snapshot de negocio nunca debe tumbar una publicacion ya exitosa

    # 8-9. Registrar y solo AHORA (publicacion verificada) marcar como preparada.
    try:
        schedule_source.mark_prepared(entry.id)
    except Exception:
        pass  # best-effort; HistoryStore ya es la fuente de verdad de idempotencia

    semaphore, semaphore_error = _evaluate_payment_semaphore(route_config, final_roster, payment_evidence, payment_decisions)
    return _finish(
        "success",
        output_file=str(published_path),
        source_file=source_file_label,
        client_count=result.client_count,
        semaphore=semaphore,
        semaphore_error=semaphore_error,
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
    storage: Optional[RouteStorage] = None,
    business_history: Optional[BusinessHistoryStore] = None,
    config_versions: Optional[ConfigVersionStore] = None,
    environment: str = "dev",
    sap_is_mock: Optional[bool] = None,
    catchup_window_days: Optional[int] = None,
    payment_evidence: Optional["payment_semaphore.PaymentEvidenceSource"] = None,
    payment_decisions: Optional[Dict[str, "payment_semaphore.DecisionGerencia"]] = None,
) -> ProcessRunResult:
    # Si el llamador no lo indica, la fuente decide (candado prod+mock): una
    # fuente real se declara con is_mock=False, cualquier otra cuenta como mock.
    if sap_is_mock is None:
        sap_is_mock = getattr(sap_data_source, "is_mock", True)
    history = history or HistoryStore()
    notifier = notifier or default_notifier()
    storage = storage or LocalRouteStorage()
    today = today or date.today()
    run_id = run_id or f"{today.isoformat()}-{uuid.uuid4().hex[:8]}"

    entries = schedule_source.list_entries()
    due = compute_due_entries(entries, today, lead_business_days, calendar, catchup_window_days)

    run_result = ProcessRunResult(run_id=run_id)

    if catchup_window_days is not None:
        expired = compute_expired_entries(entries, today, lead_business_days, calendar, catchup_window_days)
        for entry in expired:
            days_late = (today - entry.fecha_salida).days
            job_result = RouteJobResult(
                vendor=entry.vendor,
                route=entry.route,
                fecha_salida=entry.fecha_salida,
                natural_key=entry.natural_key,
                status="error",
                schedule_entry_id=entry.id,
                error_category="catchup_expirado",
                error_message=(
                    f"La fecha de salida quedo {days_late} dias atras, mas alla de la ventana de "
                    f"catch-up configurada ({catchup_window_days} dias). No se genero automaticamente; "
                    "requiere revision manual."
                ),
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
                    error_categoria="catchup_expirado",
                    error_mensaje=job_result.error_message,
                )
            )
            run_result.routes.append(job_result)
            notifier.notify_error(
                f"{entry.route} ({entry.fecha_salida}) quedo fuera de la ventana de catch-up y no se generara "
                "automaticamente.",
                job_result.to_dict(),
            )

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
                storage=storage,
                business_history=business_history,
                config_versions=config_versions,
                environment=environment,
                sap_is_mock=sap_is_mock,
                payment_evidence=payment_evidence,
                payment_decisions=payment_decisions,
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
