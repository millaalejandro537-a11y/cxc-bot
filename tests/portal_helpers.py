"""Utilidades de prueba del portal: app real en un hilo, cliente HTTP con cookies/CSRF, usuarios de prueba (valores de prueba locales)."""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from cxc_portal.app import Contexto, make_handler
from cxc_portal.demo import FuenteSintetica, sembrar_demo
from cxc_portal.estado import Estado
from cxc_portal.store import PortalServicio

CLAVE_ADMIN = "Admin-Prueba#2026x"
CLAVE_GER = "Gerencia-Prueba#2026x"
CLAVE_VEN = "Vendedor-Prueba#2026x"
CLAVE_VEN2 = "Vendedor2-Prueba#2026x"
ACTOR = {"id": None, "usuario": "test", "rol": "admin", "nombre": "test"}


class Reloj:
    """Reloj controlable para probar expiracion de sesiones."""

    def __init__(self):
        from datetime import datetime, timezone
        self.t = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def avanzar(self, segundos):
        from datetime import timedelta
        self.t = self.t + timedelta(seconds=segundos)


def crear_entorno(tmp_path: Path, reloj=None, hosts=None, https=False, confiar_proxy=False, limitador=None):
    estado = Estado(tmp_path / "estado" / "portal.sqlite3", **({"reloj": reloj} if reloj else {}))
    sembrar_demo(estado, ACTOR)
    estado.crear_vendedor(ACTOR, "Otro Vendedor")
    estado.guardar_ruta(ACTOR, "OTRA", "Ruta de Otro", "Otro Vendedor", "LOCAL", None, zonas={"OT": "Otra zona"},
                        clientes=[{"codigo": "C90101", "folder": "1 OT", "zona": "OT", "nombre": "CLIENTE AJENO"}])
    for usuario, nombre, rol, clave, vend in (("admin1", "Admin", "admin", CLAVE_ADMIN, None), ("ger1", "Gerente Uno", "gerencia", CLAVE_GER, None),
                                              ("ven1", "Vendedor Demo", "vendedor", CLAVE_VEN, "Vendedor Demo"),
                                              ("ven2", "Otro", "vendedor", CLAVE_VEN2, "Otro Vendedor")):
        estado.crear_usuario(ACTOR, usuario, nombre, rol, clave, vend, debe_cambiar=False)
    servicio = PortalServicio(estado, FuenteSintetica(), tmp_path / "estado", None, tmp_path / "datos")
    (tmp_path / "datos").mkdir(exist_ok=True)
    ctx = Contexto(servicio, estado, https=https, hosts_permitidos=hosts, confiar_proxy=confiar_proxy, limitador=limitador)
    return ctx


class Servidor:
    def __init__(self, ctx, tls=None):
        self.ctx = ctx
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctx, None))
        if tls:
            import ssl
            c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            c.load_cert_chain(*tls)
            self.srv.socket = c.wrap_socket(self.srv.socket, server_side=True)
        self.puerto = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def cerrar(self):
        self.srv.shutdown()
        self.srv.server_close()


class Cliente:
    def __init__(self, puerto, host="127.0.0.1", tls=None):
        self.puerto, self.host, self.tls = puerto, host, tls
        self.cookie, self.csrf, self.set_cookie = None, None, None

    def _con(self):
        if self.tls:  # `tls` = ruta del certificado autofirmado de la prueba, que se CONFIA explicitamente (verificacion activa)
            import ssl
            ctx = ssl.create_default_context(cafile=str(self.tls))
            return http.client.HTTPSConnection("127.0.0.1", self.puerto, context=ctx)
        return http.client.HTTPConnection("127.0.0.1", self.puerto)

    def pedir(self, metodo, ruta, cuerpo=None, csrf=True, cabeceras=None, host=None):
        h = {"Host": host or f"{self.host}:{self.puerto}"}
        if self.cookie:
            h["Cookie"] = f"cxc_session={self.cookie}"
        if csrf and self.csrf and metodo != "GET":
            h["X-CSRF-Token"] = self.csrf
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode()
            h["Content-Type"] = "application/json"
        h.update(cabeceras or {})
        c = self._con()
        c.request(metodo, ruta, body=datos, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        cab = {k.lower(): v for k, v in r.getheaders()}
        try:
            js = json.loads(raw) if cab.get("content-type", "").startswith("application/json") else None
        except json.JSONDecodeError:
            js = None
        return r.status, cab, js if js is not None else raw

    def login(self, usuario, clave):
        st, cab, js = self.pedir("POST", "/api/auth/login", {"usuario": usuario, "password": clave}, csrf=False)
        if st == 200:
            self.set_cookie = cab.get("set-cookie", "")
            self.cookie = self.set_cookie.split(";")[0].split("=", 1)[1]
            self.csrf = js["csrf"]
        return st, cab, js

    def get(self, ruta):
        return self.pedir("GET", ruta)

    def post(self, ruta, cuerpo=None, **kw):
        return self.pedir("POST", ruta, cuerpo if cuerpo is not None else {}, **kw)


def cliente_con_login(servidor, usuario, clave, **kw):
    c = Cliente(servidor.puerto, **kw)
    st, _, _ = c.login(usuario, clave)
    assert st == 200, (usuario, st)
    return c
