from datetime import date
from decimal import Decimal as D

from cxc_portal import investigacion_saldos_pg as inv


def _l(k, t, v, f, tipo="ttReceipt"):
    return {"cardCode": k, "transId": t, "tipo": tipo, "fecha": f, "v": D(v)}


def test_el_corte_por_transid_explica_diferencias_que_el_corte_por_fecha_no():
    clientes = [{"cardCode": "C1", "saldo": D("100")}, {"cardCode": "C2", "saldo": D("50")}, {"cardCode": "C3", "saldo": D("0")}]
    lineas = [_l("C1", 1, "300", date(2026, 9, 1), "ttARInvoice"), _l("C1", 2, "-200", date(2026, 9, 5)),
              _l("C2", 3, "50", date(2026, 9, 2), "ttARInvoice"),
              _l("C2", 10, "-20", date(2026, 10, 20)),          # creado DESPUES de la foto de clientes pero con fecha retroactiva
              _l("C1", 11, "70", date(2026, 11, 3), "ttARInvoice")]
    r = inv.analizar(clientes, lineas, date(2026, 10, 27))
    assert r["sin_corte"]["difieren"] == 2 and r["sin_corte"]["coinciden"] == 1
    assert r["corte_por_fecha"]["difieren"] == 1                                     # la fecha no basta: la linea retroactiva sigue dentro
    assert r["corte_por_transid"]["minimas_diferencias"] == 0
    assert r["corte_por_transid"]["transid_limite_desde"] == 4 and r["corte_por_transid"]["transid_limite_hasta"] == 4
    assert r["posteriores_al_corte"]["lineas"] == 2 and r["posteriores_al_corte"]["retrofechadas_al_corte_de_clientes"] == 1
    assert r["posteriores_al_corte"]["neto"] == "50"


def test_sin_libro_y_sin_explicacion_quedan_visibles():
    clientes = [{"cardCode": "C1", "saldo": D("10")}, {"cardCode": "C2", "saldo": D("0")}]
    r = inv.analizar(clientes, [_l("C1", 1, "99", date(2026, 9, 1))])
    assert r["sin_libro"] == 1 and r["sin_libro_con_saldo"] == 0
    assert r["corte_por_transid"]["minimas_diferencias"] == 1                         # ningun corte lo explica: no se fuerza
    assert "C1" not in str(r)                                                          # solo agregados


def test_las_consultas_pasan_el_validador_de_solo_lectura():
    for sql in (inv.SQL_CORTES, inv.SQL_CLIENTES, inv.SQL_LINEAS):
        assert ip_valida(sql)


def ip_valida(sql):
    from cxc_portal import inspeccion_pg_pruebas as ip
    return ip.validar_sql(sql) == sql
