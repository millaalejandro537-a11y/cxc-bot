"""Segundo factor (TOTP), perimetro, salud/monitoreo, respaldo/restauracion y exportacion portable de la BD propia."""

import json
from pathlib import Path

import pytest

from cxc_portal import security as sec
from cxc_portal.estado import ErrorNegocio, Estado
from tests.portal_helpers import (ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, Cliente, Servidor, cliente_con_login, crear_entorno)


class RelojTotp:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def avanzar(self, seg):
        self.t += seg


# ----------------------------------------------------------------------------- TOTP
def test_totp_cumple_el_vector_de_prueba_de_la_rfc_6238():
    secreto = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"      # ASCII "12345678901234567890" en base32
    assert sec.totp_codigo(secreto, 59) == "287082"      # RFC 6238, T=59 (6 dígitos del vector 94287082)
    assert sec.totp_codigo(secreto, 1111111109) == "081804"
    assert sec.totp_verificar(secreto, "287082", 59) == 1
    assert sec.totp_verificar(secreto, "287082", 59, ultimo_paso=1) is None          # anti-repetición
    assert sec.totp_verificar(secreto, "000000", 59) is None and sec.totp_verificar(secreto, "12", 59) is None
    assert sec.totp_verificar(secreto, "287082", 59 + 30) == 1                         # tolera ±1 intervalo
    assert sec.totp_verificar(secreto, "287082", 59 + 120) is None                     # fuera de la ventana


@pytest.fixture()
def env2fa(tmp_path):
    reloj = RelojTotp()
    ctx = crear_entorno(tmp_path, roles_2fa=("admin", "gerencia"), reloj_totp=reloj)
    s = Servidor(ctx)
    yield s, ctx, reloj
    s.cerrar()


def _activar(s, ctx, reloj, usuario="ger1", clave=CLAVE_GER):
    c = cliente_con_login(s, usuario, clave)
    st, _, js = c.get("/api/rutas")
    assert st == 403 and js["debe_configurar_2fa"] is True                              # el rol exige 2FA: todo bloqueado salvo el alta
    secreto = c.post("/api/auth/2fa/iniciar")[2]["secreto"]
    assert c.post("/api/auth/2fa/activar", {"codigo": "000000"})[0] == 400
    st, _, js = c.post("/api/auth/2fa/activar", {"codigo": sec.totp_codigo(secreto, reloj())})
    assert st == 200 and len(js["codigos_recuperacion"]) == sec.CODIGOS_RECUPERACION
    assert c.get("/api/rutas")[0] == 200                                                 # ya activo: acceso normal
    return secreto, js["codigos_recuperacion"]


def test_login_en_dos_pasos_rechaza_codigo_malo_repeticion_y_acepta_el_correcto(env2fa):
    s, ctx, reloj = env2fa
    secreto, _ = _activar(s, ctx, reloj)
    c = Cliente(s.puerto)
    st, _, js = c.login("ger1", CLAVE_GER)
    assert st == 200 and js["requiere_2fa"] is True and "csrf" not in js and c.cookie is None       # contraseña OK pero aun SIN sesion
    reto = js["reto"]
    assert c.get("/api/rutas")[0] == 401
    assert c.post("/api/auth/login/2fa", {"reto": reto, "codigo": "123456"}, csrf=False)[0] == 401
    reloj.avanzar(30)                                                                    # el codigo del alta ya se uso: se exige uno nuevo
    codigo = sec.totp_codigo(secreto, reloj())
    st, cab, js = c.post("/api/auth/login/2fa", {"reto": reto, "codigo": codigo}, csrf=False)
    assert st == 200 and "HttpOnly" in cab["set-cookie"] and js["usuario"]["rol"] == "gerencia"
    # el mismo codigo no se puede reutilizar (anti-repeticion) ni el reto una segunda vez
    c2 = Cliente(s.puerto)
    reto2 = c2.login("ger1", CLAVE_GER)[2]["reto"]
    assert c2.post("/api/auth/login/2fa", {"reto": reto2, "codigo": codigo}, csrf=False)[0] == 401
    assert c.post("/api/auth/login/2fa", {"reto": reto, "codigo": sec.totp_codigo(secreto, reloj() + 30)}, csrf=False)[0] == 401


def test_reto_expira_y_limita_intentos(env2fa):
    s, ctx, reloj = env2fa
    _activar(s, ctx, reloj)
    c = Cliente(s.puerto)
    reto = c.login("ger1", CLAVE_GER)[2]["reto"]
    for _ in range(5):
        assert c.post("/api/auth/login/2fa", {"reto": reto, "codigo": "111111"}, csrf=False)[0] == 401
    assert c.post("/api/auth/login/2fa", {"reto": reto, "codigo": "111111"}, csrf=False)[0] == 429
    assert c.post("/api/auth/login/2fa", {"reto": "inventado", "codigo": "111111"}, csrf=False)[0] == 401


def test_codigos_de_recuperacion_sirven_una_sola_vez(env2fa):
    s, ctx, reloj = env2fa
    _, rec = _activar(s, ctx, reloj)
    c = Cliente(s.puerto)
    reto = c.login("ger1", CLAVE_GER)[2]["reto"]
    assert c.post("/api/auth/login/2fa", {"reto": reto, "codigo": rec[0]}, csrf=False)[0] == 200
    reto = Cliente(s.puerto).login("ger1", CLAVE_GER)[2]["reto"]
    assert Cliente(s.puerto).post("/api/auth/login/2fa", {"reto": reto, "codigo": rec[0]}, csrf=False)[0] == 401
    assert any(e["accion"] == "2fa.recuperacion_usada" for e in ctx.estado.listar_auditoria())


def test_admin_restablece_el_2fa_y_se_cierran_sus_sesiones(env2fa):
    s, ctx, reloj = env2fa
    _activar(s, ctx, reloj)
    adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
    assert adm.get("/api/rutas")[0] == 403                                               # admin tambien debe activar: bloqueado hasta hacerlo
    secreto = adm.post("/api/auth/2fa/iniciar")[2]["secreto"]
    adm.post("/api/auth/2fa/activar", {"codigo": sec.totp_codigo(secreto, reloj())})
    reloj.avanzar(30)
    c = Cliente(s.puerto)
    reto = c.login("admin1", CLAVE_ADMIN)[2]["reto"]
    assert c.login_2fa(reto, sec.totp_codigo(secreto, reloj()))[0] == 200
    uid = next(u["id"] for u in ctx.estado.listar_usuarios() if u["usuario"] == "ger1")
    assert c.post(f"/api/admin/usuarios/{uid}/2fa/restablecer")[0] == 200
    st, _, js = Cliente(s.puerto).login("ger1", CLAVE_GER)
    assert st == 200 and "reto" not in js and js["debe_configurar_2fa"] is True              # debe volver a enrolar


def test_roles_sin_2fa_requerido_no_se_bloquean(env2fa):
    s, _, _ = env2fa
    ven = cliente_con_login(s, "ven1", CLAVE_VEN)
    assert ven.get("/api/rutas")[0] == 200


def test_el_servidor_exige_2fa_y_perimetro_al_exponerse(monkeypatch):
    from cxc_portal import server
    with pytest.raises(SystemExit, match="HTTPS"):
        server.validar_exposicion("0.0.0.0", False, False, ["x.com"])
    monkeypatch.delenv("CXC_PERIMETER_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="CXC_PERIMETER_TOKEN"):
        server.main(["--exigir-perimetro", "--host", "127.0.0.1", "--estado", "x", "--datos", "y"])


# ----------------------------------------------------------------------------- perimetro, salud y monitoreo
def test_perimetro_bloquea_el_acceso_directo_sin_el_secreto_compartido(tmp_path):
    token = "perimetro-secreto-de-prueba-0123456789"
    ctx = crear_entorno(tmp_path, perimetro_token=token)
    s = Servidor(ctx)
    try:
        directo = Cliente(s.puerto)
        assert directo.get("/api/health")[0] == 200                                       # el monitor de salud no necesita el secreto
        assert directo.login("ger1", CLAVE_GER)[0] == 403                                  # evadir el perimetro no permite ni iniciar sesion
        assert directo.pedir("GET", "/", cabeceras={"X-Perimeter-Token": "otro"})[0] == 403
        ok = Cliente(s.puerto, extra={"X-Perimeter-Token": token})
        assert ok.login("ger1", CLAVE_GER)[0] == 200 and ok.get("/api/rutas")[0] == 200
        sin = Cliente(s.puerto)
        sin.cookie, sin.csrf = ok.cookie, ok.csrf                                          # una cookie robada tampoco sirve sin pasar por el perimetro
        assert sin.get("/api/rutas")[0] == 403
    finally:
        s.cerrar()


def test_salud_reporta_ok_y_503_si_la_bd_falla(tmp_path, monkeypatch):
    ctx = crear_entorno(tmp_path)
    s = Servidor(ctx)
    try:
        st, _, js = Cliente(s.puerto).get("/api/health")
        assert st == 200 and js == {"status": "ok", "db": "ok"}
        def roto(*a, **k):
            raise RuntimeError("bd caida")
        monkeypatch.setattr(ctx.estado, "tx", roto)
        st, _, js = Cliente(s.puerto).get("/api/health")
        assert st == 503 and js["status"] == "error"
    finally:
        s.cerrar()


# ----------------------------------------------------------------------------- respaldo / restauracion / exportacion
def test_respaldo_verificado_y_restauracion_conserva_decisiones_usuarios_visitas_y_auditoria(tmp_path):
    ctx = crear_entorno(tmp_path)
    est = ctx.estado
    ger = est.listar_usuarios()
    uid = next(u["id"] for u in ger if u["usuario"] == "ger1")
    actor = {"id": uid, "usuario": "ger1", "rol": "gerencia", "nombre": "Gerente Uno"}
    est.registrar_decision(actor, "C90004", "SOLO_CONTADO", "Decisión de prueba para el respaldo.", route_id="DEMO", calculado="CANDIDATO_SOLO_CONTADO")
    est.registrar_visita(actor, "DEMO", "2026-10-13", "C90001", "visitada_firmada")
    destino = est.respaldar(tmp_path / "respaldos")
    assert destino.is_file() and destino.with_name(destino.name + ".sha256").is_file()
    # se destruye la BD original y se restaura
    original = tmp_path / "estado" / "portal.sqlite3"
    nueva = tmp_path / "otra" / "portal.sqlite3"
    Estado.restaurar(destino, nueva)
    r = Estado(nueva)
    assert r.decisiones_vigentes()["C90004"]["clasificacion_final"] == "SOLO_CONTADO"
    assert r.listar_visitas(route_id="DEMO")[0]["estado"] == "visitada_firmada"
    assert r.autenticar("ger1", CLAVE_GER) is not None
    assert any(e["accion"] == "decision.registrar" for e in r.listar_auditoria())
    assert {x["route_id"] for x in r.listar_rutas()} == {x["route_id"] for x in est.listar_rutas()}
    # no pisa una BD existente sin `forzar`; con forzar conserva copia
    with pytest.raises(ErrorNegocio, match="forzar"):
        Estado.restaurar(destino, original)
    Estado.restaurar(destino, original, forzar=True)
    assert original.with_suffix(".sqlite3.pre-restauracion").is_file()


def test_restauracion_rechaza_respaldos_alterados_o_sin_suma(tmp_path):
    est = crear_entorno(tmp_path).estado
    b = est.respaldar(tmp_path / "r")
    suma = b.with_name(b.name + ".sha256")
    original = b.read_bytes()
    b.write_bytes(original + b"basura")
    with pytest.raises(ErrorNegocio, match="SHA-256"):
        Estado.restaurar(b, tmp_path / "x.sqlite3")
    b.write_bytes(original)
    suma.unlink()
    with pytest.raises(ErrorNegocio, match="sha256"):
        Estado.restaurar(b, tmp_path / "x.sqlite3")


def test_el_respaldo_se_puede_hacer_con_el_servidor_en_marcha_y_conserva_solo_los_ultimos(tmp_path):
    ctx = crear_entorno(tmp_path)
    s = Servidor(ctx)
    try:
        c = cliente_con_login(s, "ger1", CLAVE_GER)
        for i in range(4):
            assert c.get("/api/rutas")[0] == 200
            ctx.estado.reloj = lambda i=i: __import__("datetime").datetime(2026, 10, 10, 12, 0, i, tzinfo=__import__("datetime").timezone.utc)
            ctx.estado.respaldar(tmp_path / "r", conservar=2)
        assert len(list((tmp_path / "r").glob("portal-*.sqlite3"))) == 2 and len(list((tmp_path / "r").glob("*.sha256"))) == 2
    finally:
        s.cerrar()


def test_exportacion_portable_roundtrip_a_una_bd_vacia(tmp_path):
    ctx = crear_entorno(tmp_path)
    est = ctx.estado
    est.registrar_decision(ACTOR, "C90004", "C", "Decisión de prueba para la exportación.", route_id="DEMO")
    dump = json.loads(json.dumps(est.exportar_json(), default=str))
    assert dump["formato"] == "cxc-portal-export/1" and len(dump["tablas"]["usuarios"]) >= 4 and dump["tablas"]["decisiones"]
    destino = Estado(tmp_path / "nuevo.sqlite3")
    cuenta = destino.importar_json(dump)
    assert cuenta["usuarios"] == len(dump["tablas"]["usuarios"]) and cuenta["decisiones"] == len(dump["tablas"]["decisiones"])
    assert destino.autenticar("admin1", CLAVE_ADMIN) is not None                       # los hashes viajan: mismas claves
    assert destino.decisiones_vigentes()["C90004"]["clasificacion_final"] == "C"
    assert [r["route_id"] for r in destino.listar_rutas()] == [r["route_id"] for r in est.listar_rutas()]
    with pytest.raises(ErrorNegocio, match="no está vacía"):
        destino.importar_json(dump)                                                   # nunca mezcla ni sobrescribe
    with pytest.raises(ErrorNegocio, match="Formato"):
        Estado(tmp_path / "otro.sqlite3").importar_json({"formato": "x"})


def test_la_cli_respalda_exporta_e_importa(tmp_path):
    from cxc_portal import admin
    crear_entorno(tmp_path)
    estado_dir = str(tmp_path / "estado")
    assert admin.main(["respaldar", "--estado", estado_dir, "--destino", str(tmp_path / "bk")]) == 0
    assert admin.main(["exportar", "--estado", estado_dir, "--destino", str(tmp_path / "dump.json")]) == 0
    nuevo = tmp_path / "estado2"
    nuevo.mkdir()
    assert admin.main(["importar", "--estado", str(nuevo), "--archivo", str(tmp_path / "dump.json")]) == 0
    assert Estado(nuevo / "portal.sqlite3").autenticar("ger1", CLAVE_GER) is not None
