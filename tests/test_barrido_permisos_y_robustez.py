"""Barrido de TODOS los endpoints declarados (88): (1) sin sesion nadie entra; (2) un rol que no esta en `roles` recibe 403 (no 200/404/500);
(3) ningun endpoint responde 5xx (salvo el 503 deliberado de cxc-reporting-core sin configurar) ante peticiones vacias o con identificadores inexistentes, con cualquier rol. Datos sinteticos."""

import re

import pytest

from cxc_portal import app
from tests.portal_helpers import CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, Servidor, cliente_con_login, crear_entorno

VALORES = {"rid": "NO_EXISTE", "codigo": "C00000", "uid": "999", "did": "999", "sid": "999", "eid": "999", "iid": "999", "aid": "999", "cid": "999", "vid": "999",
           "fecha": "2026-01-01"}


def _concreta(rx):
    return re.sub(r"\(\?P<(\w+)>\[\^/\]\+\)", lambda m: VALORES.get(m.group(1), "999"), rx.pattern.strip("^$"))


ENTRADAS = [(m, _concreta(rx), roles, publico) for m, rx, roles, _, publico in app.RUTAS]


@pytest.fixture(scope="module")
def sitio(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("barrido")
    ctx = crear_entorno(tmp)
    s = Servidor(ctx)
    clientes = {"admin": cliente_con_login(s, "admin1", CLAVE_ADMIN), "gerencia": cliente_con_login(s, "ger1", CLAVE_GER), "vendedor": cliente_con_login(s, "ven1", CLAVE_VEN)}
    yield s, clientes
    s.cerrar()


def _llamar(cli, metodo, ruta):
    return cli.pedir(metodo, ruta, {} if metodo in ("POST", "PATCH", "PUT", "DELETE") else None)


def test_se_barren_todos_los_endpoints():
    assert len(ENTRADAS) >= 85


def test_sin_sesion_nadie_entra_a_un_endpoint_protegido(sitio):
    import http.client
    s, _ = sitio
    fallos = []
    for metodo, ruta, roles, publico in ENTRADAS:
        if publico:
            continue
        c = http.client.HTTPConnection("127.0.0.1", s.puerto, timeout=10)
        c.request(metodo, ruta, body=b"{}" if metodo != "GET" else None, headers={"Content-Type": "application/json"})
        st = c.getresponse().status
        c.close()
        if st not in (401, 403):
            fallos.append((metodo, ruta, st))
    assert fallos == []


def test_un_rol_fuera_de_la_lista_recibe_403(sitio):
    _, clientes = sitio
    fallos = []
    for metodo, ruta, roles, publico in ENTRADAS:
        if publico or roles is None:
            continue
        for rol, cli in clientes.items():
            if rol in roles:
                continue
            st = _llamar(cli, metodo, ruta)[0]
            if st != 403:
                fallos.append((rol, metodo, ruta, st))
    assert fallos == []


def test_ningun_endpoint_responde_5xx_con_peticiones_vacias_o_ids_inexistentes(sitio):
    _, clientes = sitio
    fallos = []
    for metodo, ruta, roles, publico in ENTRADAS:
        if publico or "logout" in ruta or "ciclo" in ruta:
            continue
        for rol, cli in clientes.items():
            st, _, cuerpo = _llamar(cli, metodo, ruta)
            if st >= 500 and not (st == 503 and "cxc-reporting-core no está disponible" in str(cuerpo)):      # 503 deliberado: dependencia opcional no configurada
                fallos.append((rol, metodo, ruta, st))
    assert fallos == []
