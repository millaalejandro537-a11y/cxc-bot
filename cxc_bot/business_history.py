"""Historial de NEGOCIO: snapshots inmutables de cada salida generada,
separados del historial TECNICO (history_store.py, que registra cada
intento de ejecucion exista o no snapshot). Vive en su propia base de datos
(data/business_snapshots.sqlite3) a proposito (seccion 28: separar
historial tecnico de historial de negocio).

Reglas de negocio duras (no solo documentadas, sino impuestas en codigo):

  - Un snapshot, una vez creado, NUNCA se actualiza (ni sus datos generales
    ni sus filas de clientes). Lo unico que puede cambiar despues es su
    `estado` (vigente -> sustituido/cancelado), nunca sus datos.
  - Solo puede existir UN snapshot 'vigente' por (vendedor, ruta,
    fecha_salida) a la vez -- lo impone un indice UNIQUE parcial en SQLite,
    no solo la logica de Python.
  - Un snapshot con `environment='prod'` NUNCA se crea si la fuente de
    datos usada es un mock: se degrada automaticamente a 'dev' y se marca
    la advertencia en el resultado. Esto es un candado tecnico, no una
    convencion que dependa de que alguien la respete.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from .models import ClientEntry

DEFAULT_BUSINESS_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "business_snapshots.sqlite3"

VALID_ENVIRONMENTS = {"dev", "test", "prod"}
VALID_SNAPSHOT_STATES = {"vigente", "sustituido", "cancelado"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    execution_id TEXT,
    environment TEXT NOT NULL,
    vendor TEXT NOT NULL,
    route TEXT NOT NULL,
    fecha_generacion TEXT NOT NULL,
    fecha_salida TEXT NOT NULL,
    mes INTEGER NOT NULL,
    anio INTEGER NOT NULL,
    archivo_final TEXT,
    archivo_base TEXT,
    archivo_base_hash TEXT,
    config_version_id TEXT,
    fuente_programacion TEXT,
    fuente_datos TEXT,
    fuente_datos_es_mock INTEGER NOT NULL DEFAULT 1,
    cantidad_clientes INTEGER,
    saldo_total REAL,
    estado TEXT NOT NULL DEFAULT 'vigente',
    sustituido_por TEXT,
    cancelado_motivo TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_un_vigente_por_salida
    ON snapshots(vendor, route, fecha_salida)
    WHERE estado = 'vigente';
CREATE INDEX IF NOT EXISTS idx_snapshots_route ON snapshots(route);
CREATE INDEX IF NOT EXISTS idx_snapshots_environment ON snapshots(environment);

CREATE TABLE IF NOT EXISTS snapshot_clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    codigo TEXT NOT NULL,
    nombre TEXT,
    posicion INTEGER NOT NULL,
    saldo REAL,
    componente TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshot_clients_snapshot ON snapshot_clients(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_clients_codigo ON snapshot_clients(codigo);
"""


class BusinessHistoryError(Exception):
    pass


class DuplicateVigenteSnapshotError(BusinessHistoryError):
    """Ya existe un snapshot 'vigente' para esa misma salida. Hay que
    sustituirlo explicitamente (supersede_snapshot), no crear otro vigente."""


@dataclass
class SnapshotClientInput:
    codigo: str
    nombre: str
    posicion: int
    saldo: Optional[float]
    componente: Optional[str] = None


@dataclass
class CreateSnapshotResult:
    snapshot_id: str
    environment: str  # el que realmente quedo guardado (puede diferir del pedido)
    downgraded_from_prod: bool = False


class BusinessHistoryStore:
    def __init__(self, db_path: Path = DEFAULT_BUSINESS_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def create_snapshot(
        self,
        *,
        environment: str,
        vendor: str,
        route: str,
        fecha_salida: date,
        clients: List[SnapshotClientInput],
        execution_id: Optional[str] = None,
        archivo_final: Optional[str] = None,
        archivo_base: Optional[str] = None,
        archivo_base_hash: Optional[str] = None,
        config_version_id: Optional[str] = None,
        fuente_programacion: Optional[str] = None,
        fuente_datos: Optional[str] = None,
        fuente_datos_es_mock: bool = True,
        fecha_generacion: Optional[date] = None,
    ) -> CreateSnapshotResult:
        with closing(self.connect()) as conn:
            try:
                result = self._insert_snapshot(
                    conn,
                    environment=environment, vendor=vendor, route=route, fecha_salida=fecha_salida,
                    clients=clients, execution_id=execution_id, archivo_final=archivo_final,
                    archivo_base=archivo_base, archivo_base_hash=archivo_base_hash,
                    config_version_id=config_version_id, fuente_programacion=fuente_programacion,
                    fuente_datos=fuente_datos, fuente_datos_es_mock=fuente_datos_es_mock,
                    fecha_generacion=fecha_generacion,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return result

    def _insert_snapshot(
        self,
        conn: sqlite3.Connection,
        *,
        environment: str,
        vendor: str,
        route: str,
        fecha_salida: date,
        clients: List[SnapshotClientInput],
        execution_id: Optional[str] = None,
        archivo_final: Optional[str] = None,
        archivo_base: Optional[str] = None,
        archivo_base_hash: Optional[str] = None,
        config_version_id: Optional[str] = None,
        fuente_programacion: Optional[str] = None,
        fuente_datos: Optional[str] = None,
        fuente_datos_es_mock: bool = True,
        fecha_generacion: Optional[date] = None,
    ) -> CreateSnapshotResult:
        """Insercion cruda dentro de una transaccion ya abierta por el
        llamador (create_snapshot la abre y cierra sola; replace_snapshot la
        reutiliza dentro de su propia transaccion). No hace commit."""

        if environment not in VALID_ENVIRONMENTS:
            raise BusinessHistoryError(f"Ambiente invalido '{environment}'. Validos: {sorted(VALID_ENVIRONMENTS)}")

        # Candado tecnico: nunca un snapshot 'prod' con datos de un mock.
        downgraded = False
        effective_environment = environment
        if environment == "prod" and fuente_datos_es_mock:
            effective_environment = "dev"
            downgraded = True

        fecha_generacion = fecha_generacion or date.today()
        snapshot_id = uuid.uuid4().hex
        saldo_total = sum((c.saldo or 0.0) for c in clients)

        try:
            conn.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id, execution_id, environment, vendor, route, fecha_generacion,
                    fecha_salida, mes, anio, archivo_final, archivo_base, archivo_base_hash,
                    config_version_id, fuente_programacion, fuente_datos, fuente_datos_es_mock,
                    cantidad_clientes, saldo_total, estado, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'vigente', ?)
                """,
                (
                    snapshot_id, execution_id, effective_environment, vendor, route,
                    fecha_generacion.isoformat(), fecha_salida.isoformat(),
                    fecha_salida.month, fecha_salida.year,
                    archivo_final, archivo_base, archivo_base_hash, config_version_id,
                    fuente_programacion, fuente_datos, int(bool(fuente_datos_es_mock)),
                    len(clients), saldo_total, datetime.now().isoformat(timespec="seconds"),
                ),
            )
        except sqlite3.IntegrityError as e:
            raise DuplicateVigenteSnapshotError(
                f"Ya existe un snapshot vigente para {vendor}/{route} con fecha_salida={fecha_salida}. "
                f"Usa replace_snapshot() si esta salida se va a reemplazar. Detalle: {e}"
            )

        conn.executemany(
            "INSERT INTO snapshot_clients (snapshot_id, codigo, nombre, posicion, saldo, componente) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(snapshot_id, c.codigo, c.nombre, c.posicion, c.saldo, c.componente) for c in clients],
        )

        return CreateSnapshotResult(
            snapshot_id=snapshot_id, environment=effective_environment, downgraded_from_prod=downgraded
        )

    def supersede_snapshot(self, old_snapshot_id: str, new_snapshot_id: str) -> None:
        """Marca un snapshot como 'sustituido' por otro NUEVO ya creado (no
        se borra ni se modifica su contenido, solo su estado). Para
        reemplazar una salida completa (crear la version nueva Y marcar la
        vieja) usa replace_snapshot(), que resuelve el orden correcto."""

        with closing(self.connect()) as conn:
            conn.execute(
                "UPDATE snapshots SET estado = 'sustituido', sustituido_por = ? "
                "WHERE snapshot_id = ? AND estado = 'vigente'",
                (new_snapshot_id, old_snapshot_id),
            )
            conn.commit()

    def replace_snapshot(self, old_snapshot_id: str, **new_snapshot_kwargs) -> CreateSnapshotResult:
        """Reemplaza un snapshot 'vigente' por uno nuevo, atomicamente: el
        indice unico exige que el viejo deje de ser 'vigente' ANTES de que
        el nuevo pueda insertarse como 'vigente' para la misma salida, pero
        el nuevo id no existe todavia en ese momento -- por eso esto no es
        simplemente "crear y luego sustituir" (ver test que documenta el
        problema). Se resuelve en una sola transaccion: se libera el slot
        del viejo, se inserta el nuevo, y solo si eso funciona se enlaza
        sustituido_por -> nuevo id; si algo falla, se revierte todo."""

        old_row = self.get_snapshot(old_snapshot_id)
        if old_row is None or old_row["estado"] != "vigente":
            raise BusinessHistoryError(f"'{old_snapshot_id}' no existe o no esta vigente; no hay nada que reemplazar.")

        with closing(self.connect()) as conn:
            try:
                conn.execute(
                    "UPDATE snapshots SET estado = 'sustituido' WHERE snapshot_id = ? AND estado = 'vigente'",
                    (old_snapshot_id,),
                )

                new_result = self._insert_snapshot(conn, **new_snapshot_kwargs)

                conn.execute(
                    "UPDATE snapshots SET sustituido_por = ? WHERE snapshot_id = ?",
                    (new_result.snapshot_id, old_snapshot_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return new_result

    def cancel_snapshot(self, snapshot_id: str, motivo: str) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                "UPDATE snapshots SET estado = 'cancelado', cancelado_motivo = ? "
                "WHERE snapshot_id = ? AND estado = 'vigente'",
                (motivo, snapshot_id),
            )
            conn.commit()

    def get_snapshot(self, snapshot_id: str) -> Optional[sqlite3.Row]:
        with closing(self.connect()) as conn:
            return conn.execute("SELECT * FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()

    def get_snapshot_clients(self, snapshot_id: str) -> List[sqlite3.Row]:
        with closing(self.connect()) as conn:
            return conn.execute(
                "SELECT * FROM snapshot_clients WHERE snapshot_id = ? ORDER BY posicion", (snapshot_id,)
            ).fetchall()

    def find_vigente(self, vendor: str, route: str, fecha_salida: date) -> Optional[sqlite3.Row]:
        with closing(self.connect()) as conn:
            return conn.execute(
                "SELECT * FROM snapshots WHERE vendor = ? AND route = ? AND fecha_salida = ? AND estado = 'vigente'",
                (vendor, route, fecha_salida.isoformat()),
            ).fetchone()

    def list_by_route(self, route: str, environment: Optional[str] = None, only_vigente: bool = True) -> List[sqlite3.Row]:
        query = "SELECT * FROM snapshots WHERE route = ?"
        params: list = [route]
        if environment:
            query += " AND environment = ?"
            params.append(environment)
        if only_vigente:
            query += " AND estado = 'vigente'"
        query += " ORDER BY fecha_salida"
        with closing(self.connect()) as conn:
            return conn.execute(query, params).fetchall()


def client_entries_to_snapshot_inputs(
    roster: List[ClientEntry], saldos_por_codigo: dict
) -> List[SnapshotClientInput]:
    """Convierte el roster (con .componente si es una ruta compuesta) mas
    los saldos YA CALCULADOS (leidos del Excel publicado con data_only=True)
    en filas listas para snapshot_clients."""

    inputs = []
    for i, entry in enumerate(roster, start=1):
        nombre, saldo = saldos_por_codigo.get(entry.codigo, (None, None))
        inputs.append(
            SnapshotClientInput(
                codigo=entry.codigo, nombre=nombre, posicion=i, saldo=saldo, componente=entry.componente
            )
        )
    return inputs
