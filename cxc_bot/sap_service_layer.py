"""Fuente de clientes desde SAP Business One Service Layer (SIN conexion real).

Capas (el ingeniero solo implementa la ultima):

    runner / motor de Excel
      -> SapDataSource (data_source.py)            interfaz estable
        -> SapServiceLayerDataSource (este modulo)  OData: filtro, paginacion,
                                                    mapeo, validacion, errores
          -> ServiceLayerTransport                  SOLO HTTP: login/get/logout
             - FakeServiceLayerTransport (sap_fakes.py): tests, sin red
             - UnconfiguredServiceLayerTransport: placeholder actual
             - HttpServiceLayerTransport: PENDIENTE (ver docs/SAP-INTEGRATION-TODO.md)

Todo es LECTURA. Nada de este modulo escribe en SAP. Los secretos nunca viven
aqui: el transport real los lee de su propio entorno.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .data_source import SapDataSource
from .models import SapClientRecord

logger = logging.getLogger("cxc_bot.sap_service_layer")


# --------------------------------------------------------------------- errores
class SapSourceError(Exception):
    """Base. El runner la registra como categoria 'fuente_datos_invalida'."""


class SapUnavailableError(SapSourceError):
    """Red caida, timeout, 5xx del Service Layer. Reintentar mas tarde es seguro."""


class SapAuthError(SapSourceError):
    """Credenciales invalidas o usuario sin licencia/permiso de login."""


class SapSessionExpiredError(SapAuthError):
    """401 con sesion previa: el data source re-loguea UNA vez y reintenta."""


class SapPermissionError(SapSourceError):
    """403: el usuario de servicio no tiene permiso de lectura sobre el objeto."""


class SapSchemaError(SapSourceError):
    """La respuesta no tiene la forma esperada (campo faltante / tipo invalido)."""


class SapDataQualityError(SapSourceError):
    """Respuesta valida pero inservible: vacia, codigos duplicados, etc."""


# ------------------------------------------------------------------- transport
class ServiceLayerTransport(ABC):
    """Capa HTTP minima. Debe lanzar las excepciones Sap* de arriba (nunca dejar
    pasar errores crudos de la libreria HTTP) y NO incluir credenciales ni
    cookies de sesion en ningun mensaje de error."""

    @abstractmethod
    def login(self) -> None:
        """POST /Login. Guarda la sesion (B1SESSION) internamente."""

    @abstractmethod
    def get(self, path: str, params: Optional[Dict[str, str]] = None) -> dict:
        """GET relativo a /b1s/v1/. `path` puede ser un nextLink relativo
        (en ese caso `params` es None). Devuelve el JSON ya parseado. El tamano
        de pagina (header Prefer: odata.maxpagesize) es decision del transport."""

    @abstractmethod
    def logout(self) -> None:
        """POST /Logout. Best-effort: no debe lanzar si ya no hay sesion."""


class UnconfiguredServiceLayerTransport(ServiceLayerTransport):
    """Transport por defecto: falla claro en vez de intentar conectar."""

    _MSG = (
        "No hay transport de SAP Service Layer configurado. Implementar "
        "HttpServiceLayerTransport (ver docs/SAP-INTEGRATION-TODO.md)."
    )

    def login(self) -> None:
        raise NotImplementedError(self._MSG)

    def get(self, path, params=None) -> dict:
        raise NotImplementedError(self._MSG)

    def logout(self) -> None:
        raise NotImplementedError(self._MSG)


# ---------------------------------------------------------------------- mapeo
@dataclass(frozen=True)
class BusinessPartnerFieldMap:
    """Nombre del campo del Service Layer para cada atributo de SapClientRecord.

    Los 3 primeros son estandar de BusinessPartners. Los otros 3 estan
    PENDIENTES DE CONFIRMAR con el ingeniero contra la columna real que hoy se
    exporta a Hoja2 (Direccion factura / Activo desde / Contabilizacion
    automatica): son opcionales y quedan en None si el campo no existe."""

    codigo: str = "CardCode"
    nombre: str = "CardName"
    saldo: str = "CurrentAccountBalance"
    direccion_factura: Optional[str] = "BilltoDefault"  # PENDIENTE confirmar
    activo_desde: Optional[str] = "ValidFrom"  # PENDIENTE confirmar
    contabilizacion_automatica: Optional[str] = None  # PENDIENTE: sin campo identificado

    def select(self) -> str:
        fields = [self.codigo, self.nombre, self.saldo, self.direccion_factura,
                  self.activo_desde, self.contabilizacion_automatica]
        return ",".join(f for f in fields if f)


def map_business_partner(item: dict, fm: BusinessPartnerFieldMap) -> SapClientRecord:
    """JSON de un BusinessPartner -> SapClientRecord. Estricto: a diferencia del
    mock, un saldo no numerico NO se convierte en 0 en silencio (eso falsearia
    la cobranza); se lanza SapSchemaError."""

    codigo = item.get(fm.codigo)
    if not isinstance(codigo, str) or not codigo.strip():
        raise SapSchemaError(f"BusinessPartner sin {fm.codigo} valido: {item.get(fm.codigo)!r}")
    codigo = codigo.strip()

    nombre = item.get(fm.nombre)
    if nombre is not None and not isinstance(nombre, str):
        raise SapSchemaError(f"{codigo}: {fm.nombre} no es texto ({type(nombre).__name__}).")

    saldo = item.get(fm.saldo)
    if isinstance(saldo, bool) or not isinstance(saldo, (int, float)):
        raise SapSchemaError(f"{codigo}: {fm.saldo} no es numerico ({saldo!r}).")

    def opt(field_name: Optional[str]):
        if not field_name:
            return None
        v = item.get(field_name)
        if v is None:
            return None
        v = str(v).strip()
        return v or None

    return SapClientRecord(
        codigo=codigo,
        nombre=(nombre or "").strip(),
        saldo=float(saldo),
        direccion_factura=opt(fm.direccion_factura),
        activo_desde=opt(fm.activo_desde),
        contabilizacion_automatica=opt(fm.contabilizacion_automatica),
    )


def validate_records(records: List[SapClientRecord]) -> None:
    """Controles de calidad antes de entregar la tabla al motor, que REEMPLAZA
    Hoja2 completa. Una tabla vacia o con codigos repetidos corromperia todas
    las rutas del lote, asi que se rechaza en vez de entregarla."""

    if not records:
        raise SapDataQualityError(
            "SAP devolvio 0 clientes; se rechaza para no vaciar Hoja2 de todas las rutas."
        )
    seen: Dict[str, int] = {}
    for r in records:
        seen[r.codigo.upper()] = seen.get(r.codigo.upper(), 0) + 1
    dupes = sorted(c for c, n in seen.items() if n > 1)
    if dupes:
        raise SapDataQualityError(f"CardCode duplicados en la respuesta de SAP: {dupes[:10]}")
    bad = [r.codigo for r in records if not r.codigo.upper().startswith("C")]
    if bad:
        raise SapDataQualityError(f"CardCode que no inician con 'C' pese al filtro: {bad[:10]}")


# ----------------------------------------------------------------- data source
@dataclass
class SapServiceLayerDataSource(SapDataSource):
    """Lee clientes de BusinessPartners (solo lectura) a traves de un transport."""

    transport: ServiceLayerTransport = field(default_factory=UnconfiguredServiceLayerTransport)
    field_map: BusinessPartnerFieldMap = field(default_factory=BusinessPartnerFieldMap)
    # Equivalente al filtro manual "Codigo por 'C'" + solo clientes (no proveedores/leads).
    odata_filter: str = "CardType eq 'cCustomer' and startswith(CardCode,'C')"
    max_pages: int = 200  # corta nextLink ciclicos o respuestas descontroladas

    @property
    def is_mock(self) -> bool:
        return False

    def fetch_clients(self) -> List[SapClientRecord]:
        self.transport.login()
        try:
            try:
                items = self._read_all_pages()
            except SapSessionExpiredError:
                logger.warning("Sesion de Service Layer expirada; re-login y un reintento.")
                self.transport.login()
                items = self._read_all_pages()
        finally:
            try:
                self.transport.logout()
            except Exception:  # noqa: BLE001 - logout es best-effort
                logger.warning("Logout de Service Layer fallo (ignorado).")

        records = [map_business_partner(i, self.field_map) for i in items]
        validate_records(records)
        logger.info("SAP: %d clientes leidos.", len(records))
        return records

    def _read_all_pages(self) -> List[dict]:
        params = {
            "$select": self.field_map.select(),
            "$filter": self.odata_filter,
            "$orderby": f"{self.field_map.codigo} asc",
        }
        items: List[dict] = []
        page = self.transport.get("BusinessPartners", params)
        for _ in range(self.max_pages):
            value = page.get("value") if isinstance(page, dict) else None
            if not isinstance(value, list):
                raise SapSchemaError("Respuesta de BusinessPartners sin lista 'value'.")
            items.extend(value)
            next_link = page.get("odata.nextLink") or page.get("@odata.nextLink")
            if not next_link:
                return items
            page = self.transport.get(next_link, None)
        raise SapDataQualityError(f"Mas de {self.max_pages} paginas de BusinessPartners; se aborta.")
