"""Validacion de SOLO LECTURA de una `FuenteDatos` (snapshot hoy; fuente viva mañana). Es la prueba de aceptacion para el ingeniero.

    python -m cxc_portal.validar_fuente --datos <dir snapshot>                       # valida el snapshot (esperado: FALLA frescura/en_vivo)
    python -m cxc_portal.validar_fuente --fuente paquete.modulo:crear_fuente         # valida una implementacion viva (callable sin argumentos)
        [--max-edad-dias 1] [--controles controles.json] [--muestra 25] [--json]

Solo invoca metodos de LECTURA del contrato (`facturas_por_cliente`, `nombre_cliente`, `saldo_abierto`, `documentos_abiertos`,
`registros_sap`, `frescura`). Nunca escribe. Codigo de salida: 0 = sin FALLA; 1 = hay FALLA.
`controles.json` (opcional, del lado SAP): {"por_cliente": {"C10001": 1234.5}, "total_global": 999999.99}.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

OK, FALLA, ADVERTENCIA, INFO = "OK", "FALLA", "ADVERTENCIA", "INFO"
METODOS = ("facturas_por_cliente", "nombre_cliente", "saldo_abierto", "documentos_abiertos", "registros_sap", "frescura")
ATRIBUTOS = ("tipo", "fecha_corte", "etiqueta", "en_vivo")
PROHIBIDOS = re.compile(r"^(write|update|delete|insert|post|put|patch|set_|create|drop|truncate|execute_write|commit)", re.I)
TOL = 0.01


def _check(id_: str, estado: str, detalle: str, **extra) -> Dict[str, Any]:
    return {"id": id_, "estado": estado, "detalle": detalle, **extra}


def validar_fuente(fuente, hoy: Optional[date] = None, max_edad_dias: int = 2, controles: Optional[dict] = None, muestra: int = 25) -> Dict[str, Any]:
    hoy = hoy or date.today()
    checks: List[Dict[str, Any]] = []

    # 1. contrato
    faltan = [a for a in ATRIBUTOS if not hasattr(fuente, a)] + [m for m in METODOS if not callable(getattr(fuente, m, None))]
    checks.append(_check("contrato.interfaz", FALLA if faltan else OK, f"Faltan: {', '.join(faltan)}" if faltan else "Implementa FuenteDatos completa."))
    if faltan:
        return _resumen(fuente, checks)

    # 2. solo lectura (heuristica sobre la superficie publica; la garantia real es la credencial SELECT del lado SAP)
    escritura = sorted(n for n in dir(fuente) if not n.startswith("_") and callable(getattr(fuente, n, None)) and PROHIBIDOS.match(n))
    checks.append(_check("seguridad.solo_lectura", FALLA if escritura else OK,
                         f"Métodos de escritura expuestos: {escritura}" if escritura else "No expone métodos de escritura. Debe usarse con credencial SELECT dedicada."))

    # 3. frescura
    edad = (hoy - fuente.fecha_corte).days if isinstance(fuente.fecha_corte, date) else None
    if not fuente.en_vivo:
        checks.append(_check("frescura.en_vivo", FALLA, "La fuente NO es en vivo (snapshot histórico)."))
    else:
        checks.append(_check("frescura.en_vivo", OK, "Fuente en vivo."))
    if edad is None or edad < 0:
        checks.append(_check("frescura.fecha_corte", FALLA, f"fecha_corte inválida o futura: {fuente.fecha_corte!r}"))
    elif edad > max_edad_dias:
        checks.append(_check("frescura.fecha_corte", FALLA, f"Datos de hace {edad} día(s); máximo aceptado {max_edad_dias}.", edad_dias=edad))
    else:
        checks.append(_check("frescura.fecha_corte", OK, f"Datos de hace {edad} día(s).", edad_dias=edad))

    # 4-6. integridad y consistencia sobre una muestra de clientes
    try:
        registros = list(fuente.registros_sap())
    except Exception as e:  # noqa: BLE001
        checks.append(_check("lectura.registros_sap", FALLA, f"No se pudo leer: {type(e).__name__}: {e}"))
        return _resumen(fuente, checks)
    codigos = [r[0] for r in registros]
    checks.append(_check("lectura.registros_sap", OK if codigos else FALLA, f"{len(codigos)} clientes.", clientes=len(codigos)))
    sel = codigos[:muestra]
    t0 = time.perf_counter()
    try:
        facs = fuente.facturas_por_cliente(sel)
    except Exception as e:  # noqa: BLE001
        checks.append(_check("lectura.facturas", FALLA, f"{type(e).__name__}: {e}"))
        return _resumen(fuente, checks)
    ms = (time.perf_counter() - t0) * 1000
    checks.append(_check("rendimiento.facturas_muestra", OK if ms < 15000 else ADVERTENCIA, f"{len(sel)} clientes en {ms:.0f} ms.", ms=round(ms)))

    problemas: List[str] = []
    ids = set()
    n_fact = n_cerr = n_cobr = 0
    for cod, lista in facs.items():
        abierto = 0.0
        for f in lista:
            n_fact += 1
            if f.id_factura in ids:
                problemas.append(f"{cod}: id_factura duplicado {f.id_factura}")
            ids.add(f.id_factura)
            if f.estado_sap not in ("C", "O"):
                problemas.append(f"{cod}/{f.numero}: estado_sap inválido {f.estado_sap!r}")
            if f.total < 0 or f.aplicado_sap < -TOL or f.aplicado_sap > f.total + TOL:
                problemas.append(f"{cod}/{f.numero}: total/aplicado inconsistentes ({f.total}/{f.aplicado_sap})")
            if f.estado_sap == "O":
                abierto += f.total - f.aplicado_sap
                if f.aplicado_sap >= f.total - TOL:
                    problemas.append(f"{cod}/{f.numero}: abierta con aplicado >= total")
            else:
                n_cerr += 1
                if f.recibos and round(sum(r.monto for r in f.recibos if r.fecha >= f.fecha_factura), 2) + TOL >= f.total:
                    n_cobr += 1
            for r in f.recibos:
                if r.monto <= 0:
                    problemas.append(f"{cod}/{f.numero}: recibo {r.numero} con monto <= 0")
        saldo = fuente.saldo_abierto(cod)
        if abs(saldo - abierto) > TOL:
            problemas.append(f"{cod}: saldo_abierto {saldo} != Σ facturas abiertas {round(abierto, 2)}")
    checks.append(_check("integridad.facturas", FALLA if problemas else OK,
                         f"{len(problemas)} problema(s) en {n_fact} facturas de {len(sel)} clientes." if problemas else f"{n_fact} facturas coherentes ({len(sel)} clientes).",
                         problemas=problemas[:20]))
    if n_cerr:
        pct = 100.0 * n_cobr / n_cerr
        checks.append(_check("evidencia.cobertura_recibos", INFO if pct >= 80 else ADVERTENCIA,
                             f"{n_cobr}/{n_cerr} facturas cerradas ({pct:.0f} %) quedan respaldadas íntegramente por recibos; el resto es 'cierre sin cobro acreditado' en el semáforo."))

    # 7. conciliacion contra controles de SAP
    if controles:
        difs = []
        for cod, esperado in (controles.get("por_cliente") or {}).items():
            got = fuente.saldo_abierto(cod)
            if abs(got - float(esperado)) > TOL:
                difs.append(f"{cod}: fuente {got} vs control SAP {esperado}")
        checks.append(_check("conciliacion.por_cliente", FALLA if difs else OK, f"{len(difs)} diferencia(s)." if difs else "Coincide con los controles SAP.", diferencias=difs[:20]))
        if controles.get("total_global") is not None:
            total = round(sum(fuente.saldo_abierto(c) for c in codigos), 2)
            ok = abs(total - float(controles["total_global"])) <= TOL
            checks.append(_check("conciliacion.total_global", OK if ok else FALLA, f"fuente {total} vs control {controles['total_global']}"))
    else:
        checks.append(_check("conciliacion.controles", ADVERTENCIA, "Sin controles SAP (--controles): la conciliación contable NO se verificó."))
    return _resumen(fuente, checks)


def _resumen(fuente, checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    fallas = [c for c in checks if c["estado"] == FALLA]
    return {"fuente": getattr(fuente, "etiqueta", str(fuente)), "tipo": getattr(fuente, "tipo", None), "apta_para_uso_operativo": not fallas,
            "fallas": len(fallas), "advertencias": sum(1 for c in checks if c["estado"] == ADVERTENCIA), "checks": checks}


def _cargar_fabrica(spec: str) -> Callable[[], Any]:
    modulo, _, nombre = spec.partition(":")
    return getattr(importlib.import_module(modulo), nombre)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--datos", type=Path, help="Directorio del snapshot privado")
    g.add_argument("--fuente", help="modulo:callable que devuelve una FuenteDatos")
    ap.add_argument("--max-edad-dias", type=int, default=2)
    ap.add_argument("--controles", type=Path)
    ap.add_argument("--muestra", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.datos:
        from .fuentes import FuenteSnapshotSAP
        fuente = FuenteSnapshotSAP(a.datos)
    else:
        fuente = _cargar_fabrica(a.fuente)()
    controles = json.loads(a.controles.read_text(encoding="utf-8")) if a.controles else None
    r = validar_fuente(fuente, max_edad_dias=a.max_edad_dias, controles=controles, muestra=a.muestra)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"Fuente: {r['fuente']}\nApta para uso operativo: {'SÍ' if r['apta_para_uso_operativo'] else 'NO'} ({r['fallas']} falla(s), {r['advertencias']} advertencia(s))")
        for c in r["checks"]:
            print(f"  [{c['estado']:11}] {c['id']}: {c['detalle']}")
    return 0 if r["apta_para_uso_operativo"] else 1


if __name__ == "__main__":
    sys.exit(main())
