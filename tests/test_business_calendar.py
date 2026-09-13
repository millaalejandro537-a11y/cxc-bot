from datetime import date

from cxc_bot.business_calendar import BusinessCalendar


def test_one_business_day_before_tuesday_is_monday():
    tuesday = date(2026, 9, 15)
    assert tuesday.weekday() == 1
    cal = BusinessCalendar()
    assert cal.subtract_business_days(tuesday, 1) == date(2026, 9, 14)


def test_one_business_day_before_monday_skips_weekend():
    """Si la salida es lunes, el dia habil anterior debe saltar el fin de
    semana y caer en viernes, no en domingo."""
    monday = date(2026, 9, 14)
    assert monday.weekday() == 0
    cal = BusinessCalendar()
    assert cal.subtract_business_days(monday, 1) == date(2026, 9, 11)


def test_default_three_business_days_before_thursday():
    thursday = date(2026, 9, 17)
    cal = BusinessCalendar()
    # jueves -> miercoles(1) -> martes(2) -> lunes(3)
    assert cal.subtract_business_days(thursday, 3) == date(2026, 9, 14)


def test_holiday_is_skipped_like_a_weekend():
    cal = BusinessCalendar(holidays={date(2026, 9, 16)})  # miercoles feriado
    thursday = date(2026, 9, 17)
    # jueves -> miercoles(feriado, no cuenta) -> martes(1)
    assert cal.subtract_business_days(thursday, 1) == date(2026, 9, 15)


def test_is_business_day():
    cal = BusinessCalendar()
    assert cal.is_business_day(date(2026, 9, 14)) is True  # lunes
    assert cal.is_business_day(date(2026, 9, 19)) is False  # sabado
    assert cal.is_business_day(date(2026, 9, 20)) is False  # domingo


def test_add_business_days_roundtrip():
    cal = BusinessCalendar()
    monday = date(2026, 9, 14)
    friday_before = cal.subtract_business_days(monday, 1)
    assert cal.add_business_days(friday_before, 1) == monday
