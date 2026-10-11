"""Respaldos cifrados (AES-256-GCM + RSA-OAEP) y consistencia CLI <-> servidor. Datos 100 % sinteticos; claves RSA de prueba de 2048 bits."""

import json
from datetime import datetime, timezone

import pytest

pytest.importorskip("cryptography")

from cxc_portal import admin, respaldo_cifrado as rc  # noqa: E402
from cxc_portal.estado import ErrorNegocio, Estado  # noqa: E402
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, Cliente, Servidor, cliente_con_login, crear_entorno  # noqa: E402

PASS = "contrasena-privada-de-prueba-1"
CONTENIDO = b"PK-contenido-sintetico-hoja-de-visita-" + bytes(range(256)) * 20


@pytest.fixture(scope="module")
def claves():
    pub, priv, kid = rc.generar_par_claves(PASS, 2048)
    pub2, priv2, kid2 = rc.generar_par_claves(PASS + "x", 2048)
    return {"pub": pub, "priv": priv, "kid": kid, "pub2": pub2, "priv2": priv2, "kid2": kid2}


@pytest.fixture()
def entorno(tmp_path):
    ctx = crear_entorno(tmp_path)
    d = ctx.estado.registrar_documento(ACTOR, "DEMO", "HOJA_VISITA", "demo.xlsx", CONTENIDO, {"hojas": [], "total_clientes": 0}, tmp_path / "estado" / "documentos")
    ctx.estado.registrar_decision(ACTOR, "C90004", "SOLO_CONTADO", "Decisión sintética para el respaldo.", route_id="DEMO")
    return ctx, d, tmp_path


def _reloj(i=0):
    return datetime(2026, 10, 11, 12, 0, i, tzinfo=timezone.utc)


# ------------------------------------------------------------------ ida y vuelta
def test_respaldo_cifrado_ida_y_vuelta_con_documentos(entorno, claves):
    ctx, d, tmp = entorno
    f = rc.crear_respaldo(ctx.estado, [claves["pub"]], tmp / "bk", _reloj())
    blob = f.read_bytes()
    assert blob.startswith(rc.MAGIC) and f.name == "portal-20261011-120000.cxcbak"
    assert CONTENIDO[:40] not in blob and b"SQLite format" not in blob and b"admin1" not in blob          # nada en claro
    assert not list((tmp / "bk").glob("*.parcial")) and (tmp / "bk" / (f.name + ".sha256")).is_file()
    nuevo = tmp / "otro" / "estado" / "portal.sqlite3"
    rc.restaurar_cifrado(f, claves["priv"], PASS, nuevo)
    r = Estado(nuevo)
    assert r.autenticar("admin1", CLAVE_ADMIN) is not None and r.decisiones_vigentes()["C90004"]["clasificacion_final"] == "SOLO_CONTADO"
    assert (nuevo.parent / "documentos" / d["almacenado"]).read_bytes() == CONTENIDO and r.verificar_documentos() == []


def test_dos_respaldos_del_mismo_estado_son_distintos_y_cualquier_custodio_recupera(entorno, claves):
    ctx, _, tmp = entorno
    a = rc.crear_respaldo(ctx.estado, [claves["pub"], claves["pub2"]], tmp / "bk", _reloj(1))
    b = rc.crear_respaldo(ctx.estado, [claves["pub"], claves["pub2"]], tmp / "bk", _reloj(2))
    assert a.read_bytes() != b.read_bytes()                                       # clave de datos y nonce aleatorios por respaldo
    assert rc.verificar_estructura(a)["destinatarios"] == [claves["kid"], claves["kid2"]]
    assert rc.verificar_restauracion(a, claves["priv"], PASS)["ok"] and rc.verificar_restauracion(a, claves["priv2"], PASS + "x")["ok"]


# ------------------------------------------------------------------ fallos
def test_contrasena_incorrecta_o_clave_ajena_no_descifra(entorno, claves):
    ctx, _, tmp = entorno
    f = rc.crear_respaldo(ctx.estado, [claves["pub"]], tmp / "bk", _reloj())
    with pytest.raises(ErrorNegocio, match="Contraseña"):
        rc.verificar_restauracion(f, claves["priv"], "contrasena-equivocada-123")
    with pytest.raises(ErrorNegocio, match="ningún destinatario"):
        rc.verificar_restauracion(f, claves["priv2"], PASS + "x")                   # clave valida pero no autorizada
    assert not (tmp / "otro").exists()


@pytest.mark.parametrize("donde", ["cabecera", "cifrado", "etiqueta", "truncado", "magia", "cola"])
def test_archivo_alterado_se_rechaza_sin_restaurar_nada(entorno, claves, donde):
    ctx, _, tmp = entorno
    f = rc.crear_respaldo(ctx.estado, [claves["pub"]], tmp / "bk", _reloj())
    b = bytearray(f.read_bytes())
    ini_cab = len(rc.MAGIC) + 4
    ini_cifrado = ini_cab + int.from_bytes(b[len(rc.MAGIC):ini_cab], "big")
    if donde == "cabecera":
        b[ini_cab + 20] ^= 0x01                                                        # bit dentro de la cabecera autenticada (AAD)
    elif donde == "cifrado":
        b[ini_cifrado + 100] ^= 0x01
    elif donde == "etiqueta":
        b[-1] ^= 0x01
    elif donde == "truncado":
        b = b[:-50]
    elif donde == "magia":
        b[0] ^= 0x01
    elif donde == "cola":
        b += b"basura"
    f.write_bytes(bytes(b))
    destino = tmp / "x" / "portal.sqlite3"
    with pytest.raises(ErrorNegocio):
        rc.restaurar_cifrado(f, claves["priv"], PASS, destino)
    assert not destino.exists()


def test_suma_externa_detecta_alteracion_sin_clave(entorno, claves):
    ctx, _, tmp = entorno
    f = rc.crear_respaldo(ctx.estado, [claves["pub"]], tmp / "bk", _reloj())
    assert rc.verificar_estructura(f)["bytes"] == f.stat().st_size
    f.write_bytes(f.read_bytes() + b"x")
    with pytest.raises(ErrorNegocio, match="SHA-256"):
        rc.verificar_estructura(f)


def test_la_clave_privada_no_se_acepta_corta_ni_la_publica_debil(claves):
    with pytest.raises(ErrorNegocio, match="16 caracteres"):
        rc.generar_par_claves("corta", 2048)
    with pytest.raises(ErrorNegocio, match="demasiado corta"):
        rc.generar_par_claves(PASS, 1024)
    with pytest.raises(ErrorNegocio, match="inválida"):
        rc.cifrar_carpeta(None, [b"no es una clave"], _reloj())


def test_no_sobrescribe_un_respaldo_existente_ni_deja_copia_en_claro(entorno, claves, tmp_path_factory):
    ctx, _, tmp = entorno
    rc.crear_respaldo(ctx.estado, [claves["pub"]], tmp / "bk", _reloj())
    with pytest.raises(ErrorNegocio, match="no se sobrescribe"):
        rc.crear_respaldo(ctx.estado, [claves["pub"]], tmp / "bk", _reloj())
    assert sorted(p.name for p in (tmp / "bk").iterdir() if not p.name.endswith(".sha256")) == ["portal-20261011-120000.cxcbak"]   # ni .sqlite3 ni carpeta de documentos


# ------------------------------------------------------------------ rotacion, retencion y ciclo programado
def test_rotacion_conserva_diarios_y_el_ultimo_de_cada_mes_reciente(tmp_path):
    nombres = [f"portal-2026{m:02d}{d:02d}-030000.cxcbak" for m, d in [(1, 5), (1, 28), (2, 3), (2, 27), (3, 2), (8, 31), (9, 1), (9, 2), (9, 3), (10, 1), (10, 2)]]
    for n in nombres:
        (tmp_path / n).write_bytes(b"x")
        (tmp_path / (n + ".sha256")).write_text("x")
    borrados = rc.rotar(tmp_path, diarios=3, mensuales=2)
    vivos = sorted(p.name for p in tmp_path.glob("*.cxcbak"))
    # diarios: los 3 mas recientes; mensuales: el ultimo de sep (09-03) y de oct (10-02)
    assert vivos == ["portal-20260903-030000.cxcbak", "portal-20261001-030000.cxcbak", "portal-20261002-030000.cxcbak"]
    assert len(borrados) == 8 and not list(tmp_path.glob("portal-202601*"))
    assert rc.rotar(tmp_path, diarios=0, mensuales=0) and len(list(tmp_path.glob("*.cxcbak"))) == 1   # nunca se queda sin respaldos


def test_ciclo_programado_crea_verifica_rota_y_deja_estado_legible(entorno, claves):
    ctx, _, tmp = entorno
    for i in range(4):
        ctx.estado.reloj = lambda i=i: _reloj(i)
        r = rc.ejecutar_ciclo(ctx.estado, [claves["pub"]], tmp / "bk", diarios=2, mensuales=0)
    est = json.loads((tmp / "bk" / "ultimo_respaldo.json").read_text(encoding="utf-8"))
    assert est["archivo"] == r["archivo"] and est["politica"] == {"diarios": 2, "mensuales": 0} and len(list((tmp / "bk").glob("*.cxcbak"))) == 2
    assert "priv" not in json.dumps(est)


# ------------------------------------------------------------------ CLI
def test_cli_genera_claves_respalda_verifica_y_restaura(entorno, monkeypatch):
    ctx, d, tmp = entorno
    monkeypatch.setenv("CXC_CLAVE_PRIVADA_PASS", PASS)
    pub, priv = tmp / "k" / "pub.pem", tmp / "k" / "priv.pem"
    (tmp / "k").mkdir()
    assert admin.main(["generar-claves-respaldo", "--publica", str(pub), "--privada", str(priv), "--bits", "2048"]) == 0
    with pytest.raises(SystemExit, match="no se sobrescribe"):
        admin.main(["generar-claves-respaldo", "--publica", str(pub), "--privada", str(priv)])
    assert admin.main(["respaldar-cifrado", "--estado", str(tmp / "estado"), "--destino", str(tmp / "bk"), "--publica", str(pub)]) == 0
    f = next((tmp / "bk").glob("*.cxcbak"))
    assert admin.main(["verificar-respaldo", "--archivo", str(f), "--privada", str(priv)]) == 0
    nuevo = tmp / "n"
    nuevo.mkdir()
    assert admin.main(["restaurar-cifrado", "--estado", str(nuevo), "--archivo", str(f), "--privada", str(priv)]) == 0
    assert (nuevo / "documentos" / d["almacenado"]).read_bytes() == CONTENIDO
    monkeypatch.setenv("CXC_CLAVE_PRIVADA_PASS", "otra-contrasena-equivocada")
    with pytest.raises(SystemExit, match="Contraseña"):
        admin.main(["verificar-respaldo", "--archivo", str(f), "--privada", str(priv)])


# ------------------------------------------------------------------ consistencia: cambios por CLI/otro proceso vs estado cargado por el servidor
def test_el_servidor_ve_los_cambios_hechos_por_otro_proceso_sin_reiniciar(tmp_path):
    ctx = crear_entorno(tmp_path)
    s = Servidor(ctx)
    try:
        adm = cliente_con_login(s, "admin1", CLAVE_ADMIN)
        ids = lambda: sorted(r["route_id"] for r in adm.get("/api/rutas")[2]["rutas"])
        assert "NUEVA" not in ids()
        otro = Estado(tmp_path / "estado" / "portal.sqlite3")                             # "otro proceso": la CLI abre su propia instancia
        otro.guardar_ruta(ACTOR, "NUEVA", "Ruta nueva por CLI", "Vendedor Demo", "LOCAL", None, zonas={"NN": "Zona"},
                          clientes=[{"codigo": "C90201", "folder": "1 NN", "zona": "NN", "nombre": "CLIENTE CLI"}])
        assert "NUEVA" in ids()                                                           # visible sin invalidar ni reiniciar
        assert any(c["codigo"] == "C90201" for c in adm.get("/api/rutas/NUEVA/clientes")[2]["clientes"])
        otro.registrar_decision(ACTOR, "C90201", "C", "Decisión registrada por otro proceso.", route_id="NUEVA")
        assert ctx.servicio.rutas()["NUEVA"].clientes[0].resultado.decision_gerencia is not None
        otro.quitar_cliente(ACTOR, "NUEVA", "C90201")
        assert ctx.servicio.rutas()["NUEVA"].clientes == []
    finally:
        s.cerrar()


def test_cache_se_mantiene_si_nada_cambia_y_las_sesiones_no_la_invalidan(tmp_path):
    ctx = crear_entorno(tmp_path)
    v0 = ctx.estado.version_catalogo()
    r1 = ctx.servicio.rutas()
    ctx.estado.crear_sesion(ctx.estado.listar_usuarios()[0]["id"])                        # escrituras de sesion/auditoria no tocan el catalogo
    with ctx.estado.tx() as c:
        ctx.estado.auditar(c, ACTOR, "prueba")
    assert ctx.estado.version_catalogo() == v0 and ctx.servicio.rutas() is r1


def test_los_disparadores_sobreviven_a_respaldo_y_restauracion(entorno, claves):
    ctx, _, tmp = entorno
    f = rc.crear_respaldo(ctx.estado, [claves["pub"]], tmp / "bk", _reloj())
    nuevo = tmp / "r" / "portal.sqlite3"
    rc.restaurar_cifrado(f, claves["priv"], PASS, nuevo)
    r = Estado(nuevo)
    v = r.version_catalogo()
    r.agregar_zona(ACTOR, "DEMO", "ZZ", "Zona nueva")
    assert r.version_catalogo() > v
