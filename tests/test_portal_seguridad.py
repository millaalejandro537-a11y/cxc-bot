"""Autenticacion, sesiones, CSRF, permisos por rol en la API, cabeceras, limites, host/origen, TLS y exposicion."""

import ssl
import subprocess
import time

import pytest

from cxc_portal import security as sec
from cxc_portal.server import validar_exposicion
from tests.portal_helpers import (CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, CLAVE_VEN2, Cliente, Reloj, Servidor, cliente_con_login, crear_entorno)


@pytest.fixture()
def env(tmp_path):
    reloj = Reloj()
    ctx = crear_entorno(tmp_path, reloj=reloj, limitador=sec.LimitadorIntentos(reloj=lambda: reloj().timestamp()))
    s = Servidor(ctx)
    yield s, ctx, reloj
    s.cerrar()


# ----------------------------------------------------------------------------- contraseñas
def test_scrypt_hash_y_verificacion():
    h = sec.hash_password("Una-Clave-Larga#123")
    assert h.startswith("scrypt$") and "Una-Clave" not in h
    assert sec.verificar_password("Una-Clave-Larga#123", h) and not sec.verificar_password("otra", h)
    assert not sec.verificar_password("x", "basura") and sec.hash_password("Una-Clave-Larga#123") != h  # sal distinta


@pytest.mark.parametrize("clave", ["corta1A!", "todominusculasylargas", "SOLOMAYUSCULASYLARGAS1", "sinsimbolosnidigitos"])
def test_politica_de_claves_rechaza_debiles(clave):
    with pytest.raises(sec.PasswordInvalida):
        sec.validar_password(clave)


def test_no_acepta_clave_con_el_nombre_de_usuario():
    with pytest.raises(sec.PasswordInvalida):
        sec.validar_password("ana-Segura#2026xx", "ana")


# ----------------------------------------------------------------------------- login / sesion
def test_toda_la_api_financiera_exige_sesion(env):
    s, _, _ = env
    c = Cliente(s.puerto)
    for ruta in ("/api/rutas", "/api/rutas/DEMO/clientes", "/api/clientes/C90001", "/api/alertas", "/api/prioridades", "/api/fuente", "/api/reglas",
                 "/api/rutas/DEMO/reporte.csv", "/api/reportes/gerencial", "/api/admin/usuarios", "/api/decisiones", "/api/visitas", "/api/auth/me"):
        st, _, js = c.get(ruta)
        assert st == 401, ruta
        assert b"saldo" not in (js if isinstance(js, bytes) else str(js).encode())
    assert c.get("/api/health")[0] == 200  # unico endpoint publico, sin datos


def test_login_ok_cookie_segura_y_credenciales_malas(env):
    s, _, _ = env
    c = Cliente(s.puerto)
    st, cab, js = c.login("ger1", CLAVE_GER)
    assert st == 200 and js["usuario"]["rol"] == "gerencia" and "password_hash" not in str(js) and CLAVE_GER not in str(js)
    ck = cab["set-cookie"]
    assert "HttpOnly" in ck and "SameSite=Strict" in ck and "Path=/" in ck and "Max-Age" in ck
    assert Cliente(s.puerto).login("ger1", "mala")[0] == 401
    assert Cliente(s.puerto).login("noexiste", "mala")[0] == 401
    # mismo mensaje: no se enumeran usuarios
    m1 = Cliente(s.puerto).login("ger1", "mala")[2]
    m2 = Cliente(s.puerto).login("noexiste", "mala")[2]
    assert m1 == m2


def test_bloqueo_tras_5_intentos_y_desbloqueo_por_tiempo(env):
    s, ctx, reloj = env
    c = Cliente(s.puerto)
    for _ in range(sec.MAX_INTENTOS):
        assert c.login("ger1", "mala")[0] == 401
    st, cab, js = c.login("ger1", CLAVE_GER)  # incluso con la clave correcta
    assert st == 429 and "retry-after" in cab
    reloj.avanzar(sec.BLOQUEO_SEG + 5)
    assert Cliente(s.puerto).login("ger1", CLAVE_GER)[0] == 200  # pasado el tiempo de bloqueo vuelve a permitir


def test_expira_por_inactividad_y_por_duracion_maxima(env):
    s, _, reloj = env
    c = cliente_con_login(s, "ger1", CLAVE_GER)
    assert c.get("/api/auth/me")[0] == 200
    reloj.avanzar(sec.SESION_IDLE_SEG + 1)
    assert c.get("/api/auth/me")[0] == 401
    c = cliente_con_login(s, "ger1", CLAVE_GER)
    for _ in range(int(sec.SESION_MAX_SEG // (sec.SESION_IDLE_SEG - 60)) + 1):  # actividad constante, pero 8 h es el tope
        reloj.avanzar(sec.SESION_IDLE_SEG - 60)
        c.get("/api/auth/me")
    assert c.get("/api/auth/me")[0] == 401


def test_logout_invalida_la_sesion(env):
    s, _, _ = env
    c = cliente_con_login(s, "ger1", CLAVE_GER)
    old = c.cookie
    assert c.post("/api/auth/logout")[0] == 200
    c.cookie = old
    assert c.get("/api/auth/me")[0] == 401


def test_usuario_desactivado_pierde_la_sesion(env):
    s, ctx, _ = env
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    uid = next(u["id"] for u in ctx.estado.listar_usuarios() if u["usuario"] == "ven1")
    assert adm.pedir("PATCH", f"/api/admin/usuarios/{uid}", {"activo": False})[0] == 200
    assert ven.get("/api/rutas")[0] == 401
    assert Cliente(s.puerto).login("ven1", CLAVE_VEN)[0] == 401


def test_cambio_de_clave_obligatorio_bloquea_el_resto(env):
    s, ctx, _ = env
    ctx.estado.crear_usuario({"id": None, "usuario": "t", "rol": "admin"}, "nuevo1", "Nuevo", "gerencia", "Temporal-Clave#2026x", None, debe_cambiar=True)
    c = cliente_con_login(s, "nuevo1", "Temporal-Clave#2026x")
    st, _, js = c.get("/api/rutas")
    assert st == 403 and js["debe_cambiar_password"] is True
    assert c.post("/api/auth/password", {"actual": "Temporal-Clave#2026x", "nueva": "corta"})[0] == 400
    assert c.post("/api/auth/password", {"actual": "mala", "nueva": "Nueva-Clave#2026xx"})[0] == 403
    assert c.post("/api/auth/password", {"actual": "Temporal-Clave#2026x", "nueva": "Nueva-Clave#2026xx"})[0] == 200
    assert c.get("/api/rutas")[0] == 401  # la sesion se cierra: debe entrar de nuevo
    c2 = cliente_con_login(s, "nuevo1", "Nueva-Clave#2026xx")
    assert c2.get("/api/rutas")[0] == 200


# ----------------------------------------------------------------------------- CSRF / origen / host
def test_csrf_obligatorio_en_mutaciones(env):
    s, _, _ = env
    c = cliente_con_login(s, "ger1", CLAVE_GER)
    cuerpo = {"codigo": "C90004", "clasificacion_final": "C", "motivo": "Decisión de prueba con motivo suficiente."}
    assert c.pedir("POST", "/api/decisiones", cuerpo, csrf=False)[0] == 403
    assert c.pedir("POST", "/api/decisiones", cuerpo, cabeceras={"X-CSRF-Token": "falso"})[0] == 403
    assert c.pedir("POST", "/api/decisiones", cuerpo)[0] == 201


def test_origen_ajeno_rechazado_en_mutaciones_y_login(env):
    s, _, _ = env
    c = Cliente(s.puerto)
    assert c.pedir("POST", "/api/auth/login", {"usuario": "ger1", "password": CLAVE_GER}, csrf=False, cabeceras={"Origin": "https://evil.example.com"})[0] == 403
    assert c.pedir("POST", "/api/auth/login", {"usuario": "ger1", "password": CLAVE_GER}, csrf=False, cabeceras={"Origin": f"http://127.0.0.1:{s.puerto}"})[0] == 200


def test_host_ajeno_rechazado_dns_rebinding(env):
    s, _, _ = env
    c = Cliente(s.puerto)
    for h in ("evil.example.com", "evil.example.com:8780", "10.0.0.5:8780"):
        assert c.pedir("GET", "/api/rutas", host=h)[0] == 403
        assert c.pedir("GET", "/api/health", host=h)[0] == 200      # solo la sonda de salud (ok/db) se atiende con cualquier Host: monitores de la plataforma
    assert c.pedir("GET", "/api/rutas", host=f"localhost:{s.puerto}")[0] == 401


def test_cabeceras_de_seguridad_y_sin_cache(env):
    s, _, _ = env
    c = cliente_con_login(s, "ger1", CLAVE_GER)
    _, cab, _ = c.get("/api/rutas")
    assert cab["x-frame-options"] == "DENY" and cab["x-content-type-options"] == "nosniff" and cab["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in cab["content-security-policy"] and "script-src 'self'" in cab["content-security-policy"]
    assert "strict-transport-security" not in cab  # solo con HTTPS
    assert "access-control-allow-origin" not in cab  # sin CORS: mismo origen


def test_campos_faltantes_y_errores_sin_detalle_interno(env):
    s, _, _ = env
    c = cliente_con_login(s, "ger1", CLAVE_GER)
    st, _, js = c.pedir("POST", "/api/decisiones", {"codigo": "C90004"})
    assert st == 400 and "Falta el campo" in js["error"]
    st, _, js = c.get("/api/clientes/C99999")
    assert st == 404 and "Traceback" not in str(js)


# ----------------------------------------------------------------------------- permisos por rol (aplicados en la API)
def test_vendedor_solo_ve_sus_rutas_y_clientes(env):
    s, _, _ = env
    v1 = cliente_con_login(s, "ven1", CLAVE_VEN)
    v2 = cliente_con_login(s, "ven2", CLAVE_VEN2)
    assert [r["route_id"] for r in v1.get("/api/rutas")[2]["rutas"]] == ["DEMO"]
    assert [r["route_id"] for r in v2.get("/api/rutas")[2]["rutas"]] == ["OTRA"]
    assert v1.get("/api/rutas/OTRA/clientes")[0] == 404 and v1.get("/api/rutas/OTRA/reporte.csv")[0] == 404
    assert v1.get("/api/clientes/C90101")[0] == 404           # cliente de otro vendedor: no se revela ni que existe
    assert v1.get("/api/clientes/C90001")[0] == 200 and v2.get("/api/clientes/C90001")[0] == 404
    al = v1.get("/api/alertas")[2]
    assert all(a["ruta"] == "DEMO" for a in al["cobro_operativas"] + al["historicas_comportamiento"])
    assert v1.get("/api/alertas?ruta=OTRA")[0] == 404


def test_vendedor_no_accede_a_cartera_global_reportes_decisiones_ni_admin(env):
    s, _, _ = env
    v = cliente_con_login(s, "ven1", CLAVE_VEN)
    for m, ruta in (("GET", "/api/prioridades"), ("GET", "/api/reportes/gerencial"), ("GET", "/api/reportes/gerencial.xlsx"), ("GET", "/api/decisiones"),
                    ("GET", "/api/admin/usuarios"), ("GET", "/api/admin/rutas"), ("GET", "/api/admin/auditoria"), ("POST", "/api/reportes/importar-corte")):
        assert v.pedir(m, ruta, {} if m == "POST" else None)[0] == 403, ruta
    assert v.post("/api/decisiones", {"codigo": "C90004", "clasificacion_final": "A", "motivo": "intento de un vendedor"})[0] == 403
    assert "historial_decisiones" not in v.get("/api/clientes/C90004")[2]


def test_solo_gerencia_decide_admin_administra_pero_no_decide(env):
    s, _, _ = env
    adm, ger = cliente_con_login(s, "admin1", CLAVE_ADMIN), cliente_con_login(s, "ger1", CLAVE_GER)
    cuerpo = {"codigo": "C90004", "clasificacion_final": "SOLO_CONTADO", "motivo": "Aprobado por gerencia en prueba."}
    assert adm.post("/api/decisiones", cuerpo)[0] == 403              # separacion de funciones
    assert ger.post("/api/decisiones", cuerpo)[0] == 201
    assert ger.post("/api/admin/vendedores", {"nombre": "X"})[0] == 403
    assert ger.post("/api/admin/rutas", {"route_id": "Z", "nombre": "Z", "vendedor": "V", "tipo_ruta": "LOCAL"})[0] == 403
    assert ger.get("/api/admin/usuarios")[0] == 403 and adm.get("/api/admin/usuarios")[0] == 200
    assert adm.get("/api/admin/auditoria")[0] == 200 and ger.get("/api/admin/auditoria")[0] == 200


def test_accesos_denegados_quedan_auditados(env):
    s, ctx, _ = env
    v = cliente_con_login(s, "ven1", CLAVE_VEN)
    v.get("/api/prioridades")
    assert any(e["accion"] == "acceso.denegado" and e["usuario"] == "ven1" for e in ctx.estado.listar_auditoria())
    Cliente(s.puerto).login("ger1", "mala")
    assert any(e["accion"] == "login.fallido" for e in ctx.estado.listar_auditoria())


# ----------------------------------------------------------------------------- exposicion / HTTPS
def test_exposicion_fuera_de_loopback_exige_https_y_hosts():
    validar_exposicion("127.0.0.1", False, False, [])
    for host in ("0.0.0.0", "192.168.1.5", "mi.dominio.com"):
        with pytest.raises(SystemExit, match="HTTPS"):
            validar_exposicion(host, False, False, ["mi.dominio.com"])
        with pytest.raises(SystemExit, match="hosts-permitidos"):
            validar_exposicion(host, True, False, [])
        validar_exposicion(host, True, False, ["mi.dominio.com"])
        validar_exposicion(host, False, True, ["mi.dominio.com"])


def _openssl_cert(tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], capture_output=True)
    if r.returncode != 0:
        pytest.skip("openssl no disponible")
    return cert, key


def test_https_directo_con_verificacion_cookie_secure_y_hsts(tmp_path):
    cert, key = _openssl_cert(tmp_path)
    ctx = crear_entorno(tmp_path, https=True, hosts=["localhost"])
    s = Servidor(ctx, tls=(str(cert), str(key)))
    try:
        c = Cliente(s.puerto, host="localhost", tls=cert)
        st, cab, js = c.login("ger1", CLAVE_GER)
        assert st == 200
        assert "Secure" in cab["set-cookie"] and "HttpOnly" in cab["set-cookie"] and "SameSite=Strict" in cab["set-cookie"]
        assert "max-age" in cab["strict-transport-security"]
        assert c.get("/api/rutas")[0] == 200
        # el servidor exige TLS >= 1.2 y rechaza HTTP plano sobre el puerto TLS
        assert s.srv.socket.context.minimum_version == ssl.TLSVersion.TLSv1_2
        import http.client
        con = http.client.HTTPConnection("127.0.0.1", s.puerto, timeout=3)
        with pytest.raises(Exception):
            con.request("GET", "/api/health")
            con.getresponse()
    finally:
        s.cerrar()


def test_detras_de_proxy_https_usa_ip_reenviada_y_cookie_secure(tmp_path):
    ctx = crear_entorno(tmp_path, https=True, hosts=["portal.ejemplo.com"], confiar_proxy=True)
    s = Servidor(ctx)
    try:
        c = Cliente(s.puerto, host="portal.ejemplo.com")
        st, cab, _ = c.pedir("POST", "/api/auth/login", {"usuario": "ger1", "password": CLAVE_GER}, csrf=False,
                             cabeceras={"X-Forwarded-For": "203.0.113.9", "Origin": "https://portal.ejemplo.com"}, host="portal.ejemplo.com")
        assert st == 200 and "Secure" in cab["set-cookie"] and "strict-transport-security" in cab
        assert any(e["ip"] == "203.0.113.9" for e in ctx.estado.listar_auditoria())
        assert Cliente(s.puerto).pedir("GET", "/api/rutas", host="otro.dominio.com")[0] == 403
    finally:
        s.cerrar()
