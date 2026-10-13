from datetime import date, timedelta

import pytest

from cxc_bot import payment_semaphore as ps

CORTE = date(2026, 10, 8)


def fac(n, emision, mora=None, plazo=60, total=1000.0, estado="C", recibos=None, venc_sap=None):
    """Pagada con `mora` dias de atraso (recibo unico por el total), o la que se indique en `recibos`."""
    if recibos is None and mora is not None:
        recibos = (ps.Recibo(f"R{n}", emision + timedelta(days=plazo + mora), total),)
    return ps.FacturaSAP(str(n), str(n), emision, venc_sap or emision + timedelta(days=30), total,
                         total if estado == "C" else 0.0, estado, tuple(recibos or ()))


def serie(moras, ini=date(2025, 1, 1), paso=20, **kw):
    return [fac(i + 1, ini + timedelta(days=paso * i), m, **kw) for i, m in enumerate(moras)]


def ev(facturas, plazo=60, dec=None):
    return ps.evaluar_cliente("C1", facturas, CORTE, plazo, dec)


def test_menos_de_4_en_historial_es_prueba_aunque_haya_mora():
    r = ev(serie([0, 300, 0]))
    assert r.calculado == ps.EN_PRUEBA and r.color_final == "BLANCO"


def test_historial_cuenta_pagadas_y_abiertas():
    r = ev(serie([0, 0]) + [fac(90, CORTE - timedelta(days=100), estado="O"), fac(91, CORTE - timedelta(days=90), estado="O")])
    assert r.calculado != ps.EN_PRUEBA and r.historial_total == 4


@pytest.mark.parametrize("mora,esperado", [(0, "A"), (5, "A"), (6, "B"), (15, "B"), (16, "C"), (60, "C"), (61, ps.SOLO_CONTADO)])
def test_umbrales(mora, esperado):
    assert ev(serie([mora] * 4)).calculado == esperado


def test_umbral_de_contado_con_la_regla_anterior_sigue_disponible_como_candidato():
    assert ps.clasificar_media(61, contado_automatico=False) == ps.CANDIDATO_SOLO_CONTADO and ps.clasificar_media(61) == ps.SOLO_CONTADO


def test_media_no_se_redondea():
    r = ev(serie([5, 6, 5, 6]))
    assert r.promedio_mora == 5.5 and r.calculado == "B"


def test_pago_anticipado_cuenta_cero_no_negativo():
    r = ev(serie([-40, -30, 10, 10]))
    assert r.promedio_mora == 5.0 and r.calculado == "A"


def test_solo_las_10_mas_recientes_y_desempate_por_numero_desc():
    r = ev(serie([200, 200, 200] + [0] * 10))
    assert r.en_ventana == 10 and r.historial_total == 13 and r.calculado == "A"
    mismo = date(2026, 1, 1)
    r2 = ev([fac(n, mismo, 0) for n in (5, 100, 20)] + serie([0] * 9))
    assert [f.numero for f in r2.facturas][:3] == ["100", "20", "5"]


def test_abierta_mas_de_5_dias_entra_y_hasta_5_no_entra():
    base = serie([10, 10])
    r = ev(base + [fac(90, CORTE - timedelta(days=63), estado="O"), fac(91, CORTE - timedelta(days=66), estado="O")])  # mora 3 y 6
    assert r.evaluables == 3 and r.suma_mora == 26 and r.abiertas == 2
    ab = {f.numero: f for f in r.facturas}
    assert not ab["90"].en_media and ps.EXC_ABIERTA_SIN_ATRASO in ab["90"].excepciones and ab["91"].en_media and ab["91"].mora == 6


def test_cierre_sin_cobro_integro_no_es_pago_ni_nota_de_credito_es_pago():
    nc = fac(90, date(2025, 6, 1), estado="C", recibos=())             # cerrada por nota de credito, sin recibos
    parcial = fac(91, date(2025, 7, 1), estado="C", total=1000.0, recibos=(ps.Recibo("R", date(2025, 8, 1), 400.0),))
    r = ev(serie([0, 0, 0]) + [nc, parcial])
    assert r.no_acreditadas == 2 and r.evaluables == 3
    assert all(f.estado == ps.CERRADA_NO_ACREDITADA and not f.en_media for f in r.facturas if f.numero in ("90", "91"))


def test_recibo_anterior_a_la_emision_no_respalda_el_pago():
    emision = date(2025, 8, 1)
    f = fac(50, emision, estado="C", total=1000.0, recibos=(
        ps.Recibo("R1", emision - timedelta(days=300), 60.0, "consulta 2"), ps.Recibo("R2", emision + timedelta(days=5), 940.0)))
    r = ev(serie([0, 0, 0]) + [f])
    ef = next(x for x in r.facturas if x.numero == "50")
    assert ef.estado == ps.CERRADA_NO_ACREDITADA and ps.EXC_RECIBO_ANTERIOR_A_EMISION in ef.excepciones


def test_pago_en_varios_recibos_usa_el_ultimo_y_exige_el_total():
    e = date(2025, 1, 1)
    f = fac(7, e, estado="C", total=1000.0, recibos=(ps.Recibo("a", e + timedelta(days=10), 400.0), ps.Recibo("b", e + timedelta(days=90), 600.0)))
    ef = next(x for x in ev(serie([0, 0, 0]) + [f]).facturas if x.numero == "7")
    assert ef.estado == ps.PAGADA and ef.mora == 30 and ef.ultimo_recibo == e + timedelta(days=90)


def test_el_plazo_de_la_ruta_manda_no_el_vencimiento_sap():
    f = [fac(i, date(2026, 1, 1) + timedelta(days=i * 10), None, recibos=(ps.Recibo("r", date(2026, 1, 1) + timedelta(days=i * 10 + 40), 1000.0),),
             venc_sap=date(2026, 1, 1) + timedelta(days=i * 10 + 365)) for i in range(4)]
    assert ev(f, plazo=60).promedio_mora == 0.0 and ev(f, plazo=30).promedio_mora == 10.0
    assert ev(f).facturas[0].vencimiento_sap is not None  # SAP se conserva para cotejo


def test_sin_base_para_la_media_va_a_revision_humana():
    r = ev([fac(i, date(2025, 1, 1) + timedelta(days=i), estado="C", recibos=()) for i in range(5)])
    assert r.calculado == ps.REVISION_HUMANA and r.requiere_gerencia


def test_contado_es_rojo_automatico_sin_aprobacion_adicional_de_gerencia():
    """Regla vigente (2026-10-12): el sistema determina; si el resultado es Contado corresponde ROJO sin aprobacion adicional."""
    r = ev(serie([90] * 5))
    assert r.calculado == r.final == ps.SOLO_CONTADO and r.color_final == "ROJO" and not r.requiere_gerencia and r.decision_gerencia is None
    assert any("ROJA" in e and "no requiere aprobación adicional" in e for e in r.explicacion)


def test_regla_anterior_candidato_pendiente_de_gerencia_sigue_disponible_por_parametro():
    r = ps.evaluar_cliente("C1", serie([90] * 5), CORTE, 60, None, contado_automatico=False)
    assert r.final == ps.CANDIDATO_SOLO_CONTADO and r.color_final == "PENDIENTE_GERENCIA" and r.requiere_gerencia


def sin_base():
    """4 facturas cerradas sin recibos ni cobro acreditado: historial suficiente pero nada aporta a la media (revision humana)."""
    return [fac(i + 1, date(2025, 1, 1) + timedelta(days=20 * i)) for i in range(4)]


def test_el_sistema_prevalece_sobre_una_decision_anterior_y_esta_se_conserva_para_trazabilidad():
    dec = ps.DecisionGerencia("C", "Mal cobro de un vendedor anterior.", "Gerencia", "2026-10-09")
    r = ev(serie([120] * 5), dec=dec)
    assert r.calculado == ps.SOLO_CONTADO and r.promedio_mora == 120.0 and r.alertas
    assert r.final == ps.SOLO_CONTADO and r.color_final == "ROJO" and not r.decision_aplicada          # contado = ROJO automatico
    assert r.decision_gerencia.fecha == "2026-10-09" and r.decision_gerencia.clasificacion_final == "C"  # la decision anterior NO se pierde
    assert any("solo para trazabilidad" in x and "prevalece el resultado vigente del sistema" in x for x in r.explicacion)
    d = r.to_dict()
    assert d["calculado"] == "SOLO_CONTADO" and d["final"] == "SOLO_CONTADO" and d["decision_aplicada"] is False and d["decision_gerencia"]["actor"] == "Gerencia"


def test_la_decision_solo_resuelve_lo_que_el_sistema_no_puede_determinar():
    dec = ps.DecisionGerencia("C", "Evidencia revisada por gerencia: cliente regular.", "Gerencia", "2026-10-13")
    r = ev(sin_base(), dec=dec)
    assert r.calculado == ps.REVISION_HUMANA and r.final == "C" and r.decision_aplicada and not r.requiere_gerencia
    assert ev(sin_base()).requiere_gerencia                                                              # sin decision: revision humana pendiente


def test_la_precedencia_del_sistema_se_puede_desactivar(monkeypatch):
    monkeypatch.setattr(ps, "SISTEMA_PREVALECE", False)
    dec = ps.DecisionGerencia("C", "Mal cobro de un vendedor anterior.", "Gerencia", "2026-10-09")
    r = ev(serie([120] * 5), dec=dec)
    assert r.final == "C" and r.decision_aplicada and r.calculado == ps.SOLO_CONTADO


def test_con_la_regla_anterior_de_candidatos_la_decision_sigue_resolviendo():
    dec = ps.DecisionGerencia(ps.SOLO_CONTADO, "Confirmado por gerencia como solo contado.", "Gerencia", "2026-10-09")
    r = ps.evaluar_cliente("C1", serie([90] * 5), CORTE, 60, dec, contado_automatico=False)
    assert r.calculado == ps.CANDIDATO_SOLO_CONTADO and r.final == ps.SOLO_CONTADO and r.decision_aplicada


def test_anticipo_anterior_a_la_emision_no_respalda_el_pago_pero_queda_en_la_evidencia():
    e = date(2025, 8, 1)
    f = fac(50, e, total=1000.0, recibos=(ps.Recibo("A1", e - timedelta(days=30), 1000.0, "consulta 2", e + timedelta(days=90)),))
    r = ev(serie([0, 0, 0]) + [f])
    ef = next(x for x in r.facturas if x.numero == "50")
    assert r.evaluables == 3 and ef.estado == ps.CERRADA_NO_ACREDITADA and ef.mora is None and not ef.en_media
    assert ef.recibos_anteriores == 1000.0 and ef.recibos_total == 0.0 and ps.EXC_RECIBO_ANTERIOR_A_EMISION in ef.excepciones
    assert "anteriores a la emisión 1,000.00" in ef.motivo and "explicado por SAP" in ef.motivo and ef.to_dict()["recibos_anteriores"] == 1000.0


def test_con_el_interruptor_de_anticipos_cuenta_como_cobro_y_la_fecha_de_conciliacion_nunca_genera_mora():
    e = date(2025, 8, 1)
    # recibo del 02/07 (antes de la emision), conciliado por SAP 200 dias despues del vencimiento: la fecha de PAGO manda, no la de conciliacion
    f = fac(50, e, total=1000.0, recibos=(ps.Recibo("A1", e - timedelta(days=30), 1000.0, "consulta 2", e + timedelta(days=260)),))
    r = ps.evaluar_cliente("C1", serie([0, 0, 0]) + [f], CORTE, 60, anticipos_respaldan_pago=True)
    ef = next(x for x in r.facturas if x.numero == "50")
    assert ef.estado == ps.PAGADA and ef.mora == 0 and ef.en_media and r.evaluables == 4
    assert ps.EXC_RECIBO_ANTERIOR_A_EMISION in ef.excepciones and "anticipo conciliado" in ef.motivo
    assert ef.recibos[0].conciliacion == e + timedelta(days=260) and ef.to_dict()["recibos"][0]["conciliacion"] == (e + timedelta(days=260)).isoformat()
    # y por defecto (regla aprobada) el resultado es el contrario
    assert ev(serie([0, 0, 0]) + [f]).evaluables == 3


def test_nota_de_credito_no_es_pago_y_el_motivo_explica_el_cierre():
    e = date(2025, 3, 1)
    f = ps.FacturaSAP("9", "9", e, None, 1500.0, 1500.0, "C", (ps.Recibo("R", e + timedelta(days=29), 300.0, "consulta 3"),), notas_credito=1200.0)
    r = ev(serie([0, 0, 0]) + [f])
    ef = next(x for x in r.facturas if x.numero == "9")
    assert ef.estado == ps.CERRADA_NO_ACREDITADA and not ef.en_media and r.evaluables == 3 and ef.notas_credito == 1200.0
    assert "recibos 300.00" in ef.motivo and "nota de crédito 1,200.00 (no es un pago)" in ef.motivo and "explicado por SAP" in ef.motivo


def test_alerta_mayor_60_aunque_la_media_sea_buena():
    # una factura abierta con 61+ dias entre varias puntuales: B/A en media, alerta individual igual
    r = ev(serie([0] * 12) + [fac(99, CORTE - timedelta(days=60 + 75), estado="O")])
    assert len(r.alertas_cobro) == 1 and "75" in r.alertas_cobro[0].texto and r.calculado in ("A", "B", "C")
    assert next(f for f in r.facturas if f.numero == "99").alerta_mayor_60


def test_alertas_separadas_abiertas_son_cobro_y_pagadas_son_historicas_nunca_cobro():
    pagada_mala = fac(50, date(2025, 1, 1), 90)                                  # pagada con 90 d de mora
    abierta_mala = fac(51, CORTE - timedelta(days=60 + 70), estado="O", total=800.0)  # abierta con 70 d
    r = ev(serie([0, 0], ini=date(2025, 6, 1)) + [pagada_mala, abierta_mala])
    assert [(a.numero, a.mora, a.saldo_pendiente) for a in r.alertas_cobro] == [("51", 70, 800.0)]
    assert [(a.numero, a.mora, a.saldo_pendiente) for a in r.alertas_historicas] == [("50", 90, None)]
    assert all(a.tipo == ps.ALERTA_COBRO for a in r.alertas_cobro) and all(a.tipo == ps.ALERTA_HISTORICA for a in r.alertas_historicas)
    assert "no es cobro pendiente" in r.alertas_historicas[0].texto
    d = r.to_dict()
    assert len(d["alertas_cobro"]) == 1 and len(d["alertas_historicas"]) == 1 and len(r.alertas) == 2


def test_saldo_pendiente_de_alerta_de_cobro_descuenta_lo_aplicado():
    f = ps.FacturaSAP("1", "1", CORTE - timedelta(days=200), None, 8400.0, 4330.0, "O", ())
    r = ev([f] + serie([0, 0, 0]))
    assert r.alertas_cobro[0].saldo_pendiente == 4070.0


class _Fuente:
    fecha_corte = CORTE
    etiqueta = "prueba"

    def __init__(self, datos):
        self.datos = datos

    def facturas_por_cliente(self, codigos):
        return {c: self.datos.get(c, []) for c in codigos}


def test_evaluar_ruta_usa_el_mismo_motor_y_aplica_decisiones():
    fuente = _Fuente({"C1": serie([90] * 5), "C2": serie([0] * 4)})
    dec = {"c1": ps.DecisionGerencia("C", "Decisión de gerencia que sustituye al automático", "Gerencia", "2026-10-10")}
    res = ps.evaluar_ruta(["C1", "C2", "C3"], fuente, 60, dec)
    assert res["C1"].calculado == ps.SOLO_CONTADO and res["C1"].final == ps.SOLO_CONTADO and res["C1"].color_final == "ROJO"   # prevalece el sistema
    assert res["C1"].decision_gerencia is not None and not res["C1"].decision_aplicada                                       # decision solo trazabilidad
    assert ps.evaluar_ruta(["C1"], fuente, 60)["C1"].color_final == "ROJO"          # sin decision: el contado es rojo automaticamente
    assert res["C2"].final == "A" and res["C3"].final == ps.EN_PRUEBA


def test_evidencia_por_factura_expone_recibos_y_motivo():
    r = ev(serie([3, 3, 3, 3]))
    f = r.facturas[0].to_dict()
    assert f["recibos"] and f["estado"] == "PAGADA" and f["motivo"] and f["vencimiento_ruta"] and f["vencimiento_sap"]


def test_motor_antiguo_esta_deprecado_y_el_unico_vigente_es_payment_semaphore():
    from cxc_bot import payment_behavior as pb
    with pytest.warns(DeprecationWarning, match="payment_semaphore"):
        pb.evaluate_client("X", [], CORTE)
