"""Regresion contra los datos PRIVADOS (consultas SAP 08/10/2026 y Excel aprobados por gerencia).

Se omite si CXC_DATOS_DIR no apunta al directorio privado (los datos nunca viven en el repositorio). Comprueba que el
motor Python, alimentado SOLO con las consultas SAP, reproduce el resumen y el detalle por factura que gerencia aprobo.
"""

import os
from pathlib import Path

import openpyxl
import pytest

from cxc_bot import payment_semaphore as ps
from cxc_portal.store import RUTAS_APROBADAS, RouteStore

DATOS = os.environ.get("CXC_DATOS_DIR", "")
pytestmark = pytest.mark.skipif(not DATOS or not (Path(DATOS) / "02_SAP_08Oct2026").is_dir(), reason="CXC_DATOS_DIR no configurado")


@pytest.fixture(scope="module")
def store():
    return RouteStore(data_dir=Path(DATOS), include_demo=False)


def test_las_dos_rutas_cargan_con_el_orden_y_tamano_confirmados(store):
    lv, yo = store.ruta("LAS_VEGAS"), store.ruta("YORO")
    assert len(lv.clientes) == 24 and len(yo.clientes) == 31
    assert [c.folder for c in lv.clientes][:3] == ["1 LV", "2 LV", "3 LV"]
    pref = [c.zona for c in yo.clientes]
    assert [pref.count(p) for p in ("SY", "MY", "PY", "YT", "YO")] == [7, 7, 1, 3, 13]
    codigos = {c.codigo for c in yo.clientes}
    assert "C10617" in codigos and "C10924" not in codigos  # IARA'S BEAUTY SALON es C10617


def test_el_motor_reproduce_el_excel_aprobado_sin_diferencias(store):
    for r in store.rutas.values():
        assert r.avisos == [], r.avisos
        assert all(c.conciliacion["coincide"] for c in r.clientes)


def test_distribucion_final_aprobada(store):
    lv, yo = store.ruta("LAS_VEGAS").resumen()["clasificacion"], store.ruta("YORO").resumen()["clasificacion"]
    # los 2 candidatos de Las Vegas fueron aprobados despues por gerencia como solo contado (registro aparte; Excel intacto)
    assert lv == {"A": 6, "B": 3, "C": 4, "EN_PRUEBA": 9, ps.SOLO_CONTADO: 2}
    assert yo == {"A": 11, "B": 8, "C": 9, ps.SOLO_CONTADO: 2, "EN_PRUEBA": 1}


def test_overrides_de_gerencia_conservan_el_calculo_automatico(store):
    ov = [c.resultado for r in store.rutas.values() for c in r.clientes if c.resultado.decision_gerencia]
    assert len(ov) == 7  # Las Vegas: 1 del Excel + 2 aprobados despues; Yoro: 4
    for r in ov:
        assert r.calculado == ps.CANDIDATO_SOLO_CONTADO and r.promedio_mora > ps.LIMITE_C
        assert r.final in (ps.C, ps.SOLO_CONTADO) and r.decision_gerencia.actor == "Gerencia" and r.decision_gerencia.motivo
    assert sum(1 for r in ov if r.final == ps.SOLO_CONTADO) == 4


def test_las_vegas_solo_contado_posterior_no_oculta_la_discrepancia_con_el_excel_original(store):
    lv = store.ruta("LAS_VEGAS")
    por = {c.folder: c for c in lv.clientes}
    for folder in ("3 LV", "6 LV"):
        c = por[folder]
        assert c.resultado.calculado == ps.CANDIDATO_SOLO_CONTADO and c.resultado.final == ps.SOLO_CONTADO
        assert c.resultado.color_final == "ROJO" and c.resultado.decision_gerencia.actor == "Gerencia"
        assert c.conciliacion["coincide"] and c.conciliacion["explicadas_por_decision_posterior"]
    assert lv.avisos == []


def test_alertas_abiertas_y_pagadas_se_separan_en_el_portal(store):
    for r in store.rutas.values():
        for c in r.clientes:
            assert all(a.tipo == ps.ALERTA_COBRO and a.saldo_pendiente is not None for a in c.resultado.alertas_cobro)
            assert all(a.tipo == ps.ALERTA_HISTORICA and a.saldo_pendiente is None for a in c.resultado.alertas_historicas)
            abiertas = {f.numero for f in c.resultado.facturas if f.estado == ps.ABIERTA}
            assert {a.numero for a in c.resultado.alertas_cobro} <= abiertas
            assert not ({a.numero for a in c.resultado.alertas_historicas} & abiertas)


def test_saldos_son_facturas_abiertas_de_la_consulta_1_y_coinciden_con_la_lista_de_ruta(store):
    for cfg in RUTAS_APROBADAS:
        wb = openpyxl.load_workbook(Path(DATOS) / "01_Rutas" / cfg["archivo"].replace("Final_Aprobado_Gerencia_09Oct2026", "Orden_Por_Zonas_Con_Leyenda")
                                    if cfg["route_id"] == "LAS_VEGAS" else Path(DATOS) / "01_Rutas" / "Yoro_Orden_Zonas_Corregido_Con_Leyenda.xlsx",
                                    data_only=True, read_only=True)
        ws = wb[wb.sheetnames[0]]
        lista = 0.0
        for r in ws.iter_rows(values_only=True):
            if r and isinstance(r[1], str) and r[1].strip().upper().startswith("C") and r[1].strip()[1:].isdigit():
                lista += float(r[5] if cfg["route_id"] == "YORO" else r[4] or 0)
        assert round(lista, 2) == store.ruta(cfg["route_id"]).resumen()["saldo_total"], cfg["route_id"]


def test_detalle_por_factura_de_las_vegas_coincide_con_el_excel_aprobado(store):
    wb = openpyxl.load_workbook(Path(DATOS) / "01_Rutas" / RUTAS_APROBADAS[0]["archivo"], data_only=True)
    ws = wb[wb.sheetnames[1]]
    filas = list(ws.iter_rows(values_only=True))
    ini = next(i for i, r in enumerate(filas) if r and str(r[0]).strip() == "Folder" and r[1] and "factura" in str(r[1]).lower())
    esperado = {}
    for r in filas[ini + 1:]:
        if r[0] and r[1]:
            esperado[(str(r[0]).strip(), str(r[1]).strip())] = (r[5], r[10], r[11])   # estado, mora, en promedio
    assert len(esperado) >= 130
    visto = 0
    for c in store.ruta("LAS_VEGAS").clientes:
        for f in c.resultado.facturas:
            est, mora, en_prom = esperado[(c.folder, f.numero)]
            visto += 1
            assert (f.estado == ps.PAGADA) == (str(est).upper() == "PAGADA"), (c.folder, f.numero)
            assert (f.estado == ps.ABIERTA) == (str(est).upper() == "ABIERTA"), (c.folder, f.numero)
            assert f.en_media == (str(en_prom).strip().lower() in ("sí", "si")), (c.folder, f.numero)
            if f.en_media:
                assert f.mora == int(float(mora)), (c.folder, f.numero)
    assert visto == len(esperado)


def test_el_generador_de_rutas_y_el_portal_dan_la_misma_clasificacion_con_el_mismo_motor(store):
    """CXC BOT (runner) y el portal usan payment_semaphore: mismo resultado, sin reglas duplicadas."""
    from cxc_bot import runner
    from cxc_bot.config import load_route_config
    from cxc_bot.models import ClientEntry
    from cxc_bot.sap_exports import SapExportEvidenceSource, cargar_decisiones

    fuente = SapExportEvidenceSource(Path(DATOS))
    decisiones = cargar_decisiones(Path(DATOS))
    for route_id, nombre in (("LAS_VEGAS", "LAS VEGAS"), ("YORO", "YORO")):
        ruta = store.ruta(route_id)
        roster = [ClientEntry(codigo=c.codigo) for c in ruta.clientes]
        resumen, error = runner._evaluate_payment_semaphore(load_route_config(nombre), roster, fuente, decisiones)
        assert error is None and resumen["plazo_dias"] == 60 and resumen["fecha_corte"] == "2026-10-08"
        for fila, c in zip(resumen["clientes"], ruta.clientes):
            assert (fila["codigo"], fila["calculado"]) == (c.codigo, c.resultado.calculado)
            assert fila["promedio_mora"] == c.resultado.to_dict()["promedio_mora"]
            assert fila["alertas_cobro"] == len(c.resultado.alertas_cobro) and fila["alertas_historicas"] == len(c.resultado.alertas_historicas)
            if c.conciliacion["explicadas_por_decision_posterior"] or not c.resultado.decision_gerencia:
                assert fila["final"] == c.resultado.final  # decisiones posteriores (archivo) y sin decision: identico
