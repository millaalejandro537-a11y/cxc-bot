import json

import pytest

from cxc_bot.schedule_source import LocalScheduleSource, ScheduleSourceError


def _write(path, entries):
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def test_list_entries_basic(tmp_path):
    path = tmp_path / "schedule.json"
    _write(path, [
        {"id": "e1", "vendor": "Erick Brizuela", "route": "Cortés", "fecha_salida": "2026-10-13",
         "estado": "programada", "aprobado": True},
    ])
    src = LocalScheduleSource(path)
    entries = src.list_entries()
    assert len(entries) == 1
    assert entries[0].vendor == "Erick Brizuela"
    assert entries[0].estado == "programada"
    assert entries[0].aprobado is True


def test_defaults_when_optional_fields_missing(tmp_path):
    path = tmp_path / "schedule.json"
    _write(path, [{"vendor": "Erick Brizuela", "route": "Cortés", "fecha_salida": "2026-10-13"}])
    entries = LocalScheduleSource(path).list_entries()
    assert entries[0].estado == "programada"
    assert entries[0].aprobado is False  # sin aprobar por defecto: no se genera nada por accidente
    assert entries[0].id == "local-0"


def test_missing_required_field_raises(tmp_path):
    path = tmp_path / "schedule.json"
    _write(path, [{"vendor": "Erick Brizuela", "fecha_salida": "2026-10-13"}])  # falta route
    with pytest.raises(ScheduleSourceError):
        LocalScheduleSource(path).list_entries()


def test_invalid_estado_raises(tmp_path):
    path = tmp_path / "schedule.json"
    _write(path, [{"vendor": "Erick Brizuela", "route": "Cortés", "fecha_salida": "2026-10-13", "estado": "inventado"}])
    with pytest.raises(ScheduleSourceError):
        LocalScheduleSource(path).list_entries()


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        LocalScheduleSource(tmp_path / "no_existe.json")


def test_mark_prepared_updates_estado_atomically(tmp_path):
    path = tmp_path / "schedule.json"
    _write(path, [
        {"id": "e1", "vendor": "Erick Brizuela", "route": "Cortés", "fecha_salida": "2026-10-13",
         "estado": "programada", "aprobado": True},
    ])
    src = LocalScheduleSource(path)
    src.mark_prepared("e1")

    entries = src.list_entries()
    assert entries[0].estado == "preparada"
    # el .tmp de escritura atomica no debe quedar tirado
    assert not path.with_suffix(".json.tmp").exists()


def test_mark_prepared_unknown_id_raises(tmp_path):
    path = tmp_path / "schedule.json"
    _write(path, [{"id": "e1", "vendor": "Erick Brizuela", "route": "Cortés", "fecha_salida": "2026-10-13"}])
    with pytest.raises(ScheduleSourceError):
        LocalScheduleSource(path).mark_prepared("no-existe")


def test_natural_key_stable_across_id_scheme_changes(tmp_path):
    """La idempotencia se basa en vendor+route+fecha_salida, no en el id
    interno de la fuente -- asi sobrevive un cambio de fuente (InSitu usara
    sus propios ids)."""
    path = tmp_path / "schedule.json"
    _write(path, [
        {"id": "id-viejo", "vendor": "Erick Brizuela", "route": "Cortés", "fecha_salida": "2026-10-13"},
        {"id": "id-nuevo-de-insitu-9999", "vendor": "Erick Brizuela", "route": "Cortés", "fecha_salida": "2026-10-13"},
    ])
    entries = LocalScheduleSource(path).list_entries()
    assert entries[0].natural_key == entries[1].natural_key
    assert entries[0].id != entries[1].id
