"""Siembra el catalogo propio (BD de estado) desde los Excel aprobados y las decisiones de gerencia ya registradas.

Idempotente: una ruta o decision que ya existe NO se sobrescribe (la BD del portal manda desde que existe).
Los Excel originales solo se LEEN.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from cxc_bot import payment_semaphore as ps
from cxc_bot.sap_exports import DECISIONES_ARCHIVO

from .estado import Estado
from .gerencia_excel import leer_aprobado
from .rutas_excel import leer_lista_ruta

RUTAS_SEMILLA = (
    dict(route_id="LAS_VEGAS", nombre="Las Vegas", vendedor="Erick Brizuela", tipo="FORANEA", archivo="Las_Vegas_Final_Aprobado_Gerencia_09Oct2026.xlsx", lista="Las_Vegas_Orden_Por_Zonas_Con_Leyenda.xlsx",
         zonas={"LV": "Las Vegas", "FB": "Flores de Bijao", "PB": "Peña Blanca", "RL": "Río Lindo", "SC": "San Antonio de Cortés"},
         estado_fisico="Terminada: 24 viñetas colocadas y numeradas.", notas="Orden de folders: lista física confirmada (24 clientes)."),
    dict(route_id="YORO", nombre="Yoro", vendedor="Darwing Milla", tipo="FORANEA", archivo="Yoro_Semaforo_Aprobacion_Gerencia_09Oct2026.xlsx", lista="Yoro_Orden_Zonas_Corregido_Con_Leyenda.xlsx",
         zonas={"SY": "Santa Rita, Yoro", "MY": "Morazán, Yoro", "PY": "Punta de Ocote", "YT": "Yorito", "YO": "Yoro"},
         estado_fisico="PENDIENTE: ≈ 4 de 31 viñetas colocadas.", plantilla_excel="YORO FOLDERS",
         notas="Lista física confirmada (31: SY 7, MY 7, PY 1, YT 3, YO 13); IARA'S BEAUTY SALON = C10617, sin C10924."),
)


def sembrar(estado: Estado, data_dir: Path, actor: dict) -> Dict[str, List[str]]:
    data_dir = Path(data_dir)
    resumen = {"rutas_creadas": [], "rutas_existentes": [], "decisiones_importadas": []}
    existentes = {r["route_id"] for r in estado.listar_rutas(solo_activas=False)}
    for cfg in RUTAS_SEMILLA:
        # Nombre PARA LA RUTA: el de la lista operativa (Orden por zonas), que puede diferir del registrado en SAP; el orden/folders salen del Excel aprobado.
        nombres = {f["codigo"]: f["nombre"] for f in leer_lista_ruta(data_dir / "01_Rutas" / cfg["lista"])}
        if cfg["route_id"] in existentes:
            resumen["rutas_existentes"].append(cfg["route_id"])
            estado.actualizar_nombres_clientes(actor, cfg["route_id"], nombres)
            if cfg.get("plantilla_excel") and not estado.obtener_ruta(cfg["route_id"]).get("plantilla_excel"):
                estado.asignar_plantilla_excel(actor, cfg["route_id"], cfg["plantilla_excel"])
            continue
        filas = leer_aprobado(data_dir / "01_Rutas" / cfg["archivo"])
        estado.guardar_ruta(actor, cfg["route_id"], cfg["nombre"], cfg["vendedor"], cfg["tipo"], ps.PLAZO_FORANEA if cfg["tipo"] == "FORANEA" else ps.PLAZO_LOCAL,
                            zonas=cfg["zonas"], clientes=[{"codigo": f.codigo, "folder": f.folder, "zona": f.zona, "nombre": nombres.get(f.codigo, f.nombre)} for f in filas],
                            estado_fisico=cfg["estado_fisico"], notas=cfg["notas"], plantilla_excel=cfg.get("plantilla_excel"))
        resumen["rutas_creadas"].append(cfg["route_id"])
    archivo = data_dir / DECISIONES_ARCHIVO
    if archivo.is_file():
        vigentes = estado.decisiones_vigentes()
        for cod, d in json.loads(archivo.read_text(encoding="utf-8")).items():
            if cod.startswith("_") or cod.upper() in vigentes:
                continue
            estado.registrar_decision({"id": None, "usuario": "importacion", "rol": "admin", "nombre": d.get("actor", "Gerencia")}, cod, d["clasificacion_final"],
                                      d["motivo"] if len(d["motivo"]) >= 10 else d["motivo"] + " (importado)", origen="archivo", fecha=d.get("fecha"))
            resumen["decisiones_importadas"].append(cod.upper())
    return resumen
