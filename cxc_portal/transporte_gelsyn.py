"""Transporte de SOLO LECTURA del Portal hacia Cosprobell Backend (Gelsyn): traduce `GET /finanzas/{entidad}` al contrato `Transporte` de `fuente_empresarial`.

Reutiliza `ReadOnlyApiClient` de cxc-reporting-core (solo GET, HTTPS, reintentos, errores sin secretos); aqui solo hay MAPEO y paginacion por cursor.
Principios (docs/CONEXION-GELSYN-PRUEBAS.md): nada se inventa. Nulo != cero; importes como texto decimal exacto (<= 6 decimales; solo se corrigen residuos de coma
flotante compatibles, como el normalizador de Gelsyn); fechas comerciales; moneda y version de corte se conservan; documentos cancelados o con cancelacion
INDETERMINADA no cuentan como credito vigente (los indeterminados se emiten invalidos para que la fuente los aparte y los reporte). Las partidas abiertas y
las conciliaciones NO se importaron en Gelsyn: este transporte declara `apto_para_publicar = False`, de modo que ninguna ruta puede publicarse como vigente.
Los nombres de campo son los de la referencia tecnica de Gelsyn (columnas de la BD); la forma exacta de `data` se confirma con `gelsyn_preflight`.
Una carga = una pasada por todas las entidades con la MISMA `version` por entidad; si la version cambia a mitad de paginacion, la carga se rechaza."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

from .fuente_empresarial import CONTRATO_VERSION, ContratoIncumplido, ErrorConexion

ENTIDADES = ("clientes", "facturas", "pagos", "aplicacionesPagos")
LIMITE_PAGINA = 100                       # maximo documentado por Gelsyn
MAX_PAGINAS = 5000
UTC_HONDURAS = timedelta(hours=-6)
MONEDA_LOCAL = "LPS"
_SI = {"true", "tyes", "y", "yes", "si", "sí", "1"}
_NO = {"false", "tno", "n", "no", "0"}
_ABIERTO = {"bost_open", "o", "open"}
_CERRADO = {"bost_close", "bost_closed", "c", "closed"}
_CANCELACION = {"cscancellation", "cscancel"}
MOTIVO_NO_APTO = ("Gelsyn no importó partidas abiertas ni conciliaciones: el saldo abierto real no está certificado (total − pagado es solo el pendiente del documento). "
                  "La fuente no puede habilitar publicación vigente hasta que existan partidas verificadas y validación contable.")


# ------------------------------------------------------------------------------------------------ conversiones exactas
def importe(v: Any) -> Optional[str]:
    """Texto decimal exacto (<= 6 decimales) o None. Los residuos de coma flotante se corrigen SOLO si son compatibles con 6 decimales (limite absoluto y relativo);
    una cantidad con un septimo decimal real se rechaza. Nunca pasa por float."""
    if v is None or isinstance(v, bool) or v == "":
        return None
    try:
        d = Decimal(repr(v) if isinstance(v, float) else str(v).strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    q = d.quantize(Decimal("0.000001"))
    if abs(d - q) > max(Decimal("1e-9"), abs(d) * Decimal("1e-12")):
        return None
    return format(q.normalize(), "f") if q != 0 else "0"


def fecha_comercial(v: Any) -> Optional[str]:
    """AAAA-MM-DD. Fecha sola o medianoche = fecha comercial tal cual; instante con zona -> hora de Honduras (UTC-6); hora ambigua sin zona -> None (no se adivina)."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        v = v.isoformat()
    s = str(v).strip().replace(" ", "T")
    try:
        if len(s) == 10:
            return date.fromisoformat(s).isoformat()
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return (dt.astimezone(timezone.utc) + UTC_HONDURAS).date().isoformat()
    return dt.date().isoformat() if (dt.hour, dt.minute, dt.second) == (0, 0, 0) else None


def _si_no(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower() if v is not None else ""
    return True if s in _SI else False if s in _NO else None


def cancelacion(fila: dict) -> Optional[bool]:
    """True = cancelado/documento de cancelacion (se excluye); False = vigente; None = INDETERMINADO ('Cancelled undefined' nunca significa no cancelado)."""
    cs = str(fila.get("cancelStatus") or "").strip().lower()
    if cs in _CANCELACION or _si_no(fila.get("cancelled")) is True or _si_no(fila.get("canceladoSap")) is True:
        return True
    if _si_no(fila.get("cancelled")) is False or cs == "csno":
        return False
    return None


def estado_documento(v: Any) -> Optional[str]:
    s = str(v).strip().lower() if v is not None else ""
    return "O" if s in _ABIERTO else "C" if s in _CERRADO else None


# ------------------------------------------------------------------------------------------------ transporte
class TransporteGelsyn:
    """Implementa el Protocolo `Transporte` (metadatos, clientes, facturas, recibos, saldos). `metadatos()` inicia una carga completa que las demas llamadas reutilizan."""

    simulado = False
    entorno = "pruebas"
    apto_para_publicar = False
    motivo_no_apto = MOTIVO_NO_APTO

    def __init__(self, cliente, entorno: str = "pruebas", ahora: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.cliente, self.entorno, self._ahora = cliente, entorno, ahora
        self._carga: Optional[dict] = None
        self.versiones: Dict[str, Any] = {}
        self.excluidos: Dict[str, int] = {}
        self.advertencias: List[str] = []

    # -- lectura paginada por cursor (cursor y version viven en `meta`)
    def _leer(self, entidad: str) -> Tuple[List[dict], dict]:
        filas: List[dict] = []
        cursor, version, meta_final, vistos = None, None, {}, set()
        for _ in range(MAX_PAGINAS):
            params: Dict[str, Any] = {"limit": LIMITE_PAGINA}
            if cursor is not None:
                params.update({"cursor": cursor, "version": version})
            resp = self._get(f"/finanzas/{entidad}", params, entidad)
            data, meta = (resp.get("data"), resp.get("meta")) if isinstance(resp, dict) else (None, None)
            if not isinstance(data, list) or not isinstance(meta, dict):
                raise ContratoIncumplido(f"/finanzas/{entidad}: se esperaba {{data: [...], meta: {{...}}}}.")
            if version is not None and meta.get("version") != version:
                raise ContratoIncumplido(f"/finanzas/{entidad}: la versión cambió durante la paginación ({version!r} → {meta.get('version')!r}); carga rechazada.")
            version, meta_final = meta.get("version"), meta
            filas.extend(x for x in data if isinstance(x, dict))
            cursor = meta.get("siguienteCursor")
            if not cursor:
                return filas, meta_final
            if cursor in vistos:
                raise ContratoIncumplido(f"/finanzas/{entidad}: paginación por cursor cíclica.")
            vistos.add(cursor)
        raise ContratoIncumplido(f"/finanzas/{entidad}: se excedió el máximo de páginas.")

    def _get(self, ruta: str, params: dict, entidad: str):
        try:
            return self.cliente.get_json(ruta, params, entidad=entidad)
        except Exception as e:  # noqa: BLE001 - se traduce a los errores del contrato; los mensajes del cliente ya no llevan secretos
            nombre = type(e).__name__
            if nombre in ("AdapterConnectionError", "AdapterAuthError"):
                raise ErrorConexion(f"{nombre} al leer {ruta}") from e
            raise ContratoIncumplido(f"{nombre} al leer {ruta}") from e

    def _cargar(self) -> dict:
        if self._carga is None:
            datos, metas = {}, {}
            for ent in ENTIDADES:
                datos[ent], metas[ent] = self._leer(ent)
            self._carga = {"datos": datos, "metas": metas}
            self.versiones = {e: m.get("version") for e, m in metas.items()}
            self.excluidos, self.advertencias = {}, []
        return self._carga

    # -- Protocolo Transporte
    def metadatos(self) -> Dict[str, Any]:
        self._carga = None                                   # cada metadatos() abre una carga nueva
        c = self._cargar()
        cortes = [m.get("ultimaCargaCompleta") for m in c["metas"].values()]
        completo = all(x for x in cortes)
        fechas = [f for f in (fecha_comercial(x) for x in cortes if x) if f]
        monedas = sorted({str(x) for m in c["metas"].values() for x in (m.get("monedas") or [])})
        if len(monedas) > 1:
            self.advertencias.append(f"Varias monedas en la carga ({', '.join(monedas)}): los importes no se suman entre monedas.")
        for ent, m in c["metas"].items():
            for adv in (m.get("advertencias") or []):
                self.advertencias.append(f"{ent}: {adv}")
        return {"fecha_corte": min(fechas) if fechas else None, "generado_en": self._ahora().astimezone(timezone.utc).isoformat(timespec="seconds"),
                "version_contrato": CONTRATO_VERSION, "completo": bool(completo) and bool(fechas),
                "sistema_origen": f"Cosprobell Backend (Gelsyn) · {self.entorno} · versiones " + ", ".join(f"{e}={v}" for e, v in sorted(self.versiones.items())),
                "monedas": monedas}

    def clientes(self) -> List[dict]:
        out = []
        for f in self._cargar()["datos"]["clientes"]:
            activo = f.get("finActivo") if isinstance(f.get("finActivo"), bool) else None
            out.append({"codigo_sap": f.get("cardCode"), "nombre": f.get("cardName"), "activo": activo, "vendedor_codigo": f.get("salesPersonCode"),
                        "zona": f.get("uZona"), "moneda": f.get("currency")})
        return out

    def facturas(self) -> List[dict]:
        out = []
        for f in self._cargar()["datos"]["facturas"]:
            cancel = cancelacion(f)
            if cancel is True:
                self.excluidos["facturas_canceladas"] = self.excluidos.get("facturas_canceladas", 0) + 1
                continue
            estado = estado_documento(f.get("documentStatus")) if cancel is False else None      # cancelacion indeterminada -> invalida (se aparta y se reporta)
            out.append({"id_factura": None if f.get("docEntry") is None else str(f["docEntry"]), "numero": None if f.get("docNum") is None else str(f["docNum"]),
                        "codigo_cliente": f.get("cardCode"), "fecha_factura": fecha_comercial(f.get("docDate")), "fecha_vencimiento": fecha_comercial(f.get("docDueDate")),
                        "total": importe(f.get("docTotal")), "aplicado": importe(f.get("paidToDate")), "estado": estado, "moneda": f.get("docCurrency"),
                        "tipo_documento": f.get("docType"), "actualizado_en": f.get("sapUpdateDate") or f.get("sincronizadoEn")})
        return out

    def recibos(self) -> List[dict]:
        c = self._cargar()["datos"]
        por_pago: Dict[Any, List[dict]] = {}
        for a in c["aplicacionesPagos"]:
            if _si_no(a.get("cancelado")) is not False:                                           # cancelada o indeterminada: no respalda ningun pago
                self.excluidos["aplicaciones_canceladas_o_indeterminadas"] = self.excluidos.get("aplicaciones_canceladas_o_indeterminadas", 0) + 1
                continue
            if a.get("facturaDocEntry") is None:                                                  # otro tipo de documento: no demuestra que sea invalida, pero no se aplica a una factura
                self.excluidos["aplicaciones_sin_factura"] = self.excluidos.get("aplicaciones_sin_factura", 0) + 1
                continue
            por_pago.setdefault(a.get("pagoDocEntry"), []).append({"id_factura": str(a["facturaDocEntry"]), "monto": importe(a.get("sumApplied"))})
        out = []
        for p in c["pagos"]:
            cancel = cancelacion(p)
            if cancel is True:
                self.excluidos["pagos_cancelados"] = self.excluidos.get("pagos_cancelados", 0) + 1
                continue
            monto = importe(p.get("total")) if cancel is False else None                         # cancelacion indeterminada -> invalido
            out.append({"id_recibo": None if p.get("docEntry") is None else str(p["docEntry"]), "numero_recibo": None if p.get("docNum") is None else str(p["docNum"]),
                        "codigo_cliente": p.get("cardCode"), "fecha_contable": fecha_comercial(p.get("docDate")), "monto": monto,
                        "aplicaciones": [x for x in por_pago.get(p.get("docEntry"), []) if x["monto"] is not None], "via": "aplicacion_directa"})
        return out

    def saldos(self) -> List[dict]:
        """Saldo de CONTROL provisto por SAP (`currentAccountBalance`). Nulo => sin fila (jamas cero). No es saldo abierto por partidas: ver MOTIVO_NO_APTO."""
        out = []
        for f in self._cargar()["datos"]["clientes"]:
            s = importe(f.get("currentAccountBalance"))
            if s is not None:
                out.append({"codigo_cliente": f.get("cardCode"), "saldo_abierto": s, "moneda": f.get("currency")})
        return out
