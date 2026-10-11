"""Auditoria previa al despliegue: integridad de respaldo/migracion (incluye documentos), salud tras el proxy, IP real tras proxy, timeouts y
coherencia de los archivos de despliegue. Todo con datos SINTETICOS; no requiere Docker."""

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from cxc_portal.app import make_handler
from cxc_portal.estado import ErrorNegocio, Estado
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, Cliente, Servidor, crear_entorno

RAIZ = Path(__file__).resolve().parent.parent


def _con_documento(tmp_path):
    ctx = crear_entorno(tmp_path)
    datos = b"PK-contenido-sintetico-de-hoja-de-visita"
    d = ctx.estado.registrar_documento(ACTOR, "DEMO", "HOJA_VISITA", "demo.xlsx", datos, {"hojas": [], "total_clientes": 0}, tmp_path / "estado" / "documentos")
    return ctx, d, datos


# ------------------------------------------------------------------ respaldo / migracion no pierden documentos
def test_respaldo_incluye_los_archivos_de_documentos(tmp_path):
    ctx, d, datos = _con_documento(tmp_path)
    ctx.estado.respaldar(tmp_path / "bk")
    copia = tmp_path / "bk" / "documentos" / d["almacenado"]
    assert copia.is_file() and copia.read_bytes() == datos
    ctx.estado.respaldar(tmp_path / "bk")                                  # idempotente: no duplica ni falla
    assert copia.read_bytes() == datos


def test_exportar_importar_conserva_documentos_cola_y_archivos(tmp_path):
    ctx, d, datos = _con_documento(tmp_path)
    ctx.estado.solicitar_impresion(ACTOR, "HOJA_VISITA", route_id="DEMO", documento_id=d["id"])
    dump = json.loads(json.dumps(ctx.estado.exportar_json(), default=str))
    assert len(dump["tablas"]["documentos"]) == 1 and len(dump["tablas"]["cola_impresion"]) == 1 and d["almacenado"] in dump["archivos_documentos"]
    destino = Estado(tmp_path / "nuevo" / "portal.sqlite3")
    cuenta = destino.importar_json(dump)
    assert cuenta["documentos"] == 1 and cuenta["cola_impresion"] == 1 and cuenta["archivos_documentos"] == 1
    assert (tmp_path / "nuevo" / "documentos" / d["almacenado"]).read_bytes() == datos
    assert destino.documento_vigente("DEMO")["sha256"] == hashlib.sha256(datos).hexdigest()


def test_importar_rechaza_un_archivo_de_documento_alterado_sin_escribir_nada(tmp_path):
    ctx, d, _ = _con_documento(tmp_path)
    dump = json.loads(json.dumps(ctx.estado.exportar_json(), default=str))
    dump["archivos_documentos"][d["almacenado"]] = base64.b64encode(b"otro contenido").decode()
    destino = Estado(tmp_path / "nuevo" / "portal.sqlite3")
    with pytest.raises(ErrorNegocio, match="hash"):
        destino.importar_json(dump)
    assert destino.listar_rutas() == [] and not (tmp_path / "nuevo" / "documentos").exists()


def test_volcado_antiguo_sin_documentos_sigue_importando(tmp_path):
    ctx = crear_entorno(tmp_path)
    dump = json.loads(json.dumps(ctx.estado.exportar_json(), default=str))
    dump.pop("archivos_documentos")
    for t in ("documentos", "salida_documentos", "agentes_impresion", "cola_impresion"):
        dump["tablas"].pop(t)
    assert Estado(tmp_path / "n" / "portal.sqlite3").importar_json(dump)["usuarios"] >= 4


# ------------------------------------------------------------------ detras del proxy de la plataforma
@pytest.fixture()
def tras_proxy(tmp_path):
    ctx = crear_entorno(tmp_path, hosts=["portal.ejemplo.test"], https=True, confiar_proxy=True)
    s = Servidor(ctx)
    yield s, ctx
    s.cerrar()


def test_salud_responde_con_el_host_del_monitor_pero_el_resto_sigue_protegido(tras_proxy):
    s, _ = tras_proxy
    c = Cliente(s.puerto)
    assert c.pedir("GET", "/api/health", host="healthcheck.railway.app")[0] == 200       # healthcheck de la plataforma
    assert c.pedir("GET", "/api/rutas", host="healthcheck.railway.app")[0] == 403        # cualquier otra ruta con Host ajeno: rechazada
    assert c.pedir("POST", "/api/auth/login", {"usuario": "a", "password": "b"}, csrf=False, host="malo.test")[0] == 403
    assert c.pedir("GET", "/api/rutas", host="portal.ejemplo.test")[0] == 401


def test_salud_no_exime_el_token_de_perimetro_de_las_demas_rutas(tmp_path):
    ctx = crear_entorno(tmp_path, hosts=["portal.ejemplo.test"], https=True, confiar_proxy=True, perimetro_token="p" * 32)
    s = Servidor(ctx)
    try:
        c = Cliente(s.puerto)
        assert c.pedir("GET", "/api/health", host="healthcheck.railway.app")[0] == 200
        assert c.pedir("GET", "/api/rutas", host="portal.ejemplo.test")[0] == 403
        assert c.pedir("GET", "/api/rutas", host="portal.ejemplo.test", cabeceras={"X-Perimeter-Token": "p" * 32})[0] == 401
    finally:
        s.cerrar()


def test_ip_tras_proxy_no_se_deja_falsificar_por_el_cliente(tras_proxy):
    """El cliente puede inventar las primeras entradas de X-Forwarded-For; la confiable es la que agrega el proxy (la ultima)."""
    s, ctx = tras_proxy
    c = Cliente(s.puerto)
    for i in range(5):  # el atacante cambia su 'IP' en cada intento, pero el proxy anota siempre la real (198.51.100.7)
        st = c.pedir("POST", "/api/auth/login", {"usuario": "admin1", "password": "incorrecta"}, csrf=False, host="portal.ejemplo.test",
                     cabeceras={"X-Forwarded-For": f"10.0.0.{i}, 198.51.100.7"})[0]
        assert st == 401
    st, _, js = c.pedir("POST", "/api/auth/login", {"usuario": "admin1", "password": CLAVE_ADMIN}, csrf=False, host="portal.ejemplo.test",
                        cabeceras={"X-Forwarded-For": "10.9.9.9, 198.51.100.7"})
    assert st == 429                                                                   # bloqueado por la IP real, no por la inventada


def test_hilo_de_conexion_tiene_timeout(tmp_path):
    ctx = crear_entorno(tmp_path)
    assert make_handler(ctx, None).timeout == 30


# ------------------------------------------------------------------ coherencia de los archivos de despliegue
def test_archivos_de_despliegue_son_coherentes():
    docker = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^\s*VOLUME\b", docker, re.M)                                  # las plataformas montan el volumen; VOLUME esta prohibido/ignorado
    assert not re.search(r"^\s*USER\b", docker, re.M) and "ENTRYPOINT" in docker        # el entrypoint corrige la propiedad del volumen y baja de privilegios
    assert "--detras-de-proxy-https" in docker and "--hosts-permitidos" in docker and "0.0.0.0" in docker
    assert re.search(r'"openpyxl==\d', docker) and "tzdata" in docker                   # dependencias de ejecucion fijadas
    assert not re.search(r"^RUN .*pytest", docker, re.M)
    entry = (RAIZ / "deploy" / "entrypoint.sh").read_bytes()
    assert entry.startswith(b"#!/bin/sh\n") and b"\r" not in entry and b"setpriv --reuid=10001" in entry
    assert "10001" in docker                                                             # mismo UID que el entrypoint
    assert "eol=lf" in (RAIZ / ".gitattributes").read_text(encoding="utf-8")
    toml = (RAIZ / "railway.toml").read_text(encoding="utf-8")
    assert 'healthcheckPath = "/api/health"' in toml and "numReplicas = 1" in toml       # SQLite: una sola instancia
    ignore = (RAIZ / ".dockerignore").read_text(encoding="utf-8")
    assert "*.sqlite3" in ignore and ".env" in ignore and "tests" in ignore              # nada de BD, secretos ni datos reales dentro de la imagen
