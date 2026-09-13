"""Versionado de configuracion: poder responder "que configuracion estaba
vigente cuando se genero esta salida" (seccion 10 del encargo).

Cada vez que se genera una ruta, se calcula un hash del contenido actual de
config/routes.json + config/vendor_catalog.json + config/route_composition.json.
Si ese hash exacto ya esta guardado, se reutiliza su version_id; si no, se
guarda una version NUEVA con el contenido completo de los tres archivos (no
solo el hash), para poder auditar exactamente que decia la configuracion en
ese momento, no solo que cambio.

Las versiones son inmutables: nunca se actualiza una fila existente, solo
se inserta una nueva si el contenido cambio.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .business_calendar import DEFAULT_SCHEDULING_CONFIG_PATH
from .catalog import DEFAULT_CATALOG_PATH
from .composite_config import DEFAULT_COMPOSITION_PATH
from .config import DEFAULT_CONFIG_PATH

DEFAULT_CONFIG_VERSIONS_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "config_versions.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS config_versions (
    version_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    routes_json TEXT NOT NULL,
    vendor_catalog_json TEXT NOT NULL,
    route_composition_json TEXT NOT NULL
);
"""


@dataclass
class ConfigVersion:
    version_id: str
    created_at: str
    routes_json: str
    vendor_catalog_json: str
    route_composition_json: str


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


class ConfigVersionStore:
    def __init__(
        self,
        db_path: Path = DEFAULT_CONFIG_VERSIONS_DB_PATH,
        routes_path: Path = DEFAULT_CONFIG_PATH,
        vendor_catalog_path: Path = DEFAULT_CATALOG_PATH,
        route_composition_path: Path = DEFAULT_COMPOSITION_PATH,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.routes_path = routes_path
        self.vendor_catalog_path = vendor_catalog_path
        self.route_composition_path = route_composition_path
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def current_snapshot_texts(self) -> tuple:
        return (
            _read_text(self.routes_path),
            _read_text(self.vendor_catalog_path),
            _read_text(self.route_composition_path),
        )

    def current_version_id(self) -> str:
        """Calcula el hash de la configuracion actual y garantiza que quede
        guardada (inserta una version nueva si este contenido exacto no
        existia todavia)."""

        routes_json, vendor_catalog_json, route_composition_json = self.current_snapshot_texts()
        combined = routes_json + "\0" + vendor_catalog_json + "\0" + route_composition_json
        version_id = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]

        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT 1 FROM config_versions WHERE version_id = ?", (version_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO config_versions (version_id, created_at, routes_json, vendor_catalog_json, "
                    "route_composition_json) VALUES (?, ?, ?, ?, ?)",
                    (version_id, datetime.now().isoformat(timespec="seconds"), routes_json, vendor_catalog_json, route_composition_json),
                )
                conn.commit()
        return version_id

    def get_version(self, version_id: str) -> Optional[ConfigVersion]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM config_versions WHERE version_id = ?", (version_id,)
            ).fetchone()
        if row is None:
            return None
        return ConfigVersion(
            version_id=row["version_id"],
            created_at=row["created_at"],
            routes_json=row["routes_json"],
            vendor_catalog_json=row["vendor_catalog_json"],
            route_composition_json=row["route_composition_json"],
        )

    def get_route_config_as_of(self, version_id: str, route_id: str) -> Optional[dict]:
        """Devuelve el bloque de config/routes.json para esa ruta, TAL COMO
        estaba en esa version -- para poder responder "con que configuracion
        se genero esta salida" incluso si routes.json ya cambio despues."""

        version = self.get_version(version_id)
        if version is None:
            return None
        try:
            raw = json.loads(version.routes_json)
        except json.JSONDecodeError:
            return None
        return raw.get(route_id.upper())
