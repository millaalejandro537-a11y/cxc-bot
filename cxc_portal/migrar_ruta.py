"""Actualizacion SEGURA de una ruta ya integrada cuando cambia su organizacion fisica aprobada (nuevos clientes, zonas nuevas, prefijos renombrados).

Complementa `integrar_ruta` (que solo admite una ruta virgen y se detiene ante cualquier diferencia): aqui las diferencias se aceptan UNICAMENTE si son de
estos tipos declarados y verificables; todo lo demas es un conflicto y no se escribe nada:
  * zona renombrada (`zone_renames`): todos sus clientes conservan codigo y numero y solo cambia el prefijo (p. ej. `1 LL` -> `1 LO`);
  * zona nueva (con al menos un cliente nuevo);
  * cliente nuevo (codigo que no estaba en la ruta) en una posicion fisica concreta, que no existia en otra ruta ni fue rechazado;
  * reordenamiento SOLO como consecuencia de esas inserciones (el orden relativo de los clientes existentes no cambia).
Nunca se borra un cliente, nunca se cambia un codigo ni un nombre, nunca se reutiliza una etiqueta de folder (los prefijos retirados conservan su marca de
agua), y no se tocan documentos, decisiones, clasificaciones ni saldos. Todo ocurre en UNA transaccion y queda auditado.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .estado import ErrorNegocio, Estado
from .integrar_ruta import ConflictoIntegracion, _norm, validar_archivo


def construir_json(xlsx: Path, base_json: Path, renombres: Dict[str, str], zonas_nuevas: Dict[str, str], vendedor: Optional[str] = None) -> dict:
    """Arma el JSON de organizacion fisica (mismo esquema que el de importacion) desde el Excel operativo (Hoja1: '#', 'Codigo', 'NOMBRE DEL CLIENTE ').
    Los nombres de zona de los prefijos existentes salen del JSON anterior (con los renombres aplicados); los de zonas nuevas se declaran explicitamente."""
    import hashlib

    import openpyxl
    previo = json.loads(Path(base_json).read_text(encoding="utf-8"))
    ws = openpyxl.load_workbook(xlsx, data_only=True)["Hoja1"]
    cab = next(r for r in range(1, 20) if str(ws.cell(r, 1).value).strip() == "#")
    filas = []
    for r in range(cab + 1, ws.max_row + 1):
        f, c, n = ws.cell(r, 1).value, ws.cell(r, 2).value, ws.cell(r, 3).value
        if isinstance(c, str) and re.fullmatch(r"C\d{3,8}", c.strip()) and isinstance(f, str):
            filas.append((f.strip(), c.strip().upper(), " ".join(str(n or "").split())))
    nombres_zona = {z["prefix"]: z["name"] for z in previo["zones"]}
    for viejo, nuevo in renombres.items():
        nombres_zona[nuevo] = nombres_zona.pop(viejo)
    nombres_zona.update(zonas_nuevas)
    clientes, cuenta = [], {}
    for i, (folder, cod, nombre) in enumerate(filas, 1):
        m = re.fullmatch(r"(\d+) ([A-Z]{1,4})", folder)
        if not m or m.group(2) not in nombres_zona:
            raise ErrorNegocio(f"Folder o zona desconocida en la fila {i}: '{folder}'. Declare la zona en zonas_nuevas.")
        z = m.group(2)
        cuenta[z] = cuenta.get(z, 0) + 1
        clientes.append({"physical_order": i, "folder_label": folder, "zone_prefix": z, "zone_name": nombres_zona[z], "folder_number": int(m.group(1)), "sap_code": cod, "client_name": nombre})
    usadas = list(dict.fromkeys(c["zone_prefix"] for c in clientes))   # orden de primera aparicion fisica
    return {**{k: previo[k] for k in ("schema_version", "route_name", "route_type", "credit_term_days") if k in previo}, "scope": previo.get("scope", ""),
            "vendor": vendedor or previo["vendor"], "source_file": Path(xlsx).name, "source_sha256": hashlib.sha256(Path(xlsx).read_bytes()).hexdigest(),
            "approved_basis": "Versión vigente aprobada (119 clientes): incorpora 3 clientes nuevos y renombra prefijos de zona.",
            "route_customer_count": len(clientes), "zone_count": len(usadas), "zones": [{"prefix": z, "name": nombres_zona[z], "customer_count": cuenta[z]} for z in usadas],
            "zone_renames": [{"from": a, "to": b} for a, b in renombres.items()], "customers": clientes, "import_policy": previo.get("import_policy", {})}


def planificar(estado: Estado, archivo: Path, route_id: str = "OCCIDENTE") -> dict:
    """Compara la BD con el archivo y devuelve el PLAN (o lanza ConflictoIntegracion). No escribe nada."""
    d = json.loads(Path(archivo).read_text(encoding="utf-8"))
    conflictos = [f"archivo: {x}" for x in validar_archivo(d)]
    cl, zs = d["customers"], d["zones"]
    renombres = {r["from"]: r["to"] for r in d.get("zone_renames", [])}
    with estado.tx() as c:
        r = c.execute("SELECT r.*, v.nombre AS vendedor FROM rutas r JOIN vendedores v ON v.id = r.vendedor_id WHERE route_id = ?", (route_id,)).fetchone()
        if not r:
            raise ErrorNegocio(f"La ruta {route_id} no existe en la BD.", 404)
        for k, esp in (("vendedor", d["vendor"]), ("nombre", d["route_name"])):
            if _norm(r[k]) != _norm(esp):
                conflictos.append(f"{k} de la ruta: BD '{r[k]}' vs archivo '{esp}'")
        if (r["tipo_ruta"], r["plazo_dias"]) != ("FORANEA", 60):
            conflictos.append(f"tipo/plazo de la ruta: BD {r['tipo_ruta']}/{r['plazo_dias']} vs archivo foránea/60")
        bd_cli = {x["codigo"]: dict(x) for x in c.execute("SELECT * FROM clientes_ruta WHERE route_id = ? ORDER BY orden", (route_id,))}
        bd_zon = {x["prefijo"]: x["nombre"] for x in c.execute("SELECT prefijo, nombre FROM zonas WHERE route_id = ?", (route_id,))}
        cand = {x["codigo"]: dict(x) for x in c.execute("SELECT * FROM candidatos WHERE route_id = ?", (route_id,))}
        json_zon = {z["prefix"]: z["name"] for z in zs}
        # zonas
        zonas_renombradas, zonas_nuevas = [], []
        ya_aplicados = {v: n for v, n in renombres.items() if v not in bd_zon and n in bd_zon}   # renombre ya hecho: la repeticion no cambia nada
        for viejo, nuevo in renombres.items():
            if viejo in ya_aplicados:
                if _norm(bd_zon[nuevo]) != _norm(json_zon.get(nuevo, "")):
                    conflictos.append(f"nombre de la zona {nuevo}: BD '{bd_zon[nuevo]}' vs archivo '{json_zon.get(nuevo)}'")
                continue
            if viejo not in bd_zon:
                conflictos.append(f"renombre {viejo}→{nuevo}: la zona {viejo} no existe en la BD")
            if nuevo in bd_zon:
                conflictos.append(f"renombre {viejo}→{nuevo}: el prefijo {nuevo} ya existe en la BD")
            zonas_renombradas.append({"de": viejo, "a": nuevo, "nombre": json_zon.get(nuevo)})
        esperado = {renombres.get(p, p): n for p, n in bd_zon.items()}
        for p, n in bd_zon.items():
            nuevo_p = renombres.get(p, p)
            if nuevo_p not in json_zon:
                conflictos.append(f"la zona {p} de la BD no está en el archivo")
            elif p not in renombres and _norm(json_zon[nuevo_p]) != _norm(n):
                conflictos.append(f"nombre de la zona {p}: BD '{n}' vs archivo '{json_zon[nuevo_p]}'")
        for p, n in json_zon.items():
            if p not in esperado:
                zonas_nuevas.append({"prefijo": p, "nombre": n})
        # clientes
        json_cod = {x["sap_code"]: x for x in cl}
        for cod in sorted(set(bd_cli) - set(json_cod)):
            conflictos.append(f"{cod} está en la ruta (BD) pero no en el archivo: no se borran clientes")
        nuevos, renombrados, sin_cambio = [], [], 0
        for x in cl:
            cod = x["sap_code"]
            otra = c.execute("SELECT route_id FROM clientes_ruta WHERE codigo = ? AND route_id <> ?", (cod, route_id)).fetchone()
            if otra:
                conflictos.append(f"{cod} ya pertenece a la ruta {otra['route_id']}")
            if cod in bd_cli:
                b = bd_cli[cod]
                if _norm(b["nombre"]) != _norm(x["client_name"]):
                    conflictos.append(f"{cod}: nombre BD '{b['nombre']}' vs archivo '{x['client_name']}'")
                m = re.fullmatch(r"(\d+) ([A-Z]{1,4})", b["folder"])
                esp = f"{m.group(1)} {renombres.get(m.group(2), m.group(2))}" if m else None
                if esp != x["folder_label"]:
                    conflictos.append(f"{cod}: folder BD {b['folder']} (esperado tras renombres: {esp}) vs archivo {x['folder_label']}")
                elif b["folder"] != x["folder_label"]:
                    renombrados.append({"codigo": cod, "antes": b["folder"], "despues": x["folder_label"]})
                else:
                    sin_cambio += 1
            else:
                k = cand.get(cod)
                if k and k["estado"] == "rechazada":
                    conflictos.append(f"{cod} fue RECHAZADO en la BD ({k['motivo_rechazo']})")
                nuevos.append({"codigo": cod, "folder": x["folder_label"], "nombre": x["client_name"], "posicion": x["physical_order"],
                               "despues_de": cl[x["physical_order"] - 2]["client_name"] if x["physical_order"] > 1 else None})
        # el orden relativo de los clientes existentes no cambia
        previo = [cod for cod in bd_cli]
        ahora = [x["sap_code"] for x in cl if x["sap_code"] in bd_cli]
        if ahora != previo:
            conflictos.append("el orden relativo de los clientes existentes cambia: solo se permiten inserciones")
        # un folder nuevo no puede reutilizar una etiqueta ya usada en una zona que NO se renombra
        usados = {x["folder"] for x in bd_cli.values()}
        for x in nuevos:
            if x["folder"] in usados:
                conflictos.append(f"el folder {x['folder']} del cliente nuevo {x['codigo']} ya está en uso")
        hwm = {x["zona"]: x["maximo"] for x in c.execute("SELECT zona, maximo FROM folder_hwm WHERE route_id = ?", (route_id,))}
        for x in nuevos:
            m = re.fullmatch(r"(\d+) ([A-Z]{1,4})", x["folder"])
            if m and m.group(2) in hwm and int(m.group(1)) <= hwm[m.group(2)] and m.group(2) not in renombres.values():
                conflictos.append(f"el folder {x['folder']} ya fue usado antes ({m.group(2)} llegó a {hwm[m.group(2)]}) y no se reutiliza")
        doc = c.execute("SELECT id, version, contenido_json FROM documentos WHERE route_id = ? AND tipo = 'HOJA_VISITA' AND estado = 'VIGENTE'", (route_id,)).fetchone()
        doc_clientes = json.loads(doc["contenido_json"]).get("total_clientes") if doc else None
    if conflictos:
        raise ConflictoIntegracion(conflictos)
    return {"ruta": route_id, "clientes_archivo": len(cl), "clientes_bd": len(bd_cli), "zonas_renombradas": zonas_renombradas, "zonas_nuevas": zonas_nuevas,
            "clientes_nuevos": nuevos, "folders_renombrados": renombrados, "sin_cambio": sin_cambio, "etiquetas_retiradas": [r["antes"] for r in renombrados],
            "documento_hojas_visita": {"version": doc["version"] if doc else None, "clientes": doc_clientes, "requiere_nueva_version": bool(doc and doc_clientes != len(cl))},
            "archivo_sha256": d.get("source_sha256"), "aplicado": False}


def aplicar(estado: Estado, actor: dict, archivo: Path, route_id: str = "OCCIDENTE") -> dict:
    """Valida el plan y lo ejecuta en UNA transaccion. Idempotente: si la BD ya coincide con el archivo, no cambia nada."""
    plan = planificar(estado, archivo, route_id)
    d = json.loads(Path(archivo).read_text(encoding="utf-8"))
    cl = d["customers"]
    renombres = {r["from"]: r["to"] for r in d.get("zone_renames", [])}
    if not (plan["zonas_renombradas"] or plan["zonas_nuevas"] or plan["clientes_nuevos"] or plan["folders_renombrados"]):
        orden_ok = [x["codigo"] for x in estado.obtener_ruta(route_id)["clientes"]] == [x["sap_code"] for x in cl]
        if orden_ok:
            return {**plan, "aplicado": False, "sin_cambios": True}
    with estado.tx() as c:
        t = estado._t()
        json_zon = {z["prefix"]: z["name"] for z in d["zones"]}
        for r in plan["zonas_renombradas"]:   # el prefijo viejo queda RETIRADO: su marca de agua en folder_hwm se conserva para siempre
            c.execute("DELETE FROM zonas WHERE route_id = ? AND prefijo = ?", (route_id, r["de"]))
            c.execute("INSERT INTO zonas(route_id, prefijo, nombre) VALUES (?,?,?)", (route_id, r["a"], json_zon[r["a"]]))
        for z in plan["zonas_nuevas"]:
            c.execute("INSERT INTO zonas(route_id, prefijo, nombre) VALUES (?,?,?)", (route_id, z["prefijo"], z["nombre"]))
        for r in plan["folders_renombrados"]:
            nuevo = r["despues"]
            zona = nuevo.split(" ", 1)[1]
            c.execute("UPDATE clientes_ruta SET folder = ?, zona = ? WHERE route_id = ? AND codigo = ?", (nuevo, zona, route_id, r["codigo"]))
            c.execute("UPDATE candidatos SET folder_asignado = ?, zona_sugerida = ? WHERE route_id = ? AND codigo = ?", (nuevo, zona, route_id, r["codigo"]))
        for x in cl:   # los nuevos entran con orden provisional alto para no chocar; el orden definitivo se fija abajo
            if x["sap_code"] in {n["codigo"] for n in plan["clientes_nuevos"]}:
                c.execute("INSERT INTO clientes_ruta(route_id, codigo, folder, zona, nombre, orden) VALUES (?,?,?,?,?,?)",
                          (route_id, x["sap_code"], x["folder_label"], x["zone_prefix"], x["client_name"], 100000 + x["physical_order"]))
                if c.execute("SELECT 1 FROM candidatos WHERE route_id = ? AND codigo = ?", (route_id, x["sap_code"])).fetchone():
                    c.execute("UPDATE candidatos SET estado='confirmada', resuelto_por=?, resuelto_en=?, folder_asignado=?, zona_sugerida=? WHERE route_id=? AND codigo=? AND estado='propuesta'",
                              (actor.get("usuario"), t, x["folder_label"], x["zone_prefix"], route_id, x["sap_code"]))
                else:
                    c.execute("INSERT INTO candidatos(route_id, codigo, nombre, zona_sugerida, origen, estado, propuesto_por, propuesto_en, resuelto_por, resuelto_en, folder_asignado) "
                              "VALUES (?,?,?,?,?,'confirmada',?,?,?,?,?)", (route_id, x["sap_code"], x["client_name"], x["zone_prefix"], "migración (organización física aprobada)",
                                                                           actor.get("usuario"), t, actor.get("usuario"), t, x["folder_label"]))
        for x in cl:
            c.execute("UPDATE clientes_ruta SET orden = ? WHERE route_id = ? AND codigo = ?", (x["physical_order"], route_id, x["sap_code"]))
        for x in cl:
            estado._hwm_registrar(c, route_id, x["folder_label"])
        estado.auditar(c, actor, "ruta.migrar_organizacion", "ruta", route_id,
                       {"archivo": d.get("source_file"), "sha256_origen": d.get("source_sha256"), "clientes": len(cl), "nuevos": [n["codigo"] for n in plan["clientes_nuevos"]],
                        "renombres_zona": renombres, "folders_renombrados": plan["folders_renombrados"], "etiquetas_retiradas": plan["etiquetas_retiradas"]})
    plan["aplicado"] = True
    return plan
