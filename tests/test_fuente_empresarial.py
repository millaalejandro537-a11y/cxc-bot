"""Adaptador de lectura empresarial con TRANSPORTE SIMULADO: equivalencia con la fuente sintetica, datos incompletos/desactualizados/duplicados,
errores de conexion y recuperacion. Todo sintetico; nada representa saldos reales."""

from datetime import date

import pytest

from cxc_portal import fuente_empresarial as fe
from cxc_portal import validar_fuente as vf
from cxc_portal.demo import FuenteSintetica
from cxc_portal.estado import Estado
from cxc_portal.fuentes import es_operable
from cxc_portal.store import PortalServicio
from tests.portal_helpers import ACTOR, crear_entorno

HOY = date(2026, 10, 9)   # un dia despues del corte sintetico (2026-10-08)


def _fuente(fallas=None, **kw):
    t = fe.TransporteSimulado(FuenteSintetica(), hoy=lambda: HOY, fallas=fallas)
    return fe.FuenteEmpresarial(t, hoy=lambda: HOY, **kw), t


def test_la_fuente_simulada_nunca_se_declara_real_ni_publicable():
    f, _ = _fuente()
    f.recargar()
    assert f.tipo == fe.TIPO_SIMULADA and f.en_vivo is False and f.apta_para_publicar is False and es_operable(f)
    assert "SIMULADA" in f.etiqueta and "no representa saldos reales" in f.etiqueta
    assert f.frescura()["vigente"] is False and "SIMULADA" in f.frescura()["motivo"]


def test_equivale_a_la_fuente_sintetica_en_clasificacion_saldos_y_documentos(tmp_path):
    ref = crear_entorno(tmp_path / "ref")
    f, _ = _fuente()
    f.recargar()
    sv = PortalServicio(ref.estado, f, tmp_path / "ref" / "estado", None, None)
    a = {c.codigo: (c.resultado.calculado, c.resultado.final, c.resultado.promedio_mora, c.saldo) for c in ref.servicio.ruta("DEMO").clientes}
    b = {c.codigo: (c.resultado.calculado, c.resultado.final, c.resultado.promedio_mora, c.saldo) for c in sv.ruta("DEMO").clientes}
    assert a == b                                                                 # mismo motor, mismos resultados por cliente
    assert sorted(map(str, ref.servicio.fuente.documentos_abiertos())) == sorted(map(str, f.documentos_abiertos()))
    assert f.calidad["estado"] == "OK" and sv.info_fuente()["tipo"] == fe.TIPO_SIMULADA and sv.info_fuente()["en_vivo"] is False


def test_solo_expone_lectura_y_pasa_la_aceptacion_salvo_ser_real():
    f, _ = _fuente()
    f.recargar()
    r = vf.validar_fuente(f, hoy=HOY, max_edad_dias=2)
    fallas = [c["id"] for c in r["checks"] if c["estado"] == vf.FALLA]
    assert fallas == ["frescura.en_vivo"]                                          # honesto: la unica falla es que NO es dato en vivo
    assert not [n for n in dir(f) if not n.startswith("_") and vf.PROHIBIDOS.match(n)]


def test_datos_desactualizados_se_marcan_pero_no_se_presentan_como_vivos():
    f, _ = _fuente({"desactualizado_dias": 10})
    f.recargar()
    assert f.calidad["vigente"] is False and f.calidad["edad_dias"] == 10 and f.frescura()["vigente"] is False and f.apta_para_publicar is False
    assert any("NO es apta para publicar" in p for p in f.calidad["problemas"]) and f.nombre_cliente("C90001")


def test_carga_incompleta_o_version_de_contrato_incompatible_se_rechaza():
    with pytest.raises(fe.ContratoIncumplido, match="INCOMPLETA"):
        _fuente({"incompleto": True})[0].recargar()
    with pytest.raises(fe.ContratoIncumplido, match="incompatible"):
        _fuente({"version_contrato": "2.0"})[0].recargar()


def test_una_carga_mala_no_reemplaza_a_la_ultima_buena():
    f, t = _fuente()
    f.recargar()
    antes = f.saldo_abierto("C90007")
    t.fallas["incompleto"] = True
    cal = f.recargar()
    assert cal["estado"] == "BLOQUEADA" and cal["conserva_carga_previa"] and f.saldo_abierto("C90007") == antes and f.apta_para_publicar is False


def test_duplicados_identicos_se_ignoran_y_conflictivos_bloquean():
    f, _ = _fuente({"duplicar_identicas": True})
    f.recargar()
    assert any("duplicada idéntica" in p for p in f.calidad["problemas"]) and len(f.documentos_abiertos()) == 3
    with pytest.raises(fe.DatosBloqueados, match="contenido distinto"):
        _fuente({"duplicar_conflicto": True})[0].recargar()


def test_campos_faltantes_importes_incoherentes_y_fechas_futuras_se_apartan_y_reportan():
    with pytest.raises(fe.DatosBloqueados, match="Demasiadas filas inválidas"):
        _fuente({"quitar_campo": ("facturas", "fecha_factura")})[0].recargar()          # mitad de las facturas sin fecha: se rechaza la carga
    f, _ = _fuente({"importes_incoherentes": True, "factura_posterior_a_corte": True}, tolerancia_invalidos=0.2)
    f.recargar()
    motivos = " | ".join(d[2] for d in f.calidad["filas_descartadas"])
    assert "importes incoherentes" in motivos and "posterior a la fecha de corte" in motivos and f.calidad["estado"] == "CON_ADVERTENCIAS"


def test_saldo_del_origen_prevalece_y_la_diferencia_se_reporta_sin_corregir():
    f, _ = _fuente({"saldo_discrepante": True})
    f.recargar()
    d = f.calidad["diferencias_saldo"]
    assert len(d) == 1 and d[0]["saldo_origen"] - d[0]["suma_facturas_abiertas"] == pytest.approx(500)
    assert f.saldo_abierto(d[0]["codigo"]) == d[0]["saldo_origen"]                     # NO se recalcula con otra formula


def test_recibo_a_factura_inexistente_se_reporta():
    f, _ = _fuente({"recibo_a_factura_inexistente": True})
    f.recargar()
    assert any("factura inexistente" in p for p in f.calidad["problemas"])


# ------------------------------------------------------------------ errores de conexion
def test_reintentos_recuperan_fallas_transitorias():
    pausas = []
    f, t = _fuente({"conexion_falla_veces": 2}, reintentos=3, pausa=pausas.append)
    f.recargar()
    assert f.calidad["estado"] == "OK" and pausas == [1, 2]                              # espera creciente entre intentos


def test_sin_conexion_y_sin_carga_previa_no_inventa_datos():
    f, _ = _fuente({"conexion_falla_veces": 99}, reintentos=1)
    with pytest.raises(fe.FuenteNoDisponible):
        f.recargar()
    assert f.calidad["estado"] == "SIN_CONEXION"
    with pytest.raises(fe.FuenteNoDisponible):
        f.saldo_abierto("C90001")


def test_si_la_conexion_cae_se_conserva_la_ultima_carga_como_degradada():
    f, t = _fuente()
    f.recargar()
    ok = f.ultima_carga_ok
    t.llamadas, t.fallas["conexion_falla_veces"] = 0, 99
    cal = f.recargar()
    assert cal["estado"] == "DEGRADADA" and f.ultima_carga_ok == ok and f.saldo_abierto("C90007") > 0 and f.apta_para_publicar is False
    assert "Sin conexión" in " ".join(cal["problemas"]) and f.ultimo_error_conexion


# ------------------------------------------------------------------ el portal completo sobre la fuente empresarial simulada
def test_el_flujo_de_salida_funciona_igual_sobre_la_fuente_empresarial_simulada(tmp_path):
    from cxc_portal.flujo import FlujoOperativo
    ctx = crear_entorno(tmp_path)
    f, _ = _fuente()
    f.recargar()
    ctx.servicio.fuente = f
    ctx.servicio.invalidar()
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: date(2026, 10, 12))
    ctx.flujo.programar_salidas(ACTOR, "DEMO", ["2026-10-19"])
    r = ctx.flujo.ciclo_diario(hoy="2026-10-15")
    assert len(r["preparadas"]) == 1
    tipos = {a["tipo"] for a in ctx.estado.listar_alertas(["DEMO"])}
    assert {"CANDIDATO_PENDIENTE_GERENCIA", "DATOS_NO_VIVOS"} <= tipos
    from cxc_portal import dashboard as dash
    d = dash.construir(ctx.servicio, ctx.estado, ctx.servicio.rutas(), None)
    assert d["fuente"]["tipo"] == fe.TIPO_SIMULADA and d["fuente"]["en_vivo"] is False and d["top_saldos"]
