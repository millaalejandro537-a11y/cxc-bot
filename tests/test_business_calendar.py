from datetime import date, datetime
from zoneinfo import ZoneInfo

from cxc_bot.business_calendar import BusinessCalendar, today_in_timezone

# Regla vigente en Cosprobell: lunes a SABADO son habiles, solo domingo no lo es.


def test_friday_is_a_business_day():
    friday = date(2026, 9, 18)
    assert friday.weekday() == 4
    assert BusinessCalendar().is_business_day(friday) is True


def test_saturday_is_a_business_day():
    """Explicito porque muchas librerias/convenciones estandar de 'dias
    habiles' asumen sabado no habil -- aqui NO es el caso."""
    saturday = date(2026, 9, 19)
    assert saturday.weekday() == 5
    assert BusinessCalendar().is_business_day(saturday) is True


def test_sunday_is_not_a_business_day():
    sunday = date(2026, 9, 20)
    assert sunday.weekday() == 6
    assert BusinessCalendar().is_business_day(sunday) is False


def test_one_business_day_before_tuesday_is_monday():
    tuesday = date(2026, 9, 15)
    assert tuesday.weekday() == 1
    cal = BusinessCalendar()
    assert cal.subtract_business_days(tuesday, 1) == date(2026, 9, 14)


def test_one_business_day_before_monday_is_saturday_not_sunday():
    """Si la salida es lunes, el dia habil anterior es sabado (sabado SI es
    habil); solo se saltaria domingo, no todo el fin de semana."""
    monday = date(2026, 9, 14)
    assert monday.weekday() == 0
    cal = BusinessCalendar()
    result = cal.subtract_business_days(monday, 1)
    assert result == date(2026, 9, 12)
    assert result.weekday() == 5  # sabado


def test_default_three_business_days_before_thursday():
    thursday = date(2026, 9, 17)
    cal = BusinessCalendar()
    # jueves -> miercoles(1) -> martes(2) -> lunes(3)
    assert cal.subtract_business_days(thursday, 3) == date(2026, 9, 14)


def test_three_business_days_before_tuesday_crosses_sunday_only():
    """Ejemplo del encargo: salida martes, 3 dias habiles antes debe saltar
    SOLO el domingo (sabado cuenta como habil)."""
    tuesday = date(2026, 9, 15)
    cal = BusinessCalendar()
    # martes -> lunes(1) -> domingo(no cuenta) -> sabado(2) -> viernes(3)
    assert cal.subtract_business_days(tuesday, 3) == date(2026, 9, 11)


def test_holiday_is_skipped_like_a_weekend():
    cal = BusinessCalendar(holidays={date(2026, 9, 16)})  # miercoles feriado
    thursday = date(2026, 9, 17)
    # jueves -> miercoles(feriado, no cuenta) -> martes(1)
    assert cal.subtract_business_days(thursday, 1) == date(2026, 9, 15)


def test_holiday_on_a_saturday_still_excludes_it():
    """Un feriado puede caer en un dia que de otro modo seria habil (como
    sabado); debe excluirse igual."""
    cal = BusinessCalendar(holidays={date(2026, 9, 19)})  # sabado feriado
    assert cal.is_business_day(date(2026, 9, 19)) is False


def test_is_business_day():
    cal = BusinessCalendar()
    assert cal.is_business_day(date(2026, 9, 14)) is True  # lunes
    assert cal.is_business_day(date(2026, 9, 19)) is True  # sabado (habil)
    assert cal.is_business_day(date(2026, 9, 20)) is False  # domingo


def test_add_business_days_roundtrip():
    cal = BusinessCalendar()
    monday = date(2026, 9, 14)
    day_before = cal.subtract_business_days(monday, 1)
    assert cal.add_business_days(day_before, 1) == monday


def test_default_non_business_weekday_is_only_sunday():
    """No se usa una libreria/convencion estandar que excluya sabado
    automaticamente: el default de esta clase es SOLO domingo."""
    cal = BusinessCalendar()
    assert cal.non_business_weekdays == {6}


# --- Timezone ---------------------------------------------------------


def test_default_timezone_is_tegucigalpa():
    assert BusinessCalendar().timezone == "America/Tegucigalpa"


def test_today_in_timezone_matches_tegucigalpa_wall_clock():
    expected = datetime.now(ZoneInfo("America/Tegucigalpa")).date()
    assert today_in_timezone("America/Tegucigalpa") == expected


def test_today_in_timezone_can_differ_from_another_zone_near_midnight():
    """No es una prueba de reloj real (evita flakiness): confirma que la
    funcion realmente usa el tz pedido y no solo la hora local ingenua,
    comparando contra un momento UTC fijo cercano a medianoche en Honduras."""
    fixed_utc = datetime(2026, 9, 15, 5, 30, tzinfo=ZoneInfo("UTC"))  # 23:30 del 14 en Honduras (UTC-6)
    tegucigalpa_date = fixed_utc.astimezone(ZoneInfo("America/Tegucigalpa")).date()
    tokyo_date = fixed_utc.astimezone(ZoneInfo("Asia/Tokyo")).date()
    assert tegucigalpa_date == date(2026, 9, 14)
    assert tokyo_date == date(2026, 9, 15)
    assert tegucigalpa_date != tokyo_date
