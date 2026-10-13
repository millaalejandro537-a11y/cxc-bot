"""Auditoria de los 5 clientes de Occidente con diferencias entre el semaforo vigente (documento) y el motor.

Dos partes:
- FIXTURES SINTETICOS (siempre corren): reproducen la estructura de cada hallazgo con datos inventados; no hay cifras reales en el repositorio.
- Contra el snapshot SAP historico del 08/10/2026 (privado): se omite sin CXC_DATOS_DIR y solo comprueba invariantes, sin fijar importes ni facturas.

Hallazgos (ver docs/MIGRACION-OCCIDENTE-119.md, seccion 5):
- C10270 / C10298 / C10502: el documento solo conto recibos de aplicacion directa (consulta 3); el motor cuenta ademas los recibos que SAP
  concilio contra la factura (consulta 2). Con ellos la suma de recibos = ImporteAplicado de SAP al centavo.
- C10266: la media del documento usa el vencimiento de SAP como base del plazo (regla descartada); con emision + 60 es otra.
- C10061: facturas pagadas con ANTICIPOS (recibos anteriores a la emision, conciliados despues) y una cerrada con recibo + nota de credito.
"""

import dataclasses
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from cxc_bot import payment_semaphore as ps
from cxc_bot.sap_exports import SapExportEvidenceSource

CORTE = date(2026, 10, 8)
E = date(2024, 1, 10)


def _fac(n, emision, total, recibos, nc=0.0, venc_sap=None):
    return ps.FacturaSAP(str(n), str(n), emision, venc_sap, total, total, "C", tuple(recibos), notas_credito=nc)


def _pagada(n, i, mora=0):
    em = date(2023, 1, 1) + timedelta(days=30 * i)
    return _fac(n, em, 1000.0, [ps.Recibo(f"P{n}", em + timedelta(days=60 + mora), 1000.0, "consulta 3")])


# ----------------------------------------------------------------------------- sinteticos
def test_sintetico_recibo_conciliado_de_consulta_2_completa_el_pago_y_cuadra_con_sap():
    directos = [ps.Recibo("D1", E + timedelta(days=50), 600.0, "consulta 3"), ps.Recibo("D2", E + timedelta(days=130), 300.0, "consulta 3")]
    conciliado = ps.Recibo("C1", E + timedelta(days=70), 100.0, "consulta 2", E + timedelta(days=95))     # pago el dia 70, conciliado el 95
    f = _fac(900, E, 1000.0, directos + [conciliado])
    base = [_pagada(i, i) for i in range(1, 4)]
    con = ps.evaluar_cliente("C1", base + [f], CORTE, 60)
    ef = next(x for x in con.facturas if x.numero == "900")
    assert ef.estado == ps.PAGADA and ef.recibos_total == pytest.approx(ef.aplicado_sap) == pytest.approx(ef.total) and ef.mora == 70 and con.evaluables == 4
    solo_directos = ps.evaluar_cliente("C1", base + [dataclasses.replace(f, recibos=tuple(directos))], CORTE, 60)
    assert solo_directos.evaluables == 3 and next(x for x in solo_directos.facturas if x.numero == "900").estado == ps.CERRADA_NO_ACREDITADA
    assert solo_directos.promedio_mora == 0.0 and con.promedio_mora == pytest.approx(17.5)               # el criterio "solo directos" subestima la mora


def test_sintetico_el_vencimiento_sap_como_base_del_plazo_da_otra_media():
    em = date(2025, 1, 1)
    f = [_fac(i, em + timedelta(days=40 * i), 500.0, [ps.Recibo(f"R{i}", em + timedelta(days=40 * i + 80), 500.0, "consulta 3")],
              venc_sap=em + timedelta(days=40 * i + 52)) for i in range(5)]
    r = ps.evaluar_cliente("C1", f, CORTE, 60)
    assert r.promedio_mora == 20.0                                                                           # emision + 60 (regla aprobada)
    con_sap = sum((max(x.recibos).fecha - x.vencimiento_sap).days for x in f) / len(f)
    assert con_sap == 28.0 and con_sap != r.promedio_mora                                                    # DocDueDate: regla descartada


def _caso_anticipos():
    base = [_pagada(i, i) for i in range(1, 2)]
    em = date(2023, 6, 1)
    a = _fac(10, em, 800.0, [ps.Recibo("A", em - timedelta(days=30), 800.0, "consulta 2", em + timedelta(days=150))])           # anticipo
    b = _fac(11, em + timedelta(days=5), 900.0, [ps.Recibo("B1", em - timedelta(days=20), 700.0, "consulta 2", em + timedelta(days=60)),
                                                  ps.Recibo("B2", em + timedelta(days=40), 200.0, "consulta 3")])                 # anticipo + saldo
    c = _fac(12, em + timedelta(days=10), 600.0, [ps.Recibo("C", em + timedelta(days=45), 200.0, "consulta 3")], nc=400.0)         # recibo + nota de credito
    return base + [a, b, c]


def test_sintetico_anticipos_y_nota_de_credito_sin_forzar_categoria():
    r = ps.evaluar_cliente("C1", _caso_anticipos(), CORTE, 60)
    f = {x.numero: x for x in r.facturas}
    assert r.historial_total == 4 and r.evaluables == 1 and r.calculado == ps.A
    for n in ("10", "11"):
        assert f[n].estado == ps.CERRADA_NO_ACREDITADA and ps.EXC_RECIBO_ANTERIOR_A_EMISION in f[n].excepciones
        assert f[n].recibos_total + f[n].recibos_anteriores == pytest.approx(f[n].total) == pytest.approx(f[n].aplicado_sap)
    assert f["12"].notas_credito == 400.0 and f["12"].recibos_total + f["12"].notas_credito == pytest.approx(f["12"].total) and "no es un pago" in f["12"].motivo


def test_sintetico_con_el_interruptor_los_anticipos_cuentan_sin_mora_y_la_conciliacion_no_genera_atraso():
    cuatro_sin_pago_acreditado = _caso_anticipos()[1:] + [_fac(13, date(2023, 9, 1), 100.0, [ps.Recibo("Z", date(2023, 8, 1), 100.0, "consulta 2", date(2024, 5, 1))])]
    r0 = ps.evaluar_cliente("C1", cuatro_sin_pago_acreditado, CORTE, 60)
    assert r0.historial_total == 4 and r0.evaluables == 0 and r0.calculado == ps.REVISION_HUMANA
    r1 = ps.evaluar_cliente("C1", cuatro_sin_pago_acreditado, CORTE, 60, anticipos_respaldan_pago=True)
    pagadas = [x for x in r1.facturas if x.estado == ps.PAGADA]
    assert len(pagadas) == 3 and all(x.mora == 0 for x in pagadas)                                           # pago anterior al vencimiento aunque se concilie despues
    assert r1.final == ps.A and r1.promedio_mora == 0.0


def test_sintetico_cliente_con_cuatro_facturas_cerradas_solo_con_anticipos_y_nota_de_credito():
    em = date(2022, 11, 1)
    fs = [_fac(1, em, 500.0, [ps.Recibo("A", em - timedelta(days=30), 500.0, "consulta 2", em + timedelta(days=70))]),
          _fac(2, em + timedelta(days=100), 300.0, [ps.Recibo("B", em + timedelta(days=95), 300.0, "consulta 2", em + timedelta(days=300))]),
          _fac(3, em + timedelta(days=200), 700.0, [ps.Recibo("C1", em + timedelta(days=180), 300.0, "consulta 2"), ps.Recibo("C2", em + timedelta(days=230), 400.0, "consulta 3")]),
          _fac(4, em + timedelta(days=300), 400.0, [ps.Recibo("D", em + timedelta(days=330), 100.0, "consulta 3")], nc=300.0)]
    r = ps.evaluar_cliente("C1", fs, CORTE, 60)
    assert r.historial_total == 4 and r.evaluables == 0 and r.calculado == ps.REVISION_HUMANA and r.requiere_gerencia       # sin evidencia: revision humana
    r2 = ps.evaluar_cliente("C1", fs, CORTE, 60, anticipos_respaldan_pago=True)
    assert r2.evaluables == 3 and r2.pagadas == 3 and r2.no_acreditadas == 1 and r2.promedio_mora == 0.0 and r2.final == ps.A


# ----------------------------------------------------------------------------- snapshot privado (invariantes, sin fijar importes)
DATOS = os.environ.get("CXC_DATOS_DIR", "")
privado = pytest.mark.skipif(not DATOS or not (Path(DATOS) / "02_SAP_08Oct2026").is_dir(), reason="CXC_DATOS_DIR no configurado")


@pytest.fixture(scope="module")
def fuente():
    return SapExportEvidenceSource(Path(DATOS))


def _ev(fuente, cod, **kw):
    return ps.evaluar_cliente(cod, fuente.datos().facturas[cod], CORTE, 60, **kw)


def _solo_directos(fuente, cod):
    fs = [dataclasses.replace(f, recibos=tuple(r for r in f.recibos if r.via == "consulta 3")) for f in fuente.datos().facturas[cod]]
    return ps.evaluar_cliente(cod, fs, CORTE, 60)


@privado
@pytest.mark.parametrize("cod,clase", [("C10270", ps.C), ("C10298", ps.C), ("C10502", ps.B)])
def test_privado_la_diferencia_de_media_es_el_criterio_de_recibos_directos_del_documento(fuente, cod, clase):
    r, d = _ev(fuente, cod), _solo_directos(fuente, cod)
    assert r.final == d.final == clase and r.promedio_mora != d.promedio_mora and r.evaluables == d.evaluables + 1
    extra = [f for f in r.facturas if f.estado == ps.PAGADA and f.recibos_total > sum(x.monto for x in f.recibos if x.via == "consulta 3")]
    assert len(extra) == 1
    assert extra[0].recibos_total == pytest.approx(extra[0].aplicado_sap) and extra[0].recibos_total == pytest.approx(extra[0].total)   # SAP respalda al motor


@privado
def test_privado_c10266_la_media_del_documento_usa_el_vencimiento_sap(fuente):
    r = _ev(fuente, "C10266")
    assert r.final == ps.C and r.evaluables == 9
    pagadas = [f for f in sorted(fuente.datos().facturas["C10266"], key=lambda x: (x.fecha_factura, x.numero), reverse=True)[:10] if f.estado_sap == "C"]
    con_sap = sum(max(0, (max(x.fecha for x in f.recibos) - f.vencimiento_sap).days) for f in pagadas) / len(pagadas)
    assert con_sap > r.promedio_mora and ps.clasificar_media(con_sap) == r.final                          # otra media, misma clase


@privado
def test_privado_c10061_anticipos_y_nota_de_credito(fuente):
    r = _ev(fuente, "C10061")
    assert r.historial_total == 4 and r.evaluables == 0 and r.calculado == ps.REVISION_HUMANA            # regla aprobada vigente
    con_anticipo = [x for x in r.facturas if x.recibos_anteriores > 0]
    con_nc = [x for x in r.facturas if x.notas_credito > 0]
    assert len(con_anticipo) == 3 and len(con_nc) == 1
    for x in con_anticipo:
        assert ps.EXC_RECIBO_ANTERIOR_A_EMISION in x.excepciones
        assert x.recibos_total + x.recibos_anteriores == pytest.approx(x.total) == pytest.approx(x.aplicado_sap)
    assert con_nc[0].recibos_total + con_nc[0].notas_credito == pytest.approx(con_nc[0].total) and "no es un pago" in con_nc[0].motivo
    # fecha de pago != fecha de conciliacion: al menos un anticipo se concilio despues de la emision
    crudas = [rc for f in fuente.datos().facturas["C10061"] for rc in f.recibos if rc.conciliacion and rc.fecha < f.fecha_factura]
    assert crudas and all(rc.conciliacion > rc.fecha for rc in crudas)
    r2 = _ev(fuente, "C10061", anticipos_respaldan_pago=True)
    assert r2.evaluables == 3 and r2.promedio_mora == 0.0 and r2.final == ps.A
