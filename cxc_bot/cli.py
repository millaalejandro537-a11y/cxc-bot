"""Interfaz de linea de comandos. Pensada para uso interno hoy y para que n8n
la invoque manana sin interaccion humana (subcomandos, salida --json, codigos
de salida distintos de 0 en caso de error).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from datetime import date, datetime

from . import excel_service, validators
from .business_calendar import (
    DEFAULT_SCHEDULING_CONFIG_PATH,
    load_business_calendar,
    load_preparation_lead_days,
    load_scheduling_settings,
    today_in_timezone,
)
from .business_history import DEFAULT_BUSINESS_DB_PATH, BusinessHistoryStore
from .catalog import CatalogError, VendorCatalog
from .config import RouteNotFoundError, load_route_config
from .config_versioning import DEFAULT_CONFIG_VERSIONS_DB_PATH, ConfigVersionStore
from .data_source import LocalMockDataSource
from .file_resolver import FileResolutionError
from .history_store import DEFAULT_HISTORY_PATH, HistoryStore
from .route_engine import AddClient, MoveClient, Operation, RemoveClient, ReorderAll, RouteEngineError
from .runner import process_due_routes
from .schedule_source import DEFAULT_SCHEDULE_PATH, LocalScheduleSource, ScheduleSourceError

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "cxc_bot.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _parse_ops_json(path: Optional[str]) -> List[Operation]:
    if not path:
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    ops: List[Operation] = []
    for item in raw:
        kind = item.get("op")
        if kind == "add":
            ops.append(
                AddClient(
                    codigo=item["codigo"],
                    after_codigo=item.get("after_codigo"),
                    at_start=item.get("at_start", False),
                    no_factura=item.get("no_factura"),
                    observacion=item.get("observacion"),
                )
            )
        elif kind == "remove":
            ops.append(RemoveClient(codigo=item["codigo"]))
        elif kind == "move":
            ops.append(
                MoveClient(
                    codigo=item["codigo"],
                    after_codigo=item.get("after_codigo"),
                    at_start=item.get("at_start", False),
                )
            )
        elif kind == "reorder":
            ops.append(ReorderAll(codigos=item["codigos"]))
        else:
            raise ValueError(f"Operacion desconocida en ops-json: {item!r}")
    return ops


def _parse_simple_ops(args) -> List[Operation]:
    ops: List[Operation] = []
    for spec in args.add or []:
        codigo, after = _split_after(spec)
        ops.append(AddClient(codigo=codigo, after_codigo=after, at_start=(after is None)))
    for codigo in args.remove or []:
        ops.append(RemoveClient(codigo=codigo))
    for spec in args.move or []:
        codigo, after = _split_after(spec)
        ops.append(MoveClient(codigo=codigo, after_codigo=after, at_start=(after is None)))
    if args.order:
        ops.append(ReorderAll(codigos=[c.strip() for c in args.order.split(",") if c.strip()]))
    return ops


def _split_after(spec: str):
    if ":after:" in spec:
        codigo, after = spec.split(":after:", 1)
        return codigo.strip(), after.strip()
    return spec.strip(), None


def _add_ops_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--add", action="append", metavar="CODIGO[:after:OTRO_CODIGO]",
        help="Agregar cliente. Sin ':after:' se agrega al final. Puede repetirse.",
    )
    parser.add_argument("--remove", action="append", metavar="CODIGO", help="Eliminar cliente. Puede repetirse.")
    parser.add_argument(
        "--move", action="append", metavar="CODIGO[:after:OTRO_CODIGO]",
        help="Mover cliente. Sin ':after:' se mueve al final. Puede repetirse.",
    )
    parser.add_argument("--order", metavar="C1,C2,C3,...", help="Reordenamiento completo por lista de codigos.")
    parser.add_argument("--ops-json", metavar="PATH", help="Archivo JSON con una lista de operaciones.")


def _collect_ops(args) -> List[Operation]:
    return _parse_simple_ops(args) + _parse_ops_json(args.ops_json)


def cmd_generate_route(args) -> int:
    config = load_route_config(args.route)
    sap_records = LocalMockDataSource(args.data).fetch_clients()
    ops = _collect_ops(args)

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / "output"
    output_path = Path(args.output) if args.output else output_dir / f"{config.filename_prefix} {args.month}.xlsx"

    result = excel_service.generate_route(
        route_config=config,
        base_path=args.base,
        output_path=output_path,
        new_date=args.date,
        sap_records=sap_records,
        operations=ops,
        force=args.force,
    )

    report = validators.validate_workbook(
        output_path,
        config,
        expected_date=excel_service.normalize_date(args.date),
        expected_client_count=result.client_count,
        expect_clear_observaciones=True,
        expected_sap_count=len(sap_records),
    )

    _print_result(result, report, args.json_output)
    if not report.ok:
        validators.assert_valid(report)
    return 0


def cmd_edit_route(args) -> int:
    config = load_route_config(args.route)
    ops = _collect_ops(args)
    if not ops:
        raise SystemExit("No se especifico ninguna operacion (--add/--remove/--move/--order/--ops-json).")

    if args.output:
        output_path = Path(args.output)
    else:
        input_path = Path(args.input)
        output_path = input_path.with_name(f"{input_path.stem}_editado{input_path.suffix}")

    result = excel_service.edit_route(
        route_config=config,
        input_path=args.input,
        output_path=output_path,
        operations=ops,
        force=args.force,
    )

    report = validators.validate_workbook(
        output_path,
        config,
        expected_client_count=result.client_count,
        expect_clear_observaciones=False,
    )

    _print_result(result, report, args.json_output)
    if not report.ok:
        validators.assert_valid(report)
    return 0


def _print_result(result, report, as_json: bool) -> None:
    if as_json:
        payload = {
            "result": asdict(result) if not isinstance(result, dict) else result,
            "validation": {"ok": report.ok, "errors": report.errors, "warnings": report.warnings},
        }
        payload["result"]["new_date"] = str(result.new_date) if result.new_date else None
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    print(f"Archivo origen : {result.source_file}")
    print(f"Archivo generado: {result.output_file}")
    print(f"Clientes en ruta: {result.client_count}")
    if result.new_date:
        print(f"Fecha         : {result.new_date}")
    if result.operations_applied:
        print(f"Operaciones aplicadas: {len(result.operations_applied)}")
        for op in result.operations_applied:
            print(f"  - {op}")
    print(f"Validacion    : {'OK' if report.ok else 'FALLO'}")
    for w in report.warnings:
        print(f"  [warning] {w}")
    for e in report.errors:
        print(f"  [error] {e}")


def cmd_process_due_routes(args) -> int:
    settings = load_scheduling_settings(Path(args.scheduling_config))
    routes_root = Path(args.routes_root) if args.routes_root else Path(settings.get("routes_root", "."))
    sap_data_path = args.data or settings.get("sap_mock_data_path")
    if not sap_data_path:
        raise SystemExit("No se especifico fuente de datos SAP (--data o 'sap_mock_data_path' en scheduling.json).")
    lead_days = args.lead_days if args.lead_days is not None else load_preparation_lead_days(
        Path(args.scheduling_config)
    )

    schedule_source = LocalScheduleSource(Path(args.schedule))
    catalog = VendorCatalog.load()
    calendar = load_business_calendar(Path(args.scheduling_config))
    history = HistoryStore(Path(args.history_db))
    catchup_window_days = args.catchup_window_days if args.catchup_window_days is not None else settings.get(
        "catchup_window_dias"
    )

    business_history = None
    config_versions = None
    if not args.no_snapshot:
        business_history = BusinessHistoryStore(Path(args.business_db))
        config_versions = ConfigVersionStore(db_path=Path(args.config_versions_db))

    # "Hoy" en la zona horaria del negocio (America/Tegucigalpa por defecto),
    # no la de la maquina/servidor donde corra esto -- salvo que se pase
    # --today explicitamente (pruebas, catch-up manual de un dia especifico).
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else today_in_timezone(calendar.timezone)
    sap_data_source = LocalMockDataSource(sap_data_path)

    result = process_due_routes(
        schedule_source=schedule_source,
        catalog=catalog,
        calendar=calendar,
        lead_business_days=lead_days,
        routes_root=routes_root,
        sap_data_source=sap_data_source,
        sap_source_label=str(sap_data_path),
        history=history,
        today=today,
        business_history=business_history,
        config_versions=config_versions,
        environment=args.environment,
        sap_is_mock=True,  # LocalMockDataSource SIEMPRE es mock: el candado prod+mock aplica siempre aqui
        catchup_window_days=catchup_window_days,
    )

    payload = result.to_dict()
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Corrida {payload['run_id']}: {payload['status']}")
        print(f"  procesadas: {payload['processed']}  exitosas: {payload['succeeded']}  "
              f"omitidas: {payload['skipped']}  fallidas: {payload['failed']}")
        for r in payload["routes"]:
            marker = {"success": "OK", "error": "ERROR", "skipped": "OMITIDA"}[r["status"]]
            print(f"  [{marker}] {r['vendor']} / {r['route']} ({r['fecha_salida']})"
                  + (f" -> {r['output_file']}" if r["output_file"] else "")
                  + (f" :: {r['error_category']}: {r['error_message']}" if r["error_message"] and r["status"] == "error" else ""))

    return 0


def cmd_history(args) -> int:
    history = HistoryStore(Path(args.history_db))
    if args.route:
        rows = history.find_by_route(args.route, vendor=args.vendor)
    else:
        rows = history.list_recent(limit=args.limit)

    records = [dict(r) for r in rows]
    if args.json_output:
        print(json.dumps(records, ensure_ascii=False, indent=2, default=str))
    else:
        for r in records:
            print(
                f"{r['timestamp']} | {r['vendor']} / {r['route']} ({r['fecha_salida']}) "
                f"| {r['estado']} | {r['archivo_generado'] or '-'} "
                f"| clientes={r['cantidad_clientes']} | run={r['run_id']}"
            )
            if r["estado"] == "error":
                print(f"    -> {r['error_categoria']}: {r['error_mensaje']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cxc-bot", description="Bot de Rutas CxC - Distribuciones Cosprobell")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Salida en formato JSON.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate-route", help="Genera el Excel de un nuevo mes a partir del mes anterior.")
    p_gen.add_argument("--json", dest="json_output", action="store_true", help="Salida en formato JSON.")
    p_gen.add_argument("--route", required=True, help="Identificador de la ruta, ej. CORTES.")
    p_gen.add_argument("--base", required=True, help="Ruta al archivo Excel del mes anterior.")
    p_gen.add_argument("--month", required=True, help="Nombre del nuevo mes (para el nombre del archivo), ej. octubre.")
    p_gen.add_argument("--date", required=True, help="Nueva fecha de la ruta, formato YYYY-MM-DD.")
    p_gen.add_argument("--data", required=True, help="Archivo .xlsx/.csv con la tabla de clientes (simula SAP).")
    p_gen.add_argument("--output-dir", help="Carpeta de salida (por defecto ./output).")
    p_gen.add_argument("--output", help="Ruta completa del archivo de salida (sobreescribe --output-dir).")
    p_gen.add_argument("--force", action="store_true", help="Permite sobreescribir si el archivo de salida ya existe.")
    _add_ops_arguments(p_gen)
    p_gen.set_defaults(func=cmd_generate_route)

    p_edit = sub.add_parser("edit-route", help="Agrega/elimina/mueve clientes en una ruta ya generada.")
    p_edit.add_argument("--json", dest="json_output", action="store_true", help="Salida en formato JSON.")
    p_edit.add_argument("--route", required=True, help="Identificador de la ruta, ej. CORTES.")
    p_edit.add_argument("--input", required=True, help="Archivo Excel de la ruta a editar.")
    p_edit.add_argument(
        "--output", help="Archivo de salida (por defecto '<input>_editado.xlsx'; nunca se sobreescribe el input)."
    )
    p_edit.add_argument("--force", action="store_true", help="Permite sobreescribir el archivo de salida.")
    _add_ops_arguments(p_edit)
    p_edit.set_defaults(func=cmd_edit_route)

    p_proc = sub.add_parser(
        "process-due-routes",
        help="Detecta que rutas deben prepararse ahora (segun la programacion) y las genera automaticamente.",
    )
    p_proc.add_argument("--json", dest="json_output", action="store_true", help="Salida en formato JSON.")
    p_proc.add_argument("--schedule", default=str(DEFAULT_SCHEDULE_PATH), help="Archivo JSON de programacion de rutas.")
    p_proc.add_argument(
        "--scheduling-config", default=str(DEFAULT_SCHEDULING_CONFIG_PATH),
        help="config/scheduling.json (dias habiles de anticipacion, calendario, rutas_root, datos SAP).",
    )
    p_proc.add_argument("--routes-root", help="Raiz de 'RUTA VENDEDORES/' (sobreescribe scheduling.json).")
    p_proc.add_argument("--data", help="Archivo .xlsx/.csv que simula la extraccion SAP (sobreescribe scheduling.json).")
    p_proc.add_argument("--lead-days", type=int, help="Dias habiles de anticipacion (sobreescribe scheduling.json).")
    p_proc.add_argument("--today", help="Fecha 'de hoy' a usar, YYYY-MM-DD (por defecto: la fecha real). Util para pruebas.")
    p_proc.add_argument("--history-db", default=str(DEFAULT_HISTORY_PATH), help="Base de datos SQLite del historial tecnico.")
    p_proc.add_argument(
        "--environment", choices=["dev", "test", "prod"], default="dev",
        help="Ambiente logico de esta corrida (default: dev). Un snapshot 'prod' con datos mock se degrada "
             "automaticamente a 'dev' -- ver DEV/TEST/PROD en el manual.",
    )
    p_proc.add_argument(
        "--business-db", default=str(DEFAULT_BUSINESS_DB_PATH), help="Base de datos SQLite de snapshots de negocio."
    )
    p_proc.add_argument(
        "--config-versions-db", default=str(DEFAULT_CONFIG_VERSIONS_DB_PATH),
        help="Base de datos SQLite de versiones de configuracion.",
    )
    p_proc.add_argument(
        "--no-snapshot", action="store_true", help="No crear snapshot de negocio para esta corrida."
    )
    p_proc.add_argument(
        "--catchup-window-days", type=int,
        help="Maximo de dias de atraso a procesar automaticamente (sobreescribe scheduling.json; sin limite si no se da).",
    )
    p_proc.set_defaults(func=cmd_process_due_routes)

    p_hist = sub.add_parser("history", help="Consulta el historial de ejecuciones (cuando se genero cada ruta).")
    p_hist.add_argument("--json", dest="json_output", action="store_true", help="Salida en formato JSON.")
    p_hist.add_argument("--history-db", default=str(DEFAULT_HISTORY_PATH), help="Base de datos SQLite del historial.")
    p_hist.add_argument("--route", help="Filtrar por ruta (ej. Cortes).")
    p_hist.add_argument("--vendor", help="Filtrar por vendedor (requiere --route).")
    p_hist.add_argument("--limit", type=int, default=50, help="Maximo de filas a mostrar (sin --route).")
    p_hist.set_defaults(func=cmd_history)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.json_output = getattr(args, "json_output", False)
    try:
        return args.func(args)
    except RouteNotFoundError as e:
        print(f"Error de configuracion: {e}", file=sys.stderr)
        return 2
    except (excel_service.ExcelServiceError, validators.ValidationError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 3
    except RouteEngineError as e:
        print(f"Operacion invalida, se aborto de forma segura: {e}", file=sys.stderr)
        return 4
    except (ScheduleSourceError, CatalogError, FileResolutionError) as e:
        print(f"Error de programacion/catalogo/archivos: {e}", file=sys.stderr)
        return 5
    except Exception as e:  # noqa: BLE001 - CLI boundary: fallar de forma segura y clara
        print(f"Error inesperado: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
