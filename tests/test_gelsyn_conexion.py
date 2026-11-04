"""Preparacion de la conexion de SOLO LECTURA a Cosprobell Backend (Gelsyn): configuracion segura y preflight. Todo con transporte FALSO y datos sinteticos:
no hay red, ni credenciales reales, ni consultas. La clave de prueba es una cadena inventada."""

import json

import pytest

from cxc_portal import gelsyn_config as gc
from cxc_portal import gelsyn_preflight as gp
from tests.portal_helpers import ruta_reporting_core

CLAVE = "clave-de-prueba-INVENTADA-0123456789"
BASE = {gc.ENV_BASE_URL: "https://backend-pruebas.ejemplo.test", gc.ENV_API_KEY: CLAVE, gc.ENV_ENTORNO: "pruebas"}


def _env(**cambios):
    e = dict(BASE)
    e.update(cambios)
    return {k: v for k, v in e.items() if v is not None}


# ------------------------------------------------------------------------------------------------ configuracion segura
def test_configuracion_valida_de_pruebas_oculta_la_clave():
    c = gc.cargar_config(_env())
    assert c.entorno == "pruebas" and c.host == "backend-pruebas.ejemplo.test" and len(c.huella_clave) == 8
    for texto in (repr(c), str(c), f"{c}", json.dumps({"c": repr(c)})):
        assert CLAVE not in texto
    assert c.env_para_cliente()[gc.ENV_API_KEY] == CLAVE                  # solo viaja en memoria hacia el cliente


@pytest.mark.parametrize("cambios,fragmento", [
    ({gc.ENV_API_KEY: None}, "Faltan variables"),
    ({gc.ENV_BASE_URL: None}, "Faltan variables"),
    ({gc.ENV_ENTORNO: None}, "Faltan variables"),
    ({gc.ENV_BASE_URL: "postgresql://postgres:secreto@host:5432/railway"}, "NO usa conexión SQL"),
    ({gc.ENV_BASE_URL: "postgres://usuario:clave@host/db"}, "NO usa conexión SQL"),
    ({gc.ENV_API_KEY: "postgresql://postgres:secreto@host/railway"}, "NO usa conexión SQL"),
    ({gc.ENV_API_KEY: "una-clave-con-://-dentro-0123456789"}, "NO usa conexión SQL"),
    ({gc.ENV_BASE_URL: "http://backend.ejemplo.test"}, "HTTPS"),
    ({gc.ENV_BASE_URL: "https://usuario:clave@backend.ejemplo.test"}, "credenciales"),
    ({gc.ENV_BASE_URL: "https://backend.ejemplo.test/?key=abc"}, "credenciales"),
    ({gc.ENV_ENTORNO: "produccion-sin-confirmar"}, "'pruebas' o 'produccion'"),
    ({gc.ENV_API_KEY: "corta"}, "formato"),
    ({gc.ENV_API_KEY: "clave con espacios dentro 0123456789"}, "formato"),
    ({gc.ENV_BASE_URL: "https://cosprobell-produccion.up.railway.app"}, "PRODUCCIÓN"),
    ({gc.ENV_BASE_URL: "https://api.ejemplo.test", gc.ENV_HOSTS_PRODUCCION: "otro.test, API.ejemplo.test"}, "PRODUCCIÓN"),
])
def test_configuraciones_inseguras_se_rechazan_sin_filtrar_secretos(cambios, fragmento):
    with pytest.raises(gc.ConfigGelsynError) as ex:
        gc.cargar_config(_env(**cambios))
    assert fragmento in str(ex.value)
    for secreto in (CLAVE, "secreto", "clave:", "postgres:secreto"):
        assert secreto not in str(ex.value).replace("la clave", "").replace("clave de", "")  # el mensaje no repite valores de entorno


def test_produccion_exige_confirmacion_humana_y_no_admite_hosts_de_pruebas():
    prod = {gc.ENV_ENTORNO: "produccion", gc.ENV_BASE_URL: "https://backend.cosprobell.example.com"}
    with pytest.raises(gc.ConfigGelsynError, match="autorización humana"):
        gc.cargar_config(_env(**prod))
    ok = gc.cargar_config(_env(**prod, **{gc.ENV_CONFIRMAR_PRODUCCION: gc.CONFIRMACION_PRODUCCION}))
    assert ok.entorno == "produccion"
    with pytest.raises(gc.ConfigGelsynError, match="PRUEBAS"):
        gc.cargar_config(_env(**{gc.ENV_ENTORNO: "produccion", gc.ENV_BASE_URL: "https://cosprobell-pruebas.up.railway.app",
                                 gc.ENV_CONFIRMAR_PRODUCCION: gc.CONFIRMACION_PRODUCCION}))


def test_localhost_en_http_se_admite_solo_para_desarrollo():
    assert gc.cargar_config(_env(**{gc.ENV_BASE_URL: "http://localhost:3000"})).host == "localhost"


# ------------------------------------------------------------------------------------------------ cliente de solo lectura (reutiliza el del core) y preflight
# Las pruebas siguientes se omiten sin cxc-reporting-core.


def _transporte(rutas, registro):
    from cxc_reporting.adapters.backend.client import TransportResponse

    class T:
        def request(self, method, url, headers, timeout):
            registro.append((method, url, dict(headers)))
            path = url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
            r = rutas.get("/" + path)
            if r is None:
                return TransportResponse(404, b"")
            st, cuerpo = r if isinstance(r, tuple) else (200, r)
            return TransportResponse(st, json.dumps(cuerpo).encode())
    return T()


def _cliente(rutas, registro):
    if not (ruta_reporting_core() / "cxc_reporting").is_dir():
        pytest.skip("cxc-reporting-core no esta disponible en este equipo")
    import os
    os.environ.setdefault("CXC_REPORTING_SRC", str(ruta_reporting_core()))
    gc._importar_core()                                                     # deja cxc_reporting importable para el transporte falso
    cfg = gc.cargar_config(_env())
    return gc.cliente_solo_lectura(cfg, transport=_transporte(rutas, registro), sleep=lambda s: None), cfg


def _fila(ent):
    return {c: f"SENTINELA-{c}" for c in gp.CAMPOS_REQUERIDOS[ent]}


def _rutas_ok():
    meta = {"version": "v-sintetica", "ultimaCargaCompleta": "2026-11-04T00:00:00Z", "monedas": ["LPS"], "siguienteCursor": None, "advertencias": []}
    r = {"/health": {"status": "ok"}, "/finanzas/estado": {"facturas": {"activoId": "x"}}}
    for ent in gp.CAMPOS_REQUERIDOS:
        r[f"/finanzas/{ent}"] = {"data": [_fila(ent)], "meta": dict(meta)}
    return r


def test_preflight_apto_hace_pocas_peticiones_todas_get_y_no_imprime_datos_ni_secretos():
    reg = []
    cli, cfg = _cliente(_rutas_ok(), reg)
    res = gp.preflight(cli, cfg)
    assert res["veredicto"] == "APTO_PARA_MAPEO" and res["problemas"] == [] and res["peticiones"] == 7
    assert {m for m, _, _ in reg} == {"GET"} and all("limit=1" in u for _, u, _ in reg if "/finanzas/" in u and not u.endswith("estado"))
    assert all(h.get("X-API-Key") == CLAVE for _, _, h in reg)            # cabecera documentada por Gelsyn
    salida = json.dumps(res, ensure_ascii=False)
    assert CLAVE not in salida and "SENTINELA" not in salida              # ningun valor de datos ni la clave


def test_preflight_detecta_campos_faltantes_y_cargas_incompletas():
    reg = []
    r = _rutas_ok()
    r["/finanzas/facturas"]["data"][0].pop("docDueDate")
    r["/finanzas/pagos"]["meta"]["ultimaCargaCompleta"] = None
    cli, cfg = _cliente(r, reg)
    res = gp.preflight(cli, cfg)
    assert res["veredicto"] == "NO_APTO"
    assert any("docDueDate" in p for p in res["problemas"]) and any("pagos" in p and "completa" in p for p in res["problemas"])


def test_preflight_con_forma_desconocida_es_indeterminado_y_no_inventa_un_mapeo():
    reg = []
    r = _rutas_ok()
    r["/finanzas/clientes"] = [{"cardCode": "X"}]                          # lista suelta, sin data/meta
    cli, cfg = _cliente(r, reg)
    res = gp.preflight(cli, cfg)
    assert res["veredicto"] == "INDETERMINADO" and any("clientes" in i for i in res["indeterminado"])


def test_preflight_con_credencial_rechazada_informa_sin_filtrar_la_clave():
    reg = []
    r = {k: (401, {"error": "no"}) for k in _rutas_ok()}
    cli, cfg = _cliente(r, reg)
    res = gp.preflight(cli, cfg)
    salida = json.dumps(res, ensure_ascii=False)
    assert res["veredicto"] == "NO_APTO" and CLAVE not in salida and "AdapterAuthError" in salida


def test_el_cliente_del_core_no_ofrece_verbos_de_escritura():
    reg = []
    cli, _ = _cliente(_rutas_ok(), reg)
    assert not any(hasattr(cli, n) for n in ("post", "put", "patch", "delete", "request_json"))
    from cxc_reporting.adapters.backend.client import READ_ONLY_METHODS, UrllibTransport
    assert READ_ONLY_METHODS == frozenset({"GET"})
    from cxc_reporting.adapters.backend.client import TransportError
    with pytest.raises(Exception):
        UrllibTransport().request("POST", "https://x.test", {}, 1)


def test_cli_sin_variables_falla_de_forma_segura(monkeypatch, capsys):
    for n in (gc.ENV_BASE_URL, gc.ENV_API_KEY, gc.ENV_ENTORNO):
        monkeypatch.delenv(n, raising=False)
    assert gp.main() == 2
    assert "Faltan variables" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ plantilla de variables versionada, secretos ignorados
def _git(*args):
    import subprocess
    return subprocess.run(["git", *args], capture_output=True, text=True)


def test_env_example_esta_versionado_y_no_lo_ignora_la_regla_env_asterisco():
    assert _git("ls-files", "--error-unmatch", ".env.example").returncode == 0                  # esta bajo control de versiones
    assert _git("check-ignore", "-q", ".env.example").returncode == 1                           # 1 = NO ignorado (la excepcion !.env.example funciona)
    for secreto in (".env", ".env.local", ".env.production", ".env.gelsyn", "clave.pem", "clave.key"):
        assert _git("check-ignore", "-q", secreto).returncode == 0, secreto                     # 0 = ignorado


def test_env_example_solo_contiene_nombres_de_variables_sin_valores():
    from pathlib import Path
    lineas = [x.strip() for x in Path(".env.example").read_text(encoding="utf-8").splitlines() if x.strip() and not x.lstrip().startswith("#")]
    assert lineas and all(x.endswith("=") for x in lineas), lineas                              # NOMBRE= y nada mas
    assert {x[:-1] for x in lineas} >= {gc.ENV_BASE_URL, gc.ENV_API_KEY, gc.ENV_ENTORNO}
