"""Inspeccion de SOLO LECTURA de la copia `Cosprobell-pruebas` (PostgreSQL) para contrastarla con la referencia tecnica de Gelsyn. NO es parte del Portal en ejecucion.

Garantias (todas probadas con una conexion falsa, sin BD): sesion y transaccion READ ONLY (y `default_transaction_read_only=on` + tiempos maximos); SOLO sentencias
SELECT de una lista cerrada, validadas una a una (sin ';', sin palabras de escritura/DDL/permisos); SOLO tablas financieras de la lista (jamas usuarios, sesiones,
credenciales ni configuracion); solo agregados y enumeraciones (nunca nombres, telefonos ni correos); la URL se lee de un ARCHIVO 0600 o de una variable de entorno y NUNCA se
imprime, guarda ni aparece en mensajes de error. Identidad: sin la atestacion humana expresa NO se conecta; ademas, tras conectar, el numero de tablas (67) y los conteos exactos de
las 15 tablas financieras deben coincidir con la referencia tecnica del 4/11/2026, o se detiene sin leer nada mas. (Los conteos prueban que es esa copia, no excluyen que otro
servicio tenga los mismos datos: por eso la atestacion de que la URL pertenece al servicio `Cosprobell-pruebas` es obligatoria y queda en el informe.)
Uso:  CXC_PG_PRUEBAS_URL_FILE=<archivo 0600>  python -m cxc_portal.inspeccion_pg_pruebas --confirmo-servicio-pruebas Cosprobell-pruebas"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ENV_URL = "CXC_PG_PRUEBAS_URL"
ENV_URL_FILE = "CXC_PG_PRUEBAS_URL_FILE"
SERVICIO_ESPERADO = "Cosprobell-pruebas"
TABLAS_TOTALES = 67
# Tablas financieras y filas documentadas por Gelsyn (copia del respaldo del 4/11/2026).
TABLAS: Dict[str, int] = {
    "empresa_financiera": 1, "clientes": 1170, "vendedores": 36, "condiciones_pago": 3, "facturas": 17280, "facturas_lineas": 85979, "facturas_cuotas": 17245,
    "notas_credito": 219, "notas_credito_lineas": 1222, "pagos": 20407, "pagos_facturas": 22146, "movimientos_contables": 258179,
    "finanzas_registros": 461463, "finanzas_control": 12, "finanzas_normalizacion": 12,
}
PROHIBIDO = re.compile(r"usuario|sesion|session|credencial|api_?key|token|password|passw|clave|acceso|auth|secret|operador", re.I)
ESCRITURA = re.compile(r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|call|do|set|vacuum|analyze|reindex|comment|lock|notify|listen|execute|prepare|merge|refresh|cluster|security|reset|begin|commit|rollback|savepoint)\b", re.I)
CAMPOS_PORTAL: Dict[str, List[str]] = {
    "clientes": ["cardCode", "cardName", "salesPersonCode", "uZona", "currency", "currentAccountBalance", "finActivo", "valid", "frozen", "finVersion"],
    "facturas": ["docEntry", "docNum", "docType", "cardCode", "docDate", "docDueDate", "docTotal", "docCurrency", "documentStatus", "cancelled", "cancelStatus",
                 "canceladoSap", "paidToDate", "sapUpdateDate", "sincronizadoEn", "transId", "finVersion"],
    "pagos": ["docEntry", "docNum", "cardCode", "docDate", "docCurrency", "total", "cancelled", "canceladoSap", "sinAplicarOriginal", "transId", "finVersion"],
    "pagos_facturas": ["pagoDocEntry", "facturaDocEntry", "sumApplied", "invoiceType", "cancelado", "documentoSap", "finVersion"],
    "notas_credito": ["docEntry", "cardCode", "docDate", "docTotal", "facturaBaseEntry", "cancelled", "canceladoSap", "finVersion"],
}


class InspeccionError(RuntimeError):
    """Mensajes sin secretos."""


# ------------------------------------------------------------------------------------------------ seguridad de sentencias
def validar_sql(sql: str) -> str:
    s = sql.strip()
    if not re.match(r"(?is)^select\b", s) or ";" in s.rstrip(";") or s.count(";") > 1:
        raise InspeccionError("Solo se admite un SELECT.")
    if ESCRITURA.search(re.sub(r"'[^']*'", "''", s)):
        raise InspeccionError("Sentencia con palabras de escritura/DDL no permitidas.")
    for t in re.findall(r'(?i)\b(?:from|join)\s+"?([A-Za-z_][A-Za-z0-9_.]*)"?', s):
        if t.lower() in ("information_schema.tables", "information_schema.columns"):      # unicas vistas de catalogo admitidas (sin pg_catalog: ahi viven roles y hashes)
            continue
        if t not in TABLAS or PROHIBIDO.search(t):
            raise InspeccionError(f"Tabla fuera de la lista financiera: {t.split('.')[0]}")
    return s.rstrip(";")


def leer_url(env: Optional[Dict[str, str]] = None) -> str:
    """URL desde archivo 0600 (preferido) o variable de entorno. Jamas se imprime."""
    env = os.environ if env is None else env
    f = env.get(ENV_URL_FILE)
    if f:
        p = Path(f).expanduser()
        if not p.is_file():
            raise InspeccionError(f"{ENV_URL_FILE} no apunta a un archivo.")
        if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise InspeccionError("El archivo con la URL debe ser privado (chmod 600).")
        url = p.read_text(encoding="utf-8").strip()
    else:
        url = (env.get(ENV_URL) or "").strip()
    if not url:
        raise InspeccionError(f"Defina {ENV_URL_FILE} (archivo 0600) o {ENV_URL}.")
    if not re.match(r"(?i)^postgres(ql)?://", url):
        raise InspeccionError("La URL no es de PostgreSQL.")
    return url


def descripcion_destino(url: str) -> dict:
    """Solo datos NO secretos del destino (host, puerto, nombre de la base): sin usuario ni contraseña."""
    p = urllib.parse.urlsplit(url)
    return {"host": p.hostname, "puerto": p.port, "base": (p.path or "/").lstrip("/")}


# ------------------------------------------------------------------------------------------------ consultas (lista cerrada)
def _pub(t: str, alias: str = "") -> str:
    """Filtro por version publicada (finVersion = activoId de su entidad en finanzas_control)."""
    col = f'{alias}."finVersion"' if alias else '"finVersion"'
    return f'{col}::text = (SELECT "activoId"::text FROM finanzas_control WHERE entidad = \'{t}\')'


E_CONSULTAS: List[Tuple[str, str]] = [
    ("identidad", "SELECT current_database() AS base, current_setting('server_version') AS version, now() AS ahora"),
    ("tablas_publicas", "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"),
    ("control", 'SELECT entidad, ("activoId" IS NOT NULL) AS publicada, "finalizadoEn", "publicadoEn", secuencia, cantidad, "lecturaPublicadaDesde" FROM finanzas_control ORDER BY entidad'),
    ("normalizacion", 'SELECT entidad, "finalizadoEn", cantidad, "actualizadoEn" FROM finanzas_normalizacion ORDER BY entidad'),
    ("columnas", "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema = 'public' AND table_name IN ("
                 + ", ".join(f"'{t}'" for t in CAMPOS_PORTAL) + ") ORDER BY table_name, ordinal_position"),
    ("version_publicada", 'SELECT \'facturas\' AS t, count(*) AS total, count(*) FILTER (WHERE ' + _pub("facturas") + ') AS publicadas FROM facturas UNION ALL '
                          'SELECT \'pagos\', count(*), count(*) FILTER (WHERE ' + _pub("pagos") + ') FROM pagos UNION ALL '
                          'SELECT \'pagos_facturas\', count(*), count(*) FILTER (WHERE ' + _pub("aplicacionesPagos") + ') FROM pagos_facturas UNION ALL '
                          'SELECT \'clientes\', count(*), count(*) FILTER (WHERE ' + _pub("clientes") + ') FROM clientes'),
    ("dominio_facturas", 'SELECT "documentStatus" AS estado, cancelled, "cancelStatus", "docCurrency" AS moneda, "docType", count(*) AS n FROM facturas WHERE ' + _pub("facturas") + ' GROUP BY 1,2,3,4,5 ORDER BY n DESC'),
    ("dominio_pagos", 'SELECT cancelled, "canceladoSap", "docCurrency" AS moneda, count(*) AS n FROM pagos WHERE ' + _pub("pagos") + ' GROUP BY 1,2,3 ORDER BY n DESC'),
    ("dominio_aplicaciones", 'SELECT "invoiceType", cancelado, ("facturaDocEntry" IS NULL) AS sin_factura, count(*) AS n FROM pagos_facturas WHERE ' + _pub("aplicacionesPagos") + ' GROUP BY 1,2,3 ORDER BY n DESC'),
    ("dominio_clientes", 'SELECT valid, frozen, "finActivo", currency, count(*) AS n, count(*) FILTER (WHERE "currentAccountBalance" IS NULL) AS sin_saldo_control, '
                         'count(*) FILTER (WHERE "uZona" IS NULL) AS sin_zona, count(*) FILTER (WHERE "salesPersonCode" IS NULL) AS sin_vendedor FROM clientes WHERE ' + _pub("clientes") + ' GROUP BY 1,2,3,4 ORDER BY n DESC'),
    ("fechas", 'SELECT min("docDate") AS doc_min, max("docDate") AS doc_max, min("docDueDate") AS venc_min, max("docDueDate") AS venc_max, max("sapUpdateDate") AS sap_upd_max, '
               'max("sincronizadoEn") AS sync_max, count(*) FILTER (WHERE "docDate"::time <> \'00:00:00\') AS docdate_con_hora, '
               'count(*) FILTER (WHERE "docDueDate" IS NOT NULL AND "docDueDate"::time <> \'00:00:00\') AS venc_con_hora FROM facturas WHERE ' + _pub("facturas")),
    ("fechas_pagos", 'SELECT min("docDate") AS min, max("docDate") AS max, count(*) FILTER (WHERE "docDate"::time <> \'00:00:00\') AS con_hora FROM pagos WHERE ' + _pub("pagos")),
    ("integridad", 'SELECT (SELECT count(*) FROM facturas f WHERE ' + _pub("facturas") + ' AND NOT EXISTS (SELECT 1 FROM clientes c WHERE c."cardCode" = f."cardCode")) AS facturas_sin_cliente, '
                   '(SELECT count(*) FROM pagos p WHERE ' + _pub("pagos") + ' AND NOT EXISTS (SELECT 1 FROM clientes c WHERE c."cardCode" = p."cardCode")) AS pagos_sin_cliente, '
                   '(SELECT count(*) FROM pagos_facturas a WHERE ' + _pub("aplicacionesPagos") + ' AND a."facturaDocEntry" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM facturas f WHERE f."docEntry" = a."facturaDocEntry")) AS aplicaciones_a_factura_inexistente, '
                   '(SELECT count(*) FROM pagos_facturas a WHERE ' + _pub("aplicacionesPagos") + ' AND NOT EXISTS (SELECT 1 FROM pagos p WHERE p."docEntry" = a."pagoDocEntry")) AS aplicaciones_sin_pago, '
                   '(SELECT count(*) - count(DISTINCT "docEntry") FROM facturas WHERE ' + _pub("facturas") + ') AS facturas_docentry_duplicado, '
                   '(SELECT count(*) FROM facturas WHERE ' + _pub("facturas") + ' AND "paidToDate" > "docTotal") AS pagado_mayor_que_total, '
                   '(SELECT count(*) FROM facturas WHERE ' + _pub("facturas") + ' AND "docTotal" < 0) AS total_negativo'),
    ("saldos", 'SELECT count(*) AS clientes_con_control, count(*) FILTER (WHERE abs(c."currentAccountBalance" - coalesce(s.pendiente, 0)) > 0.01) AS con_diferencia, '
               'count(*) FILTER (WHERE abs(c."currentAccountBalance" - coalesce(s.pendiente, 0)) > 100) AS con_diferencia_mayor_100 '
               'FROM clientes c LEFT JOIN (SELECT "cardCode", sum(greatest("docTotal" - "paidToDate", 0)) AS pendiente FROM facturas WHERE ' + _pub("facturas") + ' GROUP BY 1) s ON s."cardCode" = c."cardCode" '
               'WHERE c."currentAccountBalance" IS NOT NULL AND ' + _pub("clientes").replace('"finVersion"', 'c."finVersion"')),
]


# Ampliacion (segunda ronda, tambien SELECT agregados): cortes entre entidades, pagos sin aplicar, desglose de saldos, notas de credito, formato de codigos.
E_AMPLIACION: List[Tuple[str, str]] = [
    ("cortes_facturas", 'SELECT count(*) FILTER (WHERE "docDate" > DATE \'2026-10-27\') AS despues_27oct, count(*) FILTER (WHERE "docDate" > DATE \'2026-10-30\') AS despues_30oct, '
                        'count(*) FILTER (WHERE "docDate" > DATE \'2026-11-03\') AS despues_3nov, count(*) FILTER (WHERE "docDueDate" IS NULL) AS sin_vencimiento, '
                        'count(*) FILTER (WHERE "documentStatus" = \'bost_Open\' AND "paidToDate" > 0) AS abiertas_con_pago_parcial, '
                        'count(*) FILTER (WHERE "documentStatus" = \'bost_Close\' AND NOT cancelled AND "paidToDate" < "docTotal") AS cerradas_no_pagadas_integras, '
                        'min("docEntry") AS docentry_min FROM facturas WHERE ' + _pub("facturas")),
    ("cortes_pagos", 'SELECT count(*) FILTER (WHERE p."docDate" > DATE \'2026-10-30\') AS pagos_despues_30oct, '
                     'count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM pagos_facturas a WHERE a."pagoDocEntry" = p."docEntry")) AS pagos_sin_aplicacion, '
                     'count(*) FILTER (WHERE p."sinAplicarOriginal" > 0) AS con_saldo_sin_aplicar FROM pagos p WHERE ' + _pub("pagos", "p")),
    ("saldos_detalle", 'SELECT sum(c."currentAccountBalance") AS suma_control, sum(coalesce(s.pendiente, 0)) AS suma_pendiente_documentos, '
                       'count(*) FILTER (WHERE c."currentAccountBalance" > coalesce(s.pendiente, 0) + 0.01) AS control_mayor, '
                       'count(*) FILTER (WHERE c."currentAccountBalance" < coalesce(s.pendiente, 0) - 0.01) AS control_menor, '
                       'count(*) FILTER (WHERE c."currentAccountBalance" < -0.01) AS control_negativo FROM clientes c LEFT JOIN (SELECT "cardCode", '
                       'sum(greatest("docTotal" - "paidToDate", 0)) AS pendiente FROM facturas WHERE ' + _pub("facturas") + ' GROUP BY 1) s ON s."cardCode" = c."cardCode" WHERE ' + _pub("clientes", "c")),
    ("notas_credito", 'SELECT count(*) AS n, count(*) FILTER (WHERE cancelled) AS canceladas, coalesce(sum("docTotal") FILTER (WHERE NOT cancelled), 0) AS total_vigente, '
                      'count(DISTINCT "cardCode") AS clientes, min("docDate") AS fecha_min, max("docDate") AS fecha_max FROM notas_credito WHERE ' + _pub("notasCredito")),
    ("codigos_clientes", 'SELECT count(*) AS n, count(DISTINCT "cardCode") AS distintos, count(*) FILTER (WHERE "cardCode" !~ \'^C[0-9]+$\') AS codigos_no_estandar FROM clientes WHERE ' + _pub("clientes")),
    ("cobertura_de_cobro", 'SELECT count(*) FILTER (WHERE f."documentStatus" = \'bost_Close\') AS cerradas_vigentes, '
                           'count(*) FILTER (WHERE f."documentStatus" = \'bost_Close\' AND f."docTotal" <= 0.01) AS cerradas_total_cero, '
                           'count(*) FILTER (WHERE f."documentStatus" = \'bost_Close\' AND f."docTotal" > 0.01 AND coalesce(ap.suma, 0) = 0) AS cerradas_sin_ninguna_aplicacion, '
                           'count(*) FILTER (WHERE f."documentStatus" = \'bost_Close\' AND f."docTotal" > 0.01 AND coalesce(ap.suma, 0) > 0 AND coalesce(ap.suma, 0) < f."docTotal" - 0.01) AS cerradas_con_aplicaciones_parciales, '
                           'count(*) FILTER (WHERE f."documentStatus" = \'bost_Close\' AND f."docTotal" > 0.01 AND coalesce(ap.suma, 0) >= f."docTotal" - 0.01) AS cerradas_cobradas_por_aplicaciones, '
                           'count(DISTINCT f."cardCode") FILTER (WHERE f."documentStatus" = \'bost_Close\' AND f."docTotal" > 0.01 AND coalesce(ap.suma, 0) < f."docTotal" - 0.01) AS clientes_con_cerradas_sin_cobro_acreditado '
                           'FROM facturas f LEFT JOIN (SELECT "facturaDocEntry", sum("sumApplied") AS suma FROM pagos_facturas WHERE ' + _pub("aplicacionesPagos")
                           + ' AND cancelado = \'N\' AND "facturaDocEntry" IS NOT NULL GROUP BY 1) ap ON ap."facturaDocEntry" = f."docEntry" WHERE ' + _pub("facturas", "f") + ' AND NOT f.cancelled'),
    ("aplicaciones_vs_pago", 'SELECT count(*) AS pagos_con_aplicado_mayor_al_total FROM (SELECT p."docEntry" FROM pagos p JOIN pagos_facturas a ON a."pagoDocEntry" = p."docEntry" WHERE '
                             + _pub("pagos", "p") + ' AND ' + _pub("aplicacionesPagos", "a") + ' AND a.cancelado = \'N\' GROUP BY p."docEntry", p.total HAVING sum(a."sumApplied") > p.total + 0.01) x'),
]


# ------------------------------------------------------------------------------------------------ ejecucion
class Sesion:
    """Envuelve una conexion DB-API (psycopg) en modo READ ONLY. Toda sentencia pasa por `validar_sql`."""

    def __init__(self, conn):
        self.conn, self.ejecutadas = conn, []

    def consulta(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[dict]:
        s = validar_sql(sql)
        self.ejecutadas.append(s)
        cur = self.conn.cursor()
        try:
            cur.execute(s, params or None)
            cols = [c[0] for c in cur.description or []]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()


def _conectar(url: str):
    import psycopg
    p = urllib.parse.urlsplit(url)
    sslmode = "require" if (p.hostname or "") not in ("localhost", "127.0.0.1", "::1") else "prefer"
    conn = psycopg.connect(url, connect_timeout=15, sslmode=sslmode, application_name="cxc-portal-inspeccion-solo-lectura",
                           options="-c default_transaction_read_only=on -c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000")
    conn.read_only = True                                            # BEGIN READ ONLY
    return conn


def _jsonable(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def inspeccionar(sesion: Sesion, destino: dict, atestacion: str) -> dict:
    out: Dict[str, Any] = {"destino": destino, "atestacion_humana": atestacion, "problemas": [], "resultados": {}}
    ident = sesion.consulta(E_CONSULTAS[0][1])[0]
    out["base_conectada"], out["version_servidor"] = ident["base"], ident["version"]
    n = sesion.consulta(E_CONSULTAS[1][1])[0]["n"]
    conteos: Dict[str, Any] = {}
    for t, esperado in TABLAS.items():
        conteos[t] = sesion.consulta(f'SELECT count(*) AS n FROM {t}')[0]["n"]
    out["identidad"] = {"tablas_publicas": n, "tablas_esperadas": TABLAS_TOTALES, "conteos": conteos,
                        "coinciden": {t: conteos[t] == e for t, e in TABLAS.items()}}
    if n != TABLAS_TOTALES or not all(out["identidad"]["coinciden"].values()):
        out["veredicto"] = "IDENTIDAD_NO_CONFIRMADA"
        out["problemas"].append("El número de tablas o los conteos financieros no coinciden con la copia documentada del 4/11/2026: se detiene sin leer más.")
        return out
    for nombre, sql in E_CONSULTAS[2:]:
        try:
            out["resultados"][nombre] = [{k: _jsonable(v) for k, v in fila.items()} for fila in sesion.consulta(sql)]
        except Exception as e:  # noqa: BLE001 - un error de consulta se informa sin detener el resto; nunca incluye la URL
            out["resultados"][nombre] = {"error": type(e).__name__}
            out["problemas"].append(f"{nombre}: {type(e).__name__}")
            try:
                sesion.conn.rollback()
            except Exception:  # noqa: BLE001
                pass
    cols: Dict[str, set] = {}
    for fila in out["resultados"].get("columnas") or []:
        if isinstance(fila, dict):
            cols.setdefault(fila["table_name"], set()).add(fila["column_name"])
    out["campos_faltantes"] = {t: [c for c in req if c not in cols.get(t, set())] for t, req in CAMPOS_PORTAL.items()}
    out["campos_faltantes"] = {t: f for t, f in out["campos_faltantes"].items() if f}
    out["veredicto"] = "REVISAR" if out["problemas"] or out["campos_faltantes"] else "INSPECCION_COMPLETA"
    return out


def ampliar(sesion: Sesion) -> dict:
    out: Dict[str, Any] = {}
    for nombre, sql in E_AMPLIACION:
        try:
            out[nombre] = [{k: _jsonable(v) for k, v in fila.items()} for fila in sesion.consulta(sql)]
        except Exception as e:  # noqa: BLE001
            out[nombre] = {"error": type(e).__name__}
            try:
                sesion.conn.rollback()
            except Exception:  # noqa: BLE001
                pass
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    atest = args[args.index("--confirmo-servicio-pruebas") + 1] if "--confirmo-servicio-pruebas" in args and args.index("--confirmo-servicio-pruebas") + 1 < len(args) else None
    if atest != SERVICIO_ESPERADO:
        print(f"No se conecta: falta la atestación humana expresa --confirmo-servicio-pruebas {SERVICIO_ESPERADO} (la URL debe pertenecer a ese servicio).", file=sys.stderr)
        return 2
    try:
        url = leer_url()
        conn = _conectar(url)
    except InspeccionError as e:
        print(f"No se conecta: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"No se pudo conectar ({type(e).__name__}).", file=sys.stderr)
        return 2
    try:
        ses = Sesion(conn)
        res = inspeccionar(ses, descripcion_destino(url), atest)
        if "--ampliacion" in args and res.get("veredicto") != "IDENTIDAD_NO_CONFIRMADA":
            res["ampliacion"] = ampliar(ses)
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 0 if res["veredicto"] == "INSPECCION_COMPLETA" else 1


if __name__ == "__main__":
    raise SystemExit(main())
