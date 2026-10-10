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


SPEC = [
    ("C90001", "SALON DEMO EXCELENTE (A)", [-3, 0, 2, 0, 1, 4, 0, 2, 3, 1, 0, 2], None, 0),
    ("C90002", "SALON DEMO BUENO (B)", [8, 12, 10, 9, 14, 11, 13, 10, 12, 9], None, 0),
    ("C90003", "SALON DEMO REGULAR (C)", [30, 25, 40, 35, 28, 33, 45, 38], None, 0),
    ("C90004", "SALON DEMO CANDIDATO A SOLO CONTADO", [95, 110, 70, 85, 90], None, 0),
    ("C90005", "SALON DEMO CANDIDATO CON ALERTA PAGADA", [120, 105, 90, 98, 110], None, 0),
    ("C90006", "SALON DEMO EN PRUEBA", [0, 2], None, 0),
    ("C90007", "SALON DEMO CON ABIERTAS Y EXCEPCIONES", [0, 3, 1, 4], [3, 20], 1),
    ("C90008", "SALON DEMO SIN BASE PARA LA MEDIA", [], None, 5),
    ("C90009", "SALON DEMO CON ALERTA > 60 DIAS ABIERTA", [0, 0, 0, 2, 1], [75], 0),
]


class FuenteSintetica:
    """Fuente de datos SINTETICA (clientes C9xxxx ficticios) para demostraciones y pruebas. NO son datos reales."""

    tipo = "SINTETICO_DEMO"
    en_vivo = False
    fecha_corte = CORTE
    etiqueta = "DATOS SINTÉTICOS de demostración — no son clientes ni saldos reales"

    def __init__(self):
        self._f = {c: _facturas(1000 * (i + 1), moras, abiertas, nc) for i, (c, _, moras, abiertas, nc) in enumerate(SPEC)}
        self._n = {c: n for c, n, *_ in SPEC}

    def facturas_por_cliente(self, codigos):
        return {c.upper(): self._f.get(c.upper(), []) for c in codigos}

    def nombre_cliente(self, codigo):
        return self._n.get(codigo.upper())

    def saldo_abierto(self, codigo):
        return round(sum(f.total - f.aplicado_sap for f in self._f.get(codigo.upper(), []) if f.estado_sap == "O"), 2)

    def registros_sap(self):
        return [(c, self._n[c], self.saldo_abierto(c)) for c in sorted(self._n)]

    def documentos_abiertos(self):
        return [{"cliente": c, "id": f.id_factura, "numero": f.numero, "fecha": f.fecha_factura, "vence": f.vencimiento_sap, "total": f.total, "aplicado": f.aplicado_sap}
                for c, fs in self._f.items() for f in fs if f.estado_sap == "O"]

    def frescura(self, hoy=None):
        return {"tipo": self.tipo, "fecha_corte": CORTE.isoformat(), "vigente": False, "motivo": "Datos sintéticos."}


def sembrar_demo(estado, actor):
    """Crea la ruta DEMO (sintetica) en la BD de estado si no existe."""
    if any(r["route_id"] == "DEMO" for r in estado.listar_rutas(solo_activas=False)):
        return
    estado.guardar_ruta(actor, "DEMO", "Ruta DEMO (sintética)", "Vendedor Demo", "FORANEA", PLAZO, zonas={"DM": "Zona Demo"},
                        clientes=[{"codigo": c, "folder": f"{i} DM", "zona": "DM", "nombre": n} for i, (c, n, *_) in enumerate(SPEC, 1)],
                        estado_fisico="n/a (sintética)", notas="Datos ficticios para demostración y pruebas.")
