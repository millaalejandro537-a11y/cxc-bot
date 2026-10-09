"""Comportamiento de pago: limites del semaforo, periodo de prueba, predominio,
reincidencia, mejora, facturas abiertas y excepciones. Datos 100% sinteticos."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from cxc_bot.payment_behavior import (
    ALERTA_ABIERTA_MALA,
    ALERTA_MEJORA,
    ALERTA_PAGADA_MALA,
    ALERTA_REINCIDENCIA,
    EXC_ABIERTA_SIN_ATRASO,
    EXC_CONTADO_CONFIRMADO,
    EXC_FECHA_INCONSISTENTE,
    EXC_LIQUIDACION_NO_VERIFICABLE,
    EXC_POSIBLE_CONTADO,
    EXC_SIN_VENCIMIENTO,
    PILOT_LAS_VEGAS_60D,
    BehaviorRules,
    Categoria,
    CreditPolicy,
    EstadoCliente,
    InvoiceRecord,
    assess_invoice,
    categorize_days,
    evaluate_client,
    evaluate_portfolio,
)

CORTE = date(2026, 10, 8)
BASE = date(2025, 1, 1)


def paid(n, dias, cliente="C1", plazo=30, **kw):
    """Factura n pagada con `dias` de atraso (negativo = anticipada)."""
    f = BASE + timedelta(days=40 * n)
    v = f + timedelta(days=plazo)
    return InvoiceRecord(cliente, f"F{n:03d}", f, v, v + timedelta(days=dias), **kw)


def opened(n, dias_mora, cliente="C1", plazo=30, **kw):
    """Factura n abierta que a la fecha de corte lleva `dias_mora` de mora."""
    v = CORTE - timedelta(days=dias_mora)
    f = v - timedelta(days=plazo)
    return InvoiceRecord(cliente, f"F{n:03d}", f, v, None, **kw)


def client(invoices, **kw):
    return evaluate_client("C1", invoices, CORTE, **kw)


# ---------------------------------------------------------------- limites
@pytest.mark.parametrize(
    "dias,esperada",
    [
        (-30, Categoria.A), (0, Categoria.A), (5, Categoria.A),
        (6, Categoria.B), (15, Categoria.B),
        (16, Categoria.C), (60, Categoria.C),
        (61, Categoria.MALO), (400, Categoria.MALO),
    ],
)
def test_category_boundaries(dias, esperada):
    assert categorize_days(dias) is esperada


def test_assess_paid_invoice_days_and_trace():
    a = assess_invoice(paid(1, 7), CORTE)
    assert a.dias_atraso == 7 and a.categoria is Categoria.B and a.evaluada and a.pagada_verificable
    assert a.vencimiento_usado == a.vencimiento_sap and a.origen_vencimiento == "SAP"


def test_early_payment_is_A():
    a = assess_invoice(paid(1, -10), CORTE)
    assert a.dias_atraso == -10 and a.categoria is Categoria.A


def test_amount_never_changes_category():
    chica = assess_invoice(paid(1, 20, importe=1.0), CORTE)
    grande = assess_invoice(paid(1, 20, importe=9_999_999.0), CORTE)
    assert chica.categoria == grande.categoria == Categoria.C


# ---------------------------------------------------------- periodo de prueba
@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_zero_to_three_paid_is_en_prueba_without_color(n):
    r = client([paid(i, 0) for i in range(n)])
    assert r.estado is EstadoCliente.EN_PRUEBA
    assert r.categoria is None and r.color_candidato is None


def test_four_paid_gets_category():
    r = client([paid(i, 0) for i in range(4)])
    assert r.estado is EstadoCliente.CLASIFICADO
    assert r.categoria is Categoria.A and r.color_candidato == "VERDE"


def test_open_invoices_do_not_count_toward_minimum_of_four():
    r = client([paid(i, 0) for i in range(3)] + [opened(10, 80)])
    assert r.estado is EstadoCliente.EN_PRUEBA
    assert r.pagadas_total == 3


# ------------------------------------------------------------- predominio
def test_predominance_two_A_four_B_is_B():
    r = client([paid(0, 0), paid(1, 3)] + [paid(i, 10) for i in range(2, 6)])
    assert r.categoria is Categoria.B and r.conteo == {"A": 2, "B": 4}


def test_tie_goes_to_human_review_not_average():
    r = client([paid(0, 0), paid(1, 0), paid(2, 10), paid(3, 10)])
    assert r.estado is EstadoCliente.REVISION_HUMANA and r.categoria is None
    assert any("sin predominio" in m for m in r.motivos_revision)


def test_no_average_of_days_is_used():
    # 3 A y 1 MALO (400 dias): el promedio seria enorme; predomina A.
    r = client([paid(0, 0), paid(1, 0), paid(2, 0), paid(3, 400)])
    assert r.categoria is Categoria.A


def test_bad_invoice_alone_does_not_make_client_red_but_alerts():
    r = client([paid(i, 0) for i in range(5)] + [paid(9, 90)])
    assert r.categoria is Categoria.A and r.color_candidato == "VERDE"
    assert [a.tipo for a in r.alertas] == [ALERTA_PAGADA_MALA]


def test_predominant_malo_requires_management_and_never_approves_red():
    r = client([paid(i, 70) for i in range(4)])
    assert r.categoria is Categoria.MALO and r.color_candidato == "ROJO"
    assert r.requiere_gerencia is True
    assert r.vineta_aprobada is None


def test_vineta_aprobada_is_always_none():
    for dias in (0, 10, 30, 80):
        assert client([paid(i, dias) for i in range(5)]).vineta_aprobada is None


# ---------------------------------------------------------- facturas abiertas
def test_open_invoice_70_days_late_is_bad_range_and_alerts_without_waiting_payment():
    r = client([paid(i, 0) for i in range(4)] + [opened(10, 70)])
    abierta = [f for f in r.facturas if f.abierta][0]
    assert abierta.categoria is Categoria.MALO and abierta.evaluada and not abierta.pagada_verificable
    assert ALERTA_ABIERTA_MALA in [a.tipo for a in r.alertas]
    assert r.conteo == {"A": 4, "MALO": 1}


def test_open_invoice_counts_toward_predominance():
    r = client([paid(0, 0)] + [paid(i, 0) for i in range(1, 4)] + [opened(i, 70) for i in range(10, 14)])
    assert r.estado is EstadoCliente.REVISION_HUMANA  # 4 A vs 4 MALO: empate


@pytest.mark.parametrize("mora", [-20, 0, 5])
def test_open_invoice_within_tolerance_is_not_evidence(mora):
    a = assess_invoice(opened(1, mora), CORTE)
    assert a.categoria is None and not a.evaluada
    assert EXC_ABIERTA_SIN_ATRASO in a.excepciones


def test_open_invoice_at_6_days_is_B():
    assert assess_invoice(opened(1, 6), CORTE).categoria is Categoria.B


def test_settling_keeps_real_days_and_is_not_duplicated():
    inv = opened(10, 70)
    antes = client([paid(i, 0) for i in range(4)] + [inv])
    pagada = replace(inv, fecha_pago=CORTE)  # misma factura, ahora liquidada con la mora real
    despues = client([paid(i, 0) for i in range(4)] + [pagada])
    assert sum(1 for f in antes.facturas if f.factura == inv.factura) == 1
    assert sum(1 for f in despues.facturas if f.factura == inv.factura) == 1
    f = [f for f in despues.facturas if f.factura == inv.factura][0]
    assert f.dias_atraso == 70 and f.categoria is Categoria.MALO and f.pagada_verificable
    assert despues.pagadas_verificables == 5


# ------------------------------------------------------------- reincidencia
def test_six_consecutive_late_is_recidivism():
    r = client([paid(i, 6) for i in range(6)])
    assert r.reincidencia and len(r.reincidencia_facturas) == 6
    assert ALERTA_REINCIDENCIA in [a.tipo for a in r.alertas]
    assert r.categoria is Categoria.B  # la reincidencia no cambia la categoria por si sola


def test_five_consecutive_late_is_not_recidivism():
    assert not client([paid(i, 6) for i in range(5)]).reincidencia


def test_payments_within_tolerance_do_not_count_as_late():
    # 5 dias tarde es A y rompe la racha: 3 tardias + 1 en tolerancia + 3 tardias
    r = client([paid(i, 6) for i in range(3)] + [paid(3, 5)] + [paid(i, 6) for i in range(4, 7)])
    assert not r.reincidencia


def test_recidivism_run_can_be_in_the_past():
    r = client([paid(i, 20) for i in range(6)] + [paid(i, 0) for i in range(6, 9)])
    assert r.reincidencia


# ------------------------------------------------------------------- mejora
def _con_mejora(n_puntuales):
    tardias = [paid(i, 20) for i in range(6)]
    despues = [paid(i, 0) for i in range(6, 6 + n_puntuales)]
    conversacion = tardias[-1].fecha_factura + timedelta(days=1)
    return tardias + despues, conversacion


def test_three_on_time_after_conversation_is_improvement_but_keeps_category():
    facturas, conv = _con_mejora(3)
    sin = client(facturas)
    con = client(facturas, fecha_conversacion=conv)
    assert con.mejora and con.mejora_consecutivas == 3 and con.requiere_reevaluacion
    assert ALERTA_MEJORA in [a.tipo for a in con.alertas]
    assert con.categoria == sin.categoria  # no cambia automaticamente
    assert not sin.mejora  # sin fecha de conversacion no se evalua


def test_two_on_time_is_not_enough():
    facturas, conv = _con_mejora(2)
    assert not client(facturas, fecha_conversacion=conv).mejora


def test_on_time_before_conversation_do_not_count():
    facturas, _ = _con_mejora(3)
    conv_tarde = facturas[-1].fecha_factura + timedelta(days=1)  # todas quedan antes
    assert not client(facturas, fecha_conversacion=conv_tarde).mejora


def test_one_late_breaks_the_improvement_streak():
    tardias = [paid(i, 20) for i in range(6)]
    mezcla = [paid(6, 0), paid(7, 0), paid(8, 3), paid(9, 0), paid(10, 0)]  # 3 dias tarde interrumpe
    conv = tardias[-1].fecha_factura + timedelta(days=1)
    assert not client(tardias + mezcla, fecha_conversacion=conv).mejora


def test_on_time_means_not_later_than_due_date_not_tolerance():
    tardias = [paid(i, 20) for i in range(6)]
    en_tolerancia = [paid(i, 4) for i in range(6, 9)]  # A, pero no "a mas tardar al vencimiento"
    conv = tardias[-1].fecha_factura + timedelta(days=1)
    assert not client(tardias + en_tolerancia, fecha_conversacion=conv).mejora


# --------------------------------------------------------------- excepciones
def test_possible_cash_sale_same_day_due_is_flagged_and_not_definitive():
    f = BASE
    inv = InvoiceRecord("C1", "X", f, f, f + timedelta(days=1))  # vence el mismo dia
    a = assess_invoice(inv, CORTE)
    assert EXC_POSIBLE_CONTADO in a.excepciones
    r = client([inv] + [paid(i, 0) for i in range(1, 4)])
    assert not r.definitivo
    assert any(EXC_POSIBLE_CONTADO in m for m in r.motivos_revision)


def test_confirmed_credit_sale_same_day_due_is_evaluated_normally():
    f = BASE
    inv = InvoiceRecord("C1", "X", f, f, f + timedelta(days=20), es_contado=False)
    a = assess_invoice(inv, CORTE)
    assert EXC_POSIBLE_CONTADO not in a.excepciones and a.categoria is Categoria.C


def test_confirmed_cash_sale_is_excluded_from_everything():
    f = BASE
    inv = InvoiceRecord("C1", "X", f, f, f, es_contado=True)
    r = client([inv] + [paid(i, 0) for i in range(1, 4)])
    assert r.estado is EstadoCliente.EN_PRUEBA  # solo 3 de credito
    assert all(x.factura != "X" for x in r.facturas)
    assert assess_invoice(inv, CORTE).excepciones == (EXC_CONTADO_CONFIRMADO,)


def test_settled_without_reliable_date_is_not_verifiable():
    sin_fecha = InvoiceRecord("C1", "S", BASE, BASE + timedelta(days=30), None, liquidada_sin_fecha=True)
    a = assess_invoice(sin_fecha, CORTE)
    assert a.categoria is None and not a.evaluada and not a.abierta
    assert EXC_LIQUIDACION_NO_VERIFICABLE in a.excepciones
    r = client([sin_fecha] + [paid(i, 0) for i in range(1, 4)])
    assert r.pagadas_total == 4 and r.pagadas_verificables == 3
    assert r.estado is EstadoCliente.EN_PRUEBA and not r.definitivo
    assert any("sin fecha de liquidacion" in m for m in r.motivos_revision)


def test_missing_due_date_is_excluded_and_flagged():
    inv = InvoiceRecord("C1", "N", BASE, None, BASE + timedelta(days=10))
    a = assess_invoice(inv, CORTE)
    assert EXC_SIN_VENCIMIENTO in a.excepciones and a.categoria is None


def test_inconsistent_payment_dates_are_excluded():
    antes = InvoiceRecord("C1", "I1", BASE, BASE + timedelta(days=30), BASE - timedelta(days=1))
    futuro = InvoiceRecord("C1", "I2", BASE, BASE + timedelta(days=30), CORTE + timedelta(days=1))
    for inv in (antes, futuro):
        a = assess_invoice(inv, CORTE)
        assert EXC_FECHA_INCONSISTENTE in a.excepciones and a.categoria is None


# ------------------------------------------------------------ plazo supuesto
def test_pilot_60_days_keeps_original_sap_due_date_alongside():
    # SAP dice 30 dias; con el supuesto de 60 el pago a +45 desde SAP es puntual.
    inv = paid(1, 45, plazo=30)
    sap = assess_invoice(inv, CORTE)
    piloto = assess_invoice(inv, CORTE, policy=PILOT_LAS_VEGAS_60D)
    assert sap.categoria is Categoria.C and sap.origen_vencimiento == "SAP"
    assert piloto.vencimiento_sap == inv.vencimiento_sap  # original conservado
    assert piloto.vencimiento_usado == inv.fecha_factura + timedelta(days=60)
    assert piloto.origen_vencimiento == "SUPUESTO_60D"
    assert piloto.dias_atraso == 15 and piloto.categoria is Categoria.B


def test_pilot_policy_does_not_mutate_input_record():
    inv = paid(1, 45)
    before = (inv.vencimiento_sap, inv.fecha_factura)
    assess_invoice(inv, CORTE, policy=PILOT_LAS_VEGAS_60D)
    assert (inv.vencimiento_sap, inv.fecha_factura) == before


def test_pilot_same_day_due_still_flagged_as_possible_cash_sale():
    f = BASE
    inv = InvoiceRecord("C1", "X", f, f, f + timedelta(days=10))
    a = assess_invoice(inv, CORTE, policy=PILOT_LAS_VEGAS_60D)
    assert EXC_POSIBLE_CONTADO in a.excepciones  # no se convierte en credito excelente en silencio
    assert a.vencimiento_usado == f + timedelta(days=60)


def test_custom_policy_for_other_term():
    a = assess_invoice(paid(1, 0, plazo=30), CORTE, policy=CreditPolicy(plazo_supuesto_dias=30))
    assert a.origen_vencimiento == "SUPUESTO_30D"


# --------------------------------------------------------------- portafolio
def test_portfolio_includes_clients_without_invoices_and_groups_case_insensitive():
    facturas = [paid(i, 0, cliente="c10573") for i in range(4)] + [paid(9, 0, cliente="C10573 ")]
    res = evaluate_portfolio(facturas, CORTE, clientes=["C10573", "C11133"])
    assert set(res) == {"C10573", "C11133"}
    assert res["C10573"].pagadas_verificables == 5
    assert res["C11133"].estado is EstadoCliente.EN_PRUEBA


def test_portfolio_uses_per_client_conversation_date():
    facturas, conv = _con_mejora(3)
    facturas = [replace(f, cliente="C9") for f in facturas]
    res = evaluate_portfolio(facturas, CORTE, conversaciones={"c9": conv})
    assert res["C9"].mejora


def test_custom_rules_change_thresholds():
    reglas = BehaviorRules(min_pagadas=2)
    r = client([paid(0, 0), paid(1, 0)], rules=reglas)
    assert r.estado is EstadoCliente.CLASIFICADO


def test_results_are_immutable():
    r = client([paid(i, 0) for i in range(4)])
    with pytest.raises(Exception):
        r.categoria = Categoria.MALO
