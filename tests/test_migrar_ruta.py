"""Migracion segura de una ruta ya integrada (nuevos clientes, zona nueva, prefijos renombrados). Datos sinteticos; nada es una ruta real."""

import copy
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from cxc_portal import admin
from cxc_portal.estado import ErrorNegocio, Estado
from cxc_portal.integrar_ruta import ConflictoIntegracion
from cxc_portal.migrar_ruta import aplicar, construir_json, planificar

ACTOR = {"id": None, "usuario": "test", "rol": "admin", "nombre": "test"}

ANTES = [("C20001", "1 MC", "MC", "SALON UNO (MACUELIZO)"), ("C20002", "1 LL", "LL", "SALON DOS (LA LABOR)"), ("C20003", "2 LL", "LL", "SALON TRES (LA LABOR)"),
         ("C20004", "1 D", "D", "SALON CUATRO (DULCE NOMBRE)"), ("C20005", "1 LE", "LE", "SALON CINCO (LA ENTRADA)"), ("C20006", "2 LE", "LE", "ALEJANDROS NAILS (LE)"),
         ("C20007", "1 GL", "GL", "RENATHA STUDIO (GRACIAS)"), ("C20008", "1 CU", "CU", "SALON OCHO (CUCUYAGUA)")]
ZONAS = {"MC": "Macuelizo, Copán", "LL": "La Labor, Ocotepeque", "D": "Dulce Nombre de Copán", "LE": "La Entrada, Copán", "GL": "Gracias, Lempira", "CU": "Cucuyagua, Copán"}


def _json(tmp, **mod):
    """JSON v3: renombra LL->LO y D->DN; agrega 23->'3 LE' despues de ALEJANDROS NAILS y la zona SJ (2 clientes) despues de RENATHA."""
    orden = [("C20001", "1 MC", "SALON UNO (MACUELIZO)"), ("C20002", "1 LO", "SALON DOS (LA LABOR)"), ("C20003", "2 LO", "SALON TRES (LA LABOR)"),
             ("C20004", "1 DN", "SALON CUATRO (DULCE NOMBRE)"), ("C20005", "1 LE", "SALON CINCO (LA ENTRADA)"), ("C20006", "2 LE", "ALEJANDROS NAILS (LE)"),
             ("C20009", "3 LE", "ANA BEAUTY (LE)"), ("C20007", "1 GL", "RENATHA STUDIO (GRACIAS)"), ("C20010", "1 SJ", "SB BONITAS (SAN JUAN)"),
             ("C20011", "2 SJ", "SB A Y M (SAN JUAN)"), ("C20008", "1 CU", "SALON OCHO (CUCUYAGUA)")]
    zonas = {"MC": "Macuelizo, Copán", "LO": "La Labor, Ocotepeque", "DN": "Dulce Nombre de Copán", "LE": "La Entrada, Copán", "GL": "Gracias, Lempira",
             "SJ": "San Juan, Intibucá", "CU": "Cucuyagua, Copán"}
    clientes = []
    for i, (cod, fol, nom) in enumerate(orden, 1):
        n, z = fol.split(" ")
        clientes.append({"physical_order": i, "folder_label": fol, "zone_prefix": z, "zone_name": zonas[z], "folder_number": int(n), "sap_code": cod, "client_name": nom})
    cuenta = {z: sum(1 for c in clientes if c["zone_prefix"] == z) for z in zonas}
    d = {"schema_version": 1, "route_name": "Occidente", "route_type": "foranea", "credit_term_days": 60, "vendor": "José Carpio", "source_file": "X.xlsx", "source_sha256": "cd" * 32,
         "route_customer_count": len(clientes), "zone_count": len(zonas), "zones": [{"prefix": z, "name": n, "customer_count": cuenta[z]} for z, n in zonas.items()],
         "zone_renames": [{"from": "LL", "to": "LO"}, {"from": "D", "to": "DN"}], "customers": clientes}
    d.update(mod)
    p = tmp / "v3.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d, p


@pytest.fixture()
def env(tmp_path):
    est = Estado(tmp_path / "estado" / "portal.sqlite3")
    est.guardar_ruta(ACTOR, "OCCIDENTE", "Occidente", "José Carpio", "FORANEA", 60, zonas=ZONAS, requiere_hojas_visita=True, implantacion="OPERATIVA",
                     clientes=[{"codigo": c, "folder": f, "zona": z, "nombre": n} for c, f, z, n in ANTES])
    est.registrar_documento(ACTOR, "OCCIDENTE", "HOJA_VISITA", "hojas.xlsx", b"PK-hojas-v1", {"hojas": [], "total_clientes": 8}, tmp_path / "estado" / "documentos")
    est.registrar_decision({"id": None, "usuario": "ger1", "rol": "gerencia", "nombre": "G"}, "C20005", "SOLO_CONTADO", "Decisión de gerencia de prueba.", route_id="OCCIDENTE")
    d, p = _json(tmp_path)
    return est, d, p, tmp_path


def _foto(est):
    with est.tx() as c:
        return {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1,2")] for t in ("clientes_ruta", "zonas", "candidatos", "folder_hwm", "documentos", "decisiones")}


def test_el_plan_describe_exactamente_los_cambios_sin_escribir(env):
    est, d, p, _ = env
    antes = _foto(est)
    plan = planificar(est, p)
    assert [(z["de"], z["a"]) for z in plan["zonas_renombradas"]] == [("LL", "LO"), ("D", "DN")] and [z["prefijo"] for z in plan["zonas_nuevas"]] == ["SJ"]
    assert [(n["codigo"], n["folder"], n["posicion"], n["despues_de"]) for n in plan["clientes_nuevos"]] == [
        ("C20009", "3 LE", 7, "ALEJANDROS NAILS (LE)"), ("C20010", "1 SJ", 9, "RENATHA STUDIO (GRACIAS)"), ("C20011", "2 SJ", 10, "SB BONITAS (SAN JUAN)")]
    assert [(r["antes"], r["despues"]) for r in plan["folders_renombrados"]] == [("1 LL", "1 LO"), ("2 LL", "2 LO"), ("1 D", "1 DN")]
    assert plan["sin_cambio"] == 5 and plan["documento_hojas_visita"]["requiere_nueva_version"] is True and _foto(est) == antes


def test_aplicar_integra_orden_zonas_folders_y_conserva_documentos_decisiones_y_etiquetas_retiradas(env):
    est, d, p, _ = env
    doc_antes, dec_antes = _foto(est)["documentos"], _foto(est)["decisiones"]
    r = aplicar(est, ACTOR, p)
    assert r["aplicado"]
    ruta = est.obtener_ruta("OCCIDENTE")
    assert [(c["codigo"], c["folder"], c["zona"], c["orden"]) for c in ruta["clientes"]] == [(c["sap_code"], c["folder_label"], c["zone_prefix"], c["physical_order"]) for c in d["customers"]]
    assert {z["prefijo"] for z in ruta["zonas"]} == {"MC", "LO", "DN", "LE", "GL", "SJ", "CU"}
    h = est.folders_usados("OCCIDENTE")
    assert h["LL"] == 2 and h["D"] == 1 and h["LO"] == 2 and h["DN"] == 1 and h["SJ"] == 2 and h["LE"] == 3      # los prefijos retirados conservan su marca de agua
    f = _foto(est)
    assert f["documentos"] == doc_antes and f["decisiones"] == dec_antes                                         # v1 y decisiones intactas
    cand = {c["codigo"]: c for c in est.listar_candidatos("OCCIDENTE")}
    assert all(cand[k]["estado"] == "confirmada" and cand[k]["folder_asignado"] for k in ("C20009", "C20010", "C20011"))
    assert any(a["accion"] == "ruta.migrar_organizacion" for a in est.listar_auditoria(20))
    with pytest.raises(ErrorNegocio):                                                                             # un folder retirado no se puede reutilizar
        est.agregar_cliente(ACTOR, "OCCIDENTE", "C20099", "NUEVO", "LL")


def test_es_idempotente(env):
    est, d, p, _ = env
    aplicar(est, ACTOR, p)
    antes = _foto(est)
    r = aplicar(est, ACTOR, p)
    assert r.get("sin_cambios") and _foto(est) == antes and planificar(est, p)["clientes_nuevos"] == []


@pytest.mark.parametrize("cambio", ["borrar_cliente", "cambiar_nombre", "cambiar_codigo_folder", "nombre_zona", "orden_relativo", "cliente_otra_ruta", "cliente_rechazado",
                                    "renombre_con_otro_numero", "vendedor", "folder_nuevo_en_uso", "archivo_inconsistente"])
def test_conflictos_se_detienen_sin_escribir(env, cambio):
    est, d, p, tmp = env
    x = copy.deepcopy(d)
    if cambio == "borrar_cliente":
        x["customers"] = [c for c in x["customers"] if c["sap_code"] != "C20008"]
        for i, c in enumerate(x["customers"], 1):
            c["physical_order"] = i
        x["route_customer_count"] = len(x["customers"])
        x["zones"] = [z for z in x["zones"] if z["prefix"] != "CU"]
        x["zone_count"] = len(x["zones"])
    elif cambio == "cambiar_nombre":
        x["customers"][0]["client_name"] = "OTRO NOMBRE"
    elif cambio == "cambiar_codigo_folder":
        next(c for c in x["customers"] if c["sap_code"] == "C20005").update(folder_label="9 LE", folder_number=9)
    elif cambio == "nombre_zona":
        for z in x["zones"]:
            z["name"] = "Otro nombre" if z["prefix"] == "MC" else z["name"]
        for c in x["customers"]:
            c["zone_name"] = "Otro nombre" if c["zone_prefix"] == "MC" else c["zone_name"]
    elif cambio == "orden_relativo":
        x["customers"][0], x["customers"][4] = x["customers"][4], x["customers"][0]
        for i, c in enumerate(x["customers"], 1):
            c["physical_order"] = i
    elif cambio == "cliente_otra_ruta":
        est.guardar_ruta(ACTOR, "OTRA", "Otra", "Otro", "LOCAL", None, clientes=[{"codigo": "C20010", "folder": "1 ZZ", "nombre": "X"}])
    elif cambio == "cliente_rechazado":
        est.proponer_candidatos(ACTOR, "OCCIDENTE", [{"codigo": "C20009", "nombre": "ANA BEAUTY (LE)"}])
        est.rechazar_candidato(ACTOR, "OCCIDENTE", est.listar_candidatos("OCCIDENTE", "propuesta")[0]["id"], "No pertenece")
    elif cambio == "renombre_con_otro_numero":
        next(c for c in x["customers"] if c["sap_code"] == "C20002").update(folder_label="5 LO", folder_number=5)
    elif cambio == "vendedor":
        x["vendor"] = "Otro Vendedor"
    elif cambio == "folder_nuevo_en_uso":
        next(c for c in x["customers"] if c["sap_code"] == "C20009").update(folder_label="2 LE", folder_number=2)
    elif cambio == "archivo_inconsistente":
        x["customers"][1]["physical_order"] = 99
    q = tmp / "malo.json"
    q.write_text(json.dumps(x, ensure_ascii=False), encoding="utf-8")
    antes = _foto(est)
    with pytest.raises(ConflictoIntegracion):
        aplicar(est, ACTOR, q)
    assert _foto(est) == antes


def test_construye_el_json_desde_el_excel_operativo_y_verifica_el_resultado(tmp_path):
    import openpyxl
    base = {"schema_version": 1, "route_name": "Occidente", "route_type": "foranea", "credit_term_days": 60, "vendor": "José Carpio", "scope": "x",
            "zones": [{"prefix": "LL", "name": "La Labor, Ocotepeque", "customer_count": 1}, {"prefix": "LE", "name": "La Entrada, Copán", "customer_count": 1}], "customers": [], "import_policy": {}}
    (tmp_path / "base.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hoja1"
    ws.append(["DISTRIBUCIONES COSPROBELL"])
    ws.append(["#", "Codigo", "NOMBRE DEL CLIENTE ", "Saldo"])
    ws.append(["1 LO", "C20002", "SALON DOS", 0])
    ws.append(["1 LE", "C20005", "SALON CINCO", 0])
    ws.append(["1 SJ", "C20010", "SB BONITAS", 0])
    ws.append([None, None, "TOTAL", 0])
    wb.save(tmp_path / "ruta.xlsx")
    d = construir_json(tmp_path / "ruta.xlsx", tmp_path / "base.json", {"LL": "LO"}, {"SJ": "San Juan, Intibucá"})
    assert d["route_customer_count"] == 3 and [z["prefix"] for z in d["zones"]] == ["LO", "LE", "SJ"] and d["zone_renames"] == [{"from": "LL", "to": "LO"}]
    assert [c["folder_label"] for c in d["customers"]] == ["1 LO", "1 LE", "1 SJ"] and len(d["source_sha256"]) == 64
    with pytest.raises(ErrorNegocio, match="zona desconocida"):
        construir_json(tmp_path / "ruta.xlsx", tmp_path / "base.json", {"LL": "LO"}, {})


def test_cli_ensayo_en_solo_lectura_no_toca_la_bd_y_aplicar_exige_respaldo_verificado(env, capsys):
    est, d, p, tmp = env
    bd = tmp / "estado" / "portal.sqlite3"
    est_dir = str(tmp / "estado")
    antes = bd.read_bytes()
    assert admin.main(["migrar-ruta", "--estado", est_dir, "--ruta", "OCCIDENTE", "--archivo", str(p)]) == 0
    assert "clientes_nuevos" in capsys.readouterr().out and bd.read_bytes() == antes                                     # ensayo: ni un byte cambia
    with pytest.raises(SystemExit, match="respaldo verificado"):
        admin.main(["migrar-ruta", "--estado", est_dir, "--ruta", "OCCIDENTE", "--archivo", str(p), "--aplicar"])
    assert admin.main(["respaldar", "--estado", est_dir, "--destino", str(tmp / "bk")]) == 0
    respaldo = next((tmp / "bk").glob("portal-*.sqlite3"))
    with pytest.raises(SystemExit, match="suma SHA-256"):                                                                # respaldo alterado
        copia = tmp / "bk" / "alterado.sqlite3"
        shutil.copy2(respaldo, copia)
        (tmp / "bk" / "alterado.sqlite3.sha256").write_text("0" * 64 + "  alterado.sqlite3\n")
        copia.write_bytes(copia.read_bytes() + b"x")
        admin.main(["migrar-ruta", "--estado", est_dir, "--ruta", "OCCIDENTE", "--archivo", str(p), "--aplicar", "--respaldo", str(copia)])
    assert admin.main(["migrar-ruta", "--estado", est_dir, "--ruta", "OCCIDENTE", "--archivo", str(p), "--aplicar", "--respaldo", str(respaldo)]) == 0
    assert len(Estado(bd).obtener_ruta("OCCIDENTE")["clientes"]) == 11


def test_respaldar_en_solo_lectura_no_migra_la_bd(tmp_path):
    ruta = tmp_path / "vieja.sqlite3"
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE rutas (route_id TEXT)")
    con.commit()
    con.close()
    antes = ruta.read_bytes()
    Estado(ruta, solo_lectura=True).respaldar(tmp_path / "bk")
    assert ruta.read_bytes() == antes and next((tmp_path / "bk").glob("*.sqlite3")).is_file()                            # no se creo ninguna tabla nueva
    with pytest.raises(ErrorNegocio):
        Estado(tmp_path / "no_existe.sqlite3", solo_lectura=True)


def test_verificador_de_solo_lectura_distingue_antes_y_despues_de_migrar(env):
    from cxc_portal.verificar_migracion import verificar
    est, d, p, tmp = env
    solo = lambda: Estado(tmp / "estado" / "portal.sqlite3", solo_lectura=True)
    antes = verificar(solo(), "OCCIDENTE", p, salida=lambda _: None)
    assert any("folders, zonas y orden" in f for f in antes["fallas"])
    aplicar(est, ACTOR, p)
    despues = verificar(solo(), "OCCIDENTE", p, salida=lambda _: None)
    # en este escenario sintetico la ruta esta OPERATIVA y tiene una decision: son las UNICAS fallas esperadas (las de estructura, folders y hashes pasan)
    assert all(f.startswith(("ruta NO operativa", "sin decisiones")) for f in despues["fallas"]) and any("prefijo LL retirado" in o for o in despues["ok"])
    assert any("folders, zonas y orden" in o for o in despues["ok"])


def _xlsx_hojas(path, filas_por_hoja):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i, filas in enumerate(filas_por_hoja, 1):
        ws = wb.create_sheet(f"Hoja {i:02d}")
        ws.append(["DISTRIBUCIONES COSPROBELL"])
        ws.append(["RUTA DE PRUEBA"])
        ws.append(["FECHA DE SALIDA: ____"])
        ws.append(["COLOR", "CÓDIGO SAP", "SALÓN / CLIENTE", "ENCARGADA", "COBRO", "VENTA", "OBS", "FIRMA"])
        for f in filas:
            ws.append(list(f))
        ws.append(["RESUMEN DE SALIDA: VISITADOS ___"])
    wb.save(path)


def test_el_lector_de_hojas_de_visita_no_se_corta_con_el_color_rojo_ni_con_colores_nuevos(tmp_path):
    """Regresion: 'ROJO' no estaba en la lista de colores y el lector terminaba en silencio en el primer cliente rojo (119 -> 23 clientes)."""
    from cxc_portal.hojas_visita_excel import leer_hojas_visita
    _xlsx_hojas(tmp_path / "h.xlsx", [[("VERDE", "C20001", "UNO"), ("ROJO", "C20002", "DOS"), ("AMARILLA", "C20003", "TRES")], [("MORADO", "C20004", "CUATRO"), ("AZUL", "C20005", "CINCO")]])
    d = leer_hojas_visita(tmp_path / "h.xlsx")
    assert d["total_clientes"] == 5 and [c["codigo"] for h in d["hojas"] for c in h["clientes"]] == ["C20001", "C20002", "C20003", "C20004", "C20005"]
    assert d["colores"]["ROJO"] == 1 and any("MORADO" in a for a in d["advertencias"])                                    # color desconocido: se conserva y se advierte


def test_importar_documento_compara_con_la_ruta_y_advierte_si_no_coincide(env):
    from cxc_portal.store import PortalServicio
    est, d, p, tmp = env
    datos = tmp / "datos" / "01_Rutas"
    datos.mkdir(parents=True)
    _xlsx_hojas(datos / "v2.xlsx", [[("VERDE", "C20001", "UNO"), ("ROJO", "C20002", "DOS"), ("VERDE", "C99999", "AJENO")]])
    sv = PortalServicio(est, None, tmp / "estado", None, tmp / "datos")
    doc = sv.importar_documento(ACTOR, "OCCIDENTE", "v2.xlsx", "Documento de prueba con diferencias.", nueva_version=True, confirmado=True)
    adv = " | ".join(doc["contenido"]["advertencias"])
    assert "NO están en la ruta" in adv and "C99999" in adv and "NO están en el documento" in adv and "trae 3 cliente(s) y la ruta 8" in adv
