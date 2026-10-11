"""Dashboard gerencial y resumenes. NO recalcula cartera ni reglas: la cartera (saldos, antiguedad, vendedor, zona, mayores saldos) viene de
`cxc-reporting-core` cuando esta disponible; la clasificacion viene del motor unico `payment_semaphore`; aqui solo se agregan y se cruzan
los datos propios del portal (alertas, decisiones, excepciones, visitas, cobros y ventas declarados, oportunidades).

Todo se rotula con la fuente y su fecha de corte; mientras la fuente sea un snapshot, nada es dato en vivo."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

from .fuentes import es_operable

# SUPUESTOS de "oportunidad comercial" (a confirmar con gerencia): cliente clasificado A con 2 o menos facturas en los ultimos 180 dias.
VENTANA_OPORTUNIDAD_DIAS = 180
COMPRAS_ESPORADICAS_MAX = 2


def _clientes(rutas) -> List[tuple]:
    return [(r, c) for r in rutas.values() for c in r.clientes]


def oportunidades(servicio, rutas) -> dict:
    """Clientes excelentes (A) con compras esporadicas. Solo sugiere: la visita/accion comercial la decide una persona."""
    f = servicio.fuente
    if not es_operable(f):
        return {"criterio": "Requiere una fuente con facturas.", "oportunidades": []}
    corte = f.fecha_corte
    desde = corte - timedelta(days=VENTANA_OPORTUNIDAD_DIAS)
    codigos = [c.codigo for _, c in _clientes(rutas) if c.resultado.final == "A"]
    facturas = f.facturas_por_cliente(codigos)
    out = []
    for r, c in _clientes(rutas):
        if c.resultado.final != "A":
            continue
        fs = facturas.get(c.codigo.upper(), [])
        recientes = [x for x in fs if x.fecha_factura >= desde]
        if len(recientes) <= COMPRAS_ESPORADICAS_MAX:
            ultima = max((x.fecha_factura for x in fs), default=None)
            out.append({"codigo": c.codigo, "cliente": c.nombre, "ruta": r.route_id, "vendedor": r.vendedor, "folder": c.folder,
                        "facturas_ventana": len(recientes), "ultima_compra": ultima.isoformat() if ultima else None,
                        "dias_desde_ultima": (corte - ultima).days if ultima else None})
    out.sort(key=lambda x: (x["dias_desde_ultima"] is None, -(x["dias_desde_ultima"] or 0)))
    return {"criterio": f"SUPUESTO: cliente A con ≤ {COMPRAS_ESPORADICAS_MAX} facturas en los últimos {VENTANA_OPORTUNIDAD_DIAS} días antes del corte {corte.isoformat()}.",
            "fecha_corte": corte.isoformat(), "oportunidades": out}


def cumplimiento(estado, rutas, ultimas: int = 3) -> List[dict]:
    out = []
    for rid, r in rutas.items():
        for s in estado.listar_salidas(rid)[:ultimas]:
            seg = estado.seguimiento_salida(s["id"])
            t = seg["totales"]
            if s["estado"] in ("programada", "preparada", "cancelada") and not (t["visitados"] or seg["resumen"]["no_visitada"]):
                continue   # una salida futura o sin registros no es "cumplimiento" todavia
            out.append({"ruta": rid, "ruta_nombre": r.nombre, "vendedor": r.vendedor, "salida": s["id"], "fecha_salida": s["fecha_salida"], "estado": s["estado"],
                        "clientes": t["clientes"], "visitados": t["visitados"], "no_visitados": seg["resumen"]["no_visitada"], "pendientes": seg["resumen"]["pendiente"],
                        "cumplimiento": t["cumplimiento"], "cobro_declarado": t["cobro_declarado"], "venta_declarada": t["venta_declarada"]})
    return sorted(out, key=lambda x: (x["fecha_salida"], x["ruta"]), reverse=True)


def por_ruta(servicio, estado, rutas) -> List[dict]:
    decisiones = estado.decisiones_vigentes()
    filas = []
    for rid, r in rutas.items():
        cl = {}
        for c in r.clientes:
            cl[c.resultado.final] = cl.get(c.resultado.final, 0) + 1
        alertas = estado.listar_alertas([rid], estado="nueva")
        sal = next((s for s in estado.listar_salidas(rid) if s.get("preparada_en")), None)
        rep = (sal or {}).get("reporte_excepciones") or {}
        filas.append({"ruta": rid, "nombre": r.nombre, "vendedor": r.vendedor, "tipo": r.tipo_ruta, "plazo_dias": r.plazo_dias, "implantacion": r.implantacion,
                      "clientes": len(r.clientes), "saldo": round(sum(c.saldo for c in r.clientes), 2), "clasificacion": cl,
                      "decisiones_vigentes": sum(1 for c in r.clientes if c.codigo.upper() in decisiones),
                      "pendientes_gerencia": sum(1 for c in r.clientes if c.resultado.requiere_gerencia),
                      "alertas_abiertas": {"alta": sum(1 for a in alertas if a["severidad"] == "alta"), "media": sum(1 for a in alertas if a["severidad"] == "media"),
                                           "info": sum(1 for a in alertas if a["severidad"] == "info")},
                      "ultima_salida_preparada": ({"id": sal["id"], "fecha": sal["fecha_salida"], "excepciones": rep.get("total_excepciones"), "revisada": bool(sal.get("revisada_por"))}
                                                  if sal else None)})
    return sorted(filas, key=lambda x: x["nombre"])


def por_zona(rutas) -> List[dict]:
    z: Dict[tuple, dict] = {}
    for r, c in _clientes(rutas):
        d = z.setdefault((r.route_id, c.zona), {"ruta": r.route_id, "zona": c.zona, "clientes": 0, "saldo": 0.0})
        d["clientes"] += 1
        d["saldo"] = round(d["saldo"] + c.saldo, 2)
    return sorted(z.values(), key=lambda x: (x["ruta"], x["zona"]))


def top_saldos(rutas, n: int = 10) -> List[dict]:
    items = sorted(((r, c) for r, c in _clientes(rutas) if c.saldo > 0), key=lambda rc: rc[1].saldo, reverse=True)[:n]
    return [{"codigo": c.codigo, "cliente": c.nombre, "ruta": r.route_id, "vendedor": r.vendedor, "folder": c.folder, "saldo": round(c.saldo, 2),
             "clasificacion": c.resultado.final} for r, c in items]


def construir(servicio, estado, rutas, reporting=None, global_view: bool = True, n_top: int = 10) -> dict:
    ids = list(rutas)
    alertas = estado.listar_alertas(ids, estado="nueva", limite=200)
    dec_vig = estado.decisiones_vigentes()
    codigos = {c.codigo.upper() for _, c in _clientes(rutas)}
    cls: Dict[str, int] = {}
    for _, c in _clientes(rutas):
        cls[c.resultado.final] = cls.get(c.resultado.final, 0) + 1
    out = {
        "fuente": servicio.info_fuente(),
        "aviso": "Datos del snapshot/fuente indicada, NO en vivo y sin validación contable. SAP Business One sigue siendo la fuente oficial.",
        "clasificaciones": cls,
        "por_ruta": por_ruta(servicio, estado, rutas),
        "por_zona": por_zona(rutas),
        "top_saldos": top_saldos(rutas, n_top),
        "alertas": {"abiertas": len(alertas), "por_severidad": {s: sum(1 for a in alertas if a["severidad"] == s) for s in ("alta", "media", "info")},
                    "por_tipo": {t: sum(1 for a in alertas if a["tipo"] == t) for t in sorted({a["tipo"] for a in alertas})}, "recientes": alertas[:20]},
        "decisiones": {"vigentes": [d for k, d in dec_vig.items() if k in codigos],
                       "pendientes_de_gerencia": [{"codigo": c.codigo, "cliente": c.nombre, "ruta": r.route_id, "calculado": c.resultado.calculado}
                                                  for r, c in _clientes(rutas) if c.resultado.requiere_gerencia]},
        "oportunidades": oportunidades(servicio, rutas),
        "cumplimiento": cumplimiento(estado, rutas),
    }
    if global_view:
        core = {"disponible": False, "motivo": "cxc-reporting-core no está conectado."}
        if reporting is not None:
            try:
                rep = reporting.reporte()
                core = {"disponible": True, "aviso": rep.get("aviso_portal"), "estado_validacion": str(rep["corte"]["estado_validacion"]), "cartera": rep["cartera"], "aging": rep["aging"],
                        "por_vendedor": rep["por_vendedor"], "por_zona": rep["por_zona"], "clientes_prioritarios": rep["clientes_prioritarios"][:n_top],
                        "concentracion": {k: rep["concentracion"][k] for k in ("top_n", "pct_top_n", "clase_a", "clase_b", "clase_c")}}
            except Exception as e:  # noqa: BLE001 - el dashboard no cae si el core no tiene corte importado
                core = {"disponible": False, "motivo": f"Sin corte importado en cxc-reporting-core: {e}"}
        out["core"] = core
    return out


def resumen_texto(estado, rutas, dia: date) -> str:
    """Texto breve del resumen semanal (notificacion SIMULADA). Solo agrega datos ya calculados."""
    ids = list(rutas)
    alertas = estado.listar_alertas(ids, estado="nueva", limite=500)
    lineas = [f"Resumen al {dia.isoformat()} (datos del snapshot, no en vivo)."]
    for rid, r in sorted(rutas.items()):
        cl: Dict[str, int] = {}
        for c in r.clientes:
            cl[c.resultado.final] = cl.get(c.resultado.final, 0) + 1
        lineas.append(f"- {r.nombre} ({r.vendedor}): {len(r.clientes)} clientes, saldo {sum(c.saldo for c in r.clientes):,.2f}; "
                      + ", ".join(f"{k}: {v}" for k, v in sorted(cl.items())) + f"; alertas abiertas: {sum(1 for a in alertas if a['route_id'] == rid)}.")
    altas = [a for a in alertas if a["severidad"] == "alta"]
    if altas:
        lineas.append("Alertas altas: " + "; ".join(a["titulo"] for a in altas[:5]) + ("…" if len(altas) > 5 else ""))
    lineas.append("(Mensaje SIMULADO: no sale del sistema.)")
    return "\n".join(lineas)
