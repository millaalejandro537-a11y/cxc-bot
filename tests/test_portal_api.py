import json
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer

import pytest

from cxc_bot import payment_semaphore as ps
from cxc_portal.server import make_handler
from cxc_portal.store import CORTE_SNAPSHOT, FUENTE_SNAPSHOT_SAP, ClienteRuta, RouteStore, Ruta


def _ruta_x():
    def pag(n, mora):
        e = date(2025, 1, 1) + timedelta(days=30 * n)
        return ps.FacturaSAP(str(n), str(n), e, e, 1000.0, 1000.0, "C", (ps.Recibo(f"R{n}", e + timedelta(days=60 + mora), 1000.0),))

    spec = [("C1", "UNO", [0, 0, 0, 0], None, 100.0, "AA"),
            ("C2", "DOS", [90] * 4, ps.DecisionGerencia("C", "m", "Gerencia", "2026-10-09"), 900.0, "AA"),
            ("C3", "TRES", [0], None, 500.0, "BB")]
    cli = []
    for i, (cod, nom, moras, dec, saldo, zona) in enumerate(spec, 1):
        res = ps.evaluar_cliente(cod, [pag(n, m) for n, m in enumerate(moras)], CORTE_SNAPSHOT, 60, dec)
        cli.append(ClienteRuta(f"{i} {zona}", zona, cod, nom, saldo, res, None))
    fuente = {"tipo": FUENTE_SNAPSHOT_SAP, "fecha_corte": "2026-10-08", "en_vivo": False, "etiqueta": "snap", "notas": []}
    return Ruta("RX", "Ruta X", "V", "FORANEA", 60, fuente, [], cli, ["aviso de prueba"])


@pytest.fixture()
def api():
    store = RouteStore(rutas=[_ruta_x()], include_demo=True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, None))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def _j(url):
    return json.loads(_get(url)[2])


def test_fuentes_distingue_snapshot_de_en_vivo_y_expone_avisos(api):
    d = _j(api + "/api/fuentes")
    assert d["en_vivo"]["disponible"] is False
    assert {r["route_id"]: r["fuente"]["tipo"] for r in d["rutas"]} == {"RX": "SNAPSHOT_SAP_HISTORICO", "DEMO": "SINTETICO_DEMO"}
    assert d["avisos_de_conciliacion"] == {"RX": ["aviso de prueba"]}


def test_rutas_resumen_con_fecha_de_corte_y_conteos(api):
    rx = next(r for r in _j(api + "/api/rutas")["rutas"] if r["route_id"] == "RX")
    assert rx["fuente"]["fecha_corte"] == "2026-10-08" and rx["clientes"] == 3 and rx["saldo_total"] == 1500.0
    assert rx["clasificacion_calculada"]["CANDIDATO_SOLO_CONTADO"] == 1 and rx["clasificacion"]["C"] == 1 and rx["overrides_gerencia"] == 1


def test_clientes_en_orden_de_folders_y_filtros(api):
    cs = _j(api + "/api/rutas/RX/clientes")["clientes"]
    assert [c["folder"] for c in cs] == ["1 AA", "2 AA", "3 BB"]
    assert [c["codigo"] for c in _j(api + "/api/rutas/RX/clientes?zona=bb")["clientes"]] == ["C3"]
    assert [c["codigo"] for c in _j(api + "/api/rutas/RX/clientes?q=dos")["clientes"]] == ["C2"]
    assert [c["codigo"] for c in _j(api + "/api/rutas/RX/clientes?clasificacion=C")["clientes"]] == ["C2"]
    assert [c["codigo"] for c in _j(api + "/api/rutas/RX/clientes?clasificacion=EN_PRUEBA")["clientes"]] == ["C3"]


def test_cliente_expone_calculo_automatico_decision_y_evidencia_por_factura(api):
    c = _j(api + "/api/clientes/C2")
    s = c["semaforo"]
    assert s["calculado"] == "CANDIDATO_SOLO_CONTADO" and s["final"] == "C" and s["decision_gerencia"]["actor"] == "Gerencia"
    assert s["etiqueta_calculada"] and len(s["facturas"]) == 4
    f = s["facturas"][0]
    assert f["recibos"] and f["estado"] == "PAGADA" and "ultimo_recibo" in f and "vencimiento_sap" in f
    assert c["fuente"]["tipo"] == "SNAPSHOT_SAP_HISTORICO"


def test_demo_cubre_todas_las_clases(api):
    cs = _j(api + "/api/rutas/DEMO/clientes")["clientes"]
    assert {"A", "B", "C", "CANDIDATO_SOLO_CONTADO", "EN_PRUEBA", "REVISION_HUMANA"} <= {c["semaforo"]["calculado"] for c in cs}
    assert any(c["semaforo"]["alertas_cobro"] for c in cs) and any(c["semaforo"]["alertas_historicas"] for c in cs)
    assert all(c["fuente"]["tipo"] == "SINTETICO_DEMO" for c in cs)


def test_prioridades_y_csv(api):
    p = _j(api + "/api/prioridades?ruta=RX&n=2")["prioridades"]
    assert [x["codigo"] for x in p] == ["C2", "C3"]
    todos = _j(api + "/api/prioridades?n=50")["prioridades"]
    assert all(x["fuente"]["tipo"] != "SINTETICO_DEMO" for x in todos)  # la ruta demo no contamina las prioridades
    st, ctype, body = _get(api + "/api/rutas/RX/reporte.csv")
    text = body.decode("utf-8-sig")
    assert st == 200 and ctype.startswith("text/csv") and "C2" in text and "Gerencia" in text and "2026-10-08" in text


def test_endpoint_de_alertas_separa_cobro_operativo_de_historico(api):
    d = _j(api + "/api/alertas")
    assert set(d) >= {"cobro_operativas", "historicas_comportamiento", "nota"}
    assert all(a["tipo"] == "COBRO_OPERATIVA" and a["saldo_pendiente"] is not None for a in d["cobro_operativas"])
    assert all(a["tipo"] == "COMPORTAMIENTO_HISTORICO" and a["saldo_pendiente"] is None for a in d["historicas_comportamiento"])
    assert d["historicas_comportamiento"]  # la ruta de prueba tiene facturas pagadas con > 60 d
    assert all(a["ruta"] != "DEMO" for a in d["historicas_comportamiento"] + d["cobro_operativas"])


def test_reglas_publicadas(api):
    r = _j(api + "/api/reglas")
    assert r["ventana_facturas"] == 10 and r["minimo_historial"] == 4 and r["plazos"] == {"foranea": 60, "local": 30}


def test_solo_lectura_y_404(api):
    for metodo in ("POST", "PUT", "DELETE"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(api + "/api/rutas", data=b"{}", method=metodo))
        assert e.value.code == 405
    for ruta in ("/api/rutas/NOPE/clientes", "/api/clientes/NOPE"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(api + ruta)
        assert e.value.code == 404


def test_datos_inexistentes_no_tumban_el_portal(tmp_path):
    store = RouteStore(data_dir=tmp_path / "no_existe")
    assert store.errores and set(store.rutas) == {"DEMO"}


def test_el_servidor_rechaza_escuchar_fuera_de_loopback():
    from cxc_portal.server import run
    for host in ("0.0.0.0", "192.168.1.10", ""):
        with pytest.raises(SystemExit, match="loopback"):
            run(host, 0, None, None)


def test_rechaza_peticiones_con_host_ajeno_dns_rebinding(api):
    import http.client
    puerto = int(api.rsplit(":", 1)[1])
    for host, esperado in (("evil.example.com", 403), ("evil.example.com:8780", 403), ("127.0.0.1:%d" % puerto, 200), ("localhost:%d" % puerto, 200)):
        c = http.client.HTTPConnection("127.0.0.1", puerto)
        c.request("GET", "/api/health", headers={"Host": host})
        assert c.getresponse().status == esperado, host
        c.close()
