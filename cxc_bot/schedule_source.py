"""Fuente de programacion de rutas (hoy: JSON local; manana: InSitu).

Igual que data_source.py separa "de donde vienen los clientes" de la logica
de negocio, este modulo separa "de donde viene la programacion" del runner
que decide que preparar. Sustituir LocalScheduleSource por
InsituScheduleSource no debe requerir cambios en runner.py.

Estados validos de una entrada: programada, preparada, cancelada, completada.
Solo se generan automaticamente las que esten 'programada' y 'aprobado=true'.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from .models import ScheduleEntry

VALID_STATES = {"programada", "preparada", "cancelada", "completada"}

DEFAULT_SCHEDULE_PATH = Path(__file__).resolve().parent.parent / "data" / "schedule.json"


class ScheduleSourceError(Exception):
    pass


class ScheduleSource(ABC):
    """Interfaz que debe cumplir cualquier fuente de programacion de rutas."""

    @abstractmethod
    def list_entries(self) -> List[ScheduleEntry]:
        """Devuelve TODAS las entradas conocidas (el runner filtra cuales
        estan due). Nunca debe asumir un formato de fecha ni una cantidad fija."""
        raise NotImplementedError

    def mark_prepared(self, entry_id: str) -> None:
        """Best-effort: marca una entrada como 'preparada' en la fuente.
        Algunas fuentes (por ejemplo InSitu de solo lectura) pueden no
        soportar esto; el runner NO depende de que esto funcione para su
        propia idempotencia (ver history_store.py), asi que el default es
        simplemente no hacer nada."""

        return None


def _parse_date(value) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


class LocalScheduleSource(ScheduleSource):
    """Programacion local en un archivo JSON simple, editable a mano mientras
    no exista InSitu. Formato: una lista de objetos con al menos vendor,
    route y fecha_salida; el resto de campos tiene default."""

    def __init__(self, path: Path = DEFAULT_SCHEDULE_PATH):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"No se encontro el archivo de programacion: {self.path}")

    def _load_raw(self) -> list:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_raw(self, raw: list) -> None:
        """Escritura segura: se escribe a un temporal y se reemplaza con
        rename atomico, para no dejar el archivo de programacion corrupto
        si algo falla a mitad de la escritura."""

        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)

    def list_entries(self) -> List[ScheduleEntry]:
        raw = self._load_raw()
        entries = []
        for i, item in enumerate(raw):
            try:
                vendor = item["vendor"]
                route = item["route"]
                fecha_salida = _parse_date(item["fecha_salida"])
            except KeyError as e:
                raise ScheduleSourceError(f"Entrada #{i} de {self.path} le falta el campo obligatorio {e}.")
            if fecha_salida is None:
                raise ScheduleSourceError(f"Entrada #{i} de {self.path} tiene fecha_salida vacia o invalida.")

            estado = item.get("estado", "programada")
            if estado not in VALID_STATES:
                raise ScheduleSourceError(
                    f"Entrada #{i} de {self.path} tiene estado invalido '{estado}'. Validos: {sorted(VALID_STATES)}"
                )

            entry_id = item.get("id") or f"local-{i}"
            entries.append(
                ScheduleEntry(
                    id=str(entry_id),
                    vendor=vendor,
                    route=route,
                    fecha_salida=fecha_salida,
                    fecha_regreso=_parse_date(item.get("fecha_regreso")),
                    observacion=item.get("observacion"),
                    estado=estado,
                    aprobado=bool(item.get("aprobado", False)),
                )
            )
        return entries

    def mark_prepared(self, entry_id: str) -> None:
        raw = self._load_raw()
        found = False
        for i, item in enumerate(raw):
            current_id = str(item.get("id") or f"local-{i}")
            if current_id == entry_id:
                item["estado"] = "preparada"
                found = True
                break
        if not found:
            raise ScheduleSourceError(f"No se encontro la entrada '{entry_id}' en {self.path} para marcarla preparada.")
        self._save_raw(raw)


class InsituScheduleSource(ScheduleSource):
    """Placeholder para la futura integracion con InSitu (o el sistema que
    Cosprobell decida usar para la programacion de rutas de vendedores).

    NO implementar todavia. Cuando InSitu este listo:
      - consultar su API/export de salidas programadas y aprobadas,
      - mapear cada una a ScheduleEntry (vendor, route, fecha_salida, ...),
      - devolver la lista con la misma forma que LocalScheduleSource.list_entries().
    Si InSitu no permite escribir de vuelta el estado, mark_prepared() puede
    quedarse como no-op (el runner ya no depende de eso para su idempotencia).
    """

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "La integracion con InSitu no esta implementada todavia. Usa LocalScheduleSource mientras tanto."
        )

    def list_entries(self) -> List[ScheduleEntry]:
        raise NotImplementedError
