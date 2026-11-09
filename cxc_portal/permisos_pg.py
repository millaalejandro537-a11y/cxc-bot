"""Limites DETERMINISTAS para usar la base de PRODUCCION de Gelsyn con una cuenta administrativa (`postgres`), por decision expresa de Alejandro (2026-11-09).

Regla: el Portal solo puede LEER las tablas financieras autorizadas y solo puede ESCRIBIR (INSERT/UPDATE/DELETE) en las 26 tablas de su propio esquema `cxc` listadas abajo.
Todo lo demas se rechaza ANTES de enviarlo: DDL (CREATE/ALTER/DROP/TRUNCATE/GRANT...), escrituras en `public`, tablas ajenas (`asistente_*`, `api_keys`, `operadores`...), varias sentencias,
COPY, funciones peligrosas, CTE con escritura. La sesion es SOLO LECTURA por omision; la escritura exige `escritura=True` explicito y sigue limitada a `cxc`.
Este modulo es la barrera en codigo; la barrera real y definitiva es un rol de BD con esos permisos (pendiente de Gelsyn).
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from . import inspeccion_pg_pruebas as ip

TABLAS_ESCRIBIBLES = (
    "usuarios", "sesiones", "auditoria",                                                    # usuarios y seguridad
    "vendedores", "rutas", "zonas", "clientes_ruta", "folder_hwm", "candidatos",           # rutas
    "decisiones",                                                                           # decisiones
    "salidas", "salida_resumen", "visitas", "visita_eventos", "hojas_visita",              # salidas y visitas
    "documentos", "salida_documentos", "excel_rutas", "cola_impresion", "agentes_impresion",   # documentos e impresion
    "alertas", "notificaciones", "feriados", "configuracion",                              # avisos y configuracion
    "meta", "cambios",                                                                      # control interno
)
TABLAS_LECTURA_FINANCIERA = tuple(ip.TABLAS) + ("finanzas_actualizaciones",)
VISTAS_CATALOGO = ip.VISTAS_CATALOGO
PROHIBIDAS_SIEMPRE = re.compile(r"asistente_|api_keys|operadores|sesiones_operador|telegram|pg_catalog|pg_authid|pg_shadow|_prisma_migrations\b.*\blogs\b", re.I)
PELIGROSAS = re.compile(r"\b(create|alter|drop|truncate|grant|revoke|copy|call|do|vacuum|analyze|reindex|comment|lock|notify|listen|execute|prepare|merge|refresh|cluster|security|import|reset|"
                        r"pg_sleep|pg_terminate_backend|pg_cancel_backend|lo_import|lo_export|dblink|set_config)\b", re.I)


class PermisoDenegado(RuntimeError):
    """Mensaje sin secretos."""


def _sin_literales(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _tablas(sql: str) -> List[str]:
    return [m.lower().replace('"', "") for m in re.findall(r'(?i)\b(?:from|join|into|update)\s+((?:"?[A-Za-z_][A-Za-z0-9_]*"?\.)?"?[A-Za-z_][A-Za-z0-9_]*"?)', _sin_literales(sql))]


def clasificar(sql: str) -> Tuple[str, List[str]]:
    """Devuelve ('lectura'|'escritura', tablas) o lanza PermisoDenegado."""
    s = sql.strip().rstrip(";").strip()
    plano = _sin_literales(s)
    if not s or ";" in plano:
        raise PermisoDenegado("Una sola sentencia por vez.")
    if PROHIBIDAS_SIEMPRE.search(plano):
        raise PermisoDenegado("Tabla o función fuera de lo autorizado.")
    m = re.match(r"(?is)^(select|insert\s+into|update|delete\s+from)\b", s)
    if not m:
        raise PermisoDenegado("Solo SELECT, INSERT, UPDATE o DELETE (nada de DDL ni permisos).")
    tipo = m.group(1).lower().split()[0]
    p = re.sub(r"(?i)\b(returning|on\s+conflict)\b.*$", "", plano) if tipo != "select" else plano
    if re.search(r"(?i)\bwith\b", plano) and tipo != "select":
        raise PermisoDenegado("No se admiten CTE en sentencias de escritura.")
    if tipo == "select" and re.search(r"(?i)\b(insert|update|delete)\b", plano.replace("DELETE", "DELETE")) and re.search(r"(?i)\bwith\b", plano):
        raise PermisoDenegado("CTE con escritura no permitido.")
    if PELIGROSAS.search(re.sub(r"(?i)\bon\s+conflict\b|\bdo\s+update\b|\bdo\s+nothing\b", "", p)):
        raise PermisoDenegado("Sentencia con palabras de DDL/permisos/funciones no permitidas.")
    tablas = _tablas(p)
    if not tablas:
        if tipo == "select":                      # SELECT sin tablas (p. ej. current_setting): solo metadatos de sesion
            return "lectura", []
        raise PermisoDenegado("No se identificó la tabla.")
    if tipo == "select":
        for t in tablas:
            esq, _, nom = t.rpartition(".")
            if t in VISTAS_CATALOGO or (esq in ("", "public") and nom in TABLAS_LECTURA_FINANCIERA) or (esq == "cxc" and nom in TABLAS_ESCRIBIBLES) or (esq == "" and nom == "_prisma_migrations"):
                continue
            raise PermisoDenegado(f"Lectura no autorizada de {t.split('.')[-1]}.")
        return "lectura", tablas
    for t in tablas:                                         # TODAS las tablas mencionadas (incluidas subconsultas) deben ser cxc autorizadas
        esq, _, nom = t.rpartition(".")
        if esq != "cxc" or nom not in TABLAS_ESCRIBIBLES:
            raise PermisoDenegado("Escritura permitida únicamente en las tablas autorizadas del esquema cxc.")
    return "escritura", tablas


class SesionProduccion:
    """Envuelve una conexion DB-API. SOLO LECTURA por omision; toda sentencia pasa por `clasificar`."""

    def __init__(self, conn, escritura: bool = False):
        self.conn, self.escritura, self.ejecutadas = conn, escritura, []

    def consulta(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[dict]:
        tipo, _ = clasificar(sql)
        if tipo != "lectura":
            raise PermisoDenegado("Use ejecutar() para escritura.")
        return self._correr(sql.strip().rstrip(";"), params, fetch=True)

    def ejecutar(self, sql: str, params: Optional[Sequence[Any]] = None) -> int:
        tipo, _ = clasificar(sql)
        if tipo != "escritura":
            raise PermisoDenegado("Use consulta() para lectura.")
        if not self.escritura:
            raise PermisoDenegado("Sesión de solo lectura: la escritura no fue habilitada.")
        return self._correr(sql.strip().rstrip(";"), params, fetch=False)

    def _correr(self, sql, params, fetch):
        self.ejecutadas.append(sql)
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params or None)
            if fetch:
                cols = [c[0] for c in cur.description or []]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
            return cur.rowcount
        finally:
            cur.close()
