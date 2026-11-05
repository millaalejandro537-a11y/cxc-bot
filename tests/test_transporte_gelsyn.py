"""Mapeo `GET /finanzas/*` (Gelsyn) -> contrato del Portal con RESPUESTAS SINTETICAS: nada de red, credenciales ni datos reales. Los nombres de campo son los
de la referencia tecnica de Gelsyn (columnas de la BD); la forma exacta de `data` se confirma con el preflight cuando exista el backend de pruebas."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from cxc_portal import fuente_empresarial as fe
from cxc_portal import gelsyn_config as gc
from cxc_portal import transporte_gelsyn as tg
from tests.portal_helpers import ruta_reporting_core

CLAVE = "clave-de-prueba-INVENTADA-0123456789"
AHORA = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
CARGA = "2026-11-04T03:00:00Z"                       # 03:00Z = 21:00 del 3/11 en Honduras -> corte comercial 2026-11-03


# ------------------------------------------------------------------------------------------------ conversiones puras
@pytest.mark.parametrize("v,esperado", [
    ("583.69", "583.69"), (583.6900000000001, "583.69"), (71.56999999999999, "71.57"), (8682903.859999999, "8682903.86"), ("0", "0"), (0, "0"), ("-12.5000", "-12.5"),
    ("1e2", "100"), (Decimal("10.250000"), "10.25"),
    ("10.1234567", None), (None, None), ("", None), (True, None), ("abc", None), ("NaN", None), ("Infinity", None),
])
def test_importes_exactos_sin_float_y_sin_redondeo_libre(v, esperado):
    assert tg.importe(v) == esperado


@pytest.mark.parametrize("v,esperado", [
    ("2026-10-01", "2026-10-01"), ("2026-10-01T00:00:00", "2026-10-01"), ("2026-10-01 00:00:00", "2026-10-01"),
    ("2026-10-02T05:00:00Z", "2026-10-01"), ("2026-10-02T06:00:00+00:00", "2026-10-02"),          # UTC-6
    ("2026-10-01T13:45:00", None), (None, None), ("", None), ("no es fecha", None), (datetime(2026, 10, 1), "2026-10-01"),
])
def test_fechas_comerciales_no_se_adivinan(v, esperado):
    assert tg.fecha_comercial(v) == esperado


@pytest.mark.parametrize("fila,esperado", [
    ({"cancelled": "tNO"}, False), ({"cancelled": "tYES"}, True), ({"cancelled": False}, False), ({"cancelled": True}, True),
    ({"cancelled": None, "cancelStatus": "csNo"}, False), ({"cancelled": None, "cancelStatus": "csCancellation"}, True), ({"cancelled": "tNO", "canceladoSap": True}, True),
    ({"cancelled": None}, None), ({"cancelled": "undefined"}, None), ({}, None),
    ({"cancelado": "N"}, False), ({"cancelado": "Y"}, True), ({"cancelado": "C"}, True), ({"cancelado": None}, None), ({"cancelado": "X"}, None), ({"canceladoSap": "C"}, True),
])
def test_cancelacion_indefinida_no_es_no_cancelado(fila, esperado):
    assert tg.cancelacion(fila) is esperado


# ------------------------------------------------------------------------------------------------ respuestas sinteticas con la forma documentada
def _meta(ent, **kw):
    return {"version": f"v-{ent}-1", "ultimaCargaCompleta": CARGA, "monedas": ["LPS"], "siguienteCursor": None, "advertencias": [], **kw}


def _datos():
    """Filas con los NOMBRES DE TRANSPORTE de /finanzas/* (referencia tecnica §3.21): fecha, vencimiento, pagado, cancelado Y/N/C, pagoEntry, documento, tipo, aplicado..."""
    clientes = [{"cardCode": "C90001", "nombre": "CLIENTE SINTETICO UNO", "activo": "Y", "bloqueado": "N", "moneda": "LPS", "saldo": "1500.500000", "vendedor": 7, "zona": None, "ruta": "LV"},
                {"cardCode": "C90002", "nombre": "CLIENTE SINTETICO DOS", "activo": "Y", "bloqueado": "N", "moneda": "LPS", "saldo": None, "vendedor": 7, "zona": None, "ruta": None},
                {"cardCode": "C90003", "nombre": "CLIENTE SINTETICO TRES", "activo": "N", "bloqueado": "Y", "moneda": "USD", "saldo": "250.250000", "vendedor": 8, "zona": None, "ruta": "PB"}]
    base = {"serie": 1, "vencimiento": "2026-10-31", "fechaDocumento": None, "moneda": "LPS", "totalFC": "0", "totalSC": "0", "pagadoFC": "0", "pagadoSC": "0", "vendedor": 7, "condicionPago": 1, "transId": None}
    facturas = [
        {**base, "docEntry": 101, "docNum": 5001, "cardCode": "C90001", "fecha": "2026-09-01", "total": "583.6900000000001", "pagado": "0", "estado": "bost_Open", "cancelado": "N"},
        {**base, "docEntry": 102, "docNum": 5002, "cardCode": "C90001", "fecha": "2026-08-01", "total": "2500.000000", "pagado": "2500.000000", "estado": "bost_Close", "cancelado": "N"},
        {**base, "docEntry": 103, "docNum": 5003, "cardCode": "C90001", "fecha": "2026-08-02", "total": "100", "pagado": "0", "estado": "bost_Open", "cancelado": "Y"},
        {**base, "docEntry": 104, "docNum": 5004, "cardCode": "C90002", "fecha": "2026-08-03", "total": "100", "pagado": "0", "estado": "bost_Open", "cancelado": None},
        {**base, "docEntry": 105, "docNum": 5005, "cardCode": "C90003", "fecha": "2026-08-04", "total": "75.5", "pagado": "0", "estado": "bost_Open", "cancelado": "N", "moneda": "USD"},
        {**base, "docEntry": 106, "docNum": 5006, "cardCode": "C90002", "fecha": "2026-08-05", "total": "10.1234567", "pagado": "0", "estado": "bost_Open", "cancelado": "N"},
        {**base, "docEntry": 109, "docNum": 5009, "cardCode": "C90001", "fecha": "2026-08-02", "total": "100", "pagado": "0", "estado": "bost_Close", "cancelado": "C"},    # documento de cancelacion
    ]
    pb = {"serie": 1, "moneda": "LPS", "transId": None, "total": None, "totalFC": None, "totalSC": None, "cheques": None, "tarjetas": None, "sinAplicarOriginal": None}
    pagos = [
        {**pb, "docEntry": 201, "docNum": 9001, "cardCode": "C90001", "fecha": "2026-08-20", "efectivo": "2500", "transferencia": "0", "cancelado": "N"},
        {**pb, "docEntry": 202, "docNum": 9002, "cardCode": "C90001", "fecha": "2026-08-21", "efectivo": "10", "transferencia": "0", "cancelado": "Y"},
        {**pb, "docEntry": 203, "docNum": 9003, "cardCode": "C90002", "fecha": "2026-08-22T13:45:00", "efectivo": "5", "transferencia": "0", "cancelado": "N"},
        {**pb, "docEntry": 204, "docNum": 9004, "cardCode": "C90003", "fecha": "2026-08-23", "efectivo": "10", "transferencia": "10", "cancelado": "N"},
        {**pb, "docEntry": 205, "docNum": 9005, "cardCode": "C90002", "fecha": "2026-08-24", "efectivo": "5", "transferencia": "0", "cancelado": "N"},           # sin ninguna aplicacion
        {**pb, "docEntry": 206, "docNum": 9006, "cardCode": "C90001", "fecha": "2026-08-25", "efectivo": "0", "transferencia": "0", "cancelado": "N"},           # importe propio 0: vale lo que suman sus aplicaciones
    ]
    ab = {"linea": 0, "cuota": None, "lineaDocumento": None, "aplicadoFC": "0", "aplicadoSC": "0"}
    aplic = [{**ab, "pagoEntry": 201, "cardCode": "C90001", "fecha": "2026-08-20", "cancelado": "N", "documento": 102, "tipo": "it_Invoice", "aplicado": "2500.000000"},
             {**ab, "pagoEntry": 202, "cardCode": "C90001", "fecha": "2026-08-21", "cancelado": "N", "documento": 101, "tipo": "it_Invoice", "aplicado": "10"},
             {**ab, "pagoEntry": 203, "cardCode": "C90002", "fecha": "2026-08-22", "cancelado": "N", "documento": 104, "tipo": "it_Invoice", "aplicado": "5"},
             {**ab, "pagoEntry": 204, "cardCode": "C90003", "fecha": "2026-08-23", "cancelado": "N", "documento": 0, "tipo": "it_OpeningBalance", "aplicado": "20"},        # saldo de apertura: no es factura
             {**ab, "pagoEntry": 204, "cardCode": "C90003", "fecha": "2026-08-23", "cancelado": "Y", "documento": 105, "tipo": "it_Invoice", "aplicado": "20"},          # aplicacion cancelada
             {**ab, "pagoEntry": 206, "cardCode": "C90001", "fecha": "2026-08-25", "cancelado": "N", "documento": 101, "tipo": "it_Invoice", "aplicado": "40"}]
    return {"clientes": clientes, "facturas": facturas, "pagos": pagos, "aplicacionesPagos": aplic}


class Servidor:
    """Backend FALSO: sirve `/finanzas/{entidad}` paginando de a `pagina` filas con cursor en `meta.siguienteCursor` y registra las peticiones."""

    def __init__(self, datos=None, pagina=2, cambiar_version_en_pagina=None, metas=None, cambios=1):
        self.datos, self.pagina, self.registro = _datos() if datos is None else datos, pagina, []
        self.cambiar, self.metas, self.cambios = cambiar_version_en_pagina, metas or {}, cambios

    def request(self, method, url, headers, timeout):
        from cxc_reporting.adapters.backend.client import TransportResponse
        import urllib.parse as up
        self.registro.append((method, url, dict(headers)))
        p = up.urlsplit(url)
        ent = p.path.rsplit("/", 1)[-1]
        q = dict(up.parse_qsl(p.query))
        if ent not in self.datos:
            return TransportResponse(404, b"")
        assert int(q["limit"]) <= 100
        filas, ini = self.datos[ent], int(q.get("cursor", "0"))
        trozo = filas[ini:ini + self.pagina]
        sig = str(ini + self.pagina) if ini + self.pagina < len(filas) else None
        meta = _meta(ent, siguienteCursor=sig, **self.metas.get(ent, {}))
        if self.cambiar and ini > 0 and ent == self.cambiar and self.cambios > 0:
            self.cambios -= 1
            meta["version"] = "v-otra"
        return TransportResponse(200, json.dumps({"data": trozo, "meta": meta}).encode())


def _transporte(servidor, entorno="pruebas"):
    if not (ruta_reporting_core() / "cxc_reporting").is_dir():
        pytest.skip("cxc-reporting-core no esta disponible en este equipo")
    import os
    os.environ.setdefault("CXC_REPORTING_SRC", str(ruta_reporting_core()))
    gc._importar_core()
    cfg = gc.cargar_config({gc.ENV_BASE_URL: "https://backend-pruebas.ejemplo.test", gc.ENV_API_KEY: CLAVE, gc.ENV_ENTORNO: "pruebas"})
    cli = gc.cliente_solo_lectura(cfg, transport=servidor, sleep=lambda s: None)
    return tg.TransporteGelsyn(cli, entorno=entorno, ahora=lambda: AHORA)


# ------------------------------------------------------------------------------------------------ mapeo
def test_mapeo_conserva_nulos_importes_exactos_monedas_y_excluye_canceladas():
    s = Servidor()
    t = _transporte(s)
    t.metadatos()
    cl = {c["codigo_sap"]: c for c in t.clientes()}
    assert cl["C90002"]["zona"] is None and cl["C90001"]["zona"] == "LV" and cl["C90001"]["activo"] is True and cl["C90003"]["activo"] is False and cl["C90003"]["moneda"] == "USD"
    assert cl["C90001"]["vendedor_codigo"] == "7"
    sal = {x["codigo_cliente"]: x for x in t.saldos()}
    assert sal["C90001"]["saldo_abierto"] == "1500.5" and sal["C90003"]["saldo_abierto"] == "250.25" and "C90002" not in sal       # nulo => sin fila, jamas 0
    f = {x["id_factura"]: x for x in t.facturas()}
    assert set(f) == {"101", "102", "104", "105", "106"} and t.excluidos["facturas_canceladas"] == 2                               # 103 (Y) y 109 (C: cancelacion): fuera
    assert f["101"]["total"] == "583.69" and f["101"]["estado"] == "O" and f["102"]["estado"] == "C" and f["102"]["aplicado"] == "2500"
    assert f["101"]["fecha_factura"] == "2026-09-01" and f["101"]["fecha_vencimiento"] == "2026-10-31" and f["105"]["moneda"] == "USD"
    assert f["104"]["estado"] is None                                                                                               # cancelacion indefinida: invalida, no "no cancelada"
    assert f["106"]["total"] is None                                                                                                # 7.º decimal real: rechazado, no redondeado
    r = {x["id_recibo"]: x for x in t.recibos()}
    assert set(r) == {"201", "203", "206"} and t.excluidos["pagos_cancelados"] == 1
    assert r["201"]["monto"] == "2500" and r["201"]["aplicaciones"] == [{"id_factura": "102", "monto": "2500"}] and r["201"]["fecha_contable"] == "2026-08-20"
    assert r["203"]["fecha_contable"] is None                                                                                       # hora ambigua sin zona: no se adivina
    assert r["206"]["monto"] == "40" and t.excluidos["recibos_con_importe_tomado_de_sus_aplicaciones"] == 1                         # importe propio 0: suma de sus aplicaciones
    assert t.excluidos["pagos_sin_aplicacion_a_facturas"] == 2                                                                      # 204 (solo saldo de apertura/cancelada) y 205: no respaldan ninguna factura
    assert t.excluidos["aplicaciones_a_otros_documentos"] == 1 and t.excluidos["aplicaciones_canceladas_o_indeterminadas"] == 1


def test_el_importe_del_pago_se_deriva_de_sus_componentes_cuando_total_es_nulo_como_en_la_copia():
    assert tg.TransporteGelsyn._monto_pago({"total": None, "efectivo": "10.5", "transferencia": "4.25", "cheques": None, "tarjetas": None}) == "14.75"
    assert tg.TransporteGelsyn._monto_pago({"total": "99", "efectivo": "1", "transferencia": "1"}) == "99"
    assert tg.TransporteGelsyn._monto_pago({"total": None, "efectivo": None, "transferencia": "1"}) is None                           # un componente ausente: no se supone cero


def test_si_ninguna_aplicacion_tiene_el_tipo_de_factura_la_carga_se_rechaza_en_vez_de_asumir():
    d = _datos()
    for a in d["aplicacionesPagos"]:
        a["tipo"] = "13"                                                                                                            # vocabulario distinto al observado
    t = _transporte(Servidor(datos=d))
    t.metadatos()
    with pytest.raises(fe.ContratoIncumplido, match="vocabulario de `tipo`"):
        t.recibos()


def test_metadatos_corte_comercial_version_por_entidad_y_completitud():
    t = _transporte(Servidor(metas={"pagos": {"monedas": ["LPS", "USD"], "advertencias": ["aviso de ejemplo"]}}))
    m = t.metadatos()
    assert m["fecha_corte"] == "2026-11-03" and m["completo"] is True and m["version_contrato"] == fe.CONTRATO_VERSION
    assert "facturas=v-facturas-1" in m["sistema_origen"] and "pruebas" in m["sistema_origen"] and t.versiones["clientes"] == "v-clientes-1"
    assert m["monedas"] == ["LPS", "USD"] and any("monedas" in a for a in t.advertencias) and any("aviso de ejemplo" in a for a in t.advertencias)
    t2 = _transporte(Servidor(metas={"facturas": {"ultimaCargaCompleta": None}}))
    assert t2.metadatos()["completo"] is False


def test_pagina_con_cursor_de_meta_y_conserva_la_version_solo_get():
    s = Servidor(pagina=2)
    t = _transporte(s)
    t.metadatos()
    assert len(t.facturas()) + t.excluidos["facturas_canceladas"] == 7                                                              # 4 paginas de 2
    assert {m for m, _, _ in s.registro} == {"GET"} and all(h.get("X-API-Key") == CLAVE for _, _, h in s.registro)
    paginas_fact = [u for _, u, _ in s.registro if "/finanzas/facturas" in u]
    assert len(paginas_fact) == 4 and "cursor" not in paginas_fact[0] and all("version=v-facturas-1" in u and "cursor=" in u for u in paginas_fact[1:])
    assert all("limit=100" in u for _, u, _ in s.registro)


def test_si_la_version_cambia_a_mitad_de_la_paginacion_se_reinicia_desde_la_primera_pagina():
    s = Servidor(pagina=2, cambiar_version_en_pagina="facturas", cambios=1)                 # VERSION_FINANCIERA_CAMBIO una vez: la segunda pasada es estable
    t = _transporte(s)
    t.metadatos()
    assert any("se reinició desde la primera página" in a for a in t.advertencias)
    paginas = [u for _, u, _ in s.registro if "/finanzas/facturas" in u]
    assert len([u for u in paginas if "cursor=" not in u]) == 2                              # dos arranques desde la primera pagina


def test_si_la_version_no_se_estabiliza_la_carga_se_rechaza():
    t = _transporte(Servidor(pagina=2, cambiar_version_en_pagina="facturas", cambios=99))
    with pytest.raises(fe.ContratoIncumplido, match="cambió 3 veces"):
        t.metadatos()


def test_errores_de_conexion_y_credencial_se_traducen_sin_secretos():
    t = _transporte(Servidor(datos={}))                                                                                             # todo 404 -> forma no reconocida
    with pytest.raises(fe.ContratoIncumplido) as ex:
        t.metadatos()
    assert CLAVE not in str(ex.value)


# ------------------------------------------------------------------------------------------------ integracion con FuenteEmpresarial: nunca vigente
def _fuente(tolerancia=0.5):
    t = _transporte(Servidor())
    return fe.FuenteEmpresarial(t, hoy=lambda: date(2026, 11, 5), max_edad_dias=5, tolerancia_invalidos=tolerancia), t


def test_la_fuente_de_pruebas_se_rotula_no_viva_y_nunca_es_apta_para_publicar():
    f, t = _fuente()
    cal = f.recargar()
    assert f.tipo == fe.TIPO_PRUEBAS and f.en_vivo is False and f.fecha_corte == date(2026, 11, 3) and "PRUEBAS" in f.etiqueta
    assert f.apta_para_publicar is False and f._vigente_hoy() is True                                                                # vigente por fecha, pero NO apta
    assert any("partidas abiertas ni conciliaciones" in p for p in cal["problemas"])
    assert any("sin saldo de control" in p for p in cal["problemas"]) and any("facturas_canceladas" in p for p in cal["problemas"])
    assert f.frescura()["vigente"] is False


def test_filas_indeterminadas_se_apartan_y_se_reportan_sin_inventar_saldos():
    f, t = _fuente()
    cal = f.recargar()
    motivos = " | ".join(str(d) for d in cal["filas_descartadas"])
    assert any(d[0] == "factura" and d[1] == "104" for d in cal["filas_descartadas"])                                              # cancelacion indefinida
    assert any(d[0] == "factura" and d[1] == "106" for d in cal["filas_descartadas"])                                              # 7.º decimal
    assert any(d[0] == "recibo" and d[1] == "203" for d in cal["filas_descartadas"]) and "faltan" in motivos
    assert f.saldo_abierto("C90001") == 1500.5                                                                                     # el de CONTROL de SAP, no uno calculado
    assert cal["estado"] in ("OK", "CON_ADVERTENCIAS")


def test_ninguna_ruta_con_la_fuente_gelsyn_puede_publicarse_ni_tener_viñeta_definitiva(tmp_path):
    from cxc_bot import route_markers as rm
    from cxc_portal.flujo import FlujoOperativo
    from tests.portal_helpers import ACTOR, crear_entorno
    f, t = _fuente()
    f.recargar()
    ctx = crear_entorno(tmp_path)
    ctx.estado.guardar_ruta(ACTOR, "GEL", "Ruta Gelsyn sintetica", "Vendedor Demo", "FORANEA", 60, zonas={"LV": "Las Vegas"},
                            clientes=[{"codigo": "C90001", "folder": "1 LV", "nombre": "CLIENTE SINTETICO UNO"}])
    ctx.servicio.fuente = f
    ctx.servicio.invalidar()
    ctx.flujo = FlujoOperativo(ctx.servicio, hoy=lambda: date(2026, 11, 5))
    estados = {c.resultado.codigo: rm.vineta_de(c.resultado, f.apta_para_publicar).estado for c in ctx.servicio.ruta("GEL").clientes}
    assert rm.DEFINITIVA not in estados.values()
    ev = ctx.flujo.evaluar_publicacion({"reporte_excepciones": {}})
    assert not ev["publicable"] and any("fuente" in m.lower() for m in ev["motivos"])
    assert ctx.servicio.info_fuente()["tipo"] == fe.TIPO_PRUEBAS and ctx.servicio.info_fuente()["en_vivo"] is False


def test_demasiadas_filas_indeterminadas_rechazan_la_carga_con_la_tolerancia_por_defecto():
    f, t = _fuente(tolerancia=0.02)                                   # politica del Portal: > 2 % de filas invalidas bloquea la carga completa
    with pytest.raises(fe.DatosBloqueados, match="Demasiadas filas inválidas"):
        f.recargar()
    assert f.apta_para_publicar is False and f.calidad["estado"] == "BLOQUEADA"


# ------------------------------------------------------------------------------------------------ cortes NO simultaneos entre entidades (verificado en Cosprobell-pruebas)
CORTES_REALES = {"clientes": {"ultimaCargaCompleta": "2026-10-27T18:33:28Z"}, "aplicacionesPagos": {"ultimaCargaCompleta": "2026-10-30T19:27:05Z"},
                 "facturas": {"ultimaCargaCompleta": "2026-11-04T16:07:56Z"}, "pagos": {"ultimaCargaCompleta": "2026-11-04T16:43:17Z"}}


def _datos_con_facturas_recientes():
    d = _datos()
    base = {"serie": 1, "cardCode": "C90001", "total": "50", "pagado": "0", "estado": "bost_Open", "cancelado": "N", "moneda": "LPS", "vencimiento": "2026-12-30", "fechaDocumento": None,
            "totalFC": "0", "totalSC": "0", "pagadoFC": "0", "pagadoSC": "0", "vendedor": 7, "condicionPago": 1, "transId": None}
    d["facturas"] += [{**base, "docEntry": 107, "docNum": 5007, "fecha": "2026-10-28"},                  # despues del corte de clientes
                      {**base, "docEntry": 108, "docNum": 5008, "fecha": "2026-11-04"}]                  # el mismo dia del corte de documentos
    return d


def test_el_corte_de_documentos_es_el_de_facturas_y_pagos_y_se_registran_todos_los_cortes():
    t = _transporte(Servidor(datos=_datos_con_facturas_recientes(), metas=CORTES_REALES))
    m = t.metadatos()
    assert m["fecha_corte"] == "2026-11-04"                                                       # NO el menor (27/10) que descartaria documentos recientes
    assert m["cortes"] == {"clientes": "2026-10-27", "aplicacionesPagos": "2026-10-30", "facturas": "2026-11-04", "pagos": "2026-11-04"}
    assert "cortes " in m["sistema_origen"] and "clientes=2026-10-27" in m["sistema_origen"] and m["completo"] is True
    adv = " | ".join(t.advertencias)
    assert "NO simultáneos" in adv and "no es del corte de documentos" in adv and "aplicaciones de pagos (2026-10-30) son anteriores" in adv


def test_las_facturas_recientes_se_conservan_y_no_se_descartan_por_fecha_posterior_al_corte():
    t = _transporte(Servidor(datos=_datos_con_facturas_recientes(), metas=CORTES_REALES))
    f = fe.FuenteEmpresarial(t, hoy=lambda: date(2026, 11, 5), max_edad_dias=5, tolerancia_invalidos=0.5)
    cal = f.recargar()
    ids = {x.id_factura for x in f.facturas_por_cliente(["C90001"])["C90001"]}
    assert {"107", "108"} <= ids                                                                  # las dos facturas posteriores al corte de clientes siguen ahi
    assert not any("posterior a la fecha de corte" in str(d[2]) for d in cal["filas_descartadas"])
    assert f.fecha_corte == date(2026, 11, 4)


def test_con_cortes_distintos_el_saldo_de_control_no_se_emite_ni_se_presenta_como_conciliado():
    t = _transporte(Servidor(datos=_datos_con_facturas_recientes(), metas=CORTES_REALES))
    f = fe.FuenteEmpresarial(t, hoy=lambda: date(2026, 11, 5), max_edad_dias=5, tolerancia_invalidos=0.5)
    cal = f.recargar()
    assert t.saldos() == [] and t.excluidos["saldos_de_control_de_otro_corte"] == 3
    assert f.saldo_abierto("C90001") != 1500.5                                                    # el saldo de control (otro corte) NO se mezcla: se muestra la suma de documentos abiertos
    assert abs(f.saldo_abierto("C90001") - (583.69 + 50 + 50)) < 0.01                          # exactamente la suma de sus documentos abiertos (pagado = 0)
    assert any("NO se emite ni se presenta como saldo conciliado" in p for p in cal["problemas"])
    assert cal["diferencias_saldo"] == []                                                         # y no se fabrican diferencias contra un saldo de otra fecha


def test_con_el_mismo_corte_en_todas_las_entidades_el_saldo_de_control_si_se_emite():
    t = _transporte(Servidor())
    t.metadatos()
    assert {x["codigo_cliente"] for x in t.saldos()} == {"C90001", "C90003"} and t.saldos_no_simultaneos is False


def test_la_fuente_con_cortes_distintos_sigue_sin_ser_apta_para_publicar():
    t = _transporte(Servidor(datos=_datos_con_facturas_recientes(), metas=CORTES_REALES))
    f = fe.FuenteEmpresarial(t, hoy=lambda: date(2026, 11, 5), max_edad_dias=5, tolerancia_invalidos=0.5)
    f.recargar()
    assert f.apta_para_publicar is False and t.apto_para_publicar is False and f.en_vivo is False
