"""Interfaz de FUENTE DE DATOS del portal: hoy el snapshot SAP del 08/10/2026; mañana una fuente viva autorizada.

El resto de la aplicacion (semaforo, reportes, API) solo conoce `FuenteDatos`. Para pasar a datos vivos se implementa
`FuenteDatos` (p. ej. sobre el backend de Cosprobell via el adaptador de cxc-reporting-core) y se selecciona con
`CXC_FUENTE=viva`; no hay que reescribir la aplicacion. Ver docs/INTEGRACION-SAP-VIVO.md.
"""

from __future__ import annotations

import datetime as dt
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

from cxc_bot.payment_semaphore import FacturaSAP
from cxc_bot.sap_exports import DatosSAP, cargar_consultas

FECHA_CORTE_SNAPSHOT = date(2026, 10, 8)

TIPO_SNAPSHOT = "SNAPSHOT_SAP_HISTORICO"
TIPO_VIVA = "EN_VIVO"
TIPO_SINTETICA = "SINTETICO_DEMO"


class FuenteNoConfigurada(RuntimeError):
    """La fuente en vivo no esta configurada/autorizada: las funciones que la requieren permanecen bloqueadas."""


class FuenteDatos(Protocol):
    tipo: str
    fecha_corte: date
    etiqueta: str
    en_vivo: bool

    def facturas_por_cliente(self, codigos: Sequence[str]) -> Dict[str, List[FacturaSAP]]: ...
    def nombre_cliente(self, codigo: str) -> Optional[str]: ...
    def saldo_abierto(self, codigo: str) -> float: ...
    def documentos_abiertos(self) -> List[Dict[str, Any]]: ...
    def registros_sap(self) -> List[tuple]: ...
    def frescura(self, hoy: Optional[date] = None) -> Dict[str, Any]: ...


class FuenteSnapshotSAP:
    """Consultas SAP 1/2/3 exportadas el 08/10/2026 (archivos locales privados). NO es en vivo; solo lectura."""

    tipo = TIPO_SNAPSHOT
    en_vivo = False

    def __init__(self, data_dir: Path, fecha_corte: date = FECHA_CORTE_SNAPSHOT):
        self.data_dir = Path(data_dir)
        self.fecha_corte = fecha_corte
        self.etiqueta = f"Snapshot SAP {fecha_corte.strftime('%d/%m/%Y')} (consultas exportadas) — NO es dato en vivo"
        self._datos: Optional[DatosSAP] = None

    def datos(self) -> DatosSAP:
        if self._datos is None:
            self._datos = cargar_consultas(self.data_dir, None)
        return self._datos

    def facturas_por_cliente(self, codigos: Sequence[str]) -> Dict[str, List[FacturaSAP]]:
        d = self.datos()
        return {c.upper(): d.facturas.get(c.upper(), []) for c in codigos}

    def nombre_cliente(self, codigo: str) -> Optional[str]:
        return self.datos().nombres.get(codigo.upper())

    def saldo_abierto(self, codigo: str) -> float:
        return self.datos().saldo_abierto.get(codigo.upper(), 0.0)

    def registros_sap(self) -> List[tuple]:
        """(codigo, nombre, saldo abierto) de todos los clientes del snapshot: alimenta la hoja de datos SAP de los Excel de ruta."""
        d = self.datos()
        return [(c, n, d.saldo_abierto.get(c, 0.0)) for c, n in sorted(d.nombres.items()) if c.startswith("C")]

    def documentos_abiertos(self) -> List[Dict[str, Any]]:
        """Facturas ABIERTAS (estado SAP 'O') de todos los clientes, para el pipeline de cxc-reporting-core."""
        out = []
        for cod, facturas in self.datos().facturas.items():
            for f in facturas:
                if f.estado_sap == "O":
                    out.append({"cliente": cod, "id": f.id_factura, "numero": f.numero, "fecha": f.fecha_factura, "vence": f.vencimiento_sap,
                                "total": f.total, "aplicado": f.aplicado_sap})
        return out

    def frescura(self, hoy: Optional[date] = None) -> Dict[str, Any]:
        hoy = hoy or date.today()
        edad = (hoy - self.fecha_corte).days
        return {"tipo": self.tipo, "fecha_corte": self.fecha_corte.isoformat(), "edad_dias": edad, "vigente": False,
                "motivo": "Snapshot histórico exportado de SAP; no se actualiza solo. Los saldos y facturas envejecen cada día."}


class FuenteViva:
    """Marcador de la fuente en vivo (backend de Cosprobell / SAP de solo lectura). NO implementada: falta credencial y contrato."""

    tipo = TIPO_VIVA
    en_vivo = True
    fecha_corte = date.min
    etiqueta = "Fuente en vivo (no configurada)"

    def _no(self, *a, **k):
        raise FuenteNoConfigurada("La fuente en vivo no está configurada ni autorizada (falta credencial SELECT de solo lectura y contrato de integración).")

    facturas_por_cliente = nombre_cliente = saldo_abierto = documentos_abiertos = registros_sap = _no

    def frescura(self, hoy: Optional[date] = None) -> Dict[str, Any]:
        return {"tipo": self.tipo, "vigente": False, "motivo": "Fuente en vivo no configurada."}


def crear_fuente(data_dir: Optional[Path], modo: str = "snapshot") -> Optional[FuenteDatos]:
    if modo == "viva":
        raise FuenteNoConfigurada("CXC_FUENTE=viva: la fuente en vivo todavía no está disponible. Ver docs/INTEGRACION-SAP-VIVO.md.")
    if data_dir and (Path(data_dir) / "02_SAP_08Oct2026").is_dir():
        return FuenteSnapshotSAP(Path(data_dir))
    return None
