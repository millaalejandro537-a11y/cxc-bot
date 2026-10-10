"""Semaforo de comportamiento de pago -- UNICO motor vigente (reglas aprobadas 09/10/2026).

Lo usan CXC BOT (rutas/Excel) y el portal CxC. Modulo PURO: recibe facturas con su evidencia (lo que SAP dice y los
recibos que las respaldan) y devuelve la clasificacion, las alertas y la evidencia por factura. No lee SAP ni
Cosprobell BD, no abre Excel y no escribe nada. Solo PROPONE: la viñeta roja / solo contado la decide gerencia.

Reglas (Hub "Cosprobell CxC -- Principal", LEEME del paquete de datos 08-09/10/2026):
- Se evaluan las 10 facturas mas recientes (fecha de emision DESC, desempate numero SAP DESC); entran pagadas, abiertas
  y cierres no acreditados.
- Historial total (todas las facturas, pagadas y abiertas) de 0-3 -> EN_PRUEBA (blanca). Desde 4 es elegible.
- Plazo operativo de la ruta (foranea 60 / local 30 dias desde la emision). El vencimiento SAP se conserva para cotejo
  pero NO es la base del plazo.
- Mora de una factura = max(0, referencia - emision - plazo). Referencia: fecha contable del ultimo recibo si esta
  pagada integramente con recibos; fecha de corte si esta abierta.
- Entran a la media: pagadas integramente respaldadas por recibos y abiertas con mora > 5 dias. Abiertas con mora <= 5 y
  cierres sin cobro integral acreditado (p. ej. nota de credito) cuentan en la ventana y el historial, no en la media.
- Media sin redondear, sin predominancia: A <= 5; B > 5 y <= 15; C > 15 y <= 60; > 60 CANDIDATO_SOLO_CONTADO
  (revision humana obligatoria).
- Alerta independiente por cada factura evaluada con mora > 60, aunque la media del cliente sea mejor. Se SEPARAN:
  abiertas = alertas OPERATIVAS DE COBRO (hay saldo pendiente); pagadas = alertas HISTORICAS DE COMPORTAMIENTO (ya
  cobradas: nunca son cobro pendiente).
- Las decisiones de gerencia (override) se guardan APARTE del calculo automatico, con autor, motivo y fecha.

Sustituye a `payment_behavior.evaluate_client` (reglas superadas: predominio y minimo de 4 PAGADAS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

VENTANA_FACTURAS = 10
MIN_HISTORIAL = 4
TOLERANCIA_A = 5
LIMITE_B = 15
LIMITE_C = 60
ABIERTA_MORA_MINIMA = 5       # una abierta solo entra a la media si su mora supera este valor
ALERTA_FACTURA = 60
TOLERANCIA_MONTO = 0.005

PLAZO_FORANEA = 60
PLAZO_LOCAL = 30

# Clasificacion calculada
A, B, C = "A", "B", "C"
CANDIDATO_SOLO_CONTADO = "CANDIDATO_SOLO_CONTADO"
EN_PRUEBA = "EN_PRUEBA"
REVISION_HUMANA = "REVISION_HUMANA"
# Solo por decision de gerencia
SOLO_CONTADO = "SOLO_CONTADO"
SIN_DATOS = "SIN_DATOS"

COLOR = {
    A: "VERDE", B: "AZUL", C: "AMARILLO", SOLO_CONTADO: "ROJO", EN_PRUEBA: "BLANCO",
    CANDIDATO_SOLO_CONTADO: "PENDIENTE_GERENCIA", REVISION_HUMANA: "PENDIENTE_GERENCIA", SIN_DATOS: "SIN_DATOS",
}
ETIQUETA = {
    A: "Cliente excelente", B: "Cliente bueno", C: "Cliente regular", SOLO_CONTADO: "Solo contado",
    EN_PRUEBA: "Cliente de prueba", CANDIDATO_SOLO_CONTADO: "Candidato a solo contado (revisión de gerencia)",
    REVISION_HUMANA: "Revisión humana", SIN_DATOS: "Sin datos cargados",
}

# Estado verificado de la factura
PAGADA = "PAGADA"
ABIERTA = "ABIERTA"
CERRADA_NO_ACREDITADA = "CERRADA_NO_ACREDITADA"

EXC_ABIERTA_SIN_ATRASO = "ABIERTA_0_A_5_DIAS"
EXC_CIERRE_NO_ACREDITADO = "CIERRE_SIN_COBRO_INTEGRO_ACREDITADO"
EXC_RECIBO_ANTERIOR_A_EMISION = "RECIBO_ANTERIOR_A_EMISION"
EXC_POSIBLE_CONTADO = "VENCIMIENTO_SAP_IGUAL_A_EMISION"


@dataclass(frozen=True)
class Recibo:
    numero: str
    fecha: date                 # fecha CONTABLE del recibo (no necesariamente el dia bancario)
    monto: float
    via: str = ""               # de que consulta/relacion sale la evidencia


@dataclass(frozen=True)
class FacturaSAP:
    id_factura: str
    numero: str
    fecha_factura: date
    vencimiento_sap: Optional[date]
    total: float
    aplicado_sap: float
    estado_sap: str             # 'C' cerrada / 'O' abierta (tal como la entrega SAP)
    recibos: Tuple[Recibo, ...] = ()


ALERTA_COBRO = "COBRO_OPERATIVA"          # factura ABIERTA con mora > 60: gestion de cobro
ALERTA_HISTORICA = "COMPORTAMIENTO_HISTORICO"  # factura PAGADA con mora > 60: solo historial de conducta


@dataclass(frozen=True)
class Alerta:
    tipo: str
    numero: str
    mora: int
    saldo_pendiente: Optional[float]   # solo en alertas de cobro; None en historicas (no hay nada que cobrar)
    texto: str

    def to_dict(self) -> dict:
        return {"tipo": self.tipo, "numero": self.numero, "mora": self.mora, "saldo_pendiente": self.saldo_pendiente, "texto": self.texto}


@dataclass(frozen=True)
class DecisionGerencia:
    """Override individual, fundamentado y autorizado. No cambia umbrales ni el calculo base."""

    clasificacion_final: str    # A | B | C | SOLO_CONTADO | EN_PRUEBA
    motivo: str
    actor: str
    fecha: Optional[str] = None


@dataclass(frozen=True)
class EvidenciaFactura:
    id_factura: str
    numero: str
    fecha_factura: date
    vencimiento_ruta: date
    vencimiento_sap: Optional[date]
    estado: str
    total: float
    aplicado_sap: float
    recibos_total: float
    ultimo_recibo: Optional[date]
    recibos: Tuple[Recibo, ...]
    mora: Optional[int]
    en_media: bool
    alerta_mayor_60: bool
    excepciones: Tuple[str, ...]
    motivo: str

    def to_dict(self) -> dict:
        return {
            "id_factura": self.id_factura, "numero": self.numero, "fecha_factura": self.fecha_factura.isoformat(),
            "vencimiento_ruta": self.vencimiento_ruta.isoformat(),
            "vencimiento_sap": self.vencimiento_sap.isoformat() if self.vencimiento_sap else None,
            "estado": self.estado, "total": self.total, "aplicado_sap": self.aplicado_sap,
            "recibos_total": self.recibos_total,
            "ultimo_recibo": self.ultimo_recibo.isoformat() if self.ultimo_recibo else None,
            "recibos": [{"numero": r.numero, "fecha": r.fecha.isoformat(), "monto": r.monto, "via": r.via} for r in self.recibos],
            "mora": self.mora, "en_media": self.en_media, "alerta_mayor_60": self.alerta_mayor_60,
            "excepciones": list(self.excepciones), "motivo": self.motivo,
        }


@dataclass(frozen=True)
class ResultadoCliente:
    codigo: str
    calculado: str
    final: str
    promedio_mora: Optional[float]
    suma_mora: int
    historial_total: int
    en_ventana: int
    pagadas: int
    abiertas: int
    no_acreditadas: int
    evaluables: int
    alertas_cobro: Tuple[Alerta, ...]
    alertas_historicas: Tuple[Alerta, ...]
    explicacion: Tuple[str, ...]
    facturas: Tuple[EvidenciaFactura, ...]
    decision_gerencia: Optional[DecisionGerencia]

    @property
    def alertas(self) -> Tuple[Alerta, ...]:
        return self.alertas_cobro + self.alertas_historicas

    @property
    def color_calculado(self) -> str:
        return COLOR[self.calculado]

    @property
    def color_final(self) -> str:
        return COLOR[self.final]

    @property
    def requiere_gerencia(self) -> bool:
        return self.final in (CANDIDATO_SOLO_CONTADO, REVISION_HUMANA)

    def to_dict(self) -> dict:
        return {
            "codigo": self.codigo, "calculado": self.calculado, "color_calculado": self.color_calculado,
            "final": self.final, "color_final": self.color_final, "etiqueta_final": ETIQUETA[self.final],
            "promedio_mora": round(self.promedio_mora, 2) if self.promedio_mora is not None else None,
            "suma_mora": self.suma_mora, "facturas_historial": self.historial_total, "facturas_ventana": self.en_ventana,
            "pagadas": self.pagadas, "abiertas": self.abiertas, "no_acreditadas": self.no_acreditadas,
            "facturas_en_media": self.evaluables,
            "alertas_cobro": [a.to_dict() for a in self.alertas_cobro],
            "alertas_historicas": [a.to_dict() for a in self.alertas_historicas], "explicacion": list(self.explicacion),
            "facturas": [f.to_dict() for f in self.facturas],
            "decision_gerencia": self.decision_gerencia.__dict__ if self.decision_gerencia else None,
            "requiere_gerencia": self.requiere_gerencia,
        }


def clasificar_media(promedio: float) -> str:
    if promedio <= TOLERANCIA_A:
        return A
    if promedio <= LIMITE_B:
        return B
    if promedio <= LIMITE_C:
        return C
    return CANDIDATO_SOLO_CONTADO


def _numero_orden(f: FacturaSAP):
    try:
        return (1, int(str(f.numero).strip()))
    except ValueError:
        return (0, str(f.numero))


def _evidencia(f: FacturaSAP, corte: date, plazo: int) -> EvidenciaFactura:
    venc_ruta = f.fecha_factura + timedelta(days=plazo)
    todos = tuple(sorted(f.recibos, key=lambda r: (r.fecha, r.numero)))
    # Un recibo fechado ANTES de la emision no puede respaldar el pago de esa factura (no se fuerza fecha de pago).
    recibos = tuple(r for r in todos if r.fecha >= f.fecha_factura)
    anteriores = len(todos) - len(recibos)
    recibos_total = round(sum(r.monto for r in recibos), 2)
    ultimo = recibos[-1].fecha if recibos else None
    exc: List[str] = []
    if f.vencimiento_sap is not None and f.vencimiento_sap == f.fecha_factura:
        exc.append(EXC_POSIBLE_CONTADO)  # informativo: no se excluye; el plazo es el de la ruta

    mora: Optional[int] = None
    en_media = False
    if f.estado_sap == "O":
        estado = ABIERTA
        mora = max(0, (corte - venc_ruta).days)
        if mora > ABIERTA_MORA_MINIMA:
            en_media = True
            motivo = f"Abierta con {mora} días de mora al corte ({corte.strftime('%d/%m/%Y')}): entra a la media."
        else:
            exc.append(EXC_ABIERTA_SIN_ATRASO)
            motivo = f"Abierta con {mora} días de mora (≤ {ABIERTA_MORA_MINIMA}): cuenta en la ventana, no en la media."
    elif recibos and f.total > 0 and recibos_total + TOLERANCIA_MONTO >= f.total:
        estado = PAGADA
        mora = max(0, (ultimo - venc_ruta).days)
        en_media = True
        motivo = f"Pagada íntegramente por recibos; último recibo contable {ultimo.strftime('%d/%m/%Y')}: mora {mora} días."
    else:
        estado = CERRADA_NO_ACREDITADA
        exc.append(EXC_CIERRE_NO_ACREDITADO)
        if anteriores:
            exc.append(EXC_RECIBO_ANTERIOR_A_EMISION)
            motivo = "Cerrada, pero parte del respaldo son recibos anteriores a la emisión: no se fuerza fecha de pago; excluida de la media."
        else:
            motivo = "Cerrada sin cobro íntegro acreditado por recibos (nota de crédito/conciliación): excluida de la media."
    return EvidenciaFactura(
        id_factura=f.id_factura, numero=f.numero, fecha_factura=f.fecha_factura, vencimiento_ruta=venc_ruta,
        vencimiento_sap=f.vencimiento_sap, estado=estado, total=f.total, aplicado_sap=f.aplicado_sap,
        recibos_total=recibos_total, ultimo_recibo=ultimo, recibos=recibos, mora=mora, en_media=en_media,
        alerta_mayor_60=bool(en_media and mora is not None and mora > ALERTA_FACTURA),
        excepciones=tuple(exc), motivo=motivo)


def evaluar_cliente(
    codigo: str,
    facturas: Iterable[FacturaSAP],
    fecha_corte: date,
    plazo_dias: int,
    decision: Optional[DecisionGerencia] = None,
) -> ResultadoCliente:
    todas = list(facturas)
    historial = len(todas)
    recientes = sorted(todas, key=lambda f: (f.fecha_factura.toordinal(), _numero_orden(f)), reverse=True)[:VENTANA_FACTURAS]
    ev = [_evidencia(f, fecha_corte, plazo_dias) for f in recientes]
    moras = [e.mora for e in ev if e.en_media and e.mora is not None]
    alertas_cobro = tuple(
        Alerta(ALERTA_COBRO, e.numero, e.mora, round(max(0.0, e.total - e.aplicado_sap), 2),
               f"Factura {e.numero} ABIERTA con {e.mora} días de mora (> {ALERTA_FACTURA}): gestión de cobro.")
        for e in ev if e.alerta_mayor_60 and e.estado == ABIERTA)
    alertas_hist = tuple(
        Alerta(ALERTA_HISTORICA, e.numero, e.mora, None,
               f"Factura {e.numero} PAGADA con {e.mora} días de mora (> {ALERTA_FACTURA}): historial de comportamiento, no es cobro pendiente.")
        for e in ev if e.alerta_mayor_60 and e.estado != ABIERTA)

    expl: List[str] = []
    promedio: Optional[float] = None
    if historial < MIN_HISTORIAL:
        calculado = EN_PRUEBA
        expl.append(f"En prueba: {historial} factura(s) en todo el historial (mínimo {MIN_HISTORIAL}, pagadas y abiertas).")
    else:
        expl.append(f"Se evaluaron las {len(ev)} facturas más recientes de {historial} en el historial.")
        if not moras:
            calculado = REVISION_HUMANA
            expl.append("Ninguna factura de la ventana aporta a la media: revisión humana.")
        else:
            promedio = sum(moras) / len(moras)
            calculado = clasificar_media(promedio)
            expl.append(f"Mora media {promedio:.2f} días sobre {len(moras)} factura(s) evaluables (plazo {plazo_dias} días desde la emisión, mora ≥ 0, sin redondear).")
            expl.append(f"Umbrales: A ≤ {TOLERANCIA_A}, B ≤ {LIMITE_B}, C ≤ {LIMITE_C}, > {LIMITE_C} candidato a solo contado.")
        fuera = len(ev) - len(moras)
        if fuera:
            expl.append(f"{fuera} factura(s) de la ventana no cuentan para la media (abiertas con 0-5 días o cierres sin cobro íntegro acreditado).")
    if calculado == CANDIDATO_SOLO_CONTADO:
        expl.append("Es una PROPUESTA: la viñeta roja / solo contado requiere decisión de gerencia.")
    final = calculado
    if decision is not None:
        final = decision.clasificacion_final
        expl.append(f"Decisión de gerencia ({decision.actor}): {ETIQUETA.get(final, final)}. Motivo: {decision.motivo.rstrip('.')}. El cálculo automático se conserva.")

    return ResultadoCliente(
        codigo=codigo, calculado=calculado, final=final, promedio_mora=promedio, suma_mora=sum(moras),
        historial_total=historial, en_ventana=len(ev), pagadas=sum(1 for e in ev if e.estado == PAGADA),
        abiertas=sum(1 for e in ev if e.estado == ABIERTA), no_acreditadas=sum(1 for e in ev if e.estado == CERRADA_NO_ACREDITADA),
        evaluables=len(moras), alertas_cobro=alertas_cobro, alertas_historicas=alertas_hist, explicacion=tuple(expl), facturas=tuple(ev), decision_gerencia=decision)


class PaymentEvidenceSource(Protocol):
    """Fuente de evidencia (facturas + recibos) por cliente. Hoy: exportes SAP (cxc_bot.sap_exports); manana: Cosprobell BD."""

    fecha_corte: date
    etiqueta: str

    def facturas_por_cliente(self, codigos: Sequence[str]) -> Dict[str, List[FacturaSAP]]: ...


def evaluar_ruta(
    codigos: Iterable[str],
    fuente: PaymentEvidenceSource,
    plazo_dias: int,
    decisiones: Optional[Dict[str, DecisionGerencia]] = None,
) -> Dict[str, ResultadoCliente]:
    """Evalua todos los clientes de una ruta con el mismo motor. Un cliente sin facturas queda EN_PRUEBA (0 en historial)."""
    cods = [c.strip().upper() for c in codigos]
    facturas = fuente.facturas_por_cliente(cods)
    dec = {k.strip().upper(): v for k, v in (decisiones or {}).items()}
    return {c: evaluar_cliente(c, facturas.get(c, []), fuente.fecha_corte, plazo_dias, dec.get(c)) for c in cods}
