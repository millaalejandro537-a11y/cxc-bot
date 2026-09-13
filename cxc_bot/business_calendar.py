"""Calendario laboral configurable (dias no habiles de la semana + feriados)
y el calculo de "N dias habiles antes de una fecha".

Se separa de la logica de programacion para que agregar feriados en el
futuro sea solo editar config/scheduling.json, sin tocar el runner.

Regla de negocio vigente (Cosprobell): la semana laboral es lunes a SABADO;
solo el domingo no es habil. No se usa una libreria/logica estandar de
"dias habiles" porque esas casi siempre asumen sabado no habil -- aqui el
default es explicito y viene de configuracion, no de una convencion externa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Set
from zoneinfo import ZoneInfo

DEFAULT_SCHEDULING_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scheduling.json"
DEFAULT_TIMEZONE = "America/Tegucigalpa"

# 0=lunes ... 6=domingo. Solo domingo (6) es no-habil por defecto: sabado SI
# se trabaja en Cosprobell.
DEFAULT_NON_BUSINESS_WEEKDAYS = {6}


def today_in_timezone(tz_name: str = DEFAULT_TIMEZONE) -> date:
    """'Hoy' segun la zona horaria del negocio, no la del servidor/maquina
    donde corra el runner o n8n. Evita que una corrida cerca de medianoche
    calcule mal que dia es "hoy" si la maquina esta en otro huso horario."""

    return datetime.now(ZoneInfo(tz_name)).date()


@dataclass(frozen=True)
class BusinessCalendar:
    non_business_weekdays: Set[int] = field(default_factory=lambda: set(DEFAULT_NON_BUSINESS_WEEKDAYS))
    holidays: Set[date] = field(default_factory=set)
    timezone: str = DEFAULT_TIMEZONE

    def is_business_day(self, d: date) -> bool:
        return d.weekday() not in self.non_business_weekdays and d not in self.holidays

    def subtract_business_days(self, d: date, n: int) -> date:
        """Devuelve la fecha que queda N dias HABILES antes de d (sin contar
        d). Si n <= 0 devuelve d tal cual."""

        current = d
        remaining = n
        while remaining > 0:
            current -= timedelta(days=1)
            if self.is_business_day(current):
                remaining -= 1
        return current

    def add_business_days(self, d: date, n: int) -> date:
        current = d
        remaining = n
        while remaining > 0:
            current += timedelta(days=1)
            if self.is_business_day(current):
                remaining -= 1
        return current


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_business_calendar(path: Path = DEFAULT_SCHEDULING_CONFIG_PATH) -> BusinessCalendar:
    raw = _load_scheduling_config(path)
    non_business = set(raw.get("dias_no_habiles_semana", list(DEFAULT_NON_BUSINESS_WEEKDAYS)))
    holidays = {_parse_date(h) for h in raw.get("feriados", [])}
    timezone = raw.get("timezone", DEFAULT_TIMEZONE)
    return BusinessCalendar(non_business_weekdays=non_business, holidays=holidays, timezone=timezone)


def load_preparation_lead_days(path: Path = DEFAULT_SCHEDULING_CONFIG_PATH) -> int:
    raw = _load_scheduling_config(path)
    return int(raw.get("dias_habiles_anticipacion", 3))


def load_scheduling_settings(path: Path = DEFAULT_SCHEDULING_CONFIG_PATH) -> dict:
    """Devuelve el JSON de scheduling.json completo (routes_root,
    sap_mock_data_path, etc.) para que el CLI no tenga que releer el archivo."""

    return _load_scheduling_config(path)


def _load_scheduling_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"No se encontro la configuracion de programacion: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
