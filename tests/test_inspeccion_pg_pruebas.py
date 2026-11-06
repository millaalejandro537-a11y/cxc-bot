"""Inspeccion READ ONLY de `Cosprobell-pruebas`: pruebas de SEGURIDAD de la herramienta con una conexion FALSA. No hay base, red ni credenciales reales."""

import json
import os
import re
import sys
import types

import pytest

from cxc_portal import inspeccion_pg_pruebas as ip

URL = "postgresql://postgres:CLAVE-INVENTADA-xyz@host-ejemplo.invalid:12345/basedeprueba"


# ------------------------------------------------------------------------------------------------ validador de sentencias
def test_las_consultas_de_la_herramienta_son_select_sobre_tablas_financieras():
    assert len(ip.E_CONSULTAS) == 14 and len(ip.E_AMPLIACION) == 9
    for _, sql in ip.E_CONSULTAS + ip.E_AMPLIACION:
        ip.validar_sql(sql)
    texto = " ".join(sql for _, sql in ip.E_CONSULTAS + ip.E_AMPLIACION).lower()
    for t in ("usuarios", "sesiones", "api_keys", "pg_authid", "pg_shadow", "operadores"):
        assert t not in texto


@pytest.mark.parametrize("sql", [
    "INSERT INTO clientes VALUES (1)", "UPDATE clientes SET a = 1", "DELETE FROM clientes", "DROP TABLE clientes", "ALTER TABLE clientes ADD x int",
    "CREATE TABLE x (a int)", "GRANT ALL ON clientes TO public", "TRUNCATE clientes", "COPY clientes TO '/tmp/x'", "SET default_transaction_read_only = off",
    "SELECT 1; DROP TABLE clientes", "SELECT * FROM clientes; SELECT * FROM pagos", "VACUUM clientes", "COMMENT ON TABLE clientes IS 'x'",
    "SELECT * FROM usuarios", "SELECT * FROM sesiones", "SELECT * FROM api_keys", "SELECT * FROM pg_catalog.pg_authid", "SELECT * FROM pg_shadow",
    "SELECT * FROM information_schema.role_table_grants", "SELECT * FROM clientes c JOIN usuarios u ON u.id = c.id", "WITH x AS (DELETE FROM pagos RETURNING *) SELECT * FROM x",
    "SELECT * FROM facturas WHERE 1=1; UPDATE facturas SET docTotal = 0",
])
def test_cualquier_cosa_que_no_sea_un_select_financiero_se_rechaza(sql):
    with pytest.raises(ip.InspeccionError):
        ip.validar_sql(sql)


def test_select_simple_y_vistas_de_catalogo_permitidas_se_aceptan():
    ip.validar_sql('SELECT count(*) FROM facturas')
    ip.validar_sql("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    ip.validar_sql('SELECT "cardCode" FROM clientes c JOIN facturas f ON f."cardCode" = c."cardCode"')


# ------------------------------------------------------------------------------------------------ URL: archivo privado, nunca expuesta
def test_la_url_se_lee_de_un_archivo_0600_y_nunca_aparece_en_errores(tmp_path):
    f = tmp_path / "pg.url"
    f.write_text(URL)
    os.chmod(f, 0o644)
    with pytest.raises(ip.InspeccionError, match="privado") as ex:
        ip.leer_url({ip.ENV_URL_FILE: str(f)})
    os.chmod(f, 0o600)
    assert ip.leer_url({ip.ENV_URL_FILE: str(f)}) == URL
    for env in ({}, {ip.ENV_URL: "mysql://x:y@h/db"}, {ip.ENV_URL_FILE: str(tmp_path / "no-existe")}):
        with pytest.raises(ip.InspeccionError) as e2:
            ip.leer_url(env)
        assert "CLAVE-INVENTADA" not in str(e2.value)
    d = ip.descripcion_destino(URL)
    assert d == {"host": "host-ejemplo.invalid", "puerto": 12345, "base": "basedeprueba"} and "CLAVE" not in json.dumps(d) and "postgres:" not in json.dumps(d)


def test_sin_atestacion_humana_no_se_conecta_nunca(monkeypatch, capsys):
    monkeypatch.setattr(ip, "_conectar", lambda url: (_ for _ in ()).throw(AssertionError("no debia conectar")))
    monkeypatch.setenv(ip.ENV_URL, URL)
    assert ip.main([]) == 2
    assert ip.main(["--confirmo-servicio-pruebas", "Cosprobell-produccion"]) == 2
    err = capsys.readouterr().err
    assert "atestación" in err and "CLAVE-INVENTADA" not in err


def test_la_conexion_es_solo_lectura_con_ssl_y_tiempos_maximos(monkeypatch):
    visto = {}

    class Conn:
        read_only = False

    def connect(url, **kw):
        visto.update(kw, url=url)
        return Conn()
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=connect))
    c = ip._conectar(URL)
    assert c.read_only is True and visto["sslmode"] == "require"
    assert "default_transaction_read_only=on" in visto["options"] and "statement_timeout" in visto["options"] and "idle_in_transaction_session_timeout" in visto["options"]
    visto.clear()
    ip._conectar("postgresql://u:p@localhost/db")
    assert visto["sslmode"] == "prefer"


# ------------------------------------------------------------------------------------------------ inspeccion con conexion falsa
class FakeConn:
    def __init__(self, tablas=ip.TABLAS_TOTALES, conteos=None, columnas=None):
        self.sqls, self.tablas, self.conteos = [], tablas, dict(conteos or ip.TABLAS)
        self.columnas = columnas if columnas is not None else [(t, c, "text", "YES") for t, req in ip.CAMPOS_PORTAL.items() for c in req]

    def cursor(self):
        return FakeCur(self)

    def rollback(self):
        pass


class FakeCur:
    def __init__(self, c):
        self.c, self.description = c, []

    def execute(self, sql, params=None):
        self.c.sqls.append(sql)
        m = re.match(r"SELECT count\(\*\) AS n FROM (\w+)$", sql)
        if "current_database()" in sql:
            self.cols, self.rows = ["base", "version", "ahora"], [("railway", "16.4", "2026-11-04")]
        elif "information_schema.tables" in sql:
            self.cols, self.rows = ["n"], [(self.c.tablas,)]
        elif m:
            self.cols, self.rows = ["n"], [(self.c.conteos[m.group(1)],)]
        elif "information_schema.columns" in sql:
            self.cols, self.rows = ["table_name", "column_name", "data_type", "is_nullable"], self.c.columnas
        else:
            self.cols, self.rows = ["dato"], [("agregado",)]
        self.description = [(n,) for n in self.cols]

    def fetchall(self):
        return self.rows

    def close(self):
        pass


def test_identidad_no_confirmada_detiene_sin_leer_mas():
    conteos = dict(ip.TABLAS)
    conteos["facturas"] = 17281
    fc = FakeConn(conteos=conteos)
    r = ip.inspeccionar(ip.Sesion(fc), {"host": "h"}, "Cosprobell-pruebas")
    assert r["veredicto"] == "IDENTIDAD_NO_CONFIRMADA" and r["identidad"]["coinciden"]["facturas"] is False and r["resultados"] == {}
    assert not any("finanzas_control" in s and "activoId" in s for s in fc.sqls)                  # no se leyo nada mas que identidad y conteos
    r2 = ip.inspeccionar(ip.Sesion(FakeConn(tablas=40)), {"host": "h"}, "Cosprobell-pruebas")
    assert r2["veredicto"] == "IDENTIDAD_NO_CONFIRMADA"


def test_inspeccion_completa_ejecuta_solo_select_financieros_y_detecta_campos_faltantes():
    cols = [(t, c, "text", "YES") for t, req in ip.CAMPOS_PORTAL.items() for c in req if not (t == "facturas" and c == "docDueDate")]
    fc = FakeConn(columnas=cols)
    r = ip.inspeccionar(ip.Sesion(fc), {"host": "h"}, "Cosprobell-pruebas")
    assert r["identidad"]["coinciden"] and all(r["identidad"]["coinciden"].values()) and r["campos_faltantes"] == {"facturas": ["docDueDate"]}
    assert r["veredicto"] == "REVISAR" and set(r["resultados"]) >= {"control", "dominio_facturas", "fechas", "integridad", "saldos", "version_publicada"}
    for sql in fc.sqls:
        ip.validar_sql(sql)                                                                                  # todo lo ejecutado pasa el validador
        assert not re.search(r"(?i)usuario|sesion|api_?key|credencial|operador|pg_authid", sql)
    assert not any(re.search(r'"cardName"|phone|cellular|emailAddress', s) for s in fc.sqls)                 # ninguna columna de datos personales
    assert "CLAVE" not in json.dumps(r, default=str)


def test_una_consulta_que_falla_se_informa_sin_detener_el_resto():
    fc = FakeConn()
    original = FakeCur.execute

    def execute(self, sql, params=None):
        if "FROM clientes WHERE" in sql and "GROUP BY" in sql:
            raise RuntimeError("columna inexistente (simulada)")
        return original(self, sql, params)
    FakeCur.execute = execute
    try:
        r = ip.inspeccionar(ip.Sesion(fc), {"host": "h"}, "Cosprobell-pruebas")
    finally:
        FakeCur.execute = original
    assert r["resultados"]["dominio_clientes"] == {"error": "RuntimeError"} and any("dominio_clientes" in p for p in r["problemas"]) and "fechas" in r["resultados"]


def test_esquema_cxc_solo_tablas_permitidas_y_nunca_credenciales():
    ip.validar_sql("SELECT count(*) AS n FROM cxc.rutas")
    for prohibida in ("usuarios", "sesiones", "agentes_impresion"):
        with pytest.raises(ip.InspeccionError):
            ip.validar_sql(f"SELECT count(*) AS n FROM cxc.{prohibida}")
    with pytest.raises(ip.InspeccionError):
        ip.validar_sql("SELECT * FROM cxc.rutas; DELETE FROM cxc.rutas")
    assert set(ip.CXC_CONTABLES).isdisjoint(ip.CXC_PROHIBIDAS) and len(ip.CXC_CONTABLES) + len(ip.CXC_PROHIBIDAS) == 26
