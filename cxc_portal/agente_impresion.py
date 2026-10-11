"""Adaptador del FUTURO agente de impresion (se ejecutara en la oficina; saldra hacia el portal, nunca recibira conexiones).

Hoy NO hay impresoras conectadas: solo existe `ImpresoraSimulada`, que registra el trabajo y no imprime nada. El agente:
  1. pide el siguiente trabajo con su token propio (`POST /api/agente/siguiente`),
  2. descarga el archivo (`POST /api/agente/<id>/archivo`) y verifica su sha256,
  3. lo entrega a una `Impresora` y reporta el resultado (`POST /api/agente/<id>/resultado`).
Una impresora real implementara `Impresora.imprimir`; el agente se niega a usar una impresora real salvo `permitir_reales=True` (decision futura).
"""

from __future__ import annotations

import hashlib
import http.client
import json
from typing import List, Optional, Protocol, Tuple


class Impresora(Protocol):
    simulada: bool

    def imprimir(self, nombre: str, datos: bytes, copias: int, destino: Optional[str]) -> str: ...


class ImpresoraSimulada:
    """No imprime: guarda el trabajo para que las pruebas lo inspeccionen. Con `fallar=True` simula un atasco."""

    simulada = True

    def __init__(self, fallar: bool = False):
        self.trabajos: List[dict] = []
        self.fallar = fallar

    def imprimir(self, nombre: str, datos: bytes, copias: int, destino: Optional[str]) -> str:
        if self.fallar:
            raise RuntimeError("Impresora simulada: atasco de papel (simulado).")
        self.trabajos.append({"nombre": nombre, "bytes": len(datos), "sha256": hashlib.sha256(datos).hexdigest(), "copias": copias, "destino": destino})
        return f"SIMULADO: {nombre} x{copias} (no se imprimió nada)"


class TransporteHttp:
    """Cliente HTTP(S) del agente hacia el portal. `host` permite probar contra un servidor local con un Host publico."""

    def __init__(self, host_ip: str, puerto: int, token: str, host: Optional[str] = None, https: bool = False):
        self.host_ip, self.puerto, self.token, self.host, self.https = host_ip, puerto, token, host, https

    def _llamar(self, ruta: str, cuerpo: Optional[dict] = None) -> Tuple[int, dict, bytes]:
        c = (http.client.HTTPSConnection if self.https else http.client.HTTPConnection)(self.host_ip, self.puerto, timeout=20)
        h = {"X-Agent-Token": self.token, "Content-Type": "application/json"}
        if self.host:
            h["Host"] = self.host
        c.request("POST", ruta, body=json.dumps(cuerpo or {}), headers=h)
        r = c.getresponse()
        datos = r.read()
        cab = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, cab, datos

    def siguiente(self) -> Optional[dict]:
        st, _, b = self._llamar("/api/agente/siguiente")
        if st != 200:
            raise RuntimeError(f"El portal rechazó la solicitud del agente ({st}).")
        return json.loads(b).get("trabajo")

    def archivo(self, iid: int) -> Tuple[str, bytes, str]:
        st, cab, b = self._llamar(f"/api/agente/{iid}/archivo")
        if st != 200:
            raise RuntimeError(f"No se pudo descargar el archivo del trabajo {iid} ({st}).")
        return cab["content-disposition"].split('filename="')[1].rstrip('"'), b, cab["x-content-sha256"]

    def resultado(self, iid: int, ok: bool, detalle: str) -> dict:
        st, _, b = self._llamar(f"/api/agente/{iid}/resultado", {"ok": ok, "detalle": detalle})
        if st != 200:
            raise RuntimeError(f"No se pudo reportar el resultado ({st}).")
        return json.loads(b)


class AgenteImpresion:
    def __init__(self, transporte, impresora: Impresora, permitir_reales: bool = False):
        if not getattr(impresora, "simulada", False) and not permitir_reales:
            raise RuntimeError("Este agente solo acepta impresoras SIMULADAS hasta que se autorice el uso de impresoras reales.")
        self.transporte, self.impresora = transporte, impresora

    def procesar_uno(self) -> Optional[dict]:
        t = self.transporte.siguiente()
        if not t:
            return None
        try:
            nombre, datos, sha = self.transporte.archivo(t["id"])
            if hashlib.sha256(datos).hexdigest() != sha:
                raise RuntimeError("El archivo descargado no coincide con su hash: no se imprime.")
            detalle = self.impresora.imprimir(nombre, datos, int(t["copias"]), t.get("destino"))
            return self.transporte.resultado(t["id"], True, detalle)
        except Exception as e:  # noqa: BLE001 - un fallo se reporta al portal y el agente sigue vivo
            return self.transporte.resultado(t["id"], False, f"{type(e).__name__}: {e}"[:400])

    def vaciar_cola(self, maximo: int = 100) -> List[dict]:
        out = []
        for _ in range(maximo):
            r = self.procesar_uno()
            if r is None:
                break
            out.append(r)
        return out
