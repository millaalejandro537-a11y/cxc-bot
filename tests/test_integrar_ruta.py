"""Integracion de una ruta con organizacion fisica aprobada: solo pertenencia (zona/folder/orden), sin tocar documentos ni clasificaciones; conflictos sin escritura."""

import copy
import json

import pytest

from cxc_portal.estado import Estado
from cxc_portal.integrar_ruta import ConflictoIntegracion, integrar, validar_archivo

ACTOR = {"id": None, "usuario": "test", "rol": "admin", "nombre": "test"}

ZONAS = [("AA", "Zona A", 2), ("BB", "Zona B", 1), ("D", "Zona D", 1)]
CLIENTES = [("C10001", "AA", 1, "SALON UNO"), ("C10002", "BB", 1, "SALON DOS"), ("C10003", "AA", 2, "SALON TRES"), ("C10004", "D", 1, "SALON CUATRO")]


def _json(tmp_path):
    d = {"schema_version": 1, "route_name": "Occidente", "route_type": "foranea", "credit_term_days": 60, "vendor": "José Carpio",
         "source_file": "X.xlsx", "source_sha256": "ab" * 32, "route_customer_count": 4, "zone_count": 3,
         "zones": [{"prefix": p, "name": n, "customer_count": k} for p, n, k in ZONAS],
         "customers": [{"physical_order": i, "folder_label": f"{n} {z}", "zone_prefix": z, "zone_name": dict((p, nom) for p, nom, _ in ZONAS)[z], "folder_number": n,
                        "sap_code": cod, "client_name": nom} for i, (cod, z, n, nom) in enumerate(CLIENTES, 1)]}
    p = tmp_path / "ruta.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d, p


@pytest.fixture()
def env(tmp_path):
    est = Estado(tmp_path / "e" / "portal.sqlite3")
    est.guardar_ruta(ACTOR, "OCCIDENTE", "Occidente", "José Carpio", "FORANEA", 60, requiere_hojas_visita=True, implantacion="PENDIENTE_INFORMACION")
    est.proponer_candidatos(ACTOR, "OCCIDENTE", [{"codigo": c, "nombre": n} for c, _, _, n in CLIENTES], origen="documento v1")
    d, p = _json(tmp_path)
    return est, d, p, tmp_path


def _foto(est):
    with est.tx() as c:
        return {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1,2")] for t in ("clientes_ruta", "zonas", "candidatos", "folder_hwm", "documentos", "decisiones")}


def test_simulacion_no_escribe_y_aplicar_integra_orden_zonas_y_folders(env):
    est, d, p, _ = env
    antes = _foto(est)
    inf = integrar(est, ACTOR, p)
    assert inf["clientes_nuevos"] == 4 and inf["aplicado"] is False and _foto(est) == antes
    inf = integrar(est, ACTOR, p, aplicar=True)
    assert inf["aplicado"] is True
    r = est.obtener_ruta("OCCIDENTE")
    assert [(c["codigo"], c["folder"], c["zona"], c["orden"]) for c in r["clientes"]] == [(c["sap_code"], c["folder_label"], c["zone_prefix"], c["physical_order"]) for c in d["customers"]]
    assert {z["prefijo"]: z["nombre"] for z in r["zonas"]} == {p_: n for p_, n, _ in ZONAS}
    assert (r["tipo_ruta"], r["plazo_dias"], r["implantacion"]) == ("FORANEA", 60, "EN_CONFIRMACION")   # implantacion = decision humana aparte
    assert all(k["estado"] == "confirmada" for k in est.listar_candidatos("OCCIDENTE"))
    assert est.folders_usados("OCCIDENTE") == {"AA": 2, "BB": 1, "D": 1}
    assert est.decisiones_vigentes() == {}                                                                      # ninguna clasificacion aprobada
    assert est.agregar_cliente(ACTOR, "OCCIDENTE", "C10009", "NUEVO", "AA")["folder"] == "3 AA"                  # el hueco no se reutiliza


def test_es_idempotente(env):
    est, _, p, _ = env
    integrar(est, ACTOR, p, aplicar=True)
    antes = _foto(est)
    inf = integrar(est, ACTOR, p, aplicar=True)
    assert inf["clientes_nuevos"] == 0 and inf["ya_integrados"] == 4 and _foto(est) == antes


@pytest.mark.parametrize("cambio", ["nombre", "rechazado", "otra_ruta", "folder_distinto", "vendedor", "sobrante"])
def test_conflicto_se_detiene_sin_escribir(env, cambio):
    est, d, p, _ = env
    if cambio == "nombre":
        with est.tx() as c:
            c.execute("UPDATE candidatos SET nombre='OTRO NOMBRE' WHERE codigo='C10002'")
    elif cambio == "rechazado":
        cid = est.listar_candidatos("OCCIDENTE")[0]["id"]
        est.rechazar_candidato(ACTOR, "OCCIDENTE", cid, "no pertenece")
    elif cambio == "otra_ruta":
        est.guardar_ruta(ACTOR, "OTRA", "Otra", "Otro", "LOCAL", None, clientes=[{"codigo": "C10003", "folder": "1 ZZ", "nombre": "X"}])
    elif cambio == "folder_distinto":
        est.guardar_ruta(ACTOR, "OCCIDENTE", "Occidente", "José Carpio", "FORANEA", 60, clientes=[{"codigo": "C10001", "folder": "7 AA", "zona": "AA", "nombre": "SALON UNO"}])
    elif cambio == "vendedor":
        est.guardar_ruta(ACTOR, "OCCIDENTE", "Occidente", "Otro Vendedor", "FORANEA", 60)
    elif cambio == "sobrante":
        est.guardar_ruta(ACTOR, "OCCIDENTE", "Occidente", "José Carpio", "FORANEA", 60, clientes=[{"codigo": "C99999", "folder": "1 QQ", "nombre": "AJENO"}])
    antes = _foto(est)
    with pytest.raises(ConflictoIntegracion):
        integrar(est, ACTOR, p, aplicar=True)
    assert _foto(est) == antes


def test_archivo_inconsistente_se_rechaza(env):
    est, d, p, tmp = env
    for mut in (lambda x: x["customers"].append(copy.deepcopy(x["customers"][0])),
                lambda x: x["customers"][1].update(physical_order=9),
                lambda x: x["customers"][0].update(folder_label="1 BB"),
                lambda x: x.update(credit_term_days=30)):
        x = copy.deepcopy(d)
        mut(x)
        assert validar_archivo(x)
    x = copy.deepcopy(d)
    x["customers"][0]["folder_label"] = "5 AA"
    q = tmp / "malo.json"
    q.write_text(json.dumps(x, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ConflictoIntegracion):
        integrar(est, ACTOR, q, aplicar=True)
    assert est.obtener_ruta("OCCIDENTE")["clientes"] == []
