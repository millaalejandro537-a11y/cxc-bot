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
        if "information_schema.tables" in sql:
            return ["n"], [(self.c.tablas,)]
        if "FROM finanzas_control WHERE entidad IN" in sql:
            return ["entidad", "publicadoEn"], [("clientes", datetime(2026, 10, 27, 18)), ("facturas", datetime(2026, 11, 4, 16)), ("pagos", datetime(2026, 11, 4, 16)),
                                               ("aplicacionesPagos", datetime(2026, 11, 4, 16))]
        if sql.startswith('SELECT "cardCode" FROM clientes'):
            return ["cardCode"], [("C1",), ("C2",)]
        if "FROM facturas" in sql:
            f = lambda n, d, tot, pag, st: (n, n, "C1", datetime(2026, 9, d), datetime(2026, 11, d), Decimal(tot), Decimal(pag), st, False)  # noqa: E731
            return (["docEntry", "docNum", "cardCode", "docDate", "docDueDate", "docTotal", "paidToDate", "documentStatus", "cancelled"],
                    [f(1, 1, "100.00", "100.00", "bost_Close"), f(2, 2, "50.00", "0.00", "bost_Open")])
        if "FROM pagos p" in sql or "FROM pagos_facturas" in sql:
            return ["pagoDocEntry", "facturaDocEntry", "sumApplied", "cancelado", "invoiceType"], [(10, 1, Decimal("100.00"), "N", "it_Invoice")]
        if "FROM pagos" in sql:
            return (["docEntry", "cardCode", "docDate", "total", "cashSum", "transferSum", "cancelled"], [(10, "C1", datetime(2026, 9, 20), None, Decimal("100.00"), Decimal("0"), False)])
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
    conn = ConexionFalsa(tablas=66)
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
    assert not any("cardName" in s for s in conn.ejecutadas)                       # nunca se leen nombres
    assert f.tipo == "EMPRESARIAL_PRUEBAS" and not f.en_vivo and not f.apta_para_publicar
    assert f.calidad["estado"] == "OK"


def test_cortes_reales_por_entidad_y_clasificacion_con_el_motor():
    f = _crear(ConexionFalsa())
    assert f.fecha_corte == date(2026, 11, 4)
    assert any("NO simultáneos" in p or "no simult" in p.lower() for p in f.calidad.get("problemas", []))
    fac = f.facturas_por_cliente(["C1"])["C1"]
    assert len(fac) == 2 and {x.estado_sap for x in fac} == {"C", "O"}
    assert f.saldo_abierto("C1") == 50.0


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
