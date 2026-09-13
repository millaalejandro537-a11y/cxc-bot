"""Historial de ejecuciones (quien se preparo, cuando, con que resultado).

Es la fuente de verdad para la idempotencia del runner: antes de generar una
ruta se busca si ya existe una ejecucion EXITOSA con la misma
natural_key (vendedor + ruta + fecha_salida). Esto es independiente de que
la fuente de programacion (ScheduleSource) pueda o no marcarse como
'preparada' -- asi que sigue siendo confiable incluso si en el futuro InSitu
es de solo lectura.

SQLite es suficiente para esta fase: una sola maquina, escrituras poco
frecuentes (una corrida diaria), y permite responder despues "cuando se
genero Cortes de septiembre" con una consulta simple.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "history.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    natural_key TEXT NOT NULL,
    schedule_entry_id TEXT,
    vendor TEXT NOT NULL,
    route TEXT NOT NULL,
    fecha_salida TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    archivo_origen TEXT,
    archivo_generado TEXT,
    fuente_datos TEXT,
    cantidad_clientes INTEGER,
    validaciones_ok INTEGER,
    estado TEXT NOT NULL,
    error_categoria TEXT,
    error_mensaje TEXT,
    duracion_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_executions_natural_key ON executions(natural_key);
CREATE INDEX IF NOT EXISTS idx_executions_run_id ON executions(run_id);
"""


@dataclass
class ExecutionRecord:
    run_id: str
    natural_key: str
    vendor: str
    route: str
    fecha_salida: date
    estado: str  # 'exito' | 'error' | 'omitida'
    schedule_entry_id: Optional[str] = None
    archivo_origen: Optional[str] = None
    archivo_generado: Optional[str] = None
    fuente_datos: Optional[str] = None
    cantidad_clientes: Optional[int] = None
    validaciones_ok: Optional[bool] = None
    error_categoria: Optional[str] = None
    error_mensaje: Optional[str] = None
    duracion_ms: Optional[int] = None
    timestamp: Optional[datetime] = None


class HistoryStore:
    def __init__(self, path: Path = DEFAULT_HISTORY_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def find_successful(self, natural_key: str) -> Optional[sqlite3.Row]:
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "SELECT * FROM executions WHERE natural_key = ? AND estado = 'exito' "
                "ORDER BY timestamp DESC LIMIT 1",
                (natural_key,),
            )
            return cur.fetchone()

    def record(self, rec: ExecutionRecord) -> int:
        timestamp = (rec.timestamp or datetime.now()).isoformat(timespec="seconds")
        with closing(self._connect()) as conn:
            cur = conn.execute(
                """
                INSERT INTO executions (
                    run_id, natural_key, schedule_entry_id, vendor, route, fecha_salida,
                    timestamp, archivo_origen, archivo_generado, fuente_datos,
                    cantidad_clientes, validaciones_ok, estado, error_categoria,
                    error_mensaje, duracion_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.run_id,
                    rec.natural_key,
                    rec.schedule_entry_id,
                    rec.vendor,
                    rec.route,
                    rec.fecha_salida.isoformat(),
                    timestamp,
                    rec.archivo_origen,
                    rec.archivo_generado,
                    rec.fuente_datos,
                    rec.cantidad_clientes,
                    None if rec.validaciones_ok is None else int(rec.validaciones_ok),
                    rec.estado,
                    rec.error_categoria,
                    rec.error_mensaje,
                    rec.duracion_ms,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def list_recent(self, limit: int = 50) -> List[sqlite3.Row]:
        with closing(self._connect()) as conn:
            cur = conn.execute("SELECT * FROM executions ORDER BY timestamp DESC LIMIT ?", (limit,))
            return cur.fetchall()

    def find_by_route(self, route: str, vendor: Optional[str] = None) -> List[sqlite3.Row]:
        with closing(self._connect()) as conn:
            if vendor:
                cur = conn.execute(
                    "SELECT * FROM executions WHERE route = ? AND vendor = ? ORDER BY timestamp DESC",
                    (route, vendor),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM executions WHERE route = ? ORDER BY timestamp DESC", (route,)
                )
            return cur.fetchall()

    def find_by_run(self, run_id: str) -> List[sqlite3.Row]:
        with closing(self._connect()) as conn:
            cur = conn.execute("SELECT * FROM executions WHERE run_id = ? ORDER BY id", (run_id,))
            return cur.fetchall()
