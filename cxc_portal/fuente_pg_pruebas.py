"""Fuente del Portal leida AUTOMATICAMENTE de la copia PostgreSQL `Cosprobell-pruebas` (solo lectura, solo entorno de pruebas).

Reutiliza, sin duplicar logica:
  * conexion/identidad/validacion de SELECT: `inspeccion_pg_pruebas` (sesion READ ONLY, lista cerrada de tablas financieras);
  * traduccion SQL -> filas de transporte: `corrida_en_seco_gelsyn.payloads_transporte`;
  * lectura, validacion y clasificacion: `TransporteGelsyn` -> `FuenteEmpresarial` -> motor unico `payment_semaphore`.
El PG se lee UNA VEZ al arrancar y se cierra la conexion; el Portal sirve esa carga en memoria (reiniciar = releer). No escribe nada en PG.
NUNCA se lee cardName ni datos personales. NUNCA es apta para publicar (`TransporteGelsyn.apto_para_publicar = False`). La cuenta usada por la URL de pruebas es `postgres`:
valida para esta copia aislada, NO para produccion (ver docs/INTEGRACION-GELSYN-PG-PRUEBAS.md, rol SELECT propuesto)."""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from . import corrida_en_seco_gelsyn as ces
from . import fuente_empresarial as fe
from . import gelsyn_config as gc
from . import inspeccion_pg_pruebas as ip
from . import transporte_gelsyn as tg

ENV_ATESTACION = "CXC_GELSYN_PG_ATESTACION"      # debe valer "Cosprobell-pruebas": quien la define afirma que la URL es de ESE servicio
MODO_FUENTE = "gelsyn-pg-pruebas"

SQL_CLIENTES_TODOS = 'SELECT "cardCode" FROM clientes WHERE ' + ip._pub("clientes")
SQL_FACTURAS_TODAS = ('SELECT "docEntry", "docNum", "cardCode", "docDate", "docDueDate", "docTotal", "paidToDate", "documentStatus", cancelled FROM facturas WHERE '
                      + ip._pub("facturas"))
SQL_PAGOS_TODOS = 'SELECT "docEntry", "cardCode", "docDate", total, "cashSum", "transferSum", cancelled FROM pagos WHERE ' + ip._pub("pagos")
SQL_APLICACIONES_TODAS = ('SELECT a."pagoDocEntry", a."facturaDocEntry", a."sumApplied", a.cancelado, a."invoiceType" FROM pagos_facturas a JOIN pagos p ON p."docEntry" = a."pagoDocEntry" WHERE '
                          + ip._pub("aplicacionesPagos", "a") + ' AND ' + ip._pub("pagos", "p"))
SQL_CORTES = 'SELECT entidad, "publicadoEn" FROM finanzas_control WHERE entidad IN (\'clientes\', \'facturas\', \'pagos\', \'aplicacionesPagos\')'


class FuentePgError(RuntimeError):
    """Mensajes sin secretos."""


def verificar_identidad(ses: "ip.Sesion") -> None:
    n = ses.consulta(ip.E_CONSULTAS[1][1])[0]["n"]
    conteos = {t: ses.consulta(f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in ("facturas", "pagos", "pagos_facturas", "clientes")}
    if n != ip.TABLAS_TOTALES or any(conteos[t] != ip.TABLAS[t] for t in conteos):
        raise FuentePgError("Identidad no confirmada: la base no coincide con la copia documentada de Cosprobell-pruebas. No se lee nada más.")


def leer_todo(ses: "ip.Sesion") -> dict:
    return {"clientes": {r["cardCode"] for r in ses.consulta(SQL_CLIENTES_TODOS)}, "facturas": ses.consulta(SQL_FACTURAS_TODAS),
            "pagos": ses.consulta(SQL_PAGOS_TODOS), "aplicaciones": ses.consulta(SQL_APLICACIONES_TODAS)}


def _iso_utc(v: Any) -> Optional[str]:
    return None if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v)).replace(" ", "T")[:19] + "Z"


def construir_cliente(datos: dict, cortes: Dict[str, Any]) -> Any:
    """Cliente de solo lectura del core sobre un servidor LOCAL en memoria con la carga PG (sin red). Los cortes por entidad son `publicadoEn` de finanzas_control (se asumen UTC: por confirmar)."""
    datos_t, metas = ces.payloads_transporte(datos)
    for ent, m in metas.items():
        m["version"] = f"pg-{ent}"
        if cortes.get(ent):
            m["ultimaCargaCompleta"] = _iso_utc(cortes[ent])
    cfg = gc.cargar_config({gc.ENV_BASE_URL: "https://pg-pruebas-local.invalid", gc.ENV_API_KEY: "sin-llave-lectura-local-0123456789", gc.ENV_ENTORNO: "pruebas"})
    return gc.cliente_solo_lectura(cfg, transport=ces.ServidorFinanzasFalso(datos_t, metas), sleep=lambda s: None)


def crear_fuente_pg_pruebas(env: Optional[Dict[str, str]] = None, conectar: Callable[[str], Any] = ip._conectar, hoy: Callable[[], date] = date.today,
                            max_edad_dias: int = 5) -> "fe.FuenteEmpresarial":
    env = os.environ if env is None else env
    if env.get(ENV_ATESTACION) != ip.SERVICIO_ESPERADO:
        raise FuentePgError(f"No se conecta: defina {ENV_ATESTACION}={ip.SERVICIO_ESPERADO} (afirma que la URL pertenece a ese servicio de PRUEBAS).")
    try:
        conn = conectar(ip.leer_url(env))
    except Exception as e:  # noqa: BLE001 - jamas incluye la URL
        raise FuentePgError(f"No se pudo conectar a la base de pruebas ({type(e).__name__}).") from None
    try:
        ses = ip.Sesion(conn)
        verificar_identidad(ses)
        datos = leer_todo(ses)
        cortes = {r["entidad"]: r["publicadoEn"] for r in ses.consulta(SQL_CORTES)}
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    transporte = tg.TransporteGelsyn(construir_cliente(datos, cortes), entorno="pruebas")
    fuente = fe.FuenteEmpresarial(transporte, hoy=hoy, max_edad_dias=max_edad_dias)
    fuente.recargar()
    return fuente
