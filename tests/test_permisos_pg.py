import pytest

from cxc_portal import permisos_pg as pp

LEIBLES = [
    'SELECT "cardCode" FROM clientes WHERE "finVersion" IS NOT NULL',
    "SELECT sum(debe - haber) AS v FROM public.movimientos_contables",
    "SELECT count(*) AS n FROM cxc.rutas",
    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'cxc'",
    "SELECT current_setting('transaction_read_only') AS r",
    "SELECT f.\"docEntry\" FROM facturas f JOIN pagos p ON p.\"cardCode\" = f.\"cardCode\"",
]
ESCRIBIBLES = [
    "INSERT INTO cxc.rutas (route_id, nombre) VALUES (%s, %s)",
    "UPDATE cxc.decisiones SET motivo = %s WHERE id = %s",
    "DELETE FROM cxc.sesiones WHERE expira < %s",
    "INSERT INTO cxc.meta(clave, valor) VALUES ('a', 'b') ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor",
] + [f"INSERT INTO cxc.{t} (x) VALUES (1)" for t in pp.TABLAS_ESCRIBIBLES]
PROHIBIDOS = [
    "DROP TABLE cxc.rutas", "TRUNCATE cxc.rutas", "ALTER TABLE cxc.rutas ADD COLUMN x text", "CREATE TABLE cxc.nueva (x int)", "CREATE SCHEMA cxc", "GRANT ALL ON cxc.rutas TO x",
    "INSERT INTO facturas (\"docEntry\") VALUES (1)", "UPDATE public.clientes SET \"currentAccountBalance\" = 0", "DELETE FROM public.pagos", "UPDATE facturas SET cancelled = true",
    "INSERT INTO public.rutas (x) VALUES (1)", "DELETE FROM asistente_mensajes", "SELECT * FROM asistente_usuarios", "SELECT * FROM api_keys", "SELECT * FROM operadores",
    "INSERT INTO cxc.tabla_inventada (x) VALUES (1)", "INSERT INTO otro.rutas (x) VALUES (1)",
    "SELECT 1; DELETE FROM cxc.rutas", "SELECT * FROM cxc.rutas; DROP TABLE cxc.rutas", "COPY cxc.rutas TO '/tmp/x'", "DO $$ BEGIN END $$",
    "WITH x AS (DELETE FROM public.pagos RETURNING *) SELECT * FROM x", "SELECT pg_terminate_backend(1)", "SELECT pg_sleep(100)", "SELECT * FROM pg_catalog.pg_authid",
    "SET default_transaction_read_only = off", "CALL algo()", "VACUUM clientes", "UPDATE cxc.rutas SET x = 1; SELECT 1", "", "   ",
    "INSERT INTO cxc.rutas SELECT * FROM public.clientes", "UPDATE cxc.rutas SET x = (SELECT 1 FROM asistente_usuarios)",
]


@pytest.mark.parametrize("sql", LEIBLES)
def test_lecturas_financieras_autorizadas(sql):
    assert pp.clasificar(sql)[0] == "lectura"


@pytest.mark.parametrize("sql", ESCRIBIBLES)
def test_escritura_solo_en_tablas_cxc_autorizadas(sql):
    assert pp.clasificar(sql)[0] == "escritura"


@pytest.mark.parametrize("sql", PROHIBIDOS)
def test_todo_lo_demas_se_rechaza(sql):
    with pytest.raises(pp.PermisoDenegado):
        pp.clasificar(sql)


def test_las_26_tablas_son_exactamente_las_del_esquema_cxc_de_gelsyn():
    import re
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "docs" / "gelsyn" / "cosprobell_esquema_completo.sql").read_text(encoding="utf-8")
    assert set(re.findall(r"CREATE TABLE cxc\.(\w+) \(", sql)) == set(pp.TABLAS_ESCRIBIBLES) and len(pp.TABLAS_ESCRIBIBLES) == 26


class _Conn:
    def __init__(self):
        self.q = []

    def cursor(self):
        c = self

        class K:
            description = [("n",)]
            rowcount = 1

            def execute(s, sql, p=None):
                c.q.append(sql)

            def fetchall(s):
                return [(1,)]

            def close(s):
                pass
        return K()


def test_sesion_es_solo_lectura_por_omision_y_no_envia_lo_rechazado():
    conn = _Conn()
    s = pp.SesionProduccion(conn)
    assert s.consulta("SELECT count(*) AS n FROM clientes") == [{"n": 1}]
    with pytest.raises(pp.PermisoDenegado, match="solo lectura"):
        s.ejecutar("UPDATE cxc.rutas SET nombre = %s WHERE route_id = %s", ("a", "b"))
    for malo in ("DROP TABLE cxc.rutas", "UPDATE public.clientes SET x = 1", "SELECT * FROM asistente_mensajes"):
        with pytest.raises(pp.PermisoDenegado):
            (s.ejecutar if malo.startswith(("UPDATE", "DROP")) else s.consulta)(malo)
    assert conn.q == ["SELECT count(*) AS n FROM clientes"]                          # nada rechazado llego a la base


def test_escritura_habilitada_sigue_limitada_a_cxc():
    conn = _Conn()
    s = pp.SesionProduccion(conn, escritura=True)
    assert s.ejecutar("INSERT INTO cxc.meta(clave, valor) VALUES (%s, %s)", ("a", "b")) == 1
    with pytest.raises(pp.PermisoDenegado):
        s.ejecutar("DELETE FROM public.pagos")
    with pytest.raises(pp.PermisoDenegado):
        s.ejecutar("CREATE TABLE cxc.x (a int)")
