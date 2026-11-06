"""Discrepancias entre las RUTAS OPERATIVAS del Portal (catalogo aprobado) y la zona/vendedor FINANCIEROS de la fuente. Funcion pura y de SOLO LECTURA:
NUNCA modifica rutas, zonas, vendedores ni clientes; solo informa para que una persona decida."""
from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence


def _norm(t: Optional[str]) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().upper()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in t).split())


def _moda(valores: Sequence[Any]) -> Optional[Any]:
    c = Counter(v for v in valores if v not in (None, ""))
    return c.most_common(1)[0][0] if c else None


def _mismo_vendedor(a: str, b: str) -> bool:
    """Igualdad tolerante de nombres de persona: mismos terminos o uno contenido en el otro (p. ej. 'Juan Perez' vs 'JUAN PEREZ LOPEZ')."""
    na, nb = _norm(a), _norm(b)
    return bool(na and nb and (na == nb or na in nb or nb in na or set(na.split()) <= set(nb.split()) or set(nb.split()) <= set(na.split())))


def comparar(rutas: Sequence[dict], financieros: Dict[str, dict]) -> dict:
    """rutas: [{route_id, vendedor, clientes:[{codigo, zona, nombre}]}]. financieros: {codigo: {nombre, vendedor_codigo, vendedor_nombre, zona, activo}}."""
    out: List[dict] = []
    for r in rutas:
        cl = r["clientes"]
        fin = {c["codigo"]: financieros.get(c["codigo"]) for c in cl}
        no_en = [c["codigo"] for c in cl if fin[c["codigo"]] is None]
        presentes = [(c, fin[c["codigo"]]) for c in cl if fin[c["codigo"]] is not None]
        vend_mod = _moda([f.get("vendedor_codigo") for _, f in presentes])
        vend_mod_nombre = next((f.get("vendedor_nombre") for _, f in presentes if f.get("vendedor_codigo") == vend_mod), None)
        dist_v = Counter(f.get("vendedor_codigo") for _, f in presentes)
        otros_v = [{"codigo": c["codigo"], "nombre": f.get("nombre"), "vendedor_financiero": f.get("vendedor_nombre") or f.get("vendedor_codigo")}
                   for c, f in presentes if vend_mod is not None and f.get("vendedor_codigo") != vend_mod]
        # zonas: dentro de cada zona operativa (prefijo) los clientes deberian compartir zona financiera
        por_zona: Dict[str, List[Any]] = {}
        for c, f in presentes:
            por_zona.setdefault(c["zona"], []).append((c, f))
        zonas = []
        for z, items in sorted(por_zona.items()):
            moda_z = _moda([f.get("zona") for _, f in items])
            dist = Counter(f.get("zona") or "(sin zona)" for _, f in items)
            fuera = [{"codigo": c["codigo"], "nombre": f.get("nombre"), "zona_financiera": f.get("zona") or "(sin zona)"} for c, f in items if f.get("zona") != moda_z]
            zonas.append({"zona_operativa": z, "clientes": len(items), "zona_financiera_predominante": moda_z, "distribucion": dict(dist), "discrepantes": fuera})
        inactivos = [{"codigo": c["codigo"], "nombre": f.get("nombre")} for c, f in presentes if f.get("activo") is False]
        out.append({"route_id": r["route_id"], "vendedor_ruta": r["vendedor"], "clientes": len(cl), "no_en_fuente": no_en,
                    "vendedor": {"predominante_codigo": vend_mod, "predominante_nombre": vend_mod_nombre,
                                 "coincide_con_vendedor_de_la_ruta": bool(vend_mod_nombre and _mismo_vendedor(r["vendedor"], vend_mod_nombre)),
                                 "distribucion": {str(k): v for k, v in dist_v.items()}, "discrepantes": otros_v},
                    "zonas": zonas, "inactivos_en_fuente": inactivos,
                    "con_discrepancia": bool(no_en or otros_v or inactivos or any(z["discrepantes"] for z in zonas)
                                             or not (vend_mod_nombre and _mismo_vendedor(r["vendedor"], vend_mod_nombre)))})
    return {"rutas": out, "rutas_con_discrepancia": sum(1 for x in out if x["con_discrepancia"]),
            "nota": "Solo informativo: no se modifica ninguna ruta, zona, vendedor ni cliente operativo; la decisión es de gerencia."}
