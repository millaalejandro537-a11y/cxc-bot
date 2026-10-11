"""Integracion de una ruta con ORGANIZACION FISICA APROBADA (JSON del Excel operativo) a la BD propia del portal.

Alcance deliberadamente estrecho: confirma SOLO la pertenencia a la ruta (cliente, zona, folder, orden fisico) respaldada por el archivo.
NO toca: documentos/hojas de visita (v1 se conserva tal cual), decisiones ni clasificaciones de color, saldos, SAP/BLC, ni el estado
de implantacion. Ante cualquier contradiccion con lo ya registrado se detiene (ConflictoIntegracion) sin escribir nada.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from .estado import ErrorNegocio, Estado

_FOLDER = re.compile(r"^(\d+) ([A-Z]{1,4})$")


class ConflictoIntegracion(ErrorNegocio):
    def __init__(self, conflictos: List[str]):
        super().__init__("Conflictos con el archivo aprobado (no se escribió nada): " + " | ".join(conflictos[:10])
                         + (f" … (+{len(conflictos) - 10})" if len(conflictos) > 10 else ""), 409)
        self.conflictos = conflictos


def _norm(s: str) -> str:
    return " ".join((s or "").upper().split())


def validar_archivo(d: dict) -> List[str]:
    """Consistencia interna del JSON (no mira la BD)."""
    e: List[str] = []
    cl, zs = d.get("customers") or [], d.get("zones") or []
    if d.get("schema_version") != 1:
        e.append("schema_version distinto de 1")
    if d.get("route_type") != "foranea" or d.get("credit_term_days") != 60:
        e.append("el archivo no declara ruta foránea de 60 días")
    if len(cl) != d.get("route_customer_count") or len(zs) != d.get("zone_count"):
        e.append("conteos declarados no coinciden con el contenido")
    if len({c["sap_code"] for c in cl}) != len(cl):
        e.append("códigos de cliente duplicados")
    if len({c["folder_label"] for c in cl}) != len(cl):
        e.append("folders duplicados")
    if [c["physical_order"] for c in cl] != list(range(1, len(cl) + 1)):
        e.append("el orden físico no es 1..N consecutivo")
    nz = {z["prefix"]: z for z in zs}
    if len(nz) != len(zs):
        e.append("prefijos de zona duplicados")
    cuenta: Dict[str, int] = {}
    for c in cl:
        m = _FOLDER.match(c["folder_label"])
        z = nz.get(c["zone_prefix"])
        if not m or m.group(2) != c["zone_prefix"] or int(m.group(1)) != c["folder_number"] or z is None or z["name"] != c["zone_name"]:
            e.append(f"inconsistencia en {c['sap_code']} ({c['folder_label']})")
        if not re.fullmatch(r"C\d{3,8}", c["sap_code"]):
            e.append(f"código inválido {c['sap_code']}")
        cuenta[c["zone_prefix"]] = cuenta.get(c["zone_prefix"], 0) + 1
    for p, z in nz.items():
        if cuenta.get(p, 0) != z["customer_count"]:
            e.append(f"zona {p}: {cuenta.get(p, 0)} clientes vs {z['customer_count']} declarados")
    return e


def integrar(estado: Estado, actor: dict, archivo: Path, route_id: str = "OCCIDENTE", aplicar: bool = False) -> dict:
    d = json.loads(Path(archivo).read_text(encoding="utf-8"))
    conflictos = [f"archivo: {x}" for x in validar_archivo(d)]
    cl, zs = d["customers"], d["zones"]
    with estado.tx() as c:
        r = c.execute("SELECT r.*, v.nombre AS vendedor FROM rutas r JOIN vendedores v ON v.id = r.vendedor_id WHERE route_id = ?", (route_id,)).fetchone()
        if not r:
            raise ErrorNegocio(f"La ruta {route_id} no existe en la BD.", 404)
        if _norm(r["vendedor"]) != _norm(d["vendor"]):
            conflictos.append(f"vendedor: BD '{r['vendedor']}' vs archivo '{d['vendor']}'")
        if _norm(r["nombre"]) != _norm(d["route_name"]):
            conflictos.append(f"nombre de ruta: BD '{r['nombre']}' vs archivo '{d['route_name']}'")
        ya_cli = {x["codigo"]: dict(x) for x in c.execute("SELECT * FROM clientes_ruta WHERE route_id = ?", (route_id,))}
        ya_zon = {x["prefijo"]: x["nombre"] for x in c.execute("SELECT prefijo, nombre FROM zonas WHERE route_id = ?", (route_id,))}
        cand = {x["codigo"]: dict(x) for x in c.execute("SELECT * FROM candidatos WHERE route_id = ?", (route_id,))}
        hwm = {x["zona"]: x["maximo"] for x in c.execute("SELECT zona, maximo FROM folder_hwm WHERE route_id = ?", (route_id,))}
        for p, n in ya_zon.items():
            z = next((z for z in zs if z["prefix"] == p), None)
            if z is None or z["name"] != n:
                conflictos.append(f"zona {p}: BD '{n}' vs archivo '{z['name'] if z else '(ausente)'}'")
        for p, m in hwm.items():
            mx = max([x["folder_number"] for x in cl if x["zone_prefix"] == p] or [0])
            if m > mx:
                conflictos.append(f"zona {p}: la BD ya usó el folder {m} (el archivo llega a {mx})")
        codigos = {x["sap_code"] for x in cl}
        for cod in sorted(set(ya_cli) - codigos):
            conflictos.append(f"{cod} está en la ruta (BD) pero no en el archivo")
        for x in cl:
            cod = x["sap_code"]
            otra = c.execute("SELECT route_id FROM clientes_ruta WHERE codigo = ? AND route_id <> ?", (cod, route_id)).fetchone()
            if otra:
                conflictos.append(f"{cod} ya pertenece a la ruta {otra['route_id']}")
            if cod in ya_cli:
                b = ya_cli[cod]
                if (b["folder"], b["zona"], b["orden"]) != (x["folder_label"], x["zone_prefix"], x["physical_order"]):
                    conflictos.append(f"{cod}: BD {b['folder']}/orden {b['orden']} vs archivo {x['folder_label']}/orden {x['physical_order']}")
            elif cod not in cand:
                conflictos.append(f"{cod} no estaba entre los candidatos propuestos")
            else:
                k = cand[cod]
                if k["estado"] == "rechazada":
                    conflictos.append(f"{cod} fue RECHAZADO en la BD ({k['motivo_rechazo']})")
                if _norm(k["nombre"]) != _norm(x["client_name"]):
                    conflictos.append(f"{cod}: nombre BD '{k['nombre']}' vs archivo '{x['client_name']}'")
            f = c.execute("SELECT codigo FROM clientes_ruta WHERE route_id = ? AND folder = ? AND codigo <> ?", (route_id, x["folder_label"], cod)).fetchone()
            if f:
                conflictos.append(f"folder {x['folder_label']} ya lo ocupa {f['codigo']}")
        if conflictos:
            raise ConflictoIntegracion(conflictos)
        nuevos = [x for x in cl if x["sap_code"] not in ya_cli]
        informe = {"route_id": route_id, "clientes_archivo": len(cl), "clientes_nuevos": len(nuevos), "ya_integrados": len(cl) - len(nuevos),
                   "zonas": len(zs), "aplicado": False, "tipo": "FORANEA", "plazo_dias": 60}
        if not aplicar:
            return informe
        c.execute("UPDATE rutas SET tipo_ruta='FORANEA', plazo_dias=60 WHERE route_id = ?", (route_id,))
        c.execute("UPDATE rutas SET estado_fisico = ?, notas = ? WHERE route_id = ?", (
            "Organización física aprobada (orden, zonas, prefijos y folders). Colocación de viñetas físicas NO confirmada.",
            f"Foránea, crédito 60 días, vendedor {d['vendor']}. Pertenencia a la ruta importada de {d['source_file']} (sha256 {d['source_sha256']}). "
            "Clasificaciones de color PRELIMINARES: candidatos a solo contado y cliente sin evidencia, pendientes de gerencia.", route_id))
        for z in zs:
            if z["prefix"] not in ya_zon:
                c.execute("INSERT INTO zonas(route_id, prefijo, nombre) VALUES (?,?,?)", (route_id, z["prefix"], z["name"]))
        t = estado._t()
        for x in nuevos:
            c.execute("INSERT INTO clientes_ruta(route_id, codigo, folder, zona, nombre, orden) VALUES (?,?,?,?,?,?)",
                      (route_id, x["sap_code"], x["folder_label"], x["zone_prefix"], x["client_name"], x["physical_order"]))
            c.execute("UPDATE candidatos SET estado='confirmada', resuelto_por=?, resuelto_en=?, folder_asignado=?, zona_sugerida=? WHERE route_id=? AND codigo=? AND estado='propuesta'",
                      (actor.get("usuario"), t, x["folder_label"], x["zone_prefix"], route_id, x["sap_code"]))
        for x in cl:
            estado._hwm_registrar(c, route_id, x["folder_label"])
        estado.auditar(c, actor, "ruta.integrar_aprobada", "ruta", route_id, {"archivo": d["source_file"], "sha256_origen": d["source_sha256"], "clientes": len(cl),
                                                                            "nuevos": len(nuevos), "zonas": len(zs)})
        informe["aplicado"] = True
        return informe
