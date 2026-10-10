"""API HTTP local del portal CxC (solo libreria estandar; solo lectura; solo 127.0.0.1 por defecto).

    python -m cxc_portal.server [--port 8780] [--web portal-web/dist]

Endpoints (todos GET, JSON salvo el CSV):
    /api/health
    /api/fuentes                     fuentes cargadas y estado de la fuente EN VIVO (no conectada)
    /api/reglas                      reglas del semaforo vigentes
    /api/rutas                       resumen por ruta
    /api/rutas/<id>/clientes         ?zona=&clasificacion=&q=   (orden de folders de la ruta)
    /api/rutas/<id>/reporte.csv      descarga
    /api/clientes/<codigo>           detalle + ultimas 10 facturas + explicacion
    /api/prioridades                 ?ruta=&n=10  mayores saldos del snapshot (consulta 1, 08/10/2026)
    /api/alertas                     ?ruta=  alertas de cobro (facturas ABIERTAS > 60 d) e historicas (PAGADAS > 60 d), separadas
No hay endpoints de escritura: el portal no escribe en SAP ni en Cosprobell BD.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from cxc_bot import payment_semaphore as sm

from .bridge import estado_reporting_core
from .store import RouteStore, Ruta

DEFAULT_PORT = 8780
DEFAULT_WEB = Path(__file__).resolve().parent.parent / "portal-web" / "dist"

EN_VIVO = {
    "disponible": False,
    "motivo": "Sin credencial SELECT dedicada a Cosprobell BD ni conexión SAP. Todos los datos mostrados son snapshots históricos o sintéticos.",
}

REGLAS = {
    "vigencia": "Hub Cosprobell CxC — Principal, decisiones 08–09/10/2026",
    "ventana_facturas": sm.VENTANA_FACTURAS,
    "minimo_historial": sm.MIN_HISTORIAL,
    "umbrales_media_mora": {"A": f"≤ {sm.TOLERANCIA_A}", "B": f"> {sm.TOLERANCIA_A} y ≤ {sm.LIMITE_B}", "C": f"> {sm.LIMITE_B} y ≤ {sm.LIMITE_C}", "CANDIDATO_SOLO_CONTADO": f"> {sm.LIMITE_C}"},
    "abiertas_entran_a_la_media_si": f"mora > {sm.ABIERTA_MORA_MINIMA} días",
    "alerta_por_factura": f"mora > {sm.ALERTA_FACTURA} días: abiertas = alerta operativa de cobro; pagadas = alerta histórica de comportamiento (nunca cobro pendiente)",
    "plazos": {"foranea": sm.PLAZO_FORANEA, "local": sm.PLAZO_LOCAL},
    "notas": [
        "Se evalúan las 10 facturas más recientes (emisión DESC, desempate número DESC).",
        "0–3 facturas en todo el historial: cliente de prueba (blanca).",
        "Mora = max(0, último recibo contable (o corte si está abierta) − emisión − plazo de la ruta). El vencimiento SAP se conserva, no es la base.",
        "La media usa facturas pagadas íntegramente por recibos y abiertas con mora > 5 días; no se redondea antes de clasificar.",
        "Más de 60 días es solo candidato a solo contado: la viñeta roja la decide gerencia.",
        "Las decisiones de gerencia se guardan aparte; el cálculo base no se altera.",
    ],
}


def _clientes_filtrados(r: Ruta, qs: dict) -> List[dict]:
    zona = (qs.get("zona", [""])[0] or "").upper()
    clas = (qs.get("clasificacion", [""])[0] or "").upper()
    q = (qs.get("q", [""])[0] or "").strip().upper()
    out = []
    for c in r.clientes:  # orden de la lista = orden de folders confirmado
        if zona and c.zona.upper() != zona:
            continue
        if clas and c.resultado.final.upper() != clas:
            continue
        if q and q not in c.codigo.upper() and q not in c.nombre.upper() and q not in c.folder.upper():
            continue
        out.append(r.cliente_dict(c))
    return out


def _csv(r: Ruta) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Ruta", r.nombre, "Fuente", r.fuente.get("etiqueta"), "Corte", r.fuente.get("fecha_corte")])
    w.writerow(["Folder", "Zona", "Codigo", "Cliente", "Saldo", "Clasificacion calculada", "Clasificacion final", "Color final",
                "Mora media", "Facturas historial", "Facturas evaluables", "Alertas de cobro (abiertas > 60 d)", "Alertas historicas (pagadas > 60 d)", "Decision de gerencia"])
    for c in r.clientes:
        s = c.resultado
        dec = s.decision_gerencia
        w.writerow([c.folder, c.zona, c.codigo, c.nombre, f"{c.saldo:.2f}", s.calculado, s.final, s.color_final,
                    f"{s.promedio_mora:.2f}" if s.promedio_mora is not None else "", s.historial_total, s.evaluables, len(s.alertas_cobro), len(s.alertas_historicas),
                    f"{dec.actor}: {dec.motivo}" if dec else ""])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def make_handler(store: RouteStore, web_dir: Optional[Path]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "cxc-portal/0.1"

        def log_message(self, fmt, *args):  # sin ruido en consola
            pass

        def _cors(self):
            origin = self.headers.get("Origin", "")
            if origin.startswith(("http://localhost:", "http://127.0.0.1:")):
                self.send_header("Access-Control-Allow-Origin", origin)

        def _send(self, status: int, body: bytes, ctype: str, extra: Optional[dict] = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors()
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200):
            self._send(status, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.end_headers()

        def do_POST(self):  # noqa: N802
            self._json({"error": "El portal es de solo lectura."}, 405)

        do_PUT = do_DELETE = do_PATCH = do_POST  # noqa: N815

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
            qs = parse_qs(u.query)
            if not parts or parts[0] != "api":
                return self._static(u.path)
            try:
                return self._api(parts[1:], qs)
            except Exception as e:  # noqa: BLE001 - limite de la API
                return self._json({"error": f"Error interno: {e}"}, 500)

        def _api(self, p: List[str], qs: dict):
            if p == ["health"]:
                return self._json({"status": "ok", "modo": "solo-lectura"})
            if p == ["fuentes"]:
                return self._json({
                    "en_vivo": EN_VIVO,
                    "rutas": [{"route_id": r.route_id, "nombre": r.nombre, "fuente": r.fuente} for r in store.rutas.values()],
                    "errores_de_carga": store.errores,
                    "avisos_de_conciliacion": {r.route_id: r.avisos for r in store.rutas.values() if r.avisos},
                    "reporting_core": estado_reporting_core(),
                })
            if p == ["reglas"]:
                return self._json(REGLAS)
            if p == ["rutas"]:
                return self._json({"rutas": store.listar(), "en_vivo": EN_VIVO})
            if len(p) == 3 and p[0] == "rutas" and p[2] == "clientes":
                r = store.ruta(p[1])
                if not r:
                    return self._json({"error": "Ruta no encontrada"}, 404)
                return self._json({"ruta": r.resumen(), "zonas": r.zonas, "clientes": _clientes_filtrados(r, qs)})
            if len(p) == 3 and p[0] == "rutas" and p[2] == "reporte.csv":
                r = store.ruta(p[1])
                if not r:
                    return self._json({"error": "Ruta no encontrada"}, 404)
                return self._send(200, _csv(r), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="cxc_{r.route_id}_{r.fuente.get("fecha_corte")}.csv"'})
            if len(p) == 2 and p[0] == "clientes":
                hit = store.buscar_cliente(p[1])
                if not hit:
                    return self._json({"error": "Cliente no encontrado"}, 404)
                r, c = hit
                return self._json(r.cliente_dict(c))
            if p == ["prioridades"]:
                n = max(1, min(50, int((qs.get("n", ["10"])[0]) or 10)))
                rid = qs.get("ruta", [""])[0]
                rutas = [store.ruta(rid)] if rid and store.ruta(rid) else [r for r in store.rutas.values() if r.fuente.get("tipo") != "SINTETICO_DEMO"]
                items = [(r, c) for r in rutas for c in r.clientes if c.saldo > 0]
                items.sort(key=lambda rc: rc[1].saldo, reverse=True)
                return self._json({
                    "nota": "Mayores saldos del snapshot. No es cartera en vivo; para priorizar cobro se requiere fuente fiable.",
                    "prioridades": [{**r.cliente_dict(c), "ruta_nombre": r.nombre} for r, c in items[:n]]})
            if p == ["alertas"]:
                rid = qs.get("ruta", [""])[0]
                rutas = [store.ruta(rid)] if rid and store.ruta(rid) else [r for r in store.rutas.values() if r.fuente.get("tipo") != "SINTETICO_DEMO"]
                cobro, hist = [], []
                for r in rutas:
                    for c in r.clientes:
                        base = {"codigo": c.codigo, "cliente": c.nombre, "folder": c.folder, "ruta": r.route_id, "ruta_nombre": r.nombre,
                                "vendedor": r.vendedor, "fecha_corte": r.fuente.get("fecha_corte"), "clasificacion_final": c.resultado.final}
                        cobro += [{**base, **a.to_dict()} for a in c.resultado.alertas_cobro]
                        hist += [{**base, **a.to_dict()} for a in c.resultado.alertas_historicas]
                cobro.sort(key=lambda a: (a["saldo_pendiente"] or 0), reverse=True)
                hist.sort(key=lambda a: a["mora"], reverse=True)
                return self._json({
                    "nota": "Datos del snapshot, no en vivo. Las alertas de cobro son operativas (hay saldo pendiente); las históricas son de comportamiento y NO son cobro pendiente.",
                    "cobro_operativas": cobro, "historicas_comportamiento": hist})
            return self._json({"error": "Endpoint no encontrado"}, 404)

        def _static(self, path: str):
            if not web_dir or not web_dir.is_dir():
                return self._json({"error": "Frontend no construido. Ejecuta `npm run build` en portal-web/ o usa `npm run dev`.",
                                   "api": "/api/rutas"}, 404)
            rel = path.lstrip("/") or "index.html"
            target = (web_dir / rel).resolve()
            if web_dir.resolve() not in target.parents and target != web_dir.resolve():
                return self._json({"error": "Ruta no permitida"}, 403)
            if not target.is_file():
                target = web_dir / "index.html"  # SPA
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            return self._send(200, target.read_bytes(), ctype)

    return Handler


LOOPBACK = ("127.0.0.1", "localhost", "::1")


def run(host: str, port: int, data_dir: Optional[Path], web_dir: Optional[Path]) -> None:
    # Sin autenticacion real el portal SOLO escucha en la maquina local (datos privados de clientes).
    if host not in LOOPBACK:
        raise SystemExit(f"Host '{host}' rechazado: el portal solo puede escuchar en loopback ({', '.join(LOOPBACK)}) hasta que exista autenticación real.")
    store = RouteStore(data_dir)
    srv = ThreadingHTTPServer((host, port), make_handler(store, web_dir))
    print(f"Portal CxC (solo lectura) en http://{host}:{port}  | rutas cargadas: {', '.join(store.rutas) or '(ninguna)'}")
    for e in store.errores:
        print("  AVISO:", e, file=sys.stderr)
    for r in store.rutas.values():
        for a in r.avisos:
            print(f"  AVISO conciliación {r.route_id}:", a, file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Portal CxC - API local de solo lectura")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--datos", type=Path, help="Directorio PRIVADO con 01_Rutas/ y 02_SAP_08Oct2026/ (o variable CXC_DATOS_DIR)")
    ap.add_argument("--web", type=Path, default=DEFAULT_WEB)
    a = ap.parse_args(argv)
    run(a.host, a.port, a.datos, a.web)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
