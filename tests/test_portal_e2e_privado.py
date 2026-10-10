"""Verificacion extremo a extremo (HTTP real) con los datos PRIVADOS: 55 clientes, clasificaciones, decisiones, alertas,
facturas, filtros, CSV y persistencia tras reinicio. Se omite sin CXC_DATOS_DIR."""

import csv
import hashlib
import io
import json
import os
import threading
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from cxc_bot import payment_semaphore as ps
from cxc_bot.sap_exports import cargar_decisiones
from cxc_portal.gerencia_excel import leer_aprobado
from cxc_portal.server import make_handler
from cxc_portal.store import RUTAS_APROBADAS, RouteStore

DATOS = os.environ.get("CXC_DATOS_DIR", "")
pytestmark = pytest.mark.skipif(not DATOS or not (Path(DATOS) / "02_SAP_08Oct2026").is_dir(), reason="CXC_DATOS_DIR no configurado")

CORTE = date(2026, 10, 8)
ZONAS = {"LAS_VEGAS": {"LV": 9, "FB": 1, "PB": 8, "RL": 4, "SC": 2}, "YORO": {"SY": 7, "MY": 7, "PY": 1, "YT": 3, "YO": 13}}


def _arbol(d: Path):
    return sorted((str(p.relative_to(d)), hashlib.sha256(p.read_bytes()).hexdigest()) for p in d.rglob("*") if p.is_file())


@pytest.fixture(scope="module")
def servidor():
    antes = _arbol(Path(DATOS))
    store = RouteStore(data_dir=Path(DATOS), include_demo=True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, None))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", store
    srv.shutdown()
    srv.server_close()
    assert _arbol(Path(DATOS)) == antes, "el portal NO debe escribir ni modificar nada en el directorio de datos"


def _j(base, ruta):
    with urllib.request.urlopen(base + ruta) as r:
        return json.loads(r.read())


def _datos_aprobados():
    return {cfg["route_id"]: leer_aprobado(Path(DATOS) / "01_Rutas" / cfg["archivo"]) for cfg in RUTAS_APROBADAS}


def test_los_55_clientes_de_punta_a_punta(servidor):
    base, _ = servidor
    aprobados, decisiones = _datos_aprobados(), cargar_decisiones(Path(DATOS))
    total = 0
    for rid, filas in aprobados.items():
        lista = _j(base, f"/api/rutas/{rid}/clientes")["clientes"]
        assert [c["codigo"] for c in lista] == [f.codigo for f in filas]            # orden de folders confirmado
        assert [c["folder"] for c in lista] == [f.folder for f in filas]
        for c, f in zip(lista, filas):
            total += 1
            d = _j(base, f"/api/clientes/{c['codigo']}")
            s = d["semaforo"]
            assert d["fuente"]["tipo"] == "SNAPSHOT_SAP_HISTORICO" and d["fuente"]["fecha_corte"] == "2026-10-08"
            assert s["calculado"] == f.calculado, c["codigo"]                          # clasificacion automatica = Excel aprobado
            dec = decisiones.get(c["codigo"])
            assert s["final"] == (dec.clasificacion_final if dec else f.calculado), c["codigo"]
            assert bool(s["decision_gerencia"]) == bool(dec)
            if dec:
                assert s["decision_gerencia"]["actor"] == "Gerencia" and s["decision_gerencia"]["motivo"]
            assert d["conciliacion_gerencia"]["coincide"], (c["codigo"], d["conciliacion_gerencia"])
            fac = s["facturas"]
            assert len(fac) == min(ps.VENTANA_FACTURAS, s["facturas_historial"])
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
                assert x["motivo"]
            assert len(moras) == s["facturas_en_media"]
            if s["facturas_historial"] >= 4 and moras:   # media y categoria recalculadas de forma independiente
                assert s["promedio_mora"] == pytest.approx(sum(moras) / len(moras), abs=0.01)
                assert s["calculado"] == ps.clasificar_media(sum(moras) / len(moras))
            elif s["facturas_historial"] < 4:
                assert s["calculado"] == ps.EN_PRUEBA
            # alertas: abiertas > 60 = cobro (con saldo); pagadas > 60 = historicas (sin saldo). Nunca mezcladas.
            ab = {x["numero"] for x in fac if x["estado"] == "ABIERTA" and x["en_media"] and x["mora"] > 60}
            pg = {x["numero"] for x in fac if x["estado"] != "ABIERTA" and x["en_media"] and x["mora"] > 60}
            assert {a["numero"] for a in s["alertas_cobro"]} == ab and {a["numero"] for a in s["alertas_historicas"]} == pg
            assert all(a["saldo_pendiente"] > 0 for a in s["alertas_cobro"]) and all(a["saldo_pendiente"] is None for a in s["alertas_historicas"])
    assert total == 55


def test_distribuciones_finales(servidor):
    base, _ = servidor
    rutas = {r["route_id"]: r for r in _j(base, "/api/rutas")["rutas"]}
    assert rutas["LAS_VEGAS"]["clasificacion"] == {"A": 6, "B": 3, "C": 4, "EN_PRUEBA": 9, "SOLO_CONTADO": 2}
    assert rutas["YORO"]["clasificacion"] == {"A": 11, "B": 8, "C": 9, "SOLO_CONTADO": 2, "EN_PRUEBA": 1}
    assert rutas["LAS_VEGAS"]["clientes"] == 24 and rutas["YORO"]["clientes"] == 31
    assert all(r["avisos"] == [] for r in rutas.values())


def test_filtros_por_zona_y_clasificacion(servidor):
    base, _ = servidor
    for rid, zonas in ZONAS.items():
        todos = _j(base, f"/api/rutas/{rid}/clientes")["clientes"]
        for z, n in zonas.items():
            assert len(_j(base, f"/api/rutas/{rid}/clientes?zona={z}")["clientes"]) == n, (rid, z)
        for clase in {c["semaforo"]["final"] for c in todos}:
            esperado = [c["codigo"] for c in todos if c["semaforo"]["final"] == clase]
            assert [c["codigo"] for c in _j(base, f"/api/rutas/{rid}/clientes?clasificacion={clase}")["clientes"]] == esperado
        un = todos[3]
        assert un["codigo"] in [c["codigo"] for c in _j(base, f"/api/rutas/{rid}/clientes?q={un['codigo'].lower()}")["clientes"]]


def test_reportes_csv_de_ambas_rutas(servidor):
    base, _ = servidor
    for rid in ("LAS_VEGAS", "YORO"):
        with urllib.request.urlopen(f"{base}/api/rutas/{rid}/reporte.csv") as r:
            assert "attachment" in r.headers["Content-Disposition"] and r.headers["Content-Type"].startswith("text/csv")
            texto = r.read().decode("utf-8-sig")
        filas = list(csv.reader(io.StringIO(texto)))
        assert "2026-10-08" in filas[0]
        cab, datos = filas[1], filas[2:]
        lista = _j(base, f"/api/rutas/{rid}/clientes")["clientes"]
        assert len(datos) == len(lista)
        i = {h: n for n, h in enumerate(cab)}
        for fila, c in zip(datos, lista):
            assert fila[i["Folder"]] == c["folder"] and fila[i["Codigo"]] == c["codigo"]
            assert fila[i["Clasificacion final"]] == c["semaforo"]["final"] and fila[i["Clasificacion calculada"]] == c["semaforo"]["calculado"]
            assert float(fila[i["Saldo"]]) == pytest.approx(c["saldo"], abs=0.01)
            assert int(fila[i["Alertas de cobro (abiertas > 60 d)"]]) == len(c["semaforo"]["alertas_cobro"])
            assert int(fila[i["Alertas historicas (pagadas > 60 d)"]]) == len(c["semaforo"]["alertas_historicas"])
            assert bool(fila[i["Decision de gerencia"]]) == bool(c["semaforo"]["decision_gerencia"])


def test_alertas_endpoint_coincide_con_los_clientes(servidor):
    base, _ = servidor
    al = _j(base, "/api/alertas")
    cobro = sum(len(c["semaforo"]["alertas_cobro"]) for rid in ("LAS_VEGAS", "YORO") for c in _j(base, f"/api/rutas/{rid}/clientes")["clientes"])
    hist = sum(len(c["semaforo"]["alertas_historicas"]) for rid in ("LAS_VEGAS", "YORO") for c in _j(base, f"/api/rutas/{rid}/clientes")["clientes"])
    assert (len(al["cobro_operativas"]), len(al["historicas_comportamiento"])) == (cobro, hist)
    assert all(a["saldo_pendiente"] > 0 for a in al["cobro_operativas"])


def test_las_decisiones_persisten_tras_reiniciar_el_servidor(servidor):
    """Un 'reinicio' = un RouteStore nuevo desde el disco: el resultado completo debe ser identico."""
    _, store = servidor
    otro = RouteStore(data_dir=Path(DATOS), include_demo=True)

    def foto(s):
        return json.dumps({rid: [s.rutas[rid].cliente_dict(c) for c in s.rutas[rid].clientes] for rid in ("LAS_VEGAS", "YORO")}, sort_keys=True, default=str)

    assert foto(store) == foto(otro)
    dec = [c.codigo for r in otro.rutas.values() for c in r.clientes if c.resultado.decision_gerencia and r.route_id != "DEMO"]
    assert len(dec) == 7


def test_los_excel_originales_no_se_alteran():
    sumas = (Path(DATOS) / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    for linea in sumas:
        if not linea.strip():
            continue
        h, nombre = linea.split(None, 1)
        assert hashlib.sha256((Path(DATOS) / nombre.strip().lstrip("*")).read_bytes()).hexdigest() == h.lower(), nombre
