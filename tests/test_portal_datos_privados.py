"""Regresion extremo a extremo con los datos PRIVADOS (consultas SAP 08/10/2026 + Excel aprobados por gerencia), por HTTP real y
con sesion autenticada. Se omite sin CXC_DATOS_DIR. Verifica que el portal (BD propia sembrada + motor unico) reproduce lo que
gerencia aprobo, que los Excel originales no se alteran y que nada se escribe en el directorio de datos."""

import csv
import hashlib
import io
import os
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from cxc_bot import payment_semaphore as ps
from cxc_bot.sap_exports import cargar_decisiones
from cxc_portal.app import Contexto
from cxc_portal.estado import Estado
from cxc_portal.fuentes import FuenteSnapshotSAP
from cxc_portal.gerencia_excel import leer_aprobado
from cxc_portal.sembrar import RUTAS_SEMILLA, sembrar
from cxc_portal.store import PortalServicio
from tests.portal_helpers import ACTOR, CLAVE_ADMIN, CLAVE_GER, CLAVE_VEN, Servidor, cliente_con_login

DATOS = os.environ.get("CXC_DATOS_DIR", "")
pytestmark = pytest.mark.skipif(not DATOS or not (Path(DATOS) / "02_SAP_08Oct2026").is_dir(), reason="CXC_DATOS_DIR no configurado")

CORTE = date(2026, 10, 8)
ZONAS = {"LAS_VEGAS": {"LV": 9, "FB": 1, "PB": 8, "RL": 4, "SC": 2}, "YORO": {"SY": 7, "MY": 7, "PY": 1, "YT": 3, "YO": 13}}


def _arbol(d: Path):
    return sorted((str(p.relative_to(d)), hashlib.sha256(p.read_bytes()).hexdigest()) for p in d.rglob("*") if p.is_file())


def _montar(tmp_path):
    estado = Estado(tmp_path / "portal.sqlite3")
    sembrar(estado, Path(DATOS), ACTOR)
    for usuario, nombre, rol, clave, vend in (("admin1", "Admin", "admin", CLAVE_ADMIN, None), ("ger1", "Gerencia", "gerencia", CLAVE_GER, None),
                                              ("ven_lv", "Erick", "vendedor", CLAVE_VEN, "Erick Brizuela")):
        estado.crear_usuario(ACTOR, usuario, nombre, rol, clave, vend, debe_cambiar=False)
    # data_dir=None: el portal no debe escribir nada en el directorio de datos privados durante estas pruebas
    return Contexto(PortalServicio(estado, FuenteSnapshotSAP(Path(DATOS)), tmp_path, None, None), estado)


@pytest.fixture(scope="module")
def entorno(tmp_path_factory):
    antes = _arbol(Path(DATOS))
    tmp = tmp_path_factory.mktemp("estado_privado")
    ctx = _montar(tmp)
    srv = Servidor(ctx)
    yield srv, ctx, tmp
    srv.cerrar()
    assert _arbol(Path(DATOS)) == antes, "el portal NO debe escribir ni modificar nada en el directorio de datos"


def _aprobados():
    return {c["route_id"]: leer_aprobado(Path(DATOS) / "01_Rutas" / c["archivo"]) for c in RUTAS_SEMILLA}


def test_siembra_idempotente_y_orden_de_folders_confirmado(entorno):
    _, ctx, tmp = entorno
    r = sembrar(ctx.estado, Path(DATOS), ACTOR)
    assert r["rutas_creadas"] == [] and r["decisiones_importadas"] == []  # no se duplica ni se sobrescribe
    lv, yo = ctx.estado.obtener_ruta("LAS_VEGAS"), ctx.estado.obtener_ruta("YORO")
    assert len(lv["clientes"]) == 24 and len(yo["clientes"]) == 31 and lv["plazo_dias"] == yo["plazo_dias"] == 60
    pref = [c["zona"] for c in yo["clientes"]]
    assert [pref.count(p) for p in ("SY", "MY", "PY", "YT", "YO")] == [7, 7, 1, 3, 13]
    codigos = {c["codigo"] for c in yo["clientes"]}
    assert "C10617" in codigos and "C10924" not in codigos


def test_los_55_clientes_de_punta_a_punta(entorno):
    s, ctx, _ = entorno
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    aprobados = _aprobados()
    decisiones = cargar_decisiones(Path(DATOS))
    total = 0
    for rid, filas in aprobados.items():
        lista = ger.get(f"/api/rutas/{rid}/clientes")[2]["clientes"]
        assert [(c["folder"], c["codigo"]) for c in lista] == [(f.folder, f.codigo) for f in filas]  # orden de folders confirmado
        for c, f in zip(lista, filas):
            total += 1
            d = ger.get(f"/api/clientes/{c['codigo']}")[2]
            sem = d["semaforo"]
            assert d["fuente"]["tipo"] == "SNAPSHOT_SAP_HISTORICO" and d["fuente"]["fecha_corte"] == "2026-10-08" and d["fuente"]["en_vivo"] is False
            assert sem["calculado"] == f.calculado, c["codigo"]  # clasificacion automatica = Excel aprobado
            dec = decisiones.get(c["codigo"])
            assert sem["final"] == (dec.clasificacion_final if dec else f.calculado), c["codigo"]
            assert bool(sem["decision_gerencia"]) == bool(dec)
            for k, esp in (("facturas_historial", f.historial), ("facturas_ventana", f.ventana), ("pagadas", f.pagadas), ("abiertas", f.abiertas),
                           ("no_acreditadas", f.no_acreditadas), ("facturas_en_media", f.evaluables), ("suma_mora", f.suma_mora)):
                assert int(sem[k]) == int(esp), (c["codigo"], k)
            assert len(sem["alertas_cobro"]) + len(sem["alertas_historicas"]) == f.alertas
            fac = sem["facturas"]
            assert len(fac) == min(ps.VENTANA_FACTURAS, sem["facturas_historial"])
            assert [x["fecha_factura"] for x in fac] == sorted((x["fecha_factura"] for x in fac), reverse=True)
            moras = []
            for x in fac:
                venc = date.fromisoformat(x["vencimiento_ruta"])
                assert (venc - date.fromisoformat(x["fecha_factura"])).days == 60
                if x["estado"] == "PAGADA":
                    assert x["recibos"] and round(x["recibos_total"], 2) >= round(x["total"] - 0.01, 2)
                    assert x["mora"] == max(0, (date.fromisoformat(x["ultimo_recibo"]) - venc).days)
                    assert all(r["fecha"] >= x["fecha_factura"] for r in x["recibos"])
                elif x["estado"] == "ABIERTA":
                    assert x["mora"] == max(0, (CORTE - venc).days) and x["en_media"] == (x["mora"] > 5)
                else:
                    assert not x["en_media"] and x["mora"] is None
                if x["en_media"]:
                    moras.append(x["mora"])
            assert len(moras) == sem["facturas_en_media"]
            if sem["facturas_historial"] >= 4 and moras:  # media y categoria recalculadas de forma independiente
                assert sem["promedio_mora"] == pytest.approx(sum(moras) / len(moras), abs=0.01)
                assert sem["calculado"] == ps.clasificar_media(sum(moras) / len(moras))
            elif sem["facturas_historial"] < 4:
                assert sem["calculado"] == ps.EN_PRUEBA
            ab = {x["numero"] for x in fac if x["estado"] == "ABIERTA" and x["en_media"] and x["mora"] > 60}
            pg = {x["numero"] for x in fac if x["estado"] != "ABIERTA" and x["en_media"] and x["mora"] > 60}
            assert {a["numero"] for a in sem["alertas_cobro"]} == ab and {a["numero"] for a in sem["alertas_historicas"]} == pg
            assert all(a["saldo_pendiente"] > 0 for a in sem["alertas_cobro"]) and all(a["saldo_pendiente"] is None for a in sem["alertas_historicas"])
    assert total == 55


def test_distribuciones_finales_y_decisiones(entorno):
    s, ctx, _ = entorno
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    rutas = {r["route_id"]: r for r in ger.get("/api/rutas")[2]["rutas"]}
    assert rutas["LAS_VEGAS"]["clasificacion"] == {"A": 6, "B": 3, "C": 4, "EN_PRUEBA": 9, "SOLO_CONTADO": 2}
    assert rutas["YORO"]["clasificacion"] == {"A": 11, "B": 8, "C": 9, "SOLO_CONTADO": 2, "EN_PRUEBA": 1}
    vig = ctx.estado.decisiones_vigentes()
    assert len(vig) == 7 and all(d["actor"] and len(d["motivo"]) >= 10 for d in vig.values())
    for rid in ("LAS_VEGAS", "YORO"):
        for c in ger.get(f"/api/rutas/{rid}/clientes")[2]["clientes"]:
            sem = c["semaforo"]
            if sem["decision_gerencia"]:
                assert sem["calculado"] == "CANDIDATO_SOLO_CONTADO" and sem["promedio_mora"] > ps.LIMITE_C  # el calculo automatico se conserva


def test_saldos_son_las_facturas_abiertas_de_la_consulta_1_y_coinciden_con_la_lista_de_ruta(entorno):
    s, _, _ = entorno
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    listas = {"LAS_VEGAS": "Las_Vegas_Orden_Por_Zonas_Con_Leyenda.xlsx", "YORO": "Yoro_Orden_Zonas_Corregido_Con_Leyenda.xlsx"}
    for rid, archivo in listas.items():
        wb = openpyxl.load_workbook(Path(DATOS) / "01_Rutas" / archivo, data_only=True, read_only=True)
        total = 0.0
        for r in wb[wb.sheetnames[0]].iter_rows(values_only=True):
            if r and isinstance(r[1], str) and r[1].strip().upper().startswith("C") and r[1].strip()[1:].isdigit():
                total += float(r[5] if rid == "YORO" else r[4] or 0)
        got = next(x for x in ger.get("/api/rutas")[2]["rutas"] if x["route_id"] == rid)["saldo_total"]
        assert round(total, 2) == got, rid


def test_filtros_y_csv_de_ambas_rutas(entorno):
    s, _, _ = entorno
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    for rid, zonas in ZONAS.items():
        todos = ger.get(f"/api/rutas/{rid}/clientes")[2]["clientes"]
        for z, n in zonas.items():
            assert len(ger.get(f"/api/rutas/{rid}/clientes?zona={z}")[2]["clientes"]) == n, (rid, z)
        for clase in {c["semaforo"]["final"] for c in todos}:
            esperado = [c["codigo"] for c in todos if c["semaforo"]["final"] == clase]
            assert [c["codigo"] for c in ger.get(f"/api/rutas/{rid}/clientes?clasificacion={clase}")[2]["clientes"]] == esperado
        st, cab, body = ger.get(f"/api/rutas/{rid}/reporte.csv")
        assert st == 200 and "attachment" in cab["content-disposition"] and cab["content-type"].startswith("text/csv")
        filas = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        assert "2026-10-08" in filas[0]
        cab_i = {h: i for i, h in enumerate(filas[1])}
        assert len(filas[2:]) == len(todos)
        for fila, c in zip(filas[2:], todos):
            assert fila[cab_i["Folder"]] == c["folder"] and fila[cab_i["Clasificacion final"]] == c["semaforo"]["final"]
            assert float(fila[cab_i["Saldo"]]) == pytest.approx(c["saldo"], abs=0.01)


def test_alertas_endpoint_coincide_con_los_clientes(entorno):
    s, _, _ = entorno
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    al = ger.get("/api/alertas")[2]
    cl = [c for rid in ("LAS_VEGAS", "YORO") for c in ger.get(f"/api/rutas/{rid}/clientes")[2]["clientes"]]
    assert len(al["cobro_operativas"]) == sum(len(c["semaforo"]["alertas_cobro"]) for c in cl)
    assert len(al["historicas_comportamiento"]) == sum(len(c["semaforo"]["alertas_historicas"]) for c in cl)


def test_el_vendedor_solo_ve_su_ruta_real_y_nada_de_la_otra(entorno):
    s, _, _ = entorno
    v = cliente_con_login(s, "ven_lv", CLAVE_VEN)
    assert [r["route_id"] for r in v.get("/api/rutas")[2]["rutas"]] == ["LAS_VEGAS"]
    assert v.get("/api/rutas/YORO/clientes")[0] == 404 and v.get("/api/rutas/YORO/reporte.csv")[0] == 404
    assert v.get("/api/prioridades")[0] == 403 and v.get("/api/reportes/gerencial")[0] == 403
    al = v.get("/api/alertas")[2]
    assert all(a["ruta"] == "LAS_VEGAS" for a in al["cobro_operativas"] + al["historicas_comportamiento"])


def test_persistencia_tras_reiniciar(entorno):
    """Un 'reinicio' = reconstruir todo desde el archivo de la BD propia: decisiones, rutas y resultados identicos."""
    s, ctx, tmp = entorno
    ger = cliente_con_login(s, "ger1", CLAVE_GER)
    antes = {rid: ger.get(f"/api/rutas/{rid}/clientes")[2]["clientes"] for rid in ("LAS_VEGAS", "YORO")}
    ctx2 = Contexto(PortalServicio(Estado(tmp / "portal.sqlite3"), FuenteSnapshotSAP(Path(DATOS)), tmp, None, None), Estado(tmp / "portal.sqlite3"))
    for rid in ("LAS_VEGAS", "YORO"):
        r2 = ctx2.servicio.ruta(rid)
        despues = [r2.cliente_dict(c) for c in r2.clientes]
        assert [(c["codigo"], c["semaforo"]["final"], c["semaforo"]["calculado"], c["saldo"]) for c in despues] == \
               [(c["codigo"], c["semaforo"]["final"], c["semaforo"]["calculado"], c["saldo"]) for c in antes[rid]]
    assert len(ctx2.estado.decisiones_vigentes()) == 7


def test_el_generador_de_rutas_y_el_portal_dan_la_misma_clasificacion_con_el_mismo_motor(entorno):
    from cxc_bot import runner
    from cxc_bot.config import load_route_config
    from cxc_bot.models import ClientEntry
    from cxc_bot.sap_exports import SapExportEvidenceSource
    s, ctx, _ = entorno
    fuente = SapExportEvidenceSource(Path(DATOS))
    decisiones = {c: ps.DecisionGerencia(d["clasificacion_final"], d["motivo"], d["actor"], d["fecha"]) for c, d in ctx.estado.decisiones_vigentes().items()}
    for rid, nombre in (("LAS_VEGAS", "LAS VEGAS"), ("YORO", "YORO")):
        ruta = ctx.servicio.ruta(rid)
        resumen, error = runner._evaluate_payment_semaphore(load_route_config(nombre), [ClientEntry(codigo=c.codigo) for c in ruta.clientes], fuente, decisiones)
        assert error is None and resumen["plazo_dias"] == 60
        for fila, c in zip(resumen["clientes"], ruta.clientes):
            assert (fila["codigo"], fila["calculado"], fila["final"]) == (c.codigo, c.resultado.calculado, c.resultado.final)
            assert fila["alertas_cobro"] == len(c.resultado.alertas_cobro) and fila["alertas_historicas"] == len(c.resultado.alertas_historicas)


def test_los_excel_originales_no_se_alteran():
    for linea in (Path(DATOS) / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        if linea.strip():
            h, nombre = linea.split(None, 1)
            assert hashlib.sha256((Path(DATOS) / nombre.strip().lstrip("*")).read_bytes()).hexdigest() == h.lower(), nombre
