"""Corrida EN SECO (solo lectura) del motor de semaforo del Portal sobre los datos de `Cosprobell-pruebas` (Gelsyn), comparada con las clasificaciones aprobadas del 08/10/2026.
No es parte del Portal en ejecucion. NO publica viñetas ni archivos operativos; el informe es AGREGADO (el detalle por codigo de cliente se guarda fuera de Git y sin nombres).

Comparabilidad (se declara, no se supone):
  * COMPARABLE: el motor evaluado con los datos de Gelsyn RECONSTRUIDOS AL CORTE 08/10 (documentos y pagos con fecha <= 08/10; una factura hoy cerrada se considera abierta al corte si sus aplicaciones
    posteriores al corte son las que la cierran), mismo plazo (60), misma ventana (10) y mismas reglas vigentes. Aun asi hay diferencias de EVIDENCIA: el snapshot traia conciliaciones (consulta 2) y
    Gelsyn no las importo, por lo que una factura cerrada sin aplicacion directa queda como 'cierre no acreditado'.
  * NO COMPARABLE: la evaluacion al 04/11 (documentos y pagos posteriores al 08/10): solo mide deriva en el tiempo.
Seguridad: reutiliza `inspeccion_pg_pruebas` (sesion READ ONLY, SELECT validados, URL desde archivo 0600 nunca impresa, atestacion obligatoria). Solo lee codigo de cliente, fechas e importes; jamas nombres."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import openpyxl

from cxc_bot import payment_semaphore as ps

from . import gerencia_excel as ge
from . import inspeccion_pg_pruebas as ip

CORTE_APROBADO = date(2026, 10, 8)
CORTE_ACTUAL = date(2026, 11, 4)
PLAZO = 60
ARCHIVOS = {"LAS_VEGAS": "LAS_VEGAS_SEMAFORO_REGLA_SISTEMA.xlsx", "YORO": "YORO_SEMAFORO_REGLA_SISTEMA.xlsx", "OCCIDENTE": "SEMAFORO OCCIDENTE.xlsx"}
DETERMINADAS = {ps.A, ps.B, ps.C, ps.SOLO_CONTADO, ps.EN_PRUEBA}


# ------------------------------------------------------------------------------------------------ clasificaciones aprobadas (solo codigo, clase, evaluables, mora)
def _n(v: Any) -> str:
    return ge._n(v)


def clase_aprobada(etiqueta: str) -> str:
    """Etiqueta del Excel aprobado -> clave del motor. 'Cliente contado' / 'Rojo' = SOLO_CONTADO (regla vigente: ROJO automatico)."""
    clase = ge.clase_desde_etiqueta(etiqueta)
    if clase == ps.SIN_DATOS and re.search(r"CONTADO|ROJ", _n(etiqueta)):
        clase = ps.SOLO_CONTADO
    return ge.a_regla_vigente(clase)


def leer_aprobados(carpeta: Path) -> Dict[str, Dict[str, dict]]:
    """{ruta: {codigo: {final, historial, ventana, evaluables, promedio}}} desde las hojas '1 - Lista' de los Excel aprobados. No lee nombres."""
    out: Dict[str, Dict[str, dict]] = {}
    for ruta, nombre in ARCHIVOS.items():
        ws = openpyxl.load_workbook(carpeta / nombre, data_only=True).worksheets[0]
        filas = [list(r) for r in ws.iter_rows(values_only=True)]
        hi = next(i for i, r in enumerate(filas) if any(_n(c) in ("CODIGO SAP",) for c in r))
        col = {_n(c): i for i, c in enumerate(filas[hi]) if c is not None}
        etiqueta = next(k for k in ("CLASIFICACION", "CATEGORIA / VINETA", "RESULTADO AUTOMATICO 60D") if k in col)
        d: Dict[str, dict] = {}
        for r in filas[hi + 1:]:
            cod = str(r[col["CODIGO SAP"]] or "").strip().upper()
            if not re.fullmatch(r"C\d+", cod):
                continue
            def pick(*claves):
                for k in claves:
                    for h, i in col.items():
                        if h.startswith(k):
                            return r[i]
                return None
            d[cod] = {"final": clase_aprobada(str(r[col[etiqueta]] or "")), "historial": ge._int(pick("FACTURAS HISTORICAS", "FACT. HIST")),
                      "ventana": ge._int(pick("ULTIMAS REVISADAS", "ULTIMAS 10")), "evaluables": ge._int(pick("FACTURAS EVALUABLES", "EVALUABLES")),
                      "promedio": ge._flt(pick("MORA PROMEDIO", "PROMEDIO MORA", "PROM. MORA"))}
        out[ruta] = d
    return out


# ------------------------------------------------------------------------------------------------ lectura (solo SELECT, por codigo de cliente)
def _pub(t, alias=""):
    return ip._pub(t, alias)


SQL_FACTURAS = ('SELECT "docEntry", "docNum", "cardCode", "docDate", "docDueDate", "docTotal", "paidToDate", "documentStatus", cancelled FROM facturas WHERE '
                + _pub("facturas") + ' AND "cardCode" = ANY(%s)')
SQL_PAGOS = 'SELECT "docEntry", "cardCode", "docDate", total, "cashSum", "transferSum", cancelled FROM pagos WHERE ' + _pub("pagos") + ' AND "cardCode" = ANY(%s)'
SQL_APLICACIONES = ('SELECT a."pagoDocEntry", a."facturaDocEntry", a."sumApplied", a.cancelado, a."invoiceType" FROM pagos_facturas a JOIN pagos p ON p."docEntry" = a."pagoDocEntry" WHERE '
                    + _pub("aplicacionesPagos", "a") + ' AND ' + _pub("pagos", "p") + ' AND p."cardCode" = ANY(%s)')
SQL_CLIENTES = 'SELECT "cardCode" FROM clientes WHERE ' + _pub("clientes") + ' AND "cardCode" = ANY(%s)'


def leer_datos(sesion: "ip.Sesion", codigos: Sequence[str]) -> dict:
    cods = sorted(set(codigos))
    return {"clientes": {r["cardCode"] for r in sesion.consulta(SQL_CLIENTES, [cods])},
            "facturas": sesion.consulta(SQL_FACTURAS, [cods]), "pagos": sesion.consulta(SQL_PAGOS, [cods]), "aplicaciones": sesion.consulta(SQL_APLICACIONES, [cods])}


# ------------------------------------------------------------------------------------------------ reconstruccion al corte y evaluacion con el motor unico
def _d(v) -> Optional[date]:
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v if isinstance(v, date) else datetime.fromisoformat(str(v)[:19]).date()


def _f(v) -> float:
    return float(v or 0)


def construir_facturas(datos: dict, corte: date) -> Dict[str, List[ps.FacturaSAP]]:
    """Facturas de cada cliente tal como habria estado la evidencia al `corte`. Una factura hoy cerrada cuyas aplicaciones la cierran DESPUES del corte se considera abierta al corte;
    una cerrada SIN evidencia suficiente de cobro sigue cerrada (cierre no acreditado: asi lo trataba el motor con el snapshot cuando faltaba la conciliacion)."""
    pagos = {p["docEntry"]: p for p in datos["pagos"] if p["cancelled"] is False}
    apps: Dict[Any, List[Tuple[date, float, Any]]] = defaultdict(list)
    for a in datos["aplicaciones"]:
        p = pagos.get(a["pagoDocEntry"])
        if p is None or a["cancelado"] != "N" or a["facturaDocEntry"] is None or a["invoiceType"] != "it_Invoice":
            continue
        apps[a["facturaDocEntry"]].append((_d(p["docDate"]), _f(a["sumApplied"]), a["pagoDocEntry"]))
    out: Dict[str, List[ps.FacturaSAP]] = defaultdict(list)
    for f in datos["facturas"]:
        if f["cancelled"] is not False or _d(f["docDate"]) > corte:
            continue
        total = _f(f["docTotal"])
        todas, hasta = apps.get(f["docEntry"], []), [x for x in apps.get(f["docEntry"], []) if x[0] <= corte]
        s_todas, s_hasta = sum(x[1] for x in todas), sum(x[1] for x in hasta)
        cerrada_hoy = f["documentStatus"] == "bost_Close"
        cerrada = cerrada_hoy and (s_todas < total - 0.01 or s_hasta >= total - 0.01)
        recibos = tuple(ps.Recibo(str(pid), fecha, monto, "aplicacion_directa") for fecha, monto, pid in sorted(hasta))
        out[f["cardCode"].upper()].append(ps.FacturaSAP(str(f["docEntry"]), str(f["docNum"]), _d(f["docDate"]), _d(f["docDueDate"]), total,
                                                        total if cerrada else min(s_hasta, total), "C" if cerrada else "O", recibos))
    return out


def evaluar(datos: dict, codigos: Sequence[str], corte: date) -> Dict[str, "ps.ResultadoCliente"]:
    fac = construir_facturas(datos, corte)
    return {c: ps.evaluar_cliente(c, fac.get(c, []), corte, PLAZO, None) for c in codigos}


# ------------------------------------------------------------------------------------------------ camino PERMANENTE: las mismas filas servidas como /finanzas/* y leidas con TransporteGelsyn + FuenteEmpresarial
def _txt(v) -> Optional[str]:
    return None if v is None else format(v, "f") if hasattr(v, "quantize") else str(v)


def payloads_transporte(datos: dict, corte_iso: str = "2026-11-04T19:00:00Z") -> Tuple[Dict[str, List[dict]], Dict[str, dict]]:
    """Reescribe filas SQL como filas de TRANSPORTE (nombres de la referencia §3.21). El renombrado columna->campo es una INFERENCIA documentada (fecha<-docDate, pagado<-paidToDate,
    pagoEntry<-pagoDocEntry, documento<-facturaDocEntry, aplicado<-sumApplied, efectivo/transferencia<-cashSum/transferSum); solo sirve para probar el codigo con valores reales."""
    yn = lambda b: "Y" if b else "N"  # noqa: E731
    pagos = {p["docEntry"]: p for p in datos["pagos"]}
    datos_t = {
        "clientes": [{"cardCode": c, "nombre": "SIN NOMBRE (no se lee)", "activo": "Y", "bloqueado": "N", "moneda": "LPS", "saldo": None, "vendedor": 0, "zona": None, "ruta": None} for c in sorted(datos["clientes"])],
        "facturas": [{"docEntry": f["docEntry"], "docNum": f["docNum"], "cardCode": f["cardCode"], "fecha": _d(f["docDate"]).isoformat(), "vencimiento": _d(f["docDueDate"]).isoformat() if f["docDueDate"] else None,
                      "moneda": "LPS", "total": _txt(f["docTotal"]), "pagado": _txt(f["paidToDate"]), "estado": f["documentStatus"], "cancelado": yn(f["cancelled"])} for f in datos["facturas"]],
        "pagos": [{"docEntry": p["docEntry"], "docNum": p["docEntry"], "cardCode": p["cardCode"], "fecha": _d(p["docDate"]).isoformat(), "moneda": "LPS", "cancelado": yn(p["cancelled"]),
                   "total": _txt(p["total"]), "efectivo": _txt(p["cashSum"]), "transferencia": _txt(p["transferSum"]), "cheques": None, "tarjetas": None} for p in datos["pagos"]],
        "aplicacionesPagos": [{"pagoEntry": a["pagoDocEntry"], "linea": 0, "cardCode": pagos[a["pagoDocEntry"]]["cardCode"] if a["pagoDocEntry"] in pagos else None,
                               "fecha": _d(pagos[a["pagoDocEntry"]]["docDate"]).isoformat() if a["pagoDocEntry"] in pagos else None, "cancelado": a["cancelado"],
                               "documento": a["facturaDocEntry"] if a["facturaDocEntry"] is not None else 0, "tipo": a["invoiceType"], "aplicado": _txt(a["sumApplied"])} for a in datos["aplicaciones"]],
    }
    metas = {e: {"version": f"v-{e}", "ultimaCargaCompleta": corte_iso, "monedas": ["LPS"], "advertencias": []} for e in datos_t}
    return datos_t, metas


class ServidorFinanzasFalso:
    """Transporte HTTP FALSO (interfaz del cliente del core) que sirve /finanzas/{entidad} con cursor opaco en meta.siguienteCursor. No hay red."""

    def __init__(self, datos_t: dict, metas: dict, pagina: int = 100):
        self.datos, self.metas, self.pagina, self.peticiones = datos_t, metas, pagina, 0

    def request(self, method, url, headers, timeout):
        import urllib.parse as up
        from cxc_reporting.adapters.backend.client import TransportResponse
        self.peticiones += 1
        p = up.urlsplit(url)
        q = dict(up.parse_qsl(p.query))
        ent = p.path.rsplit("/", 1)[-1]
        if method != "GET" or ent not in self.datos:
            return TransportResponse(404, b"")
        ini = int(q.get("cursor", "0"))
        filas = self.datos[ent]
        sig = str(ini + self.pagina) if ini + self.pagina < len(filas) else None
        cuerpo = {"data": filas[ini:ini + self.pagina], "meta": {**self.metas[ent], "siguienteCursor": sig}}
        return TransportResponse(200, json.dumps(cuerpo, default=str).encode())


def evaluar_via_transporte(datos: dict, codigos: Sequence[str], hoy: date = date(2026, 11, 5)) -> Tuple[Dict[str, "ps.ResultadoCliente"], dict]:
    """Camino permanente: TransporteGelsyn (cliente de solo lectura del core) -> FuenteEmpresarial (tolerancia por defecto) -> motor unico. Devuelve resultados y la calidad de la carga."""
    from . import fuente_empresarial as fe
    from . import gelsyn_config as gc
    from . import transporte_gelsyn as tg
    datos_t, metas = payloads_transporte(datos)
    cfg = gc.cargar_config({gc.ENV_BASE_URL: "https://backend-pruebas.ejemplo.invalid", gc.ENV_API_KEY: "clave-sintetica-no-real-0123456789", gc.ENV_ENTORNO: "pruebas"})
    cli = gc.cliente_solo_lectura(cfg, transport=ServidorFinanzasFalso(datos_t, metas), sleep=lambda s: None)
    f = fe.FuenteEmpresarial(tg.TransporteGelsyn(cli, entorno="pruebas"), hoy=lambda: hoy, max_edad_dias=5)
    cal = f.recargar()
    fac = f.facturas_por_cliente(list(codigos))
    return {c: ps.evaluar_cliente(c, fac.get(c.upper(), []), f.fecha_corte, PLAZO, None) for c in codigos}, {"calidad": cal, "fuente": f}


# ------------------------------------------------------------------------------------------------ comparacion y agregados
def comparar(aprobado: dict, r: Optional["ps.ResultadoCliente"], en_gelsyn: bool) -> Tuple[str, str]:
    """(categoria, causa). Categorias: COINCIDE, CAMBIA, INDETERMINADO, SIN_DATOS_EN_GELSYN."""
    if not en_gelsyn:
        return "SIN_DATOS_EN_GELSYN", "el cliente no existe en la copia"
    if r.final not in DETERMINADAS or aprobado["final"] not in DETERMINADAS:
        quien = "Gelsyn" if r.final not in DETERMINADAS else "aprobado"
        return "INDETERMINADO", f"{quien}: {r.final if r.final not in DETERMINADAS else aprobado['final']}" + ("; faltan evaluables (" + str(r.evaluables) + ")" if r.evaluables == 0 else "")
    if r.final == aprobado["final"]:
        return "COINCIDE", ""
    ev_a, ev_g = aprobado.get("evaluables"), r.evaluables
    prom_a = aprobado.get("promedio")
    if ev_a is not None and ev_g < ev_a:
        return "CAMBIA", "menos facturas evaluables (cierres sin cobro acreditado en Gelsyn)"
    if r.no_acreditadas > 0:
        return "CAMBIA", "cierres sin cobro acreditado en Gelsyn (misma ventana)"
    if prom_a is not None and r.promedio_mora is not None and abs(prom_a - r.promedio_mora) > 1.0:
        return "CAMBIA", "mora promedio distinta con la misma evidencia (fechas de pago/aplicacion)"
    return "CAMBIA", "otra causa (revisar)"


def resumir(aprobados: Dict[str, Dict[str, dict]], datos: dict) -> Tuple[dict, List[dict]]:
    codigos = sorted({c for d in aprobados.values() for c in d})
    al_corte, actual = evaluar(datos, codigos, CORTE_APROBADO), evaluar(datos, codigos, CORTE_ACTUAL)
    filas, resumen = [], {"por_ruta": {}, "total": Counter(), "causas": Counter(), "matriz": Counter(), "deriva_4nov": Counter(), "evidencia": Counter()}
    for ruta, d in aprobados.items():
        cnt = Counter()
        for cod, ap in d.items():
            en = cod in datos["clientes"]
            r = al_corte[cod] if en else None
            cat, causa = comparar(ap, r, en)
            cnt[cat] += 1
            resumen["total"][cat] += 1
            if cat in ("CAMBIA", "INDETERMINADO", "SIN_DATOS_EN_GELSYN"):
                resumen["causas"][f"{cat}: {causa}"] += 1
            if cat in ("COINCIDE", "CAMBIA"):
                resumen["matriz"][f"{ap['final']} -> {r.final}"] += 1
            if en:
                resumen["evidencia"]["clientes con >=1 cierre sin cobro acreditado en la ventana (Gelsyn al 08/10)" if r.no_acreditadas else "clientes sin cierres sin acreditar en la ventana"] += 1
                a4 = actual[cod]
                resumen["deriva_4nov"]["misma clase que el 08/10" if a4.final == r.final else "cambia entre el 08/10 y el 04/11"] += 1
            filas.append({"ruta": ruta, "codigo": cod, "aprobada_0810": ap["final"], "gelsyn_0810": r.final if r else "-", "gelsyn_0411": actual[cod].final if en else "-",
                          "categoria": cat, "causa": causa, "evaluables_aprobado": ap.get("evaluables"), "evaluables_gelsyn": r.evaluables if r else None,
                          "mora_aprobada": ap.get("promedio"), "mora_gelsyn": None if (not r or r.promedio_mora is None) else round(r.promedio_mora, 2),
                          "no_acreditadas_gelsyn": r.no_acreditadas if r else None})
        resumen["por_ruta"][ruta] = {"clientes": len(d), **dict(cnt)}
    resumen = {k: (dict(v) if isinstance(v, Counter) else v) for k, v in resumen.items()}
    return resumen, filas


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    def opt(n, d=None):
        return args[args.index(n) + 1] if n in args and args.index(n) + 1 < len(args) else d
    if opt("--confirmo-servicio-pruebas") != ip.SERVICIO_ESPERADO:
        print("No se conecta: falta --confirmo-servicio-pruebas Cosprobell-pruebas.", file=sys.stderr)
        return 2
    aprob = leer_aprobados(Path(opt("--aprobados", os.path.expanduser("~/Downloads"))))
    try:
        url = ip.leer_url()
        conn = ip._conectar(url)
    except Exception as e:  # noqa: BLE001
        print(f"No se conecta ({type(e).__name__}).", file=sys.stderr)
        return 2
    try:
        ses = ip.Sesion(conn)
        ident = ses.consulta(ip.E_CONSULTAS[0][1])[0]
        n = ses.consulta(ip.E_CONSULTAS[1][1])[0]["n"]
        conteos = {t: ses.consulta(f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in ("facturas", "pagos", "pagos_facturas", "clientes")}
        if n != ip.TABLAS_TOTALES or any(conteos[t] != ip.TABLAS[t] for t in conteos):
            print("Identidad no confirmada: se detiene.", file=sys.stderr)
            return 1
        datos = leer_datos(ses, [c for d in aprob.values() for c in d])
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    resumen, filas = resumir(aprob, datos)
    if "--via-transporte" in args:
        codigos = sorted({c for d in aprob.values() for c in d})
        directo = evaluar(datos, codigos, CORTE_ACTUAL)
        via, info = evaluar_via_transporte(datos, codigos)
        cal = info["calidad"]
        resumen["via_transporte"] = {"clientes": len(codigos), "misma_clase_que_el_camino_directo_al_0411": sum(via[c].final == directo[c].final for c in codigos),
                                     "distinta": [c for c in codigos if via[c].final != directo[c].final], "estado_calidad": cal["estado"], "filas_descartadas": len(cal["filas_descartadas"]),
                                     "tipo_fuente": info["fuente"].tipo, "apta_para_publicar": info["fuente"].apta_para_publicar, "fecha_corte": str(info["fuente"].fecha_corte),
                                     "excluidos": dict(info["fuente"].transporte.excluidos)}
    resumen["lectura"] = {"facturas": len(datos["facturas"]), "pagos": len(datos["pagos"]), "aplicaciones": len(datos["aplicaciones"]), "base": ident["base"]}
    out = Path(opt("--salida", os.path.expanduser("~/Developer/corrida-en-seco-gelsyn")))
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    det = out / "detalle_por_codigo.json"
    det.write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(det, 0o600)
    print(json.dumps(resumen, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
