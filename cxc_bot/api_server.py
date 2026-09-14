"""API HTTP local, minima (solo libreria estandar, sin dependencias nuevas),
para que n8n pueda invocar al Bot sin duplicar ninguna logica.

Por que una API en vez de que n8n llame al CLI directamente: n8n corre
dentro de un contenedor Docker (seccion 17 del encargo pidio Docker), y el
Bot corre en el venv de Python del Mac -- el nodo "Execute Command" de n8n
ejecuta comandos DENTRO del contenedor, que no tiene el venv ni las
dependencias del proyecto. En vez de meter Python y todo el proyecto dentro
de la imagen de n8n (mas complejo, mas fragil, dos copias del codigo que
mantener sincronizadas), este servidor corre en el Mac (fuera de Docker) y
n8n le pega por HTTP a traves de `host.docker.internal`, que Docker Desktop
para Mac ya resuelve al host automaticamente. Es la opcion mas simple y
robusta para esta combinacion concreta de entorno.

Solo escucha en 127.0.0.1: nunca expuesto a la red ni a internet (seccion
17). n8n y este servidor corren en la misma maquina.

Endpoints:
    GET  /health                -> {"status": "ok"}
    POST /process-due-routes    -> corre process_due_routes() y devuelve
                                    exactamente el mismo JSON que el CLI
                                    (ver runner.ProcessRunResult.to_dict()).
                                    Body opcional: {"today": "YYYY-MM-DD",
                                    "environment": "dev"|"test"|"prod"}
    GET  /history?route=...&vendor=...&limit=...
                                 -> filas del historial tecnico (HistoryStore).

Este servidor NO reimplementa nada: llama a las mismas funciones que usa
`cxc_bot.cli` (resolucion de archivos, generacion, validacion, snapshots,
idempotencia). Eso vive todo en el codigo ya existente, no aqui.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .business_calendar import (
    DEFAULT_SCHEDULING_CONFIG_PATH,
    load_business_calendar,
    load_preparation_lead_days,
    load_scheduling_settings,
    today_in_timezone,
)
from .business_history import DEFAULT_BUSINESS_DB_PATH, BusinessHistoryStore
from .catalog import VendorCatalog
from .config_versioning import DEFAULT_CONFIG_VERSIONS_DB_PATH, ConfigVersionStore
from .data_source import LocalMockDataSource
from .history_store import DEFAULT_HISTORY_PATH, HistoryStore
from .runner import process_due_routes
from .schedule_source import DEFAULT_SCHEDULE_PATH, LocalScheduleSource

logger = logging.getLogger("cxc_bot.api_server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _run_process_due_routes(overrides: dict) -> dict:
    settings = load_scheduling_settings(DEFAULT_SCHEDULING_CONFIG_PATH)
    routes_root = Path(overrides.get("routes_root", settings.get("routes_root", ".")))
    sap_data_path = overrides.get("data", settings.get("sap_mock_data_path"))
    lead_days = overrides.get("lead_days", load_preparation_lead_days())
    catchup_window_days = overrides.get("catchup_window_days", settings.get("catchup_window_dias"))
    environment = overrides.get("environment", "dev")

    schedule_source = LocalScheduleSource(DEFAULT_SCHEDULE_PATH)
    catalog = VendorCatalog.load()
    calendar = load_business_calendar()
    history = HistoryStore(DEFAULT_HISTORY_PATH)
    business_history = None if overrides.get("no_snapshot") else BusinessHistoryStore(DEFAULT_BUSINESS_DB_PATH)
    config_versions = None if overrides.get("no_snapshot") else ConfigVersionStore(db_path=DEFAULT_CONFIG_VERSIONS_DB_PATH)

    today = (
        datetime.strptime(overrides["today"], "%Y-%m-%d").date()
        if overrides.get("today")
        else today_in_timezone(calendar.timezone)
    )

    result = process_due_routes(
        schedule_source=schedule_source,
        catalog=catalog,
        calendar=calendar,
        lead_business_days=lead_days,
        routes_root=routes_root,
        sap_data_source=LocalMockDataSource(sap_data_path),
        sap_source_label=str(sap_data_path),
        history=history,
        today=today,
        business_history=business_history,
        config_versions=config_versions,
        environment=environment,
        sap_is_mock=True,
        catchup_window_days=catchup_window_days,
    )
    return result.to_dict()


class _Handler(BaseHTTPRequestHandler):
    server_version = "cxc-bot-api/1.0"

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # silenciar el log por defecto, usar logging propio
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):  # noqa: N802 - nombre requerido por BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if parsed.path == "/history":
            qs = parse_qs(parsed.query)
            history = HistoryStore(DEFAULT_HISTORY_PATH)
            route = qs.get("route", [None])[0]
            vendor = qs.get("vendor", [None])[0]
            limit = int(qs.get("limit", ["50"])[0])
            rows = history.find_by_route(route, vendor=vendor) if route else history.list_recent(limit=limit)
            self._send_json(200, {"rows": [dict(r) for r in rows]})
            return
        self._send_json(404, {"error": f"Ruta no encontrada: {parsed.path}"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/process-due-routes":
            self._send_json(404, {"error": f"Ruta no encontrada: {parsed.path}"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            overrides = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Body invalido: se esperaba JSON."})
            return
        try:
            payload = _run_process_due_routes(overrides)
            self._send_json(200, payload)
        except Exception as e:  # noqa: BLE001 - limite de la API: nunca tumbar el servidor
            logger.exception("Error procesando /process-due-routes")
            self._send_json(500, {"error": str(e)})


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = ThreadingHTTPServer((host, port), _Handler)
    logger.info("cxc-bot API escuchando en http://%s:%d (solo localhost)", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
