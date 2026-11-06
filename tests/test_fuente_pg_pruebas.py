"""Fuente `gelsyn-pg-pruebas`: lectura automatica de PostgreSQL de PRUEBAS (conexion falsa; nada de red) + prueba opcional contra la copia real si hay credencial local."""
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cxc_portal import fuente_pg_pruebas as fp
from cxc_portal import inspeccion_pg_pruebas as ip

from tests.portal_helpers import ruta_reporting_core

ENV_OK = {fp.ENV_ATESTACION: "Cosprobell-pruebas", "CXC_PG_PRUEBAS_URL": "postgresql://u:SECRETO-NO-REAL@localhost/db"}


class ConexionFalsa:
    """Responde por patron de SQL. Registra todo lo ejecutado para comprobar que solo hay SELECT."""

    def __init__(self, tablas=67, conteos=None):
        self.ejecutadas, self.cerrada, self.rollbacks = [], False, 0
        self.tablas, self.conteos = tablas, dict(ip.TABLAS if conteos is None else conteos)
        self.sin_columnas, self.tipos, self.sin_version = set(), {}, set()

    def cursor(self):
        return _Cursor(self)

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.cerrada = True


class _Cursor:
    def __init__(self, c):
        self.c, self.description, self.filas = c, [], []

    def execute(self, sql, params=None):
        self.c.ejecutadas.append(sql)
        cols, filas = self._resolver(sql)
        self.description = [(k,) for k in cols]
        self.filas = filas

    def _resolver(self, sql):
        m = re.match(r"SELECT count\(\*\) AS n FROM (\w+)$", sql)
        if m:
            return ["n"], [(self.c.conteos[m.group(1)],)]
        if "information_schema.columns" in sql:                                      # identidad estructural: columnas y tipos de lo que el Portal lee
            faltan = self.c.sin_columnas
            return (["table_name", "column_name", "udt_name"],
                    [(t, c, self.c.tipos.get((t, c), tp)) for t, cols in ip.ESTRUCTURA_CRITICA.items() for c, tp in cols.items() if (t, c) not in faltan])
        if sql.startswith('SELECT entidad, "activoId" FROM finanzas_control'):
            return ["entidad", "activoId"], [(e, None if e in self.c.sin_version else "uuid-x") for e in ip.ENTIDADES_CONTROL]
        if "FROM vendedores" in sql:
            return ["codigo", "nombre"], [(7, "Vendedor Siete")]
        if "information_schema.tables" in sql:
            return ["n"], [(self.c.tablas,)]
        if "FROM finanzas_control WHERE entidad IN" in sql:
            if "movimientos" in sql:
                return ["entidad", "iniciadoEn", "finalizadoEn", "publicadoEn", "secuencia", "cantidad"], [("clientes", datetime(2026, 10, 27, 17), datetime(2026, 10, 27, 18), datetime(2026, 10, 27, 18), 1, 2)]
            return ["entidad", "publicadoEn"], [("clientes", datetime(2026, 10, 27, 18)), ("facturas", datetime(2026, 11, 4, 16)), ("pagos", datetime(2026, 11, 4, 16)),
                                               ("aplicacionesPagos", datetime(2026, 11, 4, 16))]
        if "currentAccountBalance" in sql:                                           # conciliacion: saldo de control
            return ["cardCode", "saldo", "currency"], [("C10001", Decimal("150.00"), "LPS"), ("C10002", Decimal("0"), "LPS")]
        if "FROM movimientos_contables" in sql:                                      # conciliacion: libro auxiliar (la linea 2 se creo DESPUES de la foto de clientes)
            return (["cardCode", "transId", "tipo", "fecha", "v", "en_fc"],
                    [("C10001", 1, "ttARInvoice", datetime(2026, 9, 1).date(), Decimal("150.00"), 0), ("C10001", 2, "ttReceipt", datetime(2026, 10, 20).date(), Decimal("-100.00"), 0)])
        if "FROM clientes" in sql:
            return ["cardCode", "nombre", "vendedor", "ruta"], [("C10001", "CLIENTE UNO SA", 7, "04"), ("C10002", "CLIENTE DOS", 7, None)]
        if "FROM facturas" in sql:
            f = lambda n, d, tot, pag, st: (n, n, "C10001", datetime(2026, 9, d), datetime(2026, 11, d), Decimal(tot), Decimal(pag), st, False)  # noqa: E731
            return (["docEntry", "docNum", "cardCode", "docDate", "docDueDate", "docTotal", "paidToDate", "documentStatus", "cancelled"],
                    [f(1, 1, "100.00", "100.00", "bost_Close"), f(2, 2, "50.00", "0.00", "bost_Open")])
        if "FROM pagos p" in sql or "FROM pagos_facturas" in sql:
            return ["pagoDocEntry", "facturaDocEntry", "sumApplied", "cancelado", "invoiceType"], [(10, 1, Decimal("100.00"), "N", "it_Invoice")]
        if "FROM pagos" in sql:
            return (["docEntry", "cardCode", "docDate", "total", "cashSum", "transferSum", "cancelled"], [(10, "C10001", datetime(2026, 9, 20), None, Decimal("100.00"), Decimal("0"), False)])
        raise AssertionError("SQL inesperado: " + sql[:80])

    def fetchall(self):
        return self.filas

    def close(self):
        pass


def _crear(conn, env=None, hoy=date(2026, 11, 5)):
    return fp.crear_fuente_pg_pruebas(env or ENV_OK, conectar=lambda url: conn, hoy=lambda: hoy)


@pytest.fixture(autouse=True)
def _core_disponible():
    from cxc_portal import gelsyn_config as gc
    if not (ruta_reporting_core() / "cxc_reporting").is_dir():
        pytest.skip("cxc-reporting-core no disponible")
    os.environ.setdefault("CXC_REPORTING_SRC", str(ruta_reporting_core()))
    gc._importar_core()


def test_sin_atestacion_no_se_conecta():
    llamado = []
    with pytest.raises(fp.FuentePgError, match="CXC_GELSYN_PG_ATESTACION"):
        fp.crear_fuente_pg_pruebas({"CXC_PG_PRUEBAS_URL": ENV_OK["CXC_PG_PRUEBAS_URL"]}, conectar=lambda u: llamado.append(u))
    assert llamado == []


def test_identidad_distinta_se_detiene_sin_leer_mas():
    conn = ConexionFalsa(tablas=40)
    with pytest.raises(fp.FuentePgError, match="Identidad no confirmada"):
        _crear(conn)
    assert conn.cerrada and not any("FROM facturas" in s and "docTotal" in s for s in conn.ejecutadas)


def test_error_de_conexion_no_filtra_la_url():
    def falla(url):
        raise RuntimeError("fallo con " + url)
    with pytest.raises(fp.FuentePgError) as e:
        fp.crear_fuente_pg_pruebas(ENV_OK, conectar=falla)
    assert "SECRETO-NO-REAL" not in str(e.value) and "postgresql://" not in str(e.value)


def test_carga_completa_solo_select_y_conexion_cerrada():
    conn = ConexionFalsa()
    f = _crear(conn)
    assert conn.cerrada and conn.rollbacks >= 1
    assert all(s.lstrip().upper().startswith("SELECT") for s in conn.ejecutadas)
    assert not any(re.search(r"(?i)\b(insert|update|delete|drop|alter|create|grant)\b", s) for s in conn.ejecutadas)
    assert not any(re.search(r"phone1|cellular|emailAddress", s) for s in conn.ejecutadas)      # nombres autorizados; telefonos y correos, nunca
    assert f.tipo == "EMPRESARIAL_PRUEBAS" and not f.en_vivo and not f.apta_para_publicar
    assert f.calidad["estado"] == "OK"


def test_cortes_reales_por_entidad_y_clasificacion_con_el_motor():
    f = _crear(ConexionFalsa())
    assert f.fecha_corte == date(2026, 11, 4)
    assert any("NO simultáneos" in p or "no simult" in p.lower() for p in f.calidad.get("problemas", []))
    fac = f.facturas_por_cliente(["C10001"])["C10001"]
    assert len(fac) == 2 and {x.estado_sap for x in fac} == {"C", "O"}
    assert f.saldo_abierto("C10001") == 50.0


def test_etiqueta_de_frescura_no_dice_simulada():
    m = _crear(ConexionFalsa()).frescura()["motivo"]
    assert m.startswith("COPIA DE PRUEBAS") and "SIMULADA" not in m


def test_crear_fuente_modo_pg_exige_atestacion(monkeypatch):
    from cxc_portal.fuentes import crear_fuente
    monkeypatch.delenv(fp.ENV_ATESTACION, raising=False)
    with pytest.raises(fp.FuentePgError):
        crear_fuente(None, "gelsyn-pg-pruebas")


@pytest.mark.skipif(not (os.environ.get(fp.ENV_ATESTACION) and os.environ.get("CXC_PG_PRUEBAS_URL_FILE")), reason="Sin credencial local de la copia de pruebas")
def test_copia_real_de_pruebas_carga_y_nunca_es_publicable():
    f = fp.crear_fuente_pg_pruebas(max_edad_dias=60)
    assert f.calidad["estado"] == "OK" and not f.apta_para_publicar and f.tipo == "EMPRESARIAL_PRUEBAS"
    assert len(f.registros_sap()) > 1000


def test_tablas_ajenas_agregadas_por_gelsyn_no_rompen_la_identidad():
    conn = ConexionFalsa(tablas=72)                               # 72 el 6/11: se agregaron asistente_*; los conteos financieros no cambian
    assert _crear(conn).calidad["estado"] == "OK"


def test_identidad_estructural_columna_ausente_tipo_distinto_o_entidad_sin_version():
    c = ConexionFalsa(); c.sin_columnas = {("facturas", "paidToDate")}
    with pytest.raises(fp.FuentePgError, match=r"facturas\.paidToDate"):
        _crear(c)
    c = ConexionFalsa(); c.tipos = {("movimientos_contables", "debe"): "float8"}
    with pytest.raises(fp.FuentePgError, match="se esperaba numeric, hay float8"):
        _crear(c)
    c = ConexionFalsa(); c.sin_version = {"movimientos"}
    with pytest.raises(fp.FuentePgError, match="Sin versión publicada: movimientos"):
        _crear(c)


def test_nombres_y_vendedor_financieros_se_leen_de_las_tablas_autorizadas():
    f = _crear(ConexionFalsa())
    assert f.nombre_cliente("C10001") == "CLIENTE UNO SA"
    fin = f.clientes_financieros()
    assert fin["C10001"]["vendedor_nombre"] == "Vendedor Siete" and fin["C10001"]["zona"] == "04" and fin["C10002"]["zona"] is None


def test_la_estructura_critica_coincide_con_el_esquema_recibido_de_gelsyn():
    import re
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "docs" / "gelsyn" / "cosprobell_esquema_completo.sql").read_text(encoding="utf-8")
    tipo = {"integer": "int4", "bigint": "int8", "text": "text", "boolean": "bool", "uuid": "uuid", "date": "date", "numeric": "numeric", "jsonb": "jsonb"}
    for t, cols in ip.ESTRUCTURA_CRITICA.items():
        cuerpo = re.search(r"CREATE TABLE public\.%s \((.*?)\n\);" % t, sql, re.S).group(1)
        for c, tp in cols.items():
            m = re.search(r'^\s*"?%s"?\s+([a-z]+)' % c, cuerpo, re.M)
            assert m, f"{t}.{c} no está en el esquema de Gelsyn"
            esperado = "timestamp" if m.group(1) == "timestamp" else tipo.get(m.group(1), m.group(1))
            assert esperado == tp, f"{t}.{c}: esquema {esperado}, constante {tp}"
