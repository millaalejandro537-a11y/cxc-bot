"""Integracion REAL con `cxc-reporting-core` (rama feature/cosprobell-backend-adapter, sin fusionar ni modificar).

El portal no recalcula cartera: alimenta el pipeline de cxc-reporting-core (RAW -> STG -> MODELO -> validacion ->
snapshot -> reportes) con un `RowProvider` propio sobre la `FuenteDatos`, y usa SUS consultas y exportes para los
reportes gerenciales. El semaforo de comportamiento de pago sigue siendo el unico motor de CXC BOT
(`cxc_bot.payment_semaphore`); ambos motores comparten solo los datos, no las reglas.

Gate contable heredado: un corte construido desde el snapshot NUNCA queda VALIDADO (`saldo_fuente_validada=False`); los
reportes se emiten explicitamente como "NO VALIDADO / snapshot historico".
Requiere que el paquete sea importable: variable CXC_REPORTING_SRC apuntando a `cxc-reporting-core/src`.
"""

from __future__ import annotations

import datetime as dt
import importlib
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ENV_SRC = "CXC_REPORTING_SRC"
MONEDA_SAP_A_CORE = {"LPS": "HNL", "HNL": "HNL"}


class ReportingNoDisponible(RuntimeError):
    pass


def _importar() -> Any:
    src = os.environ.get(ENV_SRC, "").strip()
    if src and str(src) not in sys.path and (Path(src) / "cxc_reporting").is_dir():
        sys.path.insert(0, src)
    try:
        return importlib.import_module("cxc_reporting")
    except Exception as e:  # noqa: BLE001
        raise ReportingNoDisponible(f"cxc-reporting-core no está disponible ({ENV_SRC} no definida o inválida): {e}") from e


def estado_reporting_core() -> dict:
    try:
        mod = _importar()
    except ReportingNoDisponible as e:
        return {"conectado": False, "motivo": str(e)}
    return {"conectado": True, "version": getattr(mod, "__version__", "desconocida"), "gate": "los cortes del snapshot son NO VALIDADOS (sin validación contable SAP)"}


class SnapshotRowProvider:
    """RowProvider (contrato de cxc-reporting-core) sobre la FuenteDatos: entrega nombres canonicos, sin reglas de negocio."""

    def __init__(self, fuente, rutas: List[dict]):
        self.fuente, self.rutas = fuente, rutas
        self._por_cliente: Dict[str, dict] = {}
        for r in rutas:
            for c in r["clientes"]:
                self._por_cliente.setdefault(c["codigo"], {"ruta": r, "cliente": c})

    def fetch(self, entidad: str, fecha_corte: dt.date) -> Sequence[Mapping[str, Any]]:
        docs = self.fuente.documentos_abiertos()
        if entidad == "documento":
            return [{
                "id_documento": f"OINV:{d['id']}", "numero_documento": d["numero"], "tipo_documento": "FACTURA", "codigo_cliente": d["cliente"],
                "fecha_documento": d["fecha"].isoformat(), "fecha_vencimiento": d["vence"].isoformat() if d["vence"] else None, "moneda": "HNL",
                "monto_original": f"{d['total']:.2f}", "saldo_abierto": f"{d['total'] - d['aplicado']:.2f}",
                **({"monto_aplicado": f"{d['aplicado']:.2f}"} if d["aplicado"] else {}),
            } for d in docs]
        codigos = sorted({d["cliente"] for d in docs} | set(self._por_cliente))
        if entidad == "cliente":
            out = []
            for cod in codigos:
                info = self._por_cliente.get(cod)
                fila = {"codigo_cliente": cod, "nombre_cliente": (info and info["cliente"]["nombre"]) or self.fuente.nombre_cliente(cod) or cod, "estado_activo": True}
                if info:
                    zn = next((z["nombre"] for z in info["ruta"]["zonas"] if z["prefijo"] == info["cliente"]["zona"]), None)
                    fila.update({"vendedor_nombre": info["ruta"]["vendedor"], "tipo_zona": "Foráneo" if info["ruta"]["tipo_ruta"] == "FORANEA" else "Local"})
                    if zn:
                        fila["zona"] = zn
                out.append(fila)
            return out
        if entidad == "vendedor":
            nombres = sorted({r["vendedor"] for r in self.rutas})
            return [{"codigo": f"V{i}", "nombre": n, "activo": True} for i, n in enumerate(nombres, 1)]
        if entidad == "zona":
            return [{"codigo": f"{r['route_id']}:{z['prefijo']}", "nombre": z["nombre"], "zona_normalizada": z["nombre"],
                     "tipo_zona": "Foráneo" if r["tipo_ruta"] == "FORANEA" else "Local", "fuente_mapping": "catalogo_portal"} for r in self.rutas for z in r["zonas"]]
        return []  # aplicacion: el snapshot trae los recibos solo como evidencia del semaforo, no como aplicaciones contables


class ReportingService:
    """Orquesta el pipeline de cxc-reporting-core sobre la fuente actual y sirve sus reportes."""

    def __init__(self, fuente, estado_dir: Path):
        self.fuente = fuente
        self.db_path = Path(estado_dir) / "reporting_cortes.sqlite3"
        self.mod = _importar()

    def _core(self):
        from cxc_reporting.adapters.base import ProviderAdapter  # noqa: WPS433 (import tardio: requiere sys.path ya preparado)
        from cxc_reporting.ingestion.pipeline import run_pipeline
        from cxc_reporting.storage.snapshots import SnapshotStore
        return ProviderAdapter, run_pipeline, SnapshotStore

    def store(self):
        """Una conexion NUEVA por operacion (el servidor atiende cada peticion en su propio hilo; SQLite no comparte conexiones)."""
        _, _, SnapshotStore = self._core()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return SnapshotStore(str(self.db_path))

    def importar_corte(self, rutas: List[dict]) -> dict:
        """Importa el snapshot actual como corte de cxc-reporting-core (idempotente por hash). Devuelve un resumen."""
        ProviderAdapter, run_pipeline, _ = self._core()
        fuente = self.fuente
        provider = SnapshotRowProvider(fuente, rutas)
        ahora = dt.datetime.now(dt.timezone.utc)

        class AdaptadorSnapshotPortal(ProviderAdapter):
            name = "PortalSnapshotAdapter"
            version = "0.1.0"
            fuente_label = fuente.etiqueta
            saldo_fuente_validada = False  # gate contable: sin validacion SAP/contabilidad nunca VALIDADO

        adapter = AdaptadorSnapshotPortal(fecha_corte=fuente.fecha_corte, provider=provider, clock=lambda: ahora)
        store = self.store()
        try:
            res = run_pipeline(adapter, store)
        finally:
            store.close()
        ev = res.estado_validacion
        return {"status": res.status, "estado_validacion": getattr(ev, "value", ev), "id_corte": res.id_corte, "publicable": res.publicable,
                "conteos": res.conteos, "errores": [str(e)[:300] for e in (res.errores_adapter or [])][:5],
                "advertencias": [getattr(i, "codigo", str(i)) for i in (res.issues or [])][:10]}

    def _con_store(self, fn):
        store = self.store()
        try:
            cortes = store.list_cortes()
            if not cortes:
                raise ReportingNoDisponible("Aún no hay un corte importado en cxc-reporting-core.")
            return fn(store, cortes[0].id_corte)
        finally:
            store.close()

    def reporte(self) -> dict:
        from cxc_reporting.reporting.exports import build_reporte

        def _r(store, idc):
            rep = build_reporte(store, idc, permitir_no_publicable=True)
            rep["aviso_portal"] = ("CORTE NO VALIDADO: construido desde el snapshot SAP del %s; sin validación contable. No es cartera en vivo."
                                   % self.fuente.fecha_corte.strftime("%d/%m/%Y"))
            return rep
        return self._con_store(_r)

    def cliente_360(self, codigo: str) -> Optional[dict]:
        from cxc_reporting.reporting.exports import build_cliente_360
        return self._con_store(lambda store, idc: build_cliente_360(store, codigo, idc, permitir_no_publicable=True))

    def escribir_xlsx(self, destino: Path) -> Path:
        from cxc_reporting.reporting.exports import write_xlsx
        write_xlsx(self.reporte(), str(destino))
        return Path(destino)
