"""Arranque del portal CxC (servidor HTTP propio, solo libreria estandar).

Modos de exposicion (verificables; ver docs/DESPLIEGUE-SEGURO.md):
  * LOCAL (por defecto): escucha solo en 127.0.0.1. HTTP simple; cookies sin 'Secure'.
  * HTTPS DIRECTO: --tls-cert/--tls-key (TLS >= 1.2). Permite --host no-loopback, SOLO con --hosts-permitidos.
  * DETRAS DE PROXY HTTPS (Caddy/nginx/tunel): --detras-de-proxy-https; la app sigue escuchando en loopback y el proxy termina TLS.
Nunca se escucha fuera de loopback sin TLS ni sin lista explicita de hosts. La autenticacion es SIEMPRE obligatoria.
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional

from .app import LOOPBACK, Contexto, make_handler
from .estado import Estado
from .fuentes import crear_fuente
from .store import ENV_DATOS, ENV_ESTADO, PortalServicio

DEFAULT_PORT = 8780
DEFAULT_WEB = Path(__file__).resolve().parent.parent / "portal-web" / "dist"


def construir(data_dir: Optional[Path], estado_dir: Optional[Path], *, demo: bool = False, https: bool = False,
              hosts: Optional[List[str]] = None, confiar_proxy: bool = False, reporting_src: Optional[str] = None, reloj=None,
              roles_2fa: tuple = (), perimetro_token: Optional[str] = None):
    data_dir = Path(data_dir) if data_dir else (Path(os.environ[ENV_DATOS]) if os.environ.get(ENV_DATOS) else None)
    if estado_dir is None:
        estado_dir = Path(os.environ[ENV_ESTADO]) if os.environ.get(ENV_ESTADO) else ((data_dir.parent / "estado") if data_dir else None)
    if estado_dir is None:
        raise SystemExit("Falta el directorio de estado (--estado o CXC_ESTADO_DIR): ahí vive la BD propia de usuarios, decisiones y visitas.")
    if reporting_src:
        os.environ["CXC_REPORTING_SRC"] = reporting_src
    estado = Estado(Path(estado_dir) / "portal.sqlite3", **({"reloj": reloj} if reloj else {}))
    if demo:
        from .demo import FuenteSintetica
        fuente = FuenteSintetica()
    else:
        fuente = crear_fuente(data_dir, os.environ.get("CXC_FUENTE", "snapshot"))
    reporting = None
    if fuente is not None and os.environ.get("CXC_REPORTING_SRC"):
        from .reporting_bridge import ReportingNoDisponible, ReportingService
        try:
            reporting = ReportingService(fuente, Path(estado_dir))
        except ReportingNoDisponible as e:
            print(f"AVISO: {e}", file=sys.stderr)
    if demo:
        from .demo import sembrar_demo
        sembrar_demo(estado, {"id": None, "usuario": "demo", "rol": "admin"})
    servicio = PortalServicio(estado, fuente, Path(estado_dir), reporting, data_dir)
    return Contexto(servicio, estado, https=https, hosts_permitidos=hosts, confiar_proxy=confiar_proxy, roles_2fa=roles_2fa, perimetro_token=perimetro_token)


def validar_exposicion(host: str, tls: bool, detras_de_proxy: bool, hosts: List[str]) -> None:
    if host in LOOPBACK:
        return
    if not (tls or detras_de_proxy):
        raise SystemExit(f"Host '{host}' rechazado: fuera de loopback se exige HTTPS (--tls-cert/--tls-key) o --detras-de-proxy-https.")
    if not hosts:
        raise SystemExit("Fuera de loopback se exige --hosts-permitidos con los nombres de host públicos (defensa contra Host falsos).")


def run(host: str, port: int, ctx: Contexto, web_dir: Optional[Path], tls_cert: Optional[Path] = None, tls_key: Optional[Path] = None) -> None:
    srv = ThreadingHTTPServer((host, port), make_handler(ctx, web_dir))
    if tls_cert and tls_key:
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        c.minimum_version = ssl.TLSVersion.TLSv1_2
        c.load_cert_chain(str(tls_cert), str(tls_key))
        srv.socket = c.wrap_socket(srv.socket, server_side=True)
    esquema = "https" if ctx.https else "http"
    usuarios = ctx.estado.listar_usuarios()
    print(f"Portal CxC en {esquema}://{host}:{port} | rutas: {', '.join(ctx.servicio.rutas()) or '(ninguna)'} | usuarios: {len(usuarios)}", flush=True)
    if not usuarios:
        print("AVISO: no hay usuarios. Cree el primero: python -m cxc_portal.admin crear-usuario --usuario <u> --rol admin", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Portal CxC (autenticación obligatoria, solo libreria estandar)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))  # PORT: lo inyectan las plataformas (Railway/Render)
    ap.add_argument("--datos", type=Path, help="Directorio PRIVADO con 01_Rutas/ y 02_SAP_08Oct2026/ (o CXC_DATOS_DIR)")
    ap.add_argument("--estado", type=Path, help="Directorio PRIVADO de la BD propia (o CXC_ESTADO_DIR; por defecto <datos>/../estado)")
    ap.add_argument("--web", type=Path, default=DEFAULT_WEB)
    ap.add_argument("--reporting-src", help="Ruta a cxc-reporting-core/src (o CXC_REPORTING_SRC)")
    ap.add_argument("--demo", action="store_true", help="Usa datos SINTÉTICOS (ruta DEMO) en lugar del snapshot: solo desarrollo/pruebas")
    ap.add_argument("--tls-cert", type=Path)
    ap.add_argument("--tls-key", type=Path)
    ap.add_argument("--detras-de-proxy-https", action="store_true", help="Un proxy externo termina TLS: cookies 'Secure' y HSTS")
    ap.add_argument("--hosts-permitidos", default="", help="Nombres de host públicos separados por coma")
    ap.add_argument("--requerir-2fa", default=None, help="Roles que deben activar segundo factor (coma). Por defecto: ninguno en loopback; admin,gerencia,vendedor fuera de loopback")
    ap.add_argument("--exigir-perimetro", action="store_true",
                    help="Exige el encabezado X-Perimeter-Token (valor en la variable de entorno CXC_PERIMETER_TOKEN): impide el acceso directo que evada el perímetro (Cloudflare/proxy)")
    a = ap.parse_args(argv)
    hosts = [h.strip() for h in a.hosts_permitidos.split(",") if h.strip()]
    tls = bool(a.tls_cert and a.tls_key)
    if bool(a.tls_cert) != bool(a.tls_key):
        raise SystemExit("--tls-cert y --tls-key van juntos.")
    validar_exposicion(a.host, tls, a.detras_de_proxy_https, hosts)
    expuesto = a.host not in LOOPBACK or a.detras_de_proxy_https
    roles_2fa = tuple(r.strip() for r in (a.requerir_2fa if a.requerir_2fa is not None else ("admin,gerencia,vendedor" if expuesto else "")).split(",") if r.strip())
    token = os.environ.get("CXC_PERIMETER_TOKEN") if a.exigir_perimetro else None
    if a.exigir_perimetro and (not token or len(token) < 24):
        raise SystemExit("--exigir-perimetro requiere la variable de entorno CXC_PERIMETER_TOKEN con al menos 24 caracteres (secreto compartido con el perímetro).")
    if expuesto and not roles_2fa:
        raise SystemExit("Expuesto a la red: el segundo factor es obligatorio (--requerir-2fa).")
    ctx = construir(a.datos, a.estado, demo=a.demo, https=tls or a.detras_de_proxy_https, hosts=hosts, confiar_proxy=a.detras_de_proxy_https,
                    reporting_src=a.reporting_src, roles_2fa=roles_2fa, perimetro_token=token)
    run(a.host, a.port, ctx, a.web, a.tls_cert, a.tls_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
