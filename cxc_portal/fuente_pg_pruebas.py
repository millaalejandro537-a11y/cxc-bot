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
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import corrida_en_seco_gelsyn as ces
from . import fuente_empresarial as fe
from . import gelsyn_config as gc
from . import inspeccion_pg_pruebas as ip
from . import transporte_gelsyn as tg

ENV_ATESTACION = "CXC_GELSYN_PG_ATESTACION"      # debe valer "Cosprobell-pruebas": quien la define afirma que la URL es de ESE servicio
MODO_FUENTE = "gelsyn-pg-pruebas"

SQL_CLIENTES_TODOS = 'SELECT "cardCode", "salesPersonCode" AS vendedor, "uZona" AS ruta FROM clientes WHERE ' + ip._pub("clientes")
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
    if not ip.tablas_compatibles(n) or any(conteos[t] != ip.TABLAS[t] for t in conteos):
        raise FuentePgError("Identidad no confirmada: la base no coincide con la copia documentada de Cosprobell-pruebas. No se lee nada más.")


def leer_todo(ses: "ip.Sesion") -> dict:
    cli = ses.consulta(SQL_CLIENTES_TODOS)
    return {"clientes": {r["cardCode"] for r in cli}, "clientes_info": {r["cardCode"]: {"vendedor": r["vendedor"], "ruta": r["ruta"]} for r in cli},
            "facturas": ses.consulta(SQL_FACTURAS_TODAS), "pagos": ses.consulta(SQL_PAGOS_TODOS), "aplicaciones": ses.consulta(SQL_APLICACIONES_TODAS)}


def _iso_utc(v: Any) -> Optional[str]:
    return None if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v)).replace(" ", "T")[:19] + "Z"


def _servidor(datos: dict, cortes: Dict[str, Any]) -> "ces.ServidorFinanzasFalso":
    """Servidor LOCAL en memoria (sin red) con la carga PG; cortes por entidad = `publicadoEn` de finanzas_control (se asumen UTC: por confirmar)."""
    datos_t, metas = ces.payloads_transporte(datos)
    for ent, m in metas.items():
        m["version"] = f"pg-{ent}"
        if cortes.get(ent):
            m["ultimaCargaCompleta"] = _iso_utc(cortes[ent])
    return ces.ServidorFinanzasFalso(datos_t, metas)


class LectorPg:
    """Lectura SOLO LECTURA de la base de pruebas, separada de la persistencia operativa SQLite. Cada lectura abre su conexion y la cierra."""

    def __init__(self, env: Optional[Dict[str, str]] = None, conectar: Callable[[str], Any] = ip._conectar):
        self.env = os.environ if env is None else env
        self.conectar = conectar
        if self.env.get(ENV_ATESTACION) != ip.SERVICIO_ESPERADO:
            raise FuentePgError(f"No se conecta: defina {ENV_ATESTACION}={ip.SERVICIO_ESPERADO} (afirma que la URL pertenece a ese servicio de PRUEBAS).")

    def _sesion(self):
        try:
            conn = self.conectar(ip.leer_url(self.env))
        except Exception as e:  # noqa: BLE001 - jamas incluye la URL
            raise FuentePgError(f"No se pudo conectar a la base de pruebas ({type(e).__name__}).") from None
        return conn, ip.Sesion(conn)

    def _con(self, fn):
        conn, ses = self._sesion()
        try:
            verificar_identidad(ses)
            return fn(ses)
        except FuentePgError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FuentePgError(f"Error al leer la base de pruebas ({type(e).__name__}).") from None
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()

    def leer(self) -> tuple:
        return self._con(lambda ses: (leer_todo(ses), {r["entidad"]: r["publicadoEn"] for r in ses.consulta(SQL_CORTES)}))

    def conciliacion(self) -> dict:
        from . import investigacion_saldos_pg as inv

        def f(ses):
            res = inv.investigar(ses)
            clientes, lineas = ses.consulta(inv.SQL_CLIENTES), ses.consulta(inv.SQL_LINEAS)
            lim = res["corte_por_transid"]["transid_limite_desde"] if res["corte_por_transid"]["minimas_diferencias"] == 0 else None
            res["diferencias_sin_corte"] = inv.diferencias(clientes, lineas)
            res["diferencias_con_corte_transid"] = inv.diferencias(clientes, lineas, lim) if lim else None
            return res
        return self._con(f)


class FuentePgPruebas(fe.FuenteEmpresarial):
    """`FuenteEmpresarial` sobre la copia PG de pruebas, recargable sin reiniciar. Una recarga fallida conserva la ultima carga buena (DEGRADADA)."""

    def __init__(self, lector: LectorPg, servidor: "ces.ServidorFinanzasFalso", **kw):
        cfg = gc.cargar_config({gc.ENV_BASE_URL: "https://pg-pruebas-local.invalid", gc.ENV_API_KEY: "sin-llave-lectura-local-0123456789", gc.ENV_ENTORNO: "pruebas"})
        cliente = gc.cliente_solo_lectura(cfg, transport=servidor, sleep=lambda s: None)
        super().__init__(tg.TransporteGelsyn(cliente, entorno="pruebas"), **kw)
        self.lector, self.servidor = lector, servidor
        self.lecturas_pg = 1
        self.ultima_lectura_pg: Optional[str] = None

    def recargar(self) -> Dict[str, Any]:
        if self.ultima_lectura_pg is not None or self.lecturas_pg == 0:     # la primera carga ya trae los datos leidos al crear la fuente
            try:
                datos, cortes = self.lector.leer()
            except FuentePgError as e:
                self.ultimo_error_conexion = str(e)
                if self._d is None:
                    self.calidad = {"estado": "SIN_CONEXION", "problemas": [str(e)]}
                    raise fe.FuenteNoDisponible(str(e)) from None
                self.calidad = {**self.calidad, "estado": "DEGRADADA", "problemas": self.calidad.get("problemas", []) + [f"Recarga fallida: se conserva la última carga buena ({e})"]}
                return self.calidad
            nuevo = _servidor(datos, cortes)
            self.servidor.datos, self.servidor.metas = nuevo.datos, nuevo.metas
            self.lecturas_pg += 1
        out = super().recargar()
        self.ultima_lectura_pg = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return out

    def detalle(self) -> Dict[str, Any]:
        t = self.transporte
        return {"lectura": "PostgreSQL de pruebas, solo lectura (la conexión se abre al arrancar y al recargar; luego se cierra)", "cortes_por_entidad": dict(t.cortes),
                "excluidos": dict(t.excluidos), "advertencias": list(t.advertencias), "apta_para_publicar": self.apta_para_publicar, "motivo_no_apta": tg.MOTIVO_NO_APTO,
                "lecturas_pg": self.lecturas_pg, "ultima_lectura_pg": self.ultima_lectura_pg, "calidad": self.calidad.get("estado"),
                "problemas": list(self.calidad.get("problemas", []))[:10]}

    def conciliacion(self) -> Dict[str, Any]:
        return self.lector.conciliacion()


def crear_fuente_pg_pruebas(env: Optional[Dict[str, str]] = None, conectar: Callable[[str], Any] = ip._conectar, hoy: Callable[[], date] = date.today,
                            max_edad_dias: int = 5) -> "FuentePgPruebas":
    lector = LectorPg(env, conectar)
    datos, cortes = lector.leer()
    fuente = FuentePgPruebas(lector, _servidor(datos, cortes), hoy=hoy, max_edad_dias=max_edad_dias)
    fuente.recargar()
    return fuente
