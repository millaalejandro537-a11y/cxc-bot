"""Comportamiento de pago: semaforo por factura y clasificacion del cliente.

DEPRECADO (2026-10-09): `evaluate_client` / `evaluate_portfolio` implementan reglas SUPERADAS (predominio y minimo de 4
facturas PAGADAS). El UNICO motor vigente es `cxc_bot.payment_semaphore` (ultimas 10 facturas, >= 4 en todo el historial,
media de mora >= 0, override de gerencia con evidencia por factura). Este modulo se conserva solo por sus primitivas
(`InvoiceRecord`, `assess_invoice`, `CreditPolicy`) y por compatibilidad; NO debe dirigir decisiones productivas.

Modulo PURO: recibe facturas ya leidas (de la fuente que sea) y devuelve
evaluaciones. No lee SAP ni Cosprobell BD, no abre Excel, no escribe nada y
no cambia viñetas ni credito: solo PROPONE. La viñeta roja y cualquier
suspension/restriccion de credito requieren aprobacion de gerencia, por eso
`ClientAssessment.vineta_aprobada` es siempre None y MALO solo marca
`requiere_gerencia`.

Reglas vigentes (Hub "Cosprobell CxC -- Principal", decisiones 08/10/2026):

- Dias de atraso = (fecha de pago - vencimiento); para una factura abierta,
  (fecha de corte - vencimiento). Pago anticipado/puntual da <= 0.
- Categoria por factura: A (verde) <= 5 dias; B (azul) 6-15; C (amarillo)
  16-60; MALO (rojo) >= 61. El importe NO interviene.
- Cliente con 0-3 facturas completamente pagadas (y verificables): EN_PRUEBA,
  sin viñeta. Desde 4: categoria PREDOMINANTE entre sus facturas evaluadas
  (2 A + 4 B = B). Empate sin predominio: REVISION_HUMANA. Nunca se promedian
  dias de mora.
- Facturas abiertas ya vencidas (mas alla de la tolerancia A) SI cuentan para
  categoria y alertas, sin esperar su pago, pero NO cuentan como pagadas para
  el minimo de 4. Al liquidarse se evalua la MISMA factura con sus dias
  reales (no hay doble conteo: la evaluacion se recalcula, no se acumula).
- Reincidencia: >= 6 facturas pagadas consecutivas con mas de 5 dias de
  atraso. Mejora: >= 3 facturas posteriores a la conversacion con el cliente,
  consecutivas, pagadas a mas tardar en su vencimiento. La mejora pide
  reevaluacion; no cambia la categoria.

Supuestos que NO estaban cerrados y quedan explicitos (ver PROJECT-STATE):
- Una factura abierta con atraso <= 5 dias (o aun no vencida) no cuenta: no
  es evidencia ni de buen ni de mal comportamiento todavia.
- "Predominio" = categoria con estrictamente mas facturas; un empate en el
  maximo va a revision humana. No se define un umbral de proporcion.
- "Consecutivas" se ordena por (vencimiento usado, fecha de factura, numero).
- Una factura con vencimiento SAP el mismo dia que la factura puede ser venta
  de CONTADO: mientras `es_contado` sea None queda marcada
  POSIBLE_CONTADO_PENDIENTE y el cliente no es `definitivo`.
"""

from __future__ import annotations

import warnings
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .text_utils import normalize_key


class Categoria(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    MALO = "MALO"


COLOR_POR_CATEGORIA: Dict[Categoria, str] = {
    Categoria.A: "VERDE",
    Categoria.B: "AZUL",
    Categoria.C: "AMARILLO",
    Categoria.MALO: "ROJO",
}


class EstadoCliente(str, Enum):
    EN_PRUEBA = "EN_PRUEBA"
    CLASIFICADO = "CLASIFICADO"
    REVISION_HUMANA = "REVISION_HUMANA"


# Excepciones por factura (codigos estables, utiles para reportes/pruebas).
EXC_CONTADO_CONFIRMADO = "CONTADO_CONFIRMADO"
EXC_POSIBLE_CONTADO = "POSIBLE_CONTADO_PENDIENTE"
EXC_LIQUIDACION_NO_VERIFICABLE = "LIQUIDACION_NO_VERIFICABLE"
EXC_SIN_VENCIMIENTO = "SIN_VENCIMIENTO"
EXC_FECHA_INCONSISTENTE = "FECHA_INCONSISTENTE"
EXC_ABIERTA_SIN_ATRASO = "ABIERTA_SIN_ATRASO_SIGNIFICATIVO"

# Excepciones que dejan el resultado del cliente como NO definitivo.
_PENDIENTES = {EXC_POSIBLE_CONTADO, EXC_LIQUIDACION_NO_VERIFICABLE, EXC_SIN_VENCIMIENTO, EXC_FECHA_INCONSISTENTE}

# Alertas (independientes del color del cliente).
ALERTA_ABIERTA_MALA = "ABIERTA_MORA_RANGO_MALO"
ALERTA_PAGADA_MALA = "PAGADA_RANGO_MALO"
ALERTA_REINCIDENCIA = "REINCIDENCIA"
ALERTA_MEJORA = "MEJORA_SOSTENIDA"


@dataclass(frozen=True)
class BehaviorRules:
    """Umbrales vigentes; configurables solo para poder probar limites."""

    tolerancia_a: int = 5       # A: hasta 5 dias tarde
    limite_b: int = 15          # B: 6-15
    limite_c: int = 60          # C: 16-60; MALO: 61+
    min_pagadas: int = 4        # minimo de facturas completamente pagadas
    reincidencia_n: int = 6     # consecutivas tardias (> tolerancia_a)
    mejora_n: int = 3           # consecutivas puntuales tras la conversacion


@dataclass(frozen=True)
class CreditPolicy:
    """Como se obtiene el vencimiento usado para el calculo.

    `plazo_supuesto_dias=None`: se usa el vencimiento de SAP tal cual.
    Con un valor (p.ej. 60 en el piloto de Las Vegas) el vencimiento usado es
    fecha_factura + plazo; el vencimiento SAP original se conserva siempre
    junto al usado. SAP nunca se corrige."""

    plazo_supuesto_dias: Optional[int] = None
    motivo: str = ""

    @property
    def origen(self) -> str:
        return "SAP" if self.plazo_supuesto_dias is None else f"SUPUESTO_{self.plazo_supuesto_dias}D"


SAP_DUE_DATES = CreditPolicy()
# Piloto Las Vegas (ruta foranea, credito habitual 60 dias). NO extender a
# rutas locales (30 dias) ni asumirlo para otras rutas sin decision expresa.
PILOT_LAS_VEGAS_60D = CreditPolicy(
    plazo_supuesto_dias=60,
    motivo="Piloto Las Vegas (foranea): plazo supuesto de 60 dias desde la factura; hay vencimientos de 30 dias erroneos en SAP.",
)


@dataclass(frozen=True)
class InvoiceRecord:
    """Una factura de credito de un cliente, ya leida de la fuente."""

    cliente: str
    factura: str
    fecha_factura: date
    vencimiento_sap: Optional[date]
    fecha_pago: Optional[date] = None          # fecha efectiva de liquidacion; None si abierta
    liquidada_sin_fecha: bool = False          # cerrada pero sin fecha de pago reconstruible
    es_contado: Optional[bool] = None          # None = sin confirmar
    importe: float = 0.0                       # informativo: NUNCA afecta la categoria


@dataclass(frozen=True)
class InvoiceAssessment:
    cliente: str
    factura: str
    fecha_factura: date
    vencimiento_sap: Optional[date]
    vencimiento_usado: Optional[date]
    origen_vencimiento: str
    fecha_pago: Optional[date]
    abierta: bool
    dias_atraso: Optional[int]
    categoria: Optional[Categoria]
    evaluada: bool                              # entra a predominio/alertas de categoria
    pagada_verificable: bool                    # cuenta para el minimo de facturas pagadas
    excepciones: Tuple[str, ...] = ()
    tardia: bool = False                        # atraso > tolerancia A (solo facturas pagadas verificables)


@dataclass(frozen=True)
class Alert:
    tipo: str
    cliente: str
    detalle: str
    facturas: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ClientAssessment:
    cliente: str
    estado: EstadoCliente
    categoria: Optional[Categoria]
    color_candidato: Optional[str]
    conteo: Dict[str, int]
    pagadas_total: int
    pagadas_verificables: int
    facturas: Tuple[InvoiceAssessment, ...]
    alertas: Tuple[Alert, ...]
    reincidencia: bool
    reincidencia_facturas: Tuple[str, ...]
    mejora: bool
    mejora_consecutivas: int
    requiere_gerencia: bool
    requiere_reevaluacion: bool
    motivos_revision: Tuple[str, ...]
    definitivo: bool
    vineta_aprobada: Optional[str] = None       # SIEMPRE None: la aprueba gerencia


def categorize_days(dias_atraso: int, rules: BehaviorRules = BehaviorRules()) -> Categoria:
    if dias_atraso <= rules.tolerancia_a:
        return Categoria.A
    if dias_atraso <= rules.limite_b:
        return Categoria.B
    if dias_atraso <= rules.limite_c:
        return Categoria.C
    return Categoria.MALO


def assess_invoice(
    inv: InvoiceRecord,
    fecha_corte: date,
    policy: CreditPolicy = SAP_DUE_DATES,
    rules: BehaviorRules = BehaviorRules(),
) -> InvoiceAssessment:
    excepciones: List[str] = []
    abierta = inv.fecha_pago is None and not inv.liquidada_sin_fecha

    if policy.plazo_supuesto_dias is not None:
        vencimiento_usado: Optional[date] = inv.fecha_factura + timedelta(days=policy.plazo_supuesto_dias)
        origen = policy.origen
    else:
        vencimiento_usado = inv.vencimiento_sap
        origen = "SAP"

    def _result(dias=None, categoria=None, evaluada=False, verificable=False):
        return InvoiceAssessment(
            cliente=inv.cliente, factura=inv.factura, fecha_factura=inv.fecha_factura,
            vencimiento_sap=inv.vencimiento_sap, vencimiento_usado=vencimiento_usado,
            origen_vencimiento=origen, fecha_pago=inv.fecha_pago, abierta=abierta,
            dias_atraso=dias, categoria=categoria, evaluada=evaluada,
            pagada_verificable=verificable, excepciones=tuple(excepciones),
            tardia=verificable and dias is not None and dias > rules.tolerancia_a,
        )

    if inv.es_contado is True:
        excepciones.append(EXC_CONTADO_CONFIRMADO)
        return _result()  # venta de contado: no es comportamiento de credito

    if inv.es_contado is None and inv.vencimiento_sap is not None and inv.vencimiento_sap == inv.fecha_factura:
        excepciones.append(EXC_POSIBLE_CONTADO)  # se evalua, pero provisional

    if vencimiento_usado is None:
        excepciones.append(EXC_SIN_VENCIMIENTO)
        return _result()

    if inv.fecha_pago is not None and (inv.fecha_pago < inv.fecha_factura or inv.fecha_pago > fecha_corte):
        excepciones.append(EXC_FECHA_INCONSISTENTE)
        return _result()

    if inv.liquidada_sin_fecha:
        excepciones.append(EXC_LIQUIDACION_NO_VERIFICABLE)
        return _result()  # pagada, pero sin dias de atraso confiables

    if abierta:
        dias = (fecha_corte - vencimiento_usado).days
        if dias <= rules.tolerancia_a:
            excepciones.append(EXC_ABIERTA_SIN_ATRASO)
            return _result(dias=dias)
        return _result(dias=dias, categoria=categorize_days(dias, rules), evaluada=True)

    dias = (inv.fecha_pago - vencimiento_usado).days
    return _result(dias=dias, categoria=categorize_days(dias, rules), evaluada=True, verificable=True)


def _order_key(a: InvoiceAssessment):
    return (a.vencimiento_usado or a.fecha_factura, a.fecha_factura, a.factura)


def _longest_run(items: Sequence[InvoiceAssessment], predicate) -> List[InvoiceAssessment]:
    best: List[InvoiceAssessment] = []
    current: List[InvoiceAssessment] = []
    for it in items:
        if predicate(it):
            current.append(it)
            if len(current) > len(best):
                best = list(current)
        else:
            current = []
    return best


def evaluate_client(
    cliente: str,
    invoices: Iterable[InvoiceRecord],
    fecha_corte: date,
    policy: CreditPolicy = SAP_DUE_DATES,
    rules: BehaviorRules = BehaviorRules(),
    fecha_conversacion: Optional[date] = None,
) -> ClientAssessment:
    warnings.warn("payment_behavior.evaluate_client usa reglas superadas; usar cxc_bot.payment_semaphore.evaluar_cliente",
                  DeprecationWarning, stacklevel=2)
    assessed = sorted(
        (assess_invoice(i, fecha_corte, policy, rules) for i in invoices), key=_order_key
    )
    # Los contados confirmados no son credito: fuera de todos los conteos.
    assessed = [a for a in assessed if EXC_CONTADO_CONFIRMADO not in a.excepciones]

    pagadas = [a for a in assessed if not a.abierta]  # incluye liquidadas sin fecha
    verificables = [a for a in assessed if a.pagada_verificable]
    evaluadas = [a for a in assessed if a.evaluada]
    conteo = Counter(a.categoria.value for a in evaluadas)

    motivos: List[str] = []
    alertas: List[Alert] = []

    # --- Alertas por factura (independientes del color del cliente) ----------
    for a in evaluadas:
        if a.categoria is Categoria.MALO:
            if a.abierta:
                alertas.append(Alert(ALERTA_ABIERTA_MALA, cliente,
                                     f"Factura abierta con {a.dias_atraso} dias de mora.", (a.factura,)))
            else:
                alertas.append(Alert(ALERTA_PAGADA_MALA, cliente,
                                     f"Factura pagada con {a.dias_atraso} dias de atraso.", (a.factura,)))

    # --- Reincidencia: >= N pagadas consecutivas con atraso > tolerancia -----
    run = _longest_run(verificables, lambda a: a.tardia)
    reincidencia = len(run) >= rules.reincidencia_n
    reincidencia_ids = tuple(a.factura for a in run) if reincidencia else ()
    if reincidencia:
        alertas.append(Alert(ALERTA_REINCIDENCIA, cliente,
                             f"{len(run)} facturas pagadas consecutivas con mas de {rules.tolerancia_a} dias de atraso.",
                             reincidencia_ids))
        motivos.append("reincidencia: revisar con gerencia/ventas")

    # --- Mejora: >= N posteriores a la conversacion, consecutivas y puntuales -
    mejora_run: List[InvoiceAssessment] = []
    if fecha_conversacion is not None:
        posteriores = [a for a in verificables if a.fecha_factura > fecha_conversacion]
        mejora_run = _longest_run(posteriores, lambda a: a.dias_atraso is not None and a.dias_atraso <= 0)
    mejora = len(mejora_run) >= rules.mejora_n
    if mejora:
        alertas.append(Alert(ALERTA_MEJORA, cliente,
                             f"{len(mejora_run)} facturas consecutivas pagadas a tiempo tras la conversacion.",
                             tuple(a.factura for a in mejora_run)))
        motivos.append("mejora sostenida: reevaluar (la categoria no cambia sola)")

    # --- Clasificacion del cliente -------------------------------------------
    categoria: Optional[Categoria] = None
    requiere_gerencia = False
    if len(verificables) < rules.min_pagadas:
        estado = EstadoCliente.EN_PRUEBA
        if len(pagadas) >= rules.min_pagadas:
            motivos.append("tiene facturas pagadas sin fecha de liquidacion verificable; faltan para el minimo")
    else:
        top = conteo.most_common()
        if not top:  # no deberia ocurrir con >= min_pagadas verificables
            estado = EstadoCliente.REVISION_HUMANA
            motivos.append("sin facturas evaluables")
        elif len(top) > 1 and top[0][1] == top[1][1]:
            estado = EstadoCliente.REVISION_HUMANA
            empatadas = sorted(c for c, n in top if n == top[0][1])
            motivos.append(f"sin predominio claro (empate entre {', '.join(empatadas)})")
        else:
            estado = EstadoCliente.CLASIFICADO
            categoria = Categoria(top[0][0])
            if categoria is Categoria.MALO:
                requiere_gerencia = True
                motivos.append("predominio MALO: viñeta roja/restriccion de credito solo con aprobacion de gerencia")

    pendientes = sorted({e for a in assessed for e in a.excepciones if e in _PENDIENTES})
    for e in pendientes:
        motivos.append(f"excepcion pendiente: {e}")

    return ClientAssessment(
        cliente=cliente,
        estado=estado,
        categoria=categoria,
        color_candidato=COLOR_POR_CATEGORIA[categoria] if categoria else None,
        conteo=dict(conteo),
        pagadas_total=len(pagadas),
        pagadas_verificables=len(verificables),
        facturas=tuple(assessed),
        alertas=tuple(alertas),
        reincidencia=reincidencia,
        reincidencia_facturas=reincidencia_ids,
        mejora=mejora,
        mejora_consecutivas=len(mejora_run),
        requiere_gerencia=requiere_gerencia,
        requiere_reevaluacion=mejora,
        motivos_revision=tuple(motivos),
        definitivo=not pendientes,
    )


def evaluate_portfolio(
    invoices: Iterable[InvoiceRecord],
    fecha_corte: date,
    policy: CreditPolicy = SAP_DUE_DATES,
    rules: BehaviorRules = BehaviorRules(),
    conversaciones: Optional[Dict[str, date]] = None,
    clientes: Optional[Sequence[str]] = None,
) -> Dict[str, ClientAssessment]:
    """Evalua varios clientes. `clientes` fija el universo (p.ej. los 24 codigos
    de la ruta): un cliente sin facturas sale EN_PRUEBA en vez de omitirse.
    Los codigos se agrupan sin distinguir mayusculas/espacios."""

    conversaciones = {normalize_key(k): v for k, v in (conversaciones or {}).items()}
    grupos: Dict[str, List[InvoiceRecord]] = {}
    nombres: Dict[str, str] = {}
    for c in clientes or []:
        k = normalize_key(c)
        grupos.setdefault(k, [])
        nombres.setdefault(k, c.strip())
    for inv in invoices:
        k = normalize_key(inv.cliente)
        grupos.setdefault(k, []).append(inv)
        nombres.setdefault(k, inv.cliente.strip())
    return {
        nombres[k]: evaluate_client(nombres[k], grupo, fecha_corte, policy, rules, conversaciones.get(k))
        for k, grupo in grupos.items()
    }
