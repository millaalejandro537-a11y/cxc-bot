"""Configuracion SEGURA de la conexion de SOLO LECTURA al Cosprobell Backend (Gelsyn). Sin red: solo lee variables de entorno y valida.

Reglas (decision canonica: el Portal consume ENDPOINTS de solo lectura, no SQL):
  * La credencial llega unicamente por variable de entorno (`CXC_GELSYN_API_KEY`); nunca en codigo, Git, argumentos ni logs. Todo `repr`/mensaje la oculta.
  * Una URL de PostgreSQL (postgres://, postgresql://) se RECHAZA: el Portal no se conecta a la BD ni con `postgres` ni con ningun rol.
  * `CXC_GELSYN_ENTORNO` es obligatorio (pruebas | produccion). 'pruebas' rechaza hosts de produccion conocidos o con aspecto de produccion;
    'produccion' exige ademas una confirmacion explicita y rechaza hosts que digan 'pruebas'.
  * HTTPS obligatorio (HTTP solo localhost), sin credenciales ni query en la URL.
El HTTP lo hace el cliente GET-only de `cxc-reporting-core` (no se duplica): ver `cliente_solo_lectura`."""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

ENV_BASE_URL = "CXC_GELSYN_BASE_URL"
ENV_API_KEY = "CXC_GELSYN_API_KEY"
ENV_ENTORNO = "CXC_GELSYN_ENTORNO"
ENV_HOSTS_PRODUCCION = "CXC_GELSYN_HOSTS_PRODUCCION"
ENV_CONFIRMAR_PRODUCCION = "CXC_GELSYN_CONFIRMAR_PRODUCCION"
CONFIRMACION_PRODUCCION = "SI-ES-PRODUCCION-SOLO-LECTURA"
ENTORNOS = ("pruebas", "produccion")
_LOCALES = ("localhost", "127.0.0.1", "::1")
_ETIQUETAS_PRODUCCION = {"prod", "produccion", "production", "prd"}
_ETIQUETAS_PRUEBAS = {"pruebas", "prueba", "test", "testing", "staging", "dev", "qa"}


class ConfigGelsynError(ValueError):
    """Configuracion insegura o incompleta. Los mensajes NUNCA incluyen la credencial."""


@dataclass(frozen=True)
class ConfigGelsyn:
    base_url: str
    entorno: str
    host: str
    huella_clave: str                                   # sha256[:8] de la clave: identifica cual se uso sin revelarla
    _clave: str = field(repr=False, compare=False, default="")

    def __repr__(self) -> str:
        return f"ConfigGelsyn(entorno={self.entorno!r}, host={self.host!r}, clave=<oculta:{self.huella_clave}>)"

    __str__ = __repr__

    def env_para_cliente(self) -> dict:
        """Entorno MINIMO para el cliente del core (la clave solo viaja en memoria, nunca se imprime)."""
        return {ENV_BASE_URL: self.base_url, ENV_API_KEY: self._clave}


def _etiquetas(host: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", host.lower()) if t}


def cargar_config(env: Optional[Mapping[str, str]] = None) -> ConfigGelsyn:
    env = os.environ if env is None else env
    faltan = [n for n in (ENV_BASE_URL, ENV_API_KEY, ENV_ENTORNO) if not (env.get(n) or "").strip()]
    if faltan:
        raise ConfigGelsynError("Faltan variables de entorno: " + ", ".join(faltan) + ". Ver docs/CONEXION-GELSYN-PRUEBAS.md.")
    url, clave, entorno = env[ENV_BASE_URL].strip(), env[ENV_API_KEY].strip(), env[ENV_ENTORNO].strip().lower()
    if re.match(r"(?i)^postgres(ql)?://", url) or re.match(r"(?i)^postgres(ql)?://", clave) or "://" in clave:
        raise ConfigGelsynError("El Portal NO usa conexión SQL directa (ni con 'postgres' ni con otro rol): configure la URL HTTPS del backend y su API key de solo lectura.")
    if entorno not in ENTORNOS:
        raise ConfigGelsynError(f"{ENV_ENTORNO} debe ser 'pruebas' o 'produccion'.")
    if len(clave) < 20 or re.search(r"\s", clave):
        raise ConfigGelsynError("La API key no tiene el formato esperado (mínimo 20 caracteres, sin espacios).")
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host:
        raise ConfigGelsynError("La URL base no es válida (esquema y host obligatorios).")
    if p.scheme != "https" and host not in _LOCALES:
        raise ConfigGelsynError("La URL base debe usar HTTPS (HTTP solo para localhost).")
    if p.username or p.password or p.query or p.fragment:
        raise ConfigGelsynError("La URL base no debe incluir credenciales, parámetros ni fragmentos.")
    hosts_prod = {h.strip().lower() for h in (env.get(ENV_HOSTS_PRODUCCION) or "").split(",") if h.strip()}
    if entorno == "pruebas":
        if host in hosts_prod or (_etiquetas(host) & _ETIQUETAS_PRODUCCION):
            raise ConfigGelsynError(f"Entorno 'pruebas' pero el host '{host}' es o parece de PRODUCCIÓN: no se conecta.")
    else:
        if env.get(ENV_CONFIRMAR_PRODUCCION) != CONFIRMACION_PRODUCCION:
            raise ConfigGelsynError(f"Producción exige {ENV_CONFIRMAR_PRODUCCION}={CONFIRMACION_PRODUCCION} (autorización humana explícita).")
        if _etiquetas(host) & _ETIQUETAS_PRUEBAS:
            raise ConfigGelsynError(f"Entorno 'produccion' pero el host '{host}' parece de PRUEBAS: revise la configuración.")
    return ConfigGelsyn(base_url=url.rstrip("/"), entorno=entorno, host=host, huella_clave=hashlib.sha256(clave.encode()).hexdigest()[:8], _clave=clave)


def _importar_core() -> Any:
    from .reporting_bridge import _importar                      # misma ruta de importacion que el resto del Portal (CXC_REPORTING_SRC)
    _importar()
    return importlib.import_module("cxc_reporting.adapters.backend.client")


def cliente_solo_lectura(cfg: ConfigGelsyn, transport: Any = None, sleep: Any = None):
    """Cliente HTTP del core (solo GET, HTTPS, reintentos solo ante fallas transitorias, errores sin secretos). Cabecera documentada por Gelsyn: X-API-Key."""
    m = _importar_core()
    conf = m.BackendClientConfig(base_url_env=ENV_BASE_URL, api_key_env=ENV_API_KEY, auth_header="X-API-Key", auth_scheme=None, timeout_seconds=30.0, max_retries=2)
    kw = {"env": cfg.env_para_cliente(), "transport": transport}
    if sleep is not None:
        kw["sleep"] = sleep
    return m.ReadOnlyApiClient(conf, **kw)
