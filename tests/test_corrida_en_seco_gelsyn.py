"""Corrida en seco (Gelsyn vs aprobado del 08/10): reconstruccion de la evidencia AL CORTE y comparacion. Datos 100 % sinteticos, sin BD."""

from datetime import date, datetime

import pytest

from cxc_bot import payment_semaphore as ps
from cxc_portal import corrida_en_seco_gelsyn as c
from cxc_portal import inspeccion_pg_pruebas as ip

CORTE = date(2026, 10, 8)


def _f(entry, fecha, total, estado, vence=None, cancelled=False, cliente="C90001"):
    return {"docEntry": entry, "docNum": entry, "cardCode": cliente, "docDate": datetime.combine(fecha, datetime.min.time()), "docDueDate": datetime.combine(vence or fecha, datetime.min.time()),
            "docTotal": total, "paidToDate": total if estado == "bost_Close" else 0, "documentStatus": estado, "cancelled": cancelled}


def _p(entry, fecha, total, cancelled=False, cliente="C90001"):
    return {"docEntry": entry, "cardCode": cliente, "docDate": datetime.combine(fecha, datetime.min.time()), "total": total, "cancelled": cancelled}


def _a(pago, factura, monto, cancelado="N", tipo="it_Invoice"):
    return {"pagoDocEntry": pago, "facturaDocEntry": factura, "sumApplied": monto, "cancelado": cancelado, "invoiceType": tipo}


def _datos(f, p, a, clientes=("C90001",)):
    return {"clientes": set(clientes), "facturas": f, "pagos": p, "aplicaciones": a}


def test_factura_cerrada_despues_del_corte_estaba_abierta_al_corte():
    d = _datos([_f(1, date(2026, 8, 1), 100, "bost_Close")], [_p(10, date(2026, 10, 20), 100)], [_a(10, 1, 100)])
    x = c.construir_facturas(d, CORTE)["C90001"][0]
    assert x.estado_sap == "O" and x.recibos == () and x.aplicado_sap == 0
    y = c.construir_facturas(d, date(2026, 11, 4))["C90001"][0]                                  # al 04/11 si esta cerrada y con su recibo
    assert y.estado_sap == "C" and len(y.recibos) == 1 and y.aplicado_sap == 100


def test_cerrada_sin_evidencia_de_cobro_sigue_cerrada_y_queda_como_cierre_no_acreditado():
    d = _datos([_f(1, date(2026, 8, 1), 100, "bost_Close")], [], [])
    x = c.construir_facturas(d, CORTE)["C90001"][0]
    assert x.estado_sap == "C" and x.recibos == ()
    r = ps.evaluar_cliente("C90001", [x], CORTE, 60, None)
    assert r.no_acreditadas == 1 and r.evaluables == 0                                         # el motor la trata como excepcion, no como pagada a tiempo


def test_se_excluyen_facturas_posteriores_canceladas_y_aplicaciones_no_validas():
    d = _datos([_f(1, date(2026, 8, 1), 100, "bost_Close"), _f(2, date(2026, 10, 9), 50, "bost_Open"), _f(3, date(2026, 8, 2), 70, "bost_Close", cancelled=True)],
               [_p(10, date(2026, 8, 20), 100), _p(11, date(2026, 8, 21), 70, cancelled=True)],
               [_a(10, 1, 100), _a(11, 1, 5), _a(10, None, 9, tipo="it_OpeningBalance"), _a(10, 1, 7, cancelado="Y"), _a(10, 1, 3, tipo="it_Receipt")])
    fs = c.construir_facturas(d, CORTE)["C90001"]
    assert [x.id_factura for x in fs] == ["1"]                                                  # la 2 es posterior al corte; la 3, cancelada
    assert [(r.numero, r.monto) for r in fs[0].recibos] == [("10", 100.0)]                      # pago cancelado, aplicacion cancelada, saldos de apertura y recibos no cuentan


def test_la_evaluacion_usa_el_motor_unico_y_el_plazo_60():
    fact = [_f(i, date(2026, 1, 1 + i), 100, "bost_Close", vence=date(2026, 1, 1 + i)) for i in range(1, 8)]
    pag = [_p(100 + i, date(2026, 1, 1 + i), 100) for i in range(1, 8)]                         # pagan el mismo dia de emision: sin mora
    app = [_a(100 + i, i, 100) for i in range(1, 8)]
    r = c.evaluar(_datos(fact, pag, app), ["C90001"], CORTE)["C90001"]
    assert r.final == ps.A and r.evaluables >= 4 and (r.promedio_mora or 0) == 0


@pytest.mark.parametrize("etiqueta,esperado", [("Verde (A)", ps.A), ("Azul - bueno", ps.B), ("Amarilla - Cliente regular", ps.C), ("Cliente contado", ps.SOLO_CONTADO), ("Rojo", ps.SOLO_CONTADO),
                                               ("Solo contado", ps.SOLO_CONTADO), ("En prueba / blanca", ps.EN_PRUEBA)])
def test_etiquetas_aprobadas_se_traducen_con_la_regla_vigente(etiqueta, esperado):
    assert c.clase_aprobada(etiqueta) == esperado


def _res(final, evalu=5, noacr=0, prom=10.0):
    class R:
        pass
    r = R()
    r.final, r.evaluables, r.no_acreditadas, r.promedio_mora = final, evalu, noacr, prom
    return r


def test_categorias_de_comparacion():
    ap = {"final": ps.A, "evaluables": 5, "promedio": 3.0}
    assert c.comparar(ap, _res(ps.A), True)[0] == "COINCIDE"
    assert c.comparar(ap, _res(ps.B, evalu=4), True) == ("CAMBIA", "menos facturas evaluables (cierres sin cobro acreditado en Gelsyn)")
    assert c.comparar(ap, _res(ps.B, evalu=5, noacr=1), True)[1].startswith("cierres sin cobro acreditado")
    assert c.comparar(ap, _res(ps.B, evalu=5, prom=9.0), True)[1].startswith("mora promedio distinta")
    assert c.comparar(ap, _res(ps.REVISION_HUMANA, evalu=0), True)[0] == "INDETERMINADO"
    assert c.comparar({"final": ps.EN_PRUEBA}, _res(ps.A), True)[0] == "CAMBIA"
    assert c.comparar(ap, None, False)[0] == "SIN_DATOS_EN_GELSYN"


def test_las_consultas_son_select_financieros_solo_con_codigo_fechas_e_importes():
    for q in (c.SQL_FACTURAS, c.SQL_PAGOS, c.SQL_APLICACIONES, c.SQL_CLIENTES):
        ip.validar_sql(q)
        assert "cardName" not in q and "phone" not in q.lower() and "email" not in q.lower()


def test_el_resumen_es_agregado_y_el_detalle_no_lleva_nombres():
    ap = {"LAS_VEGAS": {"C90001": {"final": ps.A, "evaluables": 7, "promedio": 0.0, "historial": 7, "ventana": 7}}}
    fact = [_f(i, date(2026, 1, 1 + i), 100, "bost_Close", vence=date(2026, 1, 1 + i)) for i in range(1, 8)]
    d = _datos(fact, [_p(100 + i, date(2026, 1, 1 + i), 100) for i in range(1, 8)], [_a(100 + i, i, 100) for i in range(1, 8)])
    resumen, filas = c.resumir(ap, d)
    assert resumen["total"] == {"COINCIDE": 1} and resumen["por_ruta"]["LAS_VEGAS"]["COINCIDE"] == 1
    assert set(filas[0]) == {"ruta", "codigo", "aprobada_0810", "gelsyn_0810", "gelsyn_0411", "categoria", "causa", "evaluables_aprobado", "evaluables_gelsyn",
                             "mora_aprobada", "mora_gelsyn", "no_acreditadas_gelsyn"}


def test_sin_atestacion_no_se_conecta(monkeypatch, capsys):
    monkeypatch.setattr(ip, "_conectar", lambda u: (_ for _ in ()).throw(AssertionError("no debia conectar")))
    assert c.main([]) == 2 and "atestación" not in capsys.readouterr().out
