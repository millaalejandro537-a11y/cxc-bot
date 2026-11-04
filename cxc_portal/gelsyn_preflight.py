"""Preflight de SOLO LECTURA contra el Cosprobell Backend de PRUEBAS (Gelsyn). Hace como maximo 7 peticiones GET minimas y NUNCA imprime valores de datos:
  GET /health · GET /finanzas/estado · GET /finanzas/{clientes,facturas,pagos,aplicacionesPagos,notasCredito}?limit=1
Comprueba entorno, versiones/publicacion de cada entidad, `meta` (version, ultimaCargaCompleta, monedas, advertencias) y que las filas traigan los campos
que el Portal necesita (nombres segun docs/CONEXION-GELSYN-PRUEBAS.md). Si algo no se reconoce, el veredicto es INDETERMINADO: no se inventa un mapeo.
Uso:  CXC_GELSYN_* en el entorno (ver .env.example) y  python -m cxc_portal.gelsyn_preflight"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from .gelsyn_config import ConfigGelsyn, ConfigGelsynError, cargar_config, cliente_solo_lectura

# Campos que el Portal necesita por entidad (nombres de columna de la referencia tecnica de Gelsyn, 4/11/2026). Se verifican, no se asumen.
CAMPOS_REQUERIDOS: Dict[str, List[str]] = {
    "clientes": ["cardCode", "cardName", "salesPersonCode", "uZona", "currentAccountBalance"],
    "facturas": ["docEntry", "docNum", "cardCode", "docDate", "docDueDate", "docTotal", "docCurrency", "documentStatus", "cancelled", "paidToDate"],
    "pagos": ["docEntry", "docNum", "cardCode", "docDate", "total", "cancelled"],
    "aplicacionesPagos": ["pagoDocEntry", "facturaDocEntry", "sumApplied", "cancelado"],
    "notasCredito": ["docEntry", "cardCode", "docDate", "docTotal", "facturaBaseEntry", "cancelled"],
}
META_ESPERADA = ["version", "ultimaCargaCompleta", "monedas", "siguienteCursor", "advertencias"]


def _filas(resp: Any) -> Optional[list]:
    if isinstance(resp, dict) and isinstance(resp.get("data"), list):
        return resp["data"]
    return None


def preflight(cliente, cfg: ConfigGelsyn) -> dict:
    out: Dict[str, Any] = {"entorno": cfg.entorno, "host": cfg.host, "clave": f"<oculta:{cfg.huella_clave}>", "peticiones": 0, "entidades": {}, "problemas": [], "indeterminado": []}

    def get(path, params=None, entidad=None):
        out["peticiones"] += 1
        return cliente.get_json(path, params, entidad=entidad)

    try:
        get("/health")
        out["health"] = "ok"
    except Exception as e:  # noqa: BLE001
        out["problemas"].append(f"/health: {type(e).__name__}")
    try:
        est = get("/finanzas/estado")
        out["estado_forma"] = sorted(est.keys())[:12] if isinstance(est, dict) else type(est).__name__
        if not isinstance(est, dict):
            out["indeterminado"].append("/finanzas/estado: forma de respuesta no reconocida")
    except Exception as e:  # noqa: BLE001
        out["problemas"].append(f"/finanzas/estado: {type(e).__name__} ({getattr(e, 'args', [''])[0][:80] if getattr(e, 'args', None) else ''})")
    for ent, requeridos in CAMPOS_REQUERIDOS.items():
        info: Dict[str, Any] = {}
        try:
            r = get(f"/finanzas/{ent}", {"limit": 1}, entidad=ent)
        except Exception as e:  # noqa: BLE001
            out["problemas"].append(f"/finanzas/{ent}: {type(e).__name__}")
            out["entidades"][ent] = {"error": type(e).__name__}
            continue
        filas = _filas(r)
        meta = r.get("meta") if isinstance(r, dict) else None
        if filas is None or not isinstance(meta, dict):
            out["indeterminado"].append(f"/finanzas/{ent}: se esperaba {{data: [...], meta: {{...}}}}")
            out["entidades"][ent] = {"forma": sorted(r.keys())[:8] if isinstance(r, dict) else type(r).__name__}
            continue
        info["meta"] = {k: (meta[k] if k in ("version", "ultimaCargaCompleta", "monedas") else "presente") for k in META_ESPERADA if k in meta}
        info["meta_faltante"] = [k for k in META_ESPERADA if k not in meta]
        if not filas:
            out["indeterminado"].append(f"/finanzas/{ent}: sin filas (no se pudo verificar los campos)")
        else:
            falta = [c for c in requeridos if c not in filas[0]]
            info["campos_faltantes"] = falta
            if falta:
                out["problemas"].append(f"/finanzas/{ent}: faltan campos {falta}")
        if meta.get("ultimaCargaCompleta") in (None, False):
            out["problemas"].append(f"/finanzas/{ent}: la carga no está marcada como completa")
        out["entidades"][ent] = info
    versiones = {e: i.get("meta", {}).get("version") for e, i in out["entidades"].items() if isinstance(i, dict) and i.get("meta")}
    out["versiones"] = versiones
    out["veredicto"] = "NO_APTO" if out["problemas"] else ("INDETERMINADO" if out["indeterminado"] else "APTO_PARA_MAPEO")
    return out


def main(argv=None) -> int:
    try:
        cfg = cargar_config()
    except ConfigGelsynError as e:
        print(f"Configuración no válida: {e}", file=sys.stderr)
        return 2
    try:
        cli = cliente_solo_lectura(cfg)
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo preparar el cliente de solo lectura: {type(e).__name__}", file=sys.stderr)
        return 2
    res = preflight(cli, cfg)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res["veredicto"] == "APTO_PARA_MAPEO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
