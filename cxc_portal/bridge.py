"""Puente OPCIONAL hacia `cxc-reporting-core` (rama feature/cosprobell-backend-adapter, sin fusionar).

MVP: solo detecta si el paquete es importable (variable CXC_REPORTING_SRC apuntando a su carpeta `src`) y reporta el
estado. NO consume cortes todavia, y NO copia ninguna regla de cartera: la analitica de saldos/antiguedad seguira
viniendo de ese paquete cuando haya un corte VALIDADO; el semaforo de comportamiento de pago vive en CXC BOT.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def estado_reporting_core() -> dict:
    src = os.environ.get("CXC_REPORTING_SRC", "").strip()
    if not src:
        return {"conectado": False, "motivo": "CXC_REPORTING_SRC no definida; el portal usa solo snapshots propios."}
    p = Path(src)
    if not (p / "cxc_reporting").is_dir():
        return {"conectado": False, "motivo": "CXC_REPORTING_SRC no apunta a una carpeta 'src' con el paquete cxc_reporting."}
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
    try:
        mod = importlib.import_module("cxc_reporting")
    except Exception as e:  # noqa: BLE001
        return {"conectado": False, "motivo": f"No se pudo importar cxc_reporting: {e}"}
    return {"conectado": True, "version": getattr(mod, "__version__", "desconocida"),
            "nota": "Importable. Aún no se consumen cortes: falta un corte VALIDADO y el contrato del backend (Gelsyn)."}
