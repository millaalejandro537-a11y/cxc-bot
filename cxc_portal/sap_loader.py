"""Lector de las tres consultas SAP exportadas (08/10/2026) -> `FacturaSAP` con evidencia de recibos.

SOLO LECTURA de archivos locales (fuera de Git; ver docs/PORTAL-MVP.md). No consulta SAP.

- consulta 1: facturas (IdFactura, NumeroFactura, fechas, MontoFactura, ImporteAplicado, Estado C/O).
- consulta 3: aplicaciones directas recibo -> factura (fecha CONTABLE del recibo, MontoAplicado).
- consulta 2: conciliaciones; las lineas de recibo (tipo 24, haber) que concilian contra la factura (tipo 13, debe)
  aportan recibos que consulta 3 no lista como aplicacion directa.
Un mismo recibo respaldando una factura se cuenta una sola vez (el mayor monto observado).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import openpyxl

from cxc_bot.payment_semaphore import FacturaSAP, Recibo

C1, C2, C3 = "consulta 1.xlsx", "consulta 2.xlsx", "consulta 3.xlsx"


def _fecha(v) -> Optional[date]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v).strip(), "%d/%m/%Y").date()


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _id(v) -> str:
    if v in (None, ""):
        return ""
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def _filas(path: Path) -> Iterable[dict]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    it = wb.active.iter_rows(values_only=True)
    head = [str(h).strip() if h is not None else "" for h in next(it)]
    for r in it:
        r = list(r) + [None] * (len(head) - len(r))
        yield dict(zip(head, r))


@dataclass
class DatosSAP:
    facturas: Dict[str, List[FacturaSAP]]       # codigo cliente -> facturas
    nombres: Dict[str, str]
    saldo_abierto: Dict[str, float]             # suma (total - aplicado) de facturas abiertas, consulta 1


def cargar_consultas(data_dir: Path, codigos: Optional[Set[str]] = None) -> DatosSAP:
    d = Path(data_dir) / "02_SAP_08Oct2026"
    cods = {c.upper() for c in codigos} if codigos is not None else None

    base: Dict[str, dict] = {}
    for r in _filas(d / C1):
        cod = str(r.get("CodigoCliente") or "").strip().upper()
        idf = _id(r.get("IdFactura"))
        if not idf or (cods is not None and cod not in cods):
            continue
        base[idf] = {"cod": cod, "nombre": str(r.get("NombreCliente") or "").strip(), "numero": _id(r.get("NumeroFactura")),
                     "fecha": _fecha(r.get("FechaFactura")), "venc": _fecha(r.get("FechaVencimiento")),
                     "total": _num(r.get("MontoFactura")), "aplicado": _num(r.get("ImporteAplicado")),
                     "estado": str(r.get("Estado") or "").strip().upper()}

    rec: Dict[str, Dict[str, Recibo]] = defaultdict(dict)   # factura -> recibo id -> Recibo

    def _poner(idf: str, rid: str, numero: str, fecha: Optional[date], monto: float, via: str):
        if fecha is None or monto <= 0:
            return
        prev = rec[idf].get(rid)
        if prev is None or monto > prev.monto:
            rec[idf][rid] = Recibo(numero or rid, fecha, round(monto, 2), via if prev is None else prev.via)

    for r in _filas(d / C3):
        idf = _id(r.get("IdFactura"))
        if idf in base:
            _poner(idf, _id(r.get("IdRecibo")), _id(r.get("NumeroRecibo")), _fecha(r.get("FechaContableRecibo")),
                   _num(r.get("MontoAplicado")), "consulta 3")

    # consulta 2: conciliaciones -> debitos (facturas) y creditos (recibos) de la misma conciliacion
    conc: Dict[str, dict] = defaultdict(lambda: {"deb": [], "cred": []})
    for r in _filas(d / C2):
        tipo, dh = str(r.get("TipoDocumento") or "").strip(), str(r.get("DebeHaber") or "").strip()
        idc = _id(r.get("IdConciliacion"))
        idd = _id(r.get("IdDocumento"))
        if tipo == "13" and dh == "D" and idd in base:
            conc[idc]["deb"].append((idd, _num(r.get("MontoConciliado"))))
        elif tipo == "24" and dh == "C":
            conc[idc]["cred"].append((idd, _id(r.get("NumeroRecibo")), _fecha(r.get("FechaRecibo")), _num(r.get("MontoConciliado")), str(r.get("CodigoCliente") or "").strip().upper()))
    for idc, g in conc.items():
        if not g["deb"] or not g["cred"]:
            continue
        # asignacion en orden: cada recibo cubre las facturas de la conciliacion hasta agotar su monto
        pend = [[idf, m] for idf, m in sorted(g["deb"])]
        for rid, num, fecha, monto, _cod in sorted(g["cred"], key=lambda x: (x[2] or date.min, x[0])):
            resto = monto
            for p in pend:
                if resto <= 0:
                    break
                if p[1] <= 0:
                    continue
                uso = min(p[1], resto)
                _poner(p[0], rid, num, fecha, uso, "consulta 2")
                p[1] -= uso
                resto -= uso

    facturas: Dict[str, List[FacturaSAP]] = defaultdict(list)
    nombres: Dict[str, str] = {}
    saldo: Dict[str, float] = defaultdict(float)
    for idf, b in base.items():
        if b["fecha"] is None:
            continue
        facturas[b["cod"]].append(FacturaSAP(
            id_factura=idf, numero=b["numero"], fecha_factura=b["fecha"], vencimiento_sap=b["venc"], total=b["total"],
            aplicado_sap=b["aplicado"], estado_sap=b["estado"], recibos=tuple(rec[idf].values())))
        nombres.setdefault(b["cod"], b["nombre"])
        if b["estado"] == "O":
            saldo[b["cod"]] += b["total"] - b["aplicado"]
    return DatosSAP(dict(facturas), nombres, {k: round(v, 2) for k, v in saldo.items()})
