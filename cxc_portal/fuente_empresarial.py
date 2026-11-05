"""Adaptador de SOLO LECTURA hacia la futura fuente empresarial (backend de BLC / Cosprobell BD), desacoplado por un TRANSPORTE.

Este modulo NO conoce ni inventa endpoints, tablas ni credenciales de BLC: define el CONTRATO DE DATOS que el portal necesita
(docs/CONTRATO-DATOS-BLC.md) y valida lo que reciba. El unico transporte implementado es `TransporteSimulado` (datos sinteticos); un
transporte real lo escribira el ingeniero cuando BLC entregue credencial SELECT y contrato, sin tocar el resto de la aplicacion.

Separacion de almacenamiento:
  * BD de LECTURA empresarial (SAP/Cosprobell BD): propiedad de BLC; el portal solo lee, nunca escribe ni crea objetos.
  * Almacenamiento PRIVADO del portal (SQLite en /data/estado): usuarios, rutas, decisiones, visitas, auditoria, documentos. Nunca se mezcla.
Politica ante datos defectuosos: nada se corrige en silencio. Filas invalidas se apartan y se reportan; conflictos o cargas incompletas BLOQUEAN
la fuente; si la conexion cae se conserva la ultima carga buena marcada como DEGRADADA (con su fecha); los datos viejos nunca se presentan como vivos.
Una fuente simulada jamas declara ser dato real (`en_vivo=False`, `apta_para_publicar=False`)."""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence

from cxc_bot.payment_semaphore import FacturaSAP, Recibo

from .fuentes import TIPO_VIVA

CONTRATO_VERSION = "1.0"          # version mayor del contrato; un cambio de mayor exige revisar el adaptador
TIPO_SIMULADA = "EMPRESARIAL_SIMULADA"
TIPO_PRUEBAS = "EMPRESARIAL_PRUEBAS"
TOL = 0.01

REQ_CLIENTE = ("codigo_sap", "nombre")
REQ_FACTURA = ("id_factura", "numero", "codigo_cliente", "fecha_factura", "total", "aplicado", "estado")
REQ_RECIBO = ("id_recibo", "numero_recibo", "codigo_cliente", "fecha_contable", "monto")
REQ_SALDO = ("codigo_cliente", "saldo_abierto")


class ErrorConexion(RuntimeError):
    """El transporte no pudo leer (red, timeout, credencial rechazada). No implica datos incorrectos."""


class ContratoIncumplido(RuntimeError):
    """Version de contrato incompatible o carga marcada incompleta por el origen."""


class FuenteNoDisponible(RuntimeError):
    """No hay ninguna carga valida (ni previa) con la que operar."""


class DatosBloqueados(RuntimeError):
    """La carga tiene conflictos que impiden usarla (ids duplicados con contenido distinto, demasiadas filas invalidas, etc.)."""


class Transporte(Protocol):
    """Contrato de LECTURA. Cada metodo devuelve una lista de dicts con los campos del contrato; el transporte nunca escribe."""
    simulado: bool

    def metadatos(self) -> Dict[str, Any]: ...      # {fecha_corte, generado_en, version_contrato, sistema_origen, completo}
    def clientes(self) -> List[Dict[str, Any]]: ...
    def facturas(self) -> List[Dict[str, Any]]: ...
    def recibos(self) -> List[Dict[str, Any]]: ...
    def saldos(self) -> List[Dict[str, Any]]: ...


def _fecha(v) -> Optional[date]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def _num(v) -> float:
    if isinstance(v, bool) or v is None or v == "":
        raise ValueError("número ausente")
    return float(v)


class FuenteEmpresarial:
    en_vivo: bool
    operable = True

    def __init__(self, transporte: Transporte, hoy: Callable[[], date] = date.today, max_edad_dias: int = 2, reintentos: int = 2,
                 pausa: Callable[[float], None] = lambda s: None, tolerancia_invalidos: float = 0.02):
        self.transporte, self._hoy, self.max_edad_dias = transporte, hoy, max_edad_dias
        self.reintentos, self._pausa, self.tolerancia_invalidos = reintentos, pausa, tolerancia_invalidos
        simulado = bool(getattr(transporte, "simulado", True))
        pruebas = (not simulado) and getattr(transporte, "entorno", None) == "pruebas"      # copia de pruebas: datos reales de un respaldo, NO vivos
        self.tipo = TIPO_SIMULADA if simulado else TIPO_PRUEBAS if pruebas else TIPO_VIVA
        self.en_vivo = not simulado and not pruebas
        self.etiqueta = ("FUENTE EMPRESARIAL SIMULADA (datos sintéticos): no representa saldos reales" if simulado else
                         "Cosprobell Backend de PRUEBAS (copia de un respaldo): no es dato vivo ni validado" if pruebas else "Fuente empresarial (solo lectura)")
        self.fecha_corte: date = date.min
        self._d: Optional[dict] = None          # ultima carga VALIDA
        self.version_datos = 0                  # +1 cada vez que una carga valida REEMPLAZA a la anterior: identidad de los datos para invalidar derivados
        self.calidad: Dict[str, Any] = {"estado": "SIN_CARGA", "problemas": []}
        self.ultima_carga_ok: Optional[str] = None
        self.ultimo_error_conexion: Optional[str] = None

    # ------------------------------------------------------------------ carga y validacion
    def recargar(self) -> Dict[str, Any]:
        crudo, err = None, None
        for intento in range(self.reintentos + 1):
            try:
                crudo = {"meta": self.transporte.metadatos(), "clientes": self.transporte.clientes(), "facturas": self.transporte.facturas(),
                         "recibos": self.transporte.recibos(), "saldos": self.transporte.saldos()}
                break
            except ErrorConexion as e:
                err = e
                self._pausa(min(2 ** intento, 30))
        if crudo is None:
            self.ultimo_error_conexion = str(err)
            if self._d is None:
                self.calidad = {"estado": "SIN_CONEXION", "problemas": [f"Sin conexión y sin carga previa: {err}"]}
                raise FuenteNoDisponible(f"No hay conexión con la fuente y no existe una carga previa: {err}")
            self.calidad = {**self.calidad, "estado": "DEGRADADA", "problemas": self.calidad.get("problemas", []) + [f"Sin conexión: se conserva la última carga buena ({self.ultima_carga_ok}). {err}"]}
            return self.calidad
        self.ultimo_error_conexion = None
        try:
            nuevo = self._validar(crudo)
        except (ContratoIncumplido, DatosBloqueados) as e:
            # una carga mala NO reemplaza a la ultima buena
            self.calidad = {"estado": "BLOQUEADA", "problemas": [str(e)], "conserva_carga_previa": self._d is not None}
            if self._d is None:
                raise
            return self.calidad
        self._d = nuevo["datos"]
        self.version_datos += 1
        self.fecha_corte = nuevo["fecha_corte"]
        self.ultima_carga_ok = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.calidad = nuevo["calidad"]
        return self.calidad

    def _validar(self, crudo: dict) -> dict:
        meta, problemas, descartes = crudo["meta"] or {}, [], []
        if str(meta.get("version_contrato", "")).split(".")[0] != CONTRATO_VERSION.split(".")[0]:
            raise ContratoIncumplido(f"Versión de contrato incompatible: origen {meta.get('version_contrato')!r}, portal {CONTRATO_VERSION}.")
        if meta.get("completo") is not True:
            raise ContratoIncumplido("El origen marcó la carga como INCOMPLETA: no se usa.")
        try:
            corte = _fecha(meta.get("fecha_corte"))
        except ValueError:
            corte = None
        if corte is None:
            raise ContratoIncumplido("Falta una fecha_corte válida en los metadatos.")

        def faltan(fila, req):
            return [k for k in req if fila.get(k) in (None, "")]

        # clientes
        clientes: Dict[str, dict] = {}
        for f in crudo["clientes"]:
            f = {**f, "codigo_sap": str(f.get("codigo_sap") or "").strip().upper()}
            if faltan(f, REQ_CLIENTE):
                descartes.append(("cliente", f.get("codigo_sap"), f"faltan {faltan(f, REQ_CLIENTE)}"))
                continue
            if f["codigo_sap"] in clientes:
                if clientes[f["codigo_sap"]] != f:
                    raise DatosBloqueados(f"Cliente {f['codigo_sap']} duplicado con datos distintos.")
                problemas.append(f"cliente duplicado idéntico {f['codigo_sap']} (ignorado)")
                continue
            clientes[f["codigo_sap"]] = f
        # facturas
        facturas: Dict[str, dict] = {}
        for f in crudo["facturas"]:
            f = dict(f)
            f["codigo_cliente"] = str(f.get("codigo_cliente") or "").strip().upper()
            try:
                if faltan(f, REQ_FACTURA):
                    raise ValueError(f"faltan {faltan(f, REQ_FACTURA)}")
                f["_fecha"], f["_venc"] = _fecha(f["fecha_factura"]), _fecha(f.get("fecha_vencimiento"))
                f["_total"], f["_aplicado"] = _num(f["total"]), _num(f["aplicado"])
                if f["estado"] not in ("O", "C"):
                    raise ValueError(f"estado {f['estado']!r} inválido (O/C)")
                if f["_total"] < 0 or f["_aplicado"] < -TOL or f["_aplicado"] > f["_total"] + TOL:
                    raise ValueError("importes incoherentes (aplicado fuera de 0..total)")
                if f["_fecha"] > corte:
                    raise ValueError("fecha de factura posterior a la fecha de corte")
            except (ValueError, TypeError) as e:
                descartes.append(("factura", f.get("id_factura"), str(e)))
                continue
            clave = str(f["id_factura"])
            if clave in facturas:
                a, b = facturas[clave], f
                if any(a[k] != b[k] for k in ("codigo_cliente", "_fecha", "_total", "_aplicado", "estado", "numero")):
                    raise DatosBloqueados(f"Factura {clave} duplicada con contenido distinto: no se elige una versión en silencio.")
                problemas.append(f"factura duplicada idéntica {clave} (ignorada)")
                continue
            facturas[clave] = f
            if f["codigo_cliente"] not in clientes:
                problemas.append(f"factura {clave} de un cliente que no está en la lista de clientes ({f['codigo_cliente']})")
        # recibos
        recibos_por_factura: Dict[str, List[Recibo]] = {}
        vistos = set()
        for r in crudo["recibos"]:
            try:
                if faltan(r, REQ_RECIBO):
                    raise ValueError(f"faltan {faltan(r, REQ_RECIBO)}")
                fecha, monto = _fecha(r["fecha_contable"]), _num(r["monto"])
                if monto <= 0:
                    raise ValueError("monto no positivo")
            except (ValueError, TypeError) as e:
                descartes.append(("recibo", r.get("id_recibo"), str(e)))
                continue
            for ap in r.get("aplicaciones") or []:
                idf = str(ap.get("id_factura"))
                if (r["id_recibo"], idf) in vistos:
                    problemas.append(f"aplicación duplicada recibo {r['id_recibo']} → factura {idf} (ignorada)")
                    continue
                vistos.add((r["id_recibo"], idf))
                if idf not in facturas:
                    problemas.append(f"recibo {r['id_recibo']} aplica a una factura inexistente ({idf})")
                    continue
                recibos_por_factura.setdefault(idf, []).append(Recibo(str(r["numero_recibo"]), fecha, _num(ap.get("monto", monto)), str(r.get("via") or "empresarial")))
        # saldos provistos por el origen (NO se recalculan)
        saldos: Dict[str, float] = {}
        for s in crudo["saldos"]:
            try:
                if faltan(s, REQ_SALDO):
                    raise ValueError(f"faltan {faltan(s, REQ_SALDO)}")
                saldos[str(s["codigo_cliente"]).strip().upper()] = _num(s["saldo_abierto"])
            except (ValueError, TypeError) as e:
                descartes.append(("saldo", s.get("codigo_cliente"), str(e)))
        total_filas = len(crudo["clientes"]) + len(crudo["facturas"]) + len(crudo["recibos"]) + len(crudo["saldos"])
        if total_filas == 0:
            raise DatosBloqueados("La carga no trae ninguna fila.")
        if len(descartes) / total_filas > self.tolerancia_invalidos:
            raise DatosBloqueados(f"Demasiadas filas inválidas ({len(descartes)} de {total_filas}): la carga se rechaza. Primeras: {descartes[:3]}")
        # conciliacion saldo provisto vs facturas abiertas (solo reporta)
        calc: Dict[str, float] = {}
        for f in facturas.values():
            if f["estado"] == "O":
                calc[f["codigo_cliente"]] = calc.get(f["codigo_cliente"], 0.0) + f["_total"] - f["_aplicado"]
        difs = [{"codigo": k, "saldo_origen": saldos.get(k), "suma_facturas_abiertas": round(v, 2)} for k, v in sorted(calc.items())
                if k in saldos and abs(saldos[k] - v) > TOL]
        sin_saldo = sorted(k for k in calc if k not in saldos)
        if difs:
            problemas.append(f"{len(difs)} cliente(s) con saldo del origen distinto de la suma de sus facturas abiertas (puede haber notas de crédito, anticipos o pagos sin aplicar): se reporta, no se corrige")
        if sin_saldo:
            problemas.append(f"{len(sin_saldo)} cliente(s) con facturas abiertas pero sin saldo provisto: se usa la suma de facturas y se marca")
        sin_control = sorted(k for k in clientes if k not in saldos)
        if saldos and sin_control:   # el origen entrega saldos de control pero faltan algunos: un nulo NO es cero, y el valor mostrado no esta certificado
            problemas.append(f"{len(sin_control)} cliente(s) sin saldo de control provisto (nulo, no cero): su saldo mostrado es la suma de sus documentos abiertos o 0 si no tienen, y NO está certificado")
        # construir FacturaSAP
        lista: Dict[str, List[FacturaSAP]] = {}
        for idf, f in facturas.items():
            lista.setdefault(f["codigo_cliente"], []).append(FacturaSAP(idf, str(f["numero"]), f["_fecha"], f["_venc"], f["_total"], f["_aplicado"], f["estado"],
                                                                      tuple(sorted(recibos_por_factura.get(idf, []), key=lambda r: (r.fecha, r.numero)))))
        edad = (self._hoy() - corte).days
        estado = "OK" if not descartes and not difs else "CON_ADVERTENCIAS"
        calidad = {"estado": estado, "problemas": problemas, "filas_descartadas": descartes, "diferencias_saldo": difs, "sin_saldo_provisto": sin_saldo,
                   "edad_dias": edad, "vigente": 0 <= edad <= self.max_edad_dias, "filas": total_filas, "version_contrato": meta.get("version_contrato"),
                   "sistema_origen": meta.get("sistema_origen")}
        if not getattr(self.transporte, "apto_para_publicar", True):
            problemas.append(getattr(self.transporte, "motivo_no_apto", "El transporte declara que la fuente no es apta para publicar."))
        problemas.extend(list(getattr(self.transporte, "advertencias", []) or []) + [f"{k}: {v} excluido(s)" for k, v in (getattr(self.transporte, "excluidos", {}) or {}).items()])
        if not calidad["vigente"]:
            calidad["problemas"] = problemas + [f"Datos de hace {edad} día(s); máximo aceptado {self.max_edad_dias}: la fuente NO es apta para publicar."]
        return {"fecha_corte": corte, "calidad": calidad,
                "datos": {"clientes": clientes, "facturas": lista, "saldos": saldos, "calculado": calc}}

    # ------------------------------------------------------------------ estado
    @property
    def apta_para_publicar(self) -> bool:
        """Solo una fuente en vivo REAL (no simulada), cargada, vigente y sin bloqueos puede respaldar documentos no-borrador."""
        return bool(self.en_vivo and self._d is not None and self._vigente_hoy() and self.calidad.get("estado") in ("OK", "CON_ADVERTENCIAS")
                    and getattr(self.transporte, "apto_para_publicar", True))     # p. ej. Gelsyn sin partidas abiertas/conciliaciones: nunca vigente

    def _vigente_hoy(self) -> bool:
        """Vigencia EVALUADA AHORA (no la del momento de la carga): un proceso largo no puede seguir publicando con datos que ya envejecieron."""
        return 0 <= (self._hoy() - self.fecha_corte).days <= self.max_edad_dias

    def _datos(self) -> dict:
        if self._d is None:
            self.recargar()
        return self._d

    def frescura(self, hoy: Optional[date] = None) -> Dict[str, Any]:
        self._datos()
        edad = ((hoy or self._hoy()) - self.fecha_corte).days
        motivo = "; ".join(self.calidad.get("problemas", [])[:3]) or "Carga validada."
        if not self.en_vivo:
            motivo = ("FUENTE SIMULADA: " if self.tipo == TIPO_SIMULADA else "COPIA DE PRUEBAS (no es dato vivo): ") + motivo
        return {"tipo": self.tipo, "fecha_corte": self.fecha_corte.isoformat(), "edad_dias": edad, "vigente": self._vigente_hoy() and self.en_vivo,
                "calidad": self.calidad.get("estado"), "motivo": motivo, "ultima_carga_ok": self.ultima_carga_ok}

    # ------------------------------------------------------------------ interfaz FuenteDatos (solo lectura)
    def facturas_por_cliente(self, codigos: Sequence[str]) -> Dict[str, List[FacturaSAP]]:
        d = self._datos()["facturas"]
        return {c.upper(): list(d.get(c.upper(), [])) for c in codigos}

    def nombre_cliente(self, codigo: str) -> Optional[str]:
        c = self._datos()["clientes"].get(codigo.upper())
        return c["nombre"] if c else None

    def saldo_abierto(self, codigo: str) -> float:
        d = self._datos()
        k = codigo.upper()
        return round(d["saldos"].get(k, d["calculado"].get(k, 0.0)), 2)

    def registros_sap(self) -> List[tuple]:
        return [(k, v["nombre"], self.saldo_abierto(k)) for k, v in sorted(self._datos()["clientes"].items())]

    def documentos_abiertos(self) -> List[Dict[str, Any]]:
        return [{"cliente": c, "id": f.id_factura, "numero": f.numero, "fecha": f.fecha_factura, "vence": f.vencimiento_sap, "total": f.total, "aplicado": f.aplicado_sap}
                for c, fs in self._datos()["facturas"].items() for f in fs if f.estado_sap == "O"]


# ====================================================================== transporte SIMULADO (datos sinteticos + fallas inyectables)
class TransporteSimulado:
    """Entrega datos SINTETICOS con el formato del contrato. `fallas` permite simular: desactualizado_dias, incompleto, duplicar_identicas,
    duplicar_conflicto, quitar_campo=(entidad, campo), conexion_falla_veces=n, saldo_discrepante, version_contrato, factura_posterior_a_corte,
    importes_incoherentes, recibo_a_factura_inexistente."""

    simulado = True

    def __init__(self, fuente_sintetica, hoy: Callable[[], date] = date.today, fallas: Optional[dict] = None):
        self.src, self._hoy, self.fallas = fuente_sintetica, hoy, dict(fallas or {})
        self.llamadas = 0

    def _conexion(self):
        self.llamadas += 1
        n = self.fallas.get("conexion_falla_veces", 0)
        if n and self.llamadas <= n:
            raise ErrorConexion("Tiempo de espera agotado (simulado).")

    def metadatos(self):
        self._conexion()
        corte = self.src.fecha_corte
        if self.fallas.get("desactualizado_dias") is not None:
            corte = self._hoy() - __import__("datetime").timedelta(days=self.fallas["desactualizado_dias"])
        return {"fecha_corte": corte.isoformat(), "generado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "version_contrato": self.fallas.get("version_contrato", CONTRATO_VERSION), "sistema_origen": "SIMULADO (sintético)",
                "completo": not self.fallas.get("incompleto", False)}

    def _quitar(self, entidad, filas):
        q = self.fallas.get("quitar_campo")
        if q and q[0] == entidad:
            filas = [{k: v for k, v in f.items() if k != q[1]} if i % 2 == 0 else f for i, f in enumerate(filas)]
        return filas

    def clientes(self):
        return self._quitar("clientes", [{"codigo_sap": c, "nombre": n, "activo": True} for c, n, _ in self.src.registros_sap()])

    def facturas(self):
        codigos = [c for c, _, _ in self.src.registros_sap()]
        filas = []
        for c, fs in self.src.facturas_por_cliente(codigos).items():
            for f in fs:
                filas.append({"id_factura": f.id_factura, "numero": f.numero, "codigo_cliente": c, "fecha_factura": f.fecha_factura.isoformat(),
                              "fecha_vencimiento": f.vencimiento_sap.isoformat() if f.vencimiento_sap else None, "total": f.total, "aplicado": f.aplicado_sap,
                              "estado": f.estado_sap, "moneda": "HNL", "actualizado_en": "2026-10-08T00:00:00Z"})
        filas = self._quitar("facturas", filas)
        if self.fallas.get("duplicar_identicas") and filas:
            filas.append(dict(filas[0]))
        if self.fallas.get("duplicar_conflicto") and filas:
            filas.append({**filas[0], "total": filas[0]["total"] + 100})
        if self.fallas.get("importes_incoherentes") and filas:
            filas[1] = {**filas[1], "aplicado": filas[1]["total"] * 2}
        if self.fallas.get("factura_posterior_a_corte") and filas:
            filas[2] = {**filas[2], "fecha_factura": "2099-01-01"}
        return filas

    def recibos(self):
        codigos = [c for c, _, _ in self.src.registros_sap()]
        filas = []
        for c, fs in self.src.facturas_por_cliente(codigos).items():
            for f in fs:
                for r in f.recibos:
                    filas.append({"id_recibo": f"{r.numero}-{f.id_factura}", "numero_recibo": r.numero, "codigo_cliente": c, "fecha_contable": r.fecha.isoformat(),
                                  "monto": r.monto, "aplicaciones": [{"id_factura": f.id_factura, "monto": r.monto}], "via": r.via or "aplicacion_directa",
                                  "actualizado_en": "2026-10-08T00:00:00Z"})
        if self.fallas.get("recibo_a_factura_inexistente"):
            filas.append({"id_recibo": "RX", "numero_recibo": "RX", "codigo_cliente": "C90001", "fecha_contable": "2026-01-01", "monto": 10.0,
                          "aplicaciones": [{"id_factura": "NO-EXISTE", "monto": 10.0}]})
        return self._quitar("recibos", filas)

    def saldos(self):
        out = [{"codigo_cliente": c, "saldo_abierto": s, "moneda": "HNL"} for c, _, s in self.src.registros_sap()]
        if self.fallas.get("saldo_discrepante") and out:
            out[6] = {**out[6], "saldo_abierto": out[6]["saldo_abierto"] + 500}
        return self._quitar("saldos", out)
