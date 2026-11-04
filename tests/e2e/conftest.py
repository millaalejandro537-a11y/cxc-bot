"""Pruebas E2E del Portal con Playwright + Chrome instalado (sin descargar navegadores). Servidor en loopback, estado temporal y datos 100 % sinteticos.
Se omiten solas (no fallan) si falta Playwright, Chrome/Chromium o portal-web/dist: `uv pip install playwright` y `cd portal-web && npm ci && npm run build`."""

import threading
import time
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from cxc_portal.app import make_handler
from cxc_portal.flujo import FlujoOperativo
from tests.portal_helpers import ACTOR, crear_entorno
from tests.test_hojas_visita_xlsx_sintetico import hojas_visita_sintetico

DIST = Path("portal-web/dist")
HOY = date(2026, 11, 6)
MOVIL = dict(viewport={"width": 375, "height": 812}, device_scale_factor=2, is_mobile=True, has_touch=True,
             user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
ESCRITORIO = dict(viewport={"width": 1280, "height": 800})
CODIGOS_OCC = [f"C91{i:03d}" for i in range(119)]


@pytest.fixture(scope="session")
def chrome():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright no instalado")
    if not (DIST / "index.html").is_file():
        pytest.skip("falta portal-web/dist (cd portal-web && npm ci && npm run build)")
    pw = sync_playwright().start()
    try:
        br = pw.chromium.launch(channel="chrome", headless=True)
    except Exception:
        try:
            br = pw.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pw.stop()
            pytest.skip(f"no hay Chrome/Chromium utilizable por Playwright: {str(e)[:80]}")
    yield br
    br.close()
    pw.stop()


@pytest.fixture()
def portal(tmp_path):
    reloj = {"t": time.time()}
    ctx = crear_entorno(tmp_path, reloj_totp=lambda: reloj["t"])
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: HOY)
    rutas = tmp_path / "datos" / "01_Rutas"
    rutas.mkdir(parents=True)
    demo = [c["codigo"] for c in ctx.estado.obtener_ruta("DEMO")["clientes"]]
    hojas_visita_sintetico(rutas / "hojas_demo.xlsx", demo)
    ctx.servicio.importar_documento(ACTOR, "DEMO", "hojas_demo.xlsx")
    # ruta del tamaño de Occidente (119 clientes, 8 hojas) para comprobar la paginacion real de la vista de impresion
    ctx.estado.guardar_ruta(ACTOR, "OCC119", "Occidente sintético", "Vendedor Demo", "FORANEA", 60, zonas={"ZA": "Zona A"}, requiere_hojas_visita=True,
                            clientes=[{"codigo": c, "folder": f"{i + 1} ZA", "zona": "ZA", "nombre": f"CLIENTE SINTETICO {c}"} for i, c in enumerate(CODIGOS_OCC)])
    hojas_visita_sintetico(rutas / "hojas_occ.xlsx", CODIGOS_OCC)
    ctx.servicio.importar_documento(ACTOR, "OCC119", "hojas_occ.xlsx")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctx, DIST))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield SimpleNamespace(base=f"http://127.0.0.1:{srv.server_address[1]}", puerto=srv.server_address[1], ctx=ctx, reloj=reloj, tmp=tmp_path)
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def abrir(chrome, portal):
    """Fabrica de paginas con vigilancia: cualquier error de JavaScript, mensaje de error en consola o respuesta 5xx hace fallar la prueba."""
    contextos, problemas = [], []

    def _abrir(modo="escritorio"):
        c = chrome.new_context(**(MOVIL if modo == "movil" else ESCRITORIO), locale="es-HN")
        contextos.append(c)
        p = c.new_page()
        p.set_default_timeout(8000)
        p.on("pageerror", lambda e: problemas.append(f"pageerror: {e}"))
        p.on("console", lambda m: problemas.append(f"console.{m.type}: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
        # 503 de /api/reportes/*: cxc-reporting-core es opcional y aqui no se configura (la interfaz lo explica); cualquier otro 5xx es un defecto
        p.on("response", lambda r: problemas.append(f"HTTP {r.status} {r.url}") if r.status >= 500 and not (r.status == 503 and "/api/reportes/" in r.url) else None)
        return p

    yield _abrir
    for c in contextos:
        c.close()
    assert problemas == [], f"errores de interfaz detectados: {problemas[:5]}"
