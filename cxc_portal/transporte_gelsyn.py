"""Transporte de SOLO LECTURA del Portal hacia Cosprobell Backend (Gelsyn): traduce `GET /finanzas/{entidad}` al contrato `Transporte` de `fuente_empresarial`.

Reutiliza `ReadOnlyApiClient` de cxc-reporting-core (solo GET, HTTPS, reintentos, errores sin secretos); aqui solo hay MAPEO y paginacion por cursor.
Principios (docs/CONEXION-GELSYN-PRUEBAS.md): nada se inventa. Nulo != cero; importes como texto decimal exacto (<= 6 decimales; solo se corrigen residuos de coma
flotante compatibles, como el normalizador de Gelsyn); fechas comerciales; moneda y version de corte se conservan; documentos cancelados o con cancelacion
INDETERMINADA no cuentan como credito vigente (los indeterminados se emiten invalidos para que la fuente los aparte y los reporte). Las partidas abiertas y
las conciliaciones NO se importaron en Gelsyn: este transporte declara `apto_para_publicar = False`, de modo que ninguna ruta puede publicarse como vigente.
Los nombres de campo son los del CONTRATO DE TRANSPORTE de `/finanzas/*` (referencia tecnica de Gelsyn, §3.21: fecha, vencimiento, pagado, cancelado Y/N/C, pagoEntry, documento,
tipo, aplicado...), que difieren de las columnas SQL. Una carga = una pasada por todas las entidades con la MISMA `version` por entidad; si la version cambia a mitad de
paginacion (VERSION_FINANCIERA_CAMBIO) se REINICIA desde la primera pagina de la nueva version (hasta 2 veces) y, si no se estabiliza, se rechaza."""

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
TIPO_FACTURA = "it_Invoice"               # valor observado en la copia de pruebas para aplicaciones a facturas; si el transporte no lo usa, la carga se rechaza (no se asume)
MAX_REINICIOS = 2
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
    """Contrato de transporte: `cancelado:b` = Y (cancelado) | N (vigente) | C (documento de cancelacion: en la copia hay tantas 'C' como 'Y' en facturas). Y y C se excluyen; N es
    vigente; ausente o desconocido es INDETERMINADO (jamas se interpreta como 'no cancelado'). Tambien se aceptan las formas SQL (`cancelled`, `canceladoSap`, `cancelStatus`)."""
    v = fila.get("cancelado", fila.get("canceladoSap"))
    cs = str(fila.get("cancelStatus") or "").strip().lower()
    if str(v).strip().upper() == "C" or cs in _CANCELACION or _si_no(v) is True or _si_no(fila.get("cancelled")) is True:
        return True
    if _si_no(v) is False or _si_no(fila.get("cancelled")) is False or cs == "csno":
        return False
    return None


def estado_documento(v: Any) -> Optional[str]:
    s = str(v).strip().lower() if v is not None else ""
    return "O" if s in _ABIERTO else "C" if s in _CERRADO else None


class _VersionCambio(Exception):
    pass


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
        self.cortes: Dict[str, Optional[str]] = {}
        self.saldos_no_simultaneos = False
        self.excluidos: Dict[str, int] = {}
        self.advertencias: List[str] = []

    # -- lectura paginada por cursor (cursor y version viven en `meta`); VERSION_FINANCIERA_CAMBIO => reiniciar desde la primera pagina de la nueva version
    def _leer(self, entidad: str) -> Tuple[List[dict], dict]:
        for intento in range(MAX_REINICIOS + 1):
            try:
                return self._pasada(entidad)
            except _VersionCambio as e:
                self.advertencias.append(f"{entidad}: la versión cambió durante la paginación ({e}); se reinició desde la primera página.")
        raise ContratoIncumplido(f"/finanzas/{entidad}: la versión cambió {MAX_REINICIOS + 1} veces durante la paginación; carga rechazada.")

    def _pasada(self, entidad: str) -> Tuple[List[dict], dict]:
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
                raise _VersionCambio(f"{version!r} → {meta.get('version')!r}")
            version, meta_final = meta.get("version"), meta
            filas.extend(x for x in data if isinstance(x, dict))
            cursor = meta.get("siguienteCursor")
            if not cursor:
                return filas, meta_final
            if cursor in vistos:
                raise ContratoIncumplido(f"/finanzas/{entidad}: paginación por cursor cíclica (PAGINACION_SIN_AVANCE).")
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
            self.excluidos, self.advertencias = {}, []
            datos, metas = {}, {}
            for ent in ENTIDADES:
                datos[ent], metas[ent] = self._leer(ent)
            self._carga = {"datos": datos, "metas": metas}
            self.versiones = {e: m.get("version") for e, m in metas.items()}
        return self._carga

    # -- Protocolo Transporte
    def metadatos(self) -> Dict[str, Any]:
        self._carga = None                                   # cada metadatos() abre una carga nueva
        c = self._cargar()
        # Cada entidad se carga en su propio momento (verificado en Cosprobell-pruebas: clientes 27/10, aplicaciones 30/10, facturas y pagos 4/11): se registran TODOS los cortes.
        # El corte de DOCUMENTOS (el que valida fechas de facturas y pagos) es el de facturas/pagos; nunca el menor entre entidades, que descartaria documentos recientes validos.
        self.cortes = {e: (fecha_comercial(m.get("ultimaCargaCompleta")) if m.get("ultimaCargaCompleta") else None) for e, m in c["metas"].items()}
        completo = all(self.cortes.values())
        docs = [self.cortes[e] for e in ("facturas", "pagos") if self.cortes.get(e)]
        corte = max(docs) if docs else None
        monedas = sorted({str(x) for m in c["metas"].values() for x in (m.get("monedas") or [])})
        if len(monedas) > 1:
            self.advertencias.append(f"Varias monedas en la carga ({', '.join(monedas)}): los importes no se suman entre monedas.")
        for ent, m in c["metas"].items():
            for adv in (m.get("advertencias") or []):
                self.advertencias.append(f"{ent}: {adv}")
        self.saldos_no_simultaneos = bool(corte and self.cortes.get("clientes") != corte)
        if len({v for v in self.cortes.values() if v}) > 1:
            self.advertencias.append("Cortes por entidad NO simultáneos (" + ", ".join(f"{e}={v}" for e, v in sorted(self.cortes.items())) + "): no se mezclan como si fueran un mismo corte.")
        if self.saldos_no_simultaneos:
            self.advertencias.append(f"El saldo de control de clientes ({self.cortes.get('clientes')}) no es del corte de documentos ({corte}): NO se emite ni se presenta como saldo conciliado; "
                                     "el saldo mostrado es la suma de documentos abiertos, sin certificar.")
        if self.cortes.get("aplicacionesPagos") and self.cortes.get("pagos") and self.cortes["aplicacionesPagos"] < self.cortes["pagos"]:
            self.advertencias.append(f"Las aplicaciones de pagos ({self.cortes['aplicacionesPagos']}) son anteriores a los pagos ({self.cortes['pagos']}): los pagos posteriores pueden no tener aplicaciones aún.")
        return {"fecha_corte": corte, "generado_en": self._ahora().astimezone(timezone.utc).isoformat(timespec="seconds"),
                "version_contrato": CONTRATO_VERSION, "completo": bool(completo) and bool(corte), "cortes": dict(self.cortes),
                "sistema_origen": f"Cosprobell Backend (Gelsyn) · {self.entorno} · versiones " + ", ".join(f"{e}={v}" for e, v in sorted(self.versiones.items()))
                                  + " · cortes " + ", ".join(f"{e}={v}" for e, v in sorted(self.cortes.items())),
                "monedas": monedas}

    def clientes(self) -> List[dict]:
        out = []
        for f in self._cargar()["datos"]["clientes"]:
            act, blo = _si_no(f.get("activo")), _si_no(f.get("bloqueado"))
            activo = False if (blo is True or act is False) else True if (act is True and blo is False) else None
            out.append({"codigo_sap": f.get("cardCode"), "nombre": f.get("nombre"), "activo": activo, "vendedor_codigo": None if f.get("vendedor") is None else str(f["vendedor"]),
                        "zona": f.get("ruta"), "moneda": f.get("moneda")})          # zona comercial = `ruta` (uZona); `zona` financiera viene nula en la copia
        return out

    def facturas(self) -> List[dict]:
        out = []
        for f in self._cargar()["datos"]["facturas"]:
            cancel = cancelacion(f)
            if cancel is True:
                self.excluidos["facturas_canceladas"] = self.excluidos.get("facturas_canceladas", 0) + 1
                continue
            estado = estado_documento(f.get("estado")) if cancel is False else None            # cancelacion indeterminada -> invalida (se aparta y se reporta)
            out.append({"id_factura": None if f.get("docEntry") is None else str(f["docEntry"]), "numero": None if f.get("docNum") is None else str(f["docNum"]),
                        "codigo_cliente": f.get("cardCode"), "fecha_factura": fecha_comercial(f.get("fecha")), "fecha_vencimiento": fecha_comercial(f.get("vencimiento")),
                        "total": importe(f.get("total")), "aplicado": importe(f.get("pagado")), "estado": estado, "moneda": f.get("moneda"),
                        "tipo_documento": None, "actualizado_en": None})
        return out

    @staticmethod
    def _monto_pago(p: dict) -> Optional[str]:
        """`total` si viene; en la copia de pruebas es nulo en el 100 % de los pagos, asi que se DERIVA de sus componentes declarados (efectivo + transferencia + cheques + tarjetas)."""
        t = importe(p.get("total"))
        if t is not None:
            return t
        partes = [importe(p.get(k)) for k in ("efectivo", "transferencia")] + [importe(p.get(k)) for k in ("cheques", "tarjetas") if p.get(k) is not None]
        if any(x is None for x in partes):
            return None
        suma = sum((Decimal(x) for x in partes), Decimal(0))
        return importe(suma)

    def recibos(self) -> List[dict]:
        c = self._cargar()["datos"]
        por_pago: Dict[Any, List[dict]] = {}
        no_factura = 0
        tipos_vistos = set()
        for a in c["aplicacionesPagos"]:
            tipos_vistos.add(a.get("tipo"))
            if _si_no(a.get("cancelado")) is not False and str(a.get("cancelado")).strip().upper() != "N":   # cancelada, de cancelacion o indeterminada: no respalda ningun pago
                self.excluidos["aplicaciones_canceladas_o_indeterminadas"] = self.excluidos.get("aplicaciones_canceladas_o_indeterminadas", 0) + 1
                continue
            if a.get("tipo") != TIPO_FACTURA or a.get("documento") is None:                          # apertura/cierre de saldo, recibos, asientos, anticipos: no son facturas
                self.excluidos["aplicaciones_a_otros_documentos"] = self.excluidos.get("aplicaciones_a_otros_documentos", 0) + 1
                no_factura += 1
                continue
            por_pago.setdefault(a.get("pagoEntry"), []).append({"id_factura": str(a["documento"]), "monto": importe(a.get("aplicado"))})
        if c["aplicacionesPagos"] and TIPO_FACTURA not in tipos_vistos:
            raise ContratoIncumplido(f"Ninguna aplicación tiene tipo {TIPO_FACTURA!r}: el vocabulario de `tipo` no está confirmado; la carga se rechaza en vez de asumirlo.")
        out = []
        for p in c["pagos"]:
            cancel = cancelacion(p)
            if cancel is True:
                self.excluidos["pagos_cancelados"] = self.excluidos.get("pagos_cancelados", 0) + 1
                continue
            apps = [x for x in por_pago.get(p.get("docEntry"), []) if x["monto"] is not None]
            if cancel is False and not apps:                                                           # sin aplicacion a una factura no respalda nada: se cuenta, no se emite
                self.excluidos["pagos_sin_aplicacion_a_facturas"] = self.excluidos.get("pagos_sin_aplicacion_a_facturas", 0) + 1
                continue
            monto = self._monto_pago(p) if cancel is False else None                                   # cancelacion indeterminada -> invalido
            if cancel is False and (monto is None or Decimal(monto) <= 0):                              # sin importe propio utilizable: el recibo vale lo que suman sus aplicaciones
                monto = importe(sum((Decimal(x["monto"]) for x in apps), Decimal(0)))
                self.excluidos["recibos_con_importe_tomado_de_sus_aplicaciones"] = self.excluidos.get("recibos_con_importe_tomado_de_sus_aplicaciones", 0) + 1
            out.append({"id_recibo": None if p.get("docEntry") is None else str(p["docEntry"]), "numero_recibo": None if p.get("docNum") is None else str(p["docNum"]),
                        "codigo_cliente": p.get("cardCode"), "fecha_contable": fecha_comercial(p.get("fecha")), "monto": monto, "aplicaciones": apps, "via": "aplicacion_directa"})
        return out

    def saldos(self) -> List[dict]:
        """Saldo de CONTROL provisto por SAP (`saldo` de clientes). Nulo => sin fila (jamas cero). Solo se emite si es del MISMO corte que los documentos; si no (cortes distintos)
        se omite entera la lista: un saldo de otra fecha no se presenta ni se compara como conciliado. No es saldo abierto por partidas: ver MOTIVO_NO_APTO."""
        self._cargar()
        if getattr(self, "saldos_no_simultaneos", False):
            self.excluidos["saldos_de_control_de_otro_corte"] = len(self._cargar()["datos"]["clientes"])
            return []
        out = []
        for f in self._cargar()["datos"]["clientes"]:
            s = importe(f.get("saldo"))
            if s is not None:
                out.append({"codigo_cliente": f.get("cardCode"), "saldo_abierto": s, "moneda": f.get("moneda")})
        return out
