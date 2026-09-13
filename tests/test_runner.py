"""Cubre los 15 escenarios pedidos para el runner/orquestador de Fase 2."""

import json
import shutil
from datetime import date
from typing import List

import pytest

from cxc_bot.business_calendar import BusinessCalendar
from cxc_bot.catalog import VendorCatalog
from cxc_bot.history_store import HistoryStore
from cxc_bot.models import SapClientRecord, ScheduleEntry
from cxc_bot.runner import compute_due_entries, process_due_routes
from cxc_bot.schedule_source import LocalScheduleSource, ScheduleSource


# ---------------------------------------------------------------------------
# Infraestructura de prueba: fuentes fake, autocontenidas
# ---------------------------------------------------------------------------
class FakeDataSource:
    def __init__(self, records=None, error: Exception = None):
        self._records = records or []
        self._error = error
        self.calls = 0

    def fetch_clients(self):
        self.calls += 1
        if self._error:
            raise self._error
        return self._records


class FakeScheduleSource(ScheduleSource):
    """Implementacion minima de la interfaz, distinta de LocalScheduleSource,
    para probar que el runner funciona contra CUALQUIER ScheduleSource
    (escenario 15: futura sustitucion, p.ej. InSitu)."""

    def __init__(self, entries: List[ScheduleEntry]):
        self._entries = entries
        self.mark_prepared_calls = []

    def list_entries(self) -> List[ScheduleEntry]:
        return list(self._entries)

    # No sobreescribe mark_prepared: usa el default no-op del ABC, como
    # haria una fuente de solo lectura tipo InSitu.


def make_entry(vendor="Eric Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13),
                estado="programada", aprobado=True, entry_id="e1"):
    return ScheduleEntry(id=entry_id, vendor=vendor, route=route, fecha_salida=fecha_salida,
                          estado=estado, aprobado=aprobado)


@pytest.fixture
def routes_root(tmp_path, base_file):
    """<root>/Eric Brizuela/Cortes/CORTES septiembre.xlsx, listo para que el
    runner genere CORTES octubre.xlsx junto a el."""
    root = tmp_path / "RUTA VENDEDORES"
    route_dir = root / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    shutil.copy2(base_file, route_dir / "CORTES septiembre.xlsx")
    return root


@pytest.fixture
def history(tmp_path):
    return HistoryStore(tmp_path / "history.sqlite3")


@pytest.fixture
def catalog():
    return VendorCatalog.load()


@pytest.fixture
def calendar():
    return BusinessCalendar()


@pytest.fixture
def sap_source(baseline_sap_records):
    return FakeDataSource(records=baseline_sap_records)


def run(entries, routes_root, sap_source, history, catalog, calendar, today, lead_days=3):
    return process_due_routes(
        schedule_source=FakeScheduleSource(entries),
        catalog=catalog,
        calendar=calendar,
        lead_business_days=lead_days,
        routes_root=routes_root,
        sap_data_source=sap_source,
        sap_source_label="fake",
        history=history,
        today=today,
    )


# 1. ninguna ruta pendiente
def test_no_due_routes(routes_root, sap_source, history, catalog, calendar):
    entries = [make_entry(fecha_salida=date(2026, 12, 1))]  # muy lejos, no due todavia
    result = run(entries, routes_root, sap_source, history, catalog, calendar, today=date(2026, 10, 1))
    assert result.processed == 0
    assert sap_source.calls == 0  # ni siquiera se debe consultar SAP si no hay nada due


# 2. una ruta pendiente
def test_one_due_route_succeeds(routes_root, sap_source, history, catalog, calendar):
    entries = [make_entry()]
    result = run(entries, routes_root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert result.processed == 1
    assert result.succeeded == 1
    assert result.routes[0].output_file.endswith("CORTES octubre.xlsx")


# 3. varias rutas pendientes
def test_several_due_routes(tmp_path, base_file, sap_source, history, catalog, calendar):
    import openpyxl

    root = tmp_path / "RUTA VENDEDORES"

    d1 = root / "Eric Brizuela" / "Cortes"
    d1.mkdir(parents=True)
    shutil.copy2(base_file, d1 / "CORTES septiembre.xlsx")

    # Segunda ruta real (config "LAS VEGAS" en config/routes.json), para
    # probar 2 rutas DISTINTAS en el mismo lote, no la misma ruta dos veces.
    d2 = root / "Eric Brizuela" / "Las Vegas"
    d2.mkdir(parents=True)
    lasvegas_path = d2 / "LASVEGAS septiembre.xlsx"
    shutil.copy2(base_file, lasvegas_path)
    wb = openpyxl.load_workbook(lasvegas_path)
    wb["CORTES"].title = "LASVEGAS"
    wb.save(lasvegas_path)

    entries = [
        make_entry(entry_id="e1", vendor="Eric Brizuela", route="Cortés", fecha_salida=date(2026, 10, 13)),
        make_entry(entry_id="e2", vendor="Eric Brizuela", route="Las Vegas", fecha_salida=date(2026, 10, 14)),
    ]
    result = run(entries, root, sap_source, history, catalog, calendar, today=date(2026, 10, 9))
    assert result.processed == 2
    assert result.succeeded == 2
    assert sap_source.calls == 1  # una sola consulta SAP para todo el lote


# 4. ruta ya preparada (estado en la fuente) no se procesa
def test_already_prepared_entry_is_not_due(routes_root, sap_source, history, catalog, calendar):
    entries = [make_entry(estado="preparada")]
    result = run(entries, routes_root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert result.processed == 0


def test_cancelled_or_unapproved_entries_are_not_due(routes_root, sap_source, history, catalog, calendar):
    entries = [
        make_entry(entry_id="e1", estado="cancelada"),
        make_entry(entry_id="e2", aprobado=False),
    ]
    result = run(entries, routes_root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert result.processed == 0


# 5. ejecucion duplicada (idempotencia via HistoryStore, no via el estado)
def test_duplicate_run_is_idempotent_via_history(routes_root, sap_source, history, catalog, calendar):
    entries = [make_entry()]
    r1 = run(entries, routes_root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert r1.succeeded == 1

    # Se vuelve a correr con la MISMA entrada 'programada' (como si la fuente
    # de programacion no se hubiera podido actualizar).
    r2 = run(entries, routes_root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert r2.processed == 1
    assert r2.skipped == 1
    assert r2.succeeded == 0

    generated = list((routes_root / "Eric Brizuela" / "Cortes").glob("CORTES octubre*.xlsx"))
    assert len(generated) == 1  # nunca se duplico el archivo


# 6. archivo base faltante
def test_missing_base_file(tmp_path, sap_source, history, catalog, calendar):
    root = tmp_path / "RUTA VENDEDORES"
    (root / "Eric Brizuela" / "Cortes").mkdir(parents=True)  # carpeta existe, sin archivos
    entries = [make_entry()]
    result = run(entries, root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert result.failed == 1
    assert result.routes[0].error_category == "archivo_base_faltante"


# 7. dos posibles archivos base (ambiguedad)
def test_ambiguous_base_files(tmp_path, base_file, sap_source, history, catalog, calendar):
    import os

    root = tmp_path / "RUTA VENDEDORES"
    route_dir = root / "Eric Brizuela" / "Cortes"
    route_dir.mkdir(parents=True)
    shutil.copy2(base_file, route_dir / "CORTES agosto.xlsx")
    shutil.copy2(base_file, route_dir / "CORTES septiembre.xlsx")
    now = 1_700_000_000.0
    os.utime(route_dir / "CORTES agosto.xlsx", (now, now))
    os.utime(route_dir / "CORTES septiembre.xlsx", (now + 0.1, now + 0.1))  # practicamente empatado

    entries = [make_entry()]
    result = run(entries, root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert result.failed == 1
    assert result.routes[0].error_category == "archivo_base_ambiguo"


# 8. fallo de DataSource
def test_data_source_failure_marks_all_due_routes_as_failed(routes_root, history, catalog, calendar):
    failing_source = FakeDataSource(error=RuntimeError("SAP no responde"))
    entries = [make_entry()]
    result = run(entries, routes_root, failing_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert result.failed == 1
    assert result.routes[0].error_category == "fuente_datos_invalida"
    assert "SAP no responde" in result.routes[0].error_message


# 9. fallo de validacion (cliente sin registro SAP -> #N/A) + rollback del archivo
def test_validation_failure_rolls_back_output_file(routes_root, history, catalog, calendar):
    # Fuente SAP vacia: NINGUN codigo de la ruta se puede resolver -> #N/A en todo.
    empty_source = FakeDataSource(records=[])
    entries = [make_entry()]
    result = run(entries, routes_root, empty_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert result.failed == 1
    assert result.routes[0].error_category == "cliente_inexistente"

    generated = list((routes_root / "Eric Brizuela" / "Cortes").glob("CORTES octubre*.xlsx"))
    assert generated == []  # no debe quedar un archivo invalido tirado


# 10. ruta/vendedor invalidos (no estan en el catalogo oficial)
def test_invalid_vendor_route_combination(routes_root, sap_source, history, catalog, calendar):
    entries = [make_entry(vendor="José Carpio", route="Cortés")]  # Cortes es de Eric, no de Jose
    result = run(entries, routes_root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert result.failed == 1
    assert result.routes[0].error_category == "programacion_invalida"


# 11. una ruta falla pero otra puede completarse
def test_one_route_fails_another_succeeds_in_same_batch(tmp_path, base_file, sap_source, history, catalog, calendar):
    root = tmp_path / "RUTA VENDEDORES"
    good_dir = root / "Eric Brizuela" / "Cortes"
    good_dir.mkdir(parents=True)
    shutil.copy2(base_file, good_dir / "CORTES septiembre.xlsx")
    # segunda entrada: carpeta de vendedor que ni siquiera existe
    entries = [
        make_entry(entry_id="ok", vendor="Eric Brizuela", route="Cortés"),
        make_entry(entry_id="bad", vendor="Darwin Milla", route="Olancho"),
    ]
    result = run(entries, root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))
    assert result.succeeded == 1
    assert result.failed == 1
    statuses = {r.schedule_entry_id: r.status for r in result.routes}
    assert statuses["ok"] == "success"
    assert statuses["bad"] == "error"


# 12. logging correcto (historial queda registrado)
def test_history_is_recorded_correctly(routes_root, sap_source, history, catalog, calendar):
    entries = [make_entry()]
    run(entries, routes_root, sap_source, history, catalog, calendar, today=date(2026, 10, 8))

    rows = history.find_by_route("Cortés", vendor="Eric Brizuela")
    assert len(rows) == 1
    row = rows[0]
    assert row["estado"] == "exito"
    assert row["cantidad_clientes"] == 32
    assert row["archivo_generado"].endswith("CORTES octubre.xlsx")
    assert row["fuente_datos"] == "fake"
    assert row["duracion_ms"] is not None


# 13. el estado de la fuente solo cambia DESPUES del exito
def test_schedule_state_only_changes_after_success(tmp_path, base_file, sap_source, history, catalog, calendar):
    schedule_path = tmp_path / "schedule.json"
    schedule_path.write_text(json.dumps([
        {"id": "ok", "vendor": "Eric Brizuela", "route": "Cortés", "fecha_salida": "2026-10-13",
         "estado": "programada", "aprobado": True},
        {"id": "bad", "vendor": "Darwin Milla", "route": "Olancho", "fecha_salida": "2026-10-13",
         "estado": "programada", "aprobado": True},
    ]), encoding="utf-8")
    real_source = LocalScheduleSource(schedule_path)

    root = tmp_path / "RUTA VENDEDORES"
    good_dir = root / "Eric Brizuela" / "Cortes"
    good_dir.mkdir(parents=True)
    shutil.copy2(base_file, good_dir / "CORTES septiembre.xlsx")
    # Olancho: carpeta de vendedor no existe -> fallara

    process_due_routes(
        schedule_source=real_source, catalog=catalog, calendar=calendar, lead_business_days=3,
        routes_root=root, sap_data_source=sap_source, sap_source_label="fake",
        history=history, today=date(2026, 10, 8),
    )

    entries_after = {e.id: e.estado for e in real_source.list_entries()}
    assert entries_after["ok"] == "preparada"
    assert entries_after["bad"] == "programada"  # NO cambio: el fallo no marca como preparada


# 14. calculo de 1 dia habil anterior (ejemplo exacto del encargo)
def test_due_calculation_matches_business_example():
    """Eric tiene Cortes el martes; el lunes debe aparecer como due (con 1
    dia habil de anticipacion)."""
    tuesday = date(2026, 9, 15)
    monday = date(2026, 9, 14)
    entry = make_entry(fecha_salida=tuesday)
    calendar = BusinessCalendar()

    due_on_monday = compute_due_entries([entry], today=monday, lead_business_days=1, calendar=calendar)
    assert due_on_monday == [entry]

    due_on_sunday = compute_due_entries([entry], today=date(2026, 9, 13), lead_business_days=1, calendar=calendar)
    assert due_on_sunday == []


# 15. futura sustitucion de ScheduleSource
def test_runner_works_with_any_schedule_source_implementation(routes_root, sap_source, history, catalog, calendar):
    """FakeScheduleSource no es LocalScheduleSource ni la conoce: prueba que
    el runner solo depende de la interfaz ScheduleSource (import ABC)."""
    fake = FakeScheduleSource([make_entry()])
    result = process_due_routes(
        schedule_source=fake, catalog=catalog, calendar=calendar, lead_business_days=3,
        routes_root=routes_root, sap_data_source=sap_source, sap_source_label="fake",
        history=history, today=date(2026, 10, 8),
    )
    assert result.succeeded == 1
    # mark_prepared() default (no-op) no rompe nada, como pasaria con una
    # fuente de solo lectura tipo InSitu.
