"""Fase 1 (Issue #2): datos simulados, antiguos, incompletos, degradados o no validados NUNCA producen viñetas definitivas ni permiten publicar.
`TransporteVivoDoble` es un DOBLE DE PRUEBA que se declara no simulado para ejercitar el camino de una fuente real validada; no es una fuente real."""

from datetime import date, timedelta

import pytest

from cxc_bot import route_markers as rm
from cxc_portal import fuente_empresarial as fe
from cxc_portal.demo import FuenteSintetica
from cxc_portal.flujo import FlujoOperativo
from cxc_portal.store import PortalServicio
from tests.portal_helpers import ACTOR, crear_entorno

CORTE = FuenteSintetica.fecha_corte             # 2026-10-08
RELOJ = {"d": CORTE + timedelta(days=1)}


class TransporteVivoDoble(fe.TransporteSimulado):
    simulado = False


def _vivo(**fallas):
    RELOJ["d"] = CORTE + timedelta(days=1)
    t = TransporteVivoDoble(FuenteSintetica(), hoy=lambda: RELOJ["d"], fallas=fallas)
    return fe.FuenteEmpresarial(t, hoy=lambda: RELOJ["d"], max_edad_dias=2), t


def _estados(servicio):
    return {c["semaforo"]["vineta"]["estado"] for c in (servicio.ruta("DEMO").cliente_dict(c) for c in servicio.ruta("DEMO").clientes)}


def _servicio(tmp_path, fuente):
    ctx = crear_entorno(tmp_path)
    ctx.servicio.fuente = fuente
    ctx.servicio.invalidar()
    return ctx


def _publicable(ctx, fuente):
    ctx.flujo = FlujoOperativo(ctx.servicio)
    return ctx.flujo.evaluar_publicacion({"reporte_excepciones": {}})["publicable"]


# ---------------------------------------------------------------- control positivo: la fuente real validada y fresca SI es definitiva
def test_control_positivo_fuente_validada_y_vigente_es_definitiva_y_publicable(tmp_path):
    f, _ = _vivo()
    f.recargar()
    ctx = _servicio(tmp_path, f)
    assert f.apta_para_publicar and rm.DEFINITIVA in _estados(ctx.servicio) and _publicable(ctx, f)


# ---------------------------------------------------------------- las fuentes que NO deben producir definitivas
def test_fuente_simulada_nunca_es_definitiva_ni_publicable(tmp_path):
    t = fe.TransporteSimulado(FuenteSintetica(), hoy=lambda: CORTE + timedelta(days=1))
    f = fe.FuenteEmpresarial(t, hoy=lambda: CORTE + timedelta(days=1))
    f.recargar()
    ctx = _servicio(tmp_path, f)
    assert not f.apta_para_publicar and rm.DEFINITIVA not in _estados(ctx.servicio) and not _publicable(ctx, f)


def test_snapshot_o_fuente_sintetica_nunca_es_definitiva_ni_publicable(tmp_path):
    ctx = crear_entorno(tmp_path)                        # FuenteSintetica: sin el atributo apta_para_publicar
    assert rm.DEFINITIVA not in _estados(ctx.servicio) and not _publicable(ctx, ctx.servicio.fuente)


def test_sin_fuente_no_hay_color_ni_se_infiere_nada(tmp_path):
    ctx = crear_entorno(tmp_path)
    ctx.servicio.fuente = None
    ctx.servicio.invalidar()
    assert _estados(ctx.servicio) == {rm.SIN_DATOS}


def test_datos_que_envejecen_dejan_de_ser_definitivos_sin_reiniciar_ni_recargar(tmp_path):
    f, _ = _vivo()
    f.recargar()
    ctx = _servicio(tmp_path, f)
    assert rm.DEFINITIVA in _estados(ctx.servicio) and f.apta_para_publicar
    RELOJ["d"] = CORTE + timedelta(days=5)               # pasan los dias: el corte ya excede el maximo aceptado (2) y nadie recargo
    assert f.apta_para_publicar is False
    assert rm.DEFINITIVA not in _estados(ctx.servicio) and not _publicable(ctx, f)


def test_carga_incompleta_posterior_no_deja_viñetas_definitivas(tmp_path):
    f, t = _vivo()
    f.recargar()
    ctx = _servicio(tmp_path, f)
    t.fallas["incompleto"] = True
    assert f.recargar()["estado"] == "BLOQUEADA"          # la carga mala no reemplaza a la buena, pero la fuente deja de ser apta
    assert not f.apta_para_publicar and rm.DEFINITIVA not in _estados(ctx.servicio) and not _publicable(ctx, f)


def test_perdida_de_conexion_degrada_la_fuente_y_quita_lo_definitivo(tmp_path):
    f, t = _vivo()
    f.recargar()
    ctx = _servicio(tmp_path, f)
    t.fallas["conexion_falla_veces"] = 10**6
    assert f.recargar()["estado"] == "DEGRADADA"
    assert not f.apta_para_publicar and rm.DEFINITIVA not in _estados(ctx.servicio) and not _publicable(ctx, f)


def test_datos_adelantados_al_reloj_tampoco_son_aptos(tmp_path):
    f, _ = _vivo()
    f.recargar()
    RELOJ["d"] = CORTE - timedelta(days=3)                # corte "en el futuro" respecto del reloj: no es un dato vigente
    assert f.apta_para_publicar is False


def test_carga_inicial_incompleta_deja_la_fuente_sin_datos():
    t = TransporteVivoDoble(FuenteSintetica(), hoy=lambda: CORTE, fallas={"incompleto": True})
    f = fe.FuenteEmpresarial(t, hoy=lambda: CORTE)
    with pytest.raises(fe.ContratoIncumplido):
        f.recargar()
    assert not f.apta_para_publicar


# ---------------------------------------------------------------- cliente ausente de SAP: bloquea la publicacion aunque la fuente sea apta y la plantilla use IFERROR
def test_cliente_ausente_de_sap_bloquea_la_publicacion_con_fuente_apta_e_iferror(tmp_path):
    import openpyxl
    from cxc_portal.estado import ErrorNegocio
    from tests.portal_helpers import FuenteApta
    ctx = crear_entorno(tmp_path)
    ctx.servicio.fuente = FuenteApta()
    wb = openpyxl.load_workbook("tests/fixtures/rutas_reales/YOROABRIL.xlsx")
    wb["Hoja1"].title, wb["Hoja2"].title = "YORO", "Hoja1"
    (tmp_path / "datos" / "01_Rutas").mkdir(parents=True)
    wb.save(tmp_path / "datos" / "01_Rutas" / "base.xlsx")
    ctx.estado.guardar_ruta(ACTOR, "YORO_T", "Yoro prueba", "Vendedor Demo", "FORANEA", 60, zonas={"SY": "Sy"}, plantilla_excel="YORO FOLDERS",
                            clientes=[{"codigo": "C90001", "folder": "1 SY", "nombre": "SALON DEMO EXCELENTE (A)"},
                                      {"codigo": "C90777", "folder": "2 SY", "nombre": "CLIENTE FUERA DE SAP"},
                                      {"codigo": "C90002", "folder": "3 SY", "nombre": "SALON DEMO BUENO (B)"}])
    ctx.servicio.invalidar()
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: date(2026, 11, 6))
    sid = ctx.estado.crear_salida(ACTOR, "YORO_T", "2026-11-10")["id"]
    with pytest.raises(ErrorNegocio) as ex:
        ctx.flujo.generar_excel_salida(ACTOR, sid, "base.xlsx")
    assert "C90777" in str(ex.value) and "REVISAR CÓDIGO SAP" in str(ex.value)
    assert ctx.estado.listar_excel("YORO_T") == []                                           # ni borrador ni publicado: no se registro nada
    assert not list(ctx.estado.carpeta_excel.rglob("*.xlsx"))                                 # y no quedo ningun archivo en Actual/Borrador/Historico
