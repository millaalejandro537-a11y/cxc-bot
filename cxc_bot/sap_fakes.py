"""Transport falso del Service Layer para pruebas y desarrollo SIN SAP real.

Datos 100% sinteticos. Permite inyectar fallos (sesion expirada, 5xx, permiso)
para ejercitar el manejo de errores igual que lo haria el transport real."""

from __future__ import annotations

from typing import Dict, List, Optional

from .sap_service_layer import ServiceLayerTransport


def make_business_partner(i: int, saldo: float = 100.0, **overrides) -> dict:
    bp = {
        "CardCode": f"C{i:05d}",
        "CardName": f"CLIENTE SINTETICO {i}",
        "CurrentAccountBalance": saldo,
        "BilltoDefault": f"DIRECCION {i}",
        "ValidFrom": "2020-01-01",
        "CardType": "cCustomer",
    }
    bp.update(overrides)
    return bp


class FakeServiceLayerTransport(ServiceLayerTransport):
    """Sirve `pages` en orden (cada una con 'odata.nextLink' hacia la siguiente).

    `fail_on_get`: {numero_de_llamada_get (1-based): excepcion} para simular fallos."""

    def __init__(self, items: List[dict], page_size: int = 2,
                 fail_on_get: Optional[Dict[int, Exception]] = None,
                 fail_login: Optional[Exception] = None):
        self._pages = [items[i:i + page_size] for i in range(0, len(items), page_size)] or [[]]
        self.fail_on_get = fail_on_get or {}
        self.fail_login = fail_login
        self.calls: List[tuple] = []
        self.logged_in = False
        self.login_count = 0
        self.logout_count = 0

    def login(self) -> None:
        self.login_count += 1
        if self.fail_login:
            raise self.fail_login
        self.logged_in = True

    def get(self, path, params=None) -> dict:
        self.calls.append((path, params))
        n = len(self.calls)
        if n in self.fail_on_get:
            raise self.fail_on_get[n]
        idx = 0 if path == "BusinessPartners" else int(path.rsplit("skip=", 1)[1])
        page = {"value": self._pages[idx]}
        if idx + 1 < len(self._pages):
            page["odata.nextLink"] = f"BusinessPartners?$skip={idx + 1}"
        return page

    def logout(self) -> None:
        self.logout_count += 1
        self.logged_in = False
