"""Ruta SINTETICA de demostracion. Codigos y nombres ficticios (DEMO-xxx); NO son clientes reales.

Muestra el calculo completo (ultimas 10 facturas, media, excepciones, override de gerencia) sin datos privados.
Siempre se etiqueta SINTETICO_DEMO. Se usa tambien en pruebas.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

from cxc_bot import payment_semaphore as ps

CORTE = date(2026, 10, 8)
PLAZO = ps.PLAZO_FORANEA


def _facturas(base: int, moras: List[int], abiertas: Optional[List[int]] = None, nc: int = 0, paso: int = 45) -> List[ps.FacturaSAP]:
    """moras: dias de atraso del pago (puede ser negativo) de las pagadas, de la mas antigua a la mas reciente.
    abiertas: dias de mora al corte de facturas abiertas. nc: cierres sin cobro acreditado (nota de credito)."""
    total = len(moras) + nc + len(abiertas or [])
    out: List[ps.FacturaSAP] = []
    n = 0
    for i, m in enumerate(moras):
        emision = CORTE - timedelta(days=paso * (total - n) + 200)
        monto = 2500.0 + 100 * i
        pago = emision + timedelta(days=PLAZO + m)
        out.append(ps.FacturaSAP(str(base + n), str(base + n), emision, emision + timedelta(days=30), monto, monto, "C",
                                 (ps.Recibo(f"R{base + n}", pago, monto, "demo"),)))
        n += 1
    for _ in range(nc):
        emision = CORTE - timedelta(days=paso * (total - n) + 200)
        out.append(ps.FacturaSAP(str(base + n), str(base + n), emision, emision + timedelta(days=30), 1800.0, 1800.0, "C", ()))
        n += 1
    for d in abiertas or []:
        emision = CORTE - timedelta(days=PLAZO + d)
        out.append(ps.FacturaSAP(str(base + n), str(base + n), emision, emision + timedelta(days=30), 3200.0, 0.0, "O", ()))
        n += 1
    return out


def demo_ruta():
    from .store import ClienteRuta, FUENTE_SINTETICO, Ruta  # import tardio: evita ciclo

    spec = [
        ("DEMO-001", "SALON DEMO EXCELENTE (A)", [-3, 0, 2, 0, 1, 4, 0, 2, 3, 1, 0, 2], None, None, 0, 0.0),
        ("DEMO-002", "SALON DEMO BUENO (B)", [8, 12, 10, 9, 14, 11, 13, 10, 12, 9], None, None, 0, 0.0),
        ("DEMO-003", "SALON DEMO REGULAR (C)", [30, 25, 40, 35, 28, 33, 45, 38], None, None, 0, 0.0),
        ("DEMO-004", "SALON DEMO CANDIDATO A SOLO CONTADO", [95, 110, 70, 85, 90], None, None, 0, 0.0),
        ("DEMO-005", "SALON DEMO CANDIDATO CON OVERRIDE", [120, 105, 90, 98, 110], None,
         ps.DecisionGerencia("C", "Mal cobro atribuible a un vendedor anterior (ejemplo sintético).", "Gerencia (demo)", "2026-10-09"), 0, 0.0),
        ("DEMO-006", "SALON DEMO EN PRUEBA", [0, 2], None, None, 0, 0.0),
        ("DEMO-007", "SALON DEMO CON ABIERTAS Y EXCEPCIONES", [0, 3, 1, 4], [3, 20], None, 1, 0.0),
        ("DEMO-008", "SALON DEMO SIN BASE PARA LA MEDIA", [], None, None, 5, 0.0),
        ("DEMO-009", "SALON DEMO CON ALERTA > 60 DIAS ABIERTA", [0, 0, 0, 2, 1], [75], None, 0, 0.0),
    ]
    clientes = []
    for i, (cod, nombre, moras, abiertas, dec, nc, _) in enumerate(spec):
        fac = _facturas(1000 * (i + 1), moras, abiertas, nc)
        r = ps.evaluar_cliente(cod, fac, CORTE, PLAZO, dec)
        saldo = round(sum(f.total - f.aplicado_sap for f in fac if f.estado_sap == "O"), 2)
        clientes.append(ClienteRuta(f"{i + 1} DM", "DM", cod, nombre, saldo, r, None))
    fuente = {"tipo": FUENTE_SINTETICO, "fecha_corte": CORTE.isoformat(), "en_vivo": False,
              "etiqueta": "DATOS SINTÉTICOS de demostración — no son clientes ni saldos reales",
              "notas": ["Sirve para ver el cálculo completo del semáforo con las reglas del 09/10/2026."]}
    return Ruta("DEMO", "Ruta DEMO (sintética)", "Vendedor Demo", "FORANEA", PLAZO, fuente, [{"prefijo": "DM", "nombre": "Zona Demo"}], clientes)
