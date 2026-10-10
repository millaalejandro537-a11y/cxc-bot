from datetime import date, timedelta

from cxc_bot import payment_semaphore as ps
from cxc_portal.demo import CORTE, FuenteSintetica
from cxc_portal.validar_fuente import FALLA, OK, validar_fuente


def _por_id(r):
    return {c["id"]: c for c in r["checks"]}


def test_snapshot_no_es_apto_para_uso_operativo_por_frescura_y_no_ser_en_vivo():
    r = validar_fuente(FuenteSintetica(), hoy=CORTE + timedelta(days=5))
    c = _por_id(r)
    assert r["apta_para_uso_operativo"] is False
    assert c["frescura.en_vivo"]["estado"] == FALLA and c["frescura.fecha_corte"]["estado"] == FALLA
    assert c["contrato.interfaz"]["estado"] == OK and c["seguridad.solo_lectura"]["estado"] == OK and c["integridad.facturas"]["estado"] == OK


class Viva(FuenteSintetica):
    en_vivo = True
    fecha_corte = date.today()


def test_una_fuente_viva_coherente_y_fresca_es_apta():
    r = validar_fuente(Viva(), controles={"por_cliente": {"C90007": 6400.0}, "total_global": None})
    assert r["apta_para_uso_operativo"] is True and _por_id(r)["conciliacion.por_cliente"]["estado"] == OK


def test_detecta_diferencias_contra_los_controles_de_sap():
    r = validar_fuente(Viva(), controles={"por_cliente": {"C90007": 1.0}, "total_global": 1.0})
    c = _por_id(r)
    assert not r["apta_para_uso_operativo"] and c["conciliacion.por_cliente"]["estado"] == FALLA and c["conciliacion.total_global"]["estado"] == FALLA


def test_detecta_datos_incoherentes_y_metodos_de_escritura():
    class Mala(Viva):
        def facturas_por_cliente(self, codigos):
            f = ps.FacturaSAP("1", "1", date(2026, 1, 1), None, 100.0, 150.0, "O", ())            # aplicado > total, abierta sin saldo
            g = ps.FacturaSAP("1", "2", date(2026, 1, 2), None, 100.0, 0.0, "X", ())              # id duplicado y estado invalido
            return {c.upper(): [f, g] for c in codigos}

        def update_saldo(self, codigo, saldo):  # método de escritura expuesto
            pass
    r = validar_fuente(Mala())
    c = _por_id(r)
    assert c["seguridad.solo_lectura"]["estado"] == FALLA
    assert c["integridad.facturas"]["estado"] == FALLA and any("duplicado" in p for p in c["integridad.facturas"]["problemas"])


def test_interfaz_incompleta_se_reporta_sin_romper():
    class Incompleta:
        en_vivo = True
    r = validar_fuente(Incompleta())
    assert r["apta_para_uso_operativo"] is False and _por_id(r)["contrato.interfaz"]["estado"] == FALLA
