"""Investigacion (SOLO LECTURA, agregada) de las diferencias entre el saldo de control de `clientes.currentAccountBalance` y la suma del libro auxiliar `movimientos_contables`
en la copia de PRUEBAS. Reutiliza la sesion READ ONLY y la lista cerrada de SELECT de `inspeccion_pg_pruebas`. Imprime solo agregados (nunca codigos ni nombres de clientes).

Metodo: el saldo de control es una FOTO de `clientes` (corte propio) y el libro llega hasta otro corte. Las fechas contables pueden ser retroactivas, asi que el corte correcto no es por
fecha sino por ORDEN DE CREACION: `transId` crece con la creacion del asiento. Se busca el `transId` limite t tal que Σ(debe−haber de lineas con transId < t) = saldo de control por cliente.

  python -m cxc_portal.investigacion_saldos_pg --confirmo-servicio-pruebas Cosprobell-pruebas
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import inspeccion_pg_pruebas as ip

TOL = Decimal("0.01")

SQL_CORTES = ('SELECT entidad, "iniciadoEn", "finalizadoEn", "publicadoEn", secuencia, cantidad FROM finanzas_control '
              'WHERE entidad IN (\'clientes\', \'facturas\', \'pagos\', \'aplicacionesPagos\', \'movimientos\', \'notasCredito\') ORDER BY entidad')
SQL_CLIENTES = 'SELECT "cardCode", "currentAccountBalance" AS saldo, currency FROM clientes WHERE ' + ip._pub("clientes") + ' AND "cardCode" LIKE \'C%\''
SQL_LINEAS = ('SELECT "cardCode", "transId", tipo, fecha, sum(debe - haber) AS v, sum(("monedaFC" IS NOT NULL)::int) AS en_fc FROM movimientos_contables WHERE '
              + ip._pub("movimientos") + ' AND "cardCode" LIKE \'C%\' GROUP BY "cardCode", "transId", tipo, fecha')


def _dec(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def analizar(clientes: Sequence[dict], lineas: Sequence[dict], corte_clientes: Optional[date] = None) -> dict:
    """Funcion pura. clientes: {cardCode, saldo}. lineas: {cardCode, transId, tipo, fecha, v}. Devuelve solo agregados."""
    ctrl = {c["cardCode"]: _dec(c["saldo"]) for c in clientes}
    total: Dict[str, Decimal] = defaultdict(Decimal)
    for r in lineas:
        total[r["cardCode"]] += _dec(r["v"])

    def difieren(S: Dict[str, Decimal]) -> int:
        return sum(1 for k, c in ctrl.items() if abs(S.get(k, Decimal(0)) - c) > TOL)

    out: Dict[str, Any] = {"clientes": len(ctrl), "con_libro": sum(1 for k in ctrl if k in total), "sin_libro": sum(1 for k in ctrl if k not in total),
                           "sin_libro_con_saldo": sum(1 for k, c in ctrl.items() if k not in total and abs(c) > TOL)}
    out["sin_corte"] = {"difieren": difieren(total), "coinciden": len(ctrl) - difieren(total),
                        "neto_libro_menos_control": str(sum((total.get(k, Decimal(0)) - c for k, c in ctrl.items() if abs(total.get(k, Decimal(0)) - c) > TOL), Decimal(0)))}
    if corte_clientes:
        S: Dict[str, Decimal] = defaultdict(Decimal)
        for r in lineas:
            if r["fecha"] <= corte_clientes:
                S[r["cardCode"]] += _dec(r["v"])
        out["corte_por_fecha"] = {"fecha": corte_clientes.isoformat(), "difieren": difieren(S), "coinciden": len(ctrl) - difieren(S)}
    # corte por transId: recorrido descendente con contador incremental de diferencias
    por_t: Dict[int, List[dict]] = defaultdict(list)
    for r in lineas:
        por_t[r["transId"]].append(r)
    S = defaultdict(Decimal, total)
    mism = difieren(S)
    historia = []
    for t in sorted(por_t, reverse=True):
        historia.append((t + 1, mism))                       # cutoff t+1: incluye todas las lineas con transId <= t
        for r in por_t[t]:
            k = r["cardCode"]
            antes = k in ctrl and abs(S[k] - ctrl[k]) > TOL
            S[k] -= _dec(r["v"])
            despues = k in ctrl and abs(S[k] - ctrl[k]) > TOL
            mism += int(despues) - int(antes)
    historia.append((min(por_t) if por_t else 0, mism))
    mejor = min(m for _, m in historia) if historia else None
    ts = sorted(t for t, m in historia if m == mejor)
    out["corte_por_transid"] = {"minimas_diferencias": mejor, "transid_limite_desde": ts[0] if ts else None, "transid_limite_hasta": ts[-1] if ts else None}
    if ts:
        limite = ts[0]
        post = [r for r in lineas if r["transId"] >= limite]
        out["posteriores_al_corte"] = {"lineas": len(post), "clientes": len({r["cardCode"] for r in post}), "tipos": dict(Counter(r["tipo"] for r in post)),
                                       "neto": str(sum((_dec(r["v"]) for r in post), Decimal(0))),
                                       "fecha_min": min(r["fecha"] for r in post).isoformat() if post else None, "fecha_max": max(r["fecha"] for r in post).isoformat() if post else None,
                                       "retrofechadas_al_corte_de_clientes": sum(1 for r in post if corte_clientes and r["fecha"] <= corte_clientes)}
    return out


def diferencias(clientes: Sequence[dict], lineas: Sequence[dict], transid_limite: Optional[int] = None, maximo: int = 500) -> List[dict]:
    """Clientes cuyo saldo de control difiere del libro (opcionalmente solo con lineas de transId < limite). Solo codigos e importes; para revision de gerencia/admin."""
    ctrl = {c["cardCode"]: _dec(c["saldo"]) for c in clientes}
    S: Dict[str, Decimal] = defaultdict(Decimal)
    for r in lineas:
        if transid_limite is None or r["transId"] < transid_limite:
            S[r["cardCode"]] += _dec(r["v"])
    out = [{"codigo": k, "control": str(c), "libro": str(S.get(k, Decimal(0))), "diferencia": str(S.get(k, Decimal(0)) - c)}
           for k, c in sorted(ctrl.items()) if abs(S.get(k, Decimal(0)) - c) > TOL]
    return out[:maximo]


def investigar(ses: "ip.Sesion") -> dict:
    cortes = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in ses.consulta(SQL_CORTES)]
    corte_cli = next((r["finalizadoEn"] for r in ses.consulta(SQL_CORTES) if r["entidad"] == "clientes"), None)
    clientes = ses.consulta(SQL_CLIENTES)
    lineas = ses.consulta(SQL_LINEAS)
    res = analizar(clientes, lineas, corte_cli.date() if corte_cli else None)
    res["cortes"] = cortes
    res["moneda_clientes"] = dict(Counter(c["currency"] for c in clientes))
    res["lineas_en_moneda_extranjera"] = sum(int(r["en_fc"] or 0) for r in lineas)
    return res


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not (("--confirmo-servicio-pruebas" in args) and args[args.index("--confirmo-servicio-pruebas") + 1:][:1] == [ip.SERVICIO_ESPERADO]):
        print(f"No se conecta: falta --confirmo-servicio-pruebas {ip.SERVICIO_ESPERADO}.", file=sys.stderr)
        return 2
    try:
        conn = ip._conectar(ip.leer_url())
    except Exception as e:  # noqa: BLE001
        print(f"No se conecta ({type(e).__name__}).", file=sys.stderr)
        return 2
    try:
        ses = ip.Sesion(conn)
        n = ses.consulta(ip.E_CONSULTAS[1][1])[0]["n"]
        if not ip.tablas_compatibles(n):
            print("Identidad no confirmada: se detiene.", file=sys.stderr)
            return 1
        print(json.dumps(investigar(ses), ensure_ascii=False, indent=1, default=str))
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
