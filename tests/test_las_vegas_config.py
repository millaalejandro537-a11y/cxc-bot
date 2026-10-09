"""La config de LAS VEGAS debe describir el archivo REAL (no la estructura
sintetica de Fase 2) y el resolvedor debe encontrar su plantilla con el
nombre real ("LAS VEGAS <mes>.xlsx"). Solo se trabaja sobre copias en tmp_path.

OJO: el fixture LAS VEGAS MAYO.xlsx (23 clientes) es historico y solo sirve para
estructura; NO representa la lista vigente de 24 clientes de Las Vegas."""

import shutil
from datetime import date

import openpyxl
import pytest

from cxc_bot.config import load_route_config
from cxc_bot.excel_service import peek_roster
from cxc_bot.file_resolver import NoBaseFileFoundError, resolve_base_file

REAL_FIXTURE = "tests/fixtures/rutas_reales/LAS VEGAS MAYO.xlsx"
VENDOR = "Erick Brizuela"
REF_DATE = date(2026, 10, 14)


@pytest.fixture
def config():
    return load_route_config("LAS VEGAS")


def _route_dir(tmp_path):
    d = tmp_path / VENDOR / "Las Vegas"
    d.mkdir(parents=True)
    return d


def test_config_describes_the_real_file(config):
    assert config.filename_prefix == "LAS VEGAS"
    assert config.main_sheet == "LAS VEGAS"
    assert config.data_sheet == "Hoja1"


def test_config_sheets_exist_in_real_template(config):
    wb = openpyxl.load_workbook(REAL_FIXTURE)
    assert config.main_sheet in wb.sheetnames
    assert config.data_sheet in wb.sheetnames


def test_resolver_finds_real_template_name(tmp_path, config):
    d = _route_dir(tmp_path)
    shutil.copy2(REAL_FIXTURE, d / "LAS VEGAS SEPTIEMBRE.xlsx")
    found = resolve_base_file(tmp_path, VENDOR, "Las Vegas", config, reference_date=REF_DATE)
    assert found.name == "LAS VEGAS SEPTIEMBRE.xlsx"


def test_resolver_picks_latest_month_among_real_names(tmp_path, config):
    d = _route_dir(tmp_path)
    shutil.copy2(REAL_FIXTURE, d / "LAS VEGAS MAYO.xlsx")
    shutil.copy2(REAL_FIXTURE, d / "LAS VEGAS SEPTIEMBRE.xlsx")
    found = resolve_base_file(tmp_path, VENDOR, "Las Vegas", config, reference_date=REF_DATE)
    assert found.name == "LAS VEGAS SEPTIEMBRE.xlsx"


def test_resolver_prefers_explicit_day_format_for_new_outputs(tmp_path, config):
    d = _route_dir(tmp_path)
    shutil.copy2(REAL_FIXTURE, d / "LAS VEGAS SEPTIEMBRE.xlsx")
    shutil.copy2(REAL_FIXTURE, d / "LAS VEGAS 14 octubre 2026.xlsx")
    found = resolve_base_file(tmp_path, VENDOR, "Las Vegas", config, reference_date=date(2026, 10, 28))
    assert found.name == "LAS VEGAS 14 octubre 2026.xlsx"


def test_old_synthetic_prefix_is_not_recognized(tmp_path, config):
    """Regresion: con el prefijo sintetico 'LASVEGAS' el archivo real no se
    encontraba. El nombre sin espacio ya no es una plantilla valida."""
    d = _route_dir(tmp_path)
    shutil.copy2(REAL_FIXTURE, d / "LASVEGAS septiembre.xlsx")
    with pytest.raises(NoBaseFileFoundError):
        resolve_base_file(tmp_path, VENDOR, "Las Vegas", config, reference_date=REF_DATE)


def test_roster_is_readable_with_real_config(tmp_path, config):
    d = _route_dir(tmp_path)
    shutil.copy2(REAL_FIXTURE, d / "LAS VEGAS SEPTIEMBRE.xlsx")
    found = resolve_base_file(tmp_path, VENDOR, "Las Vegas", config, reference_date=REF_DATE)
    roster = peek_roster(found, config)
    assert len(roster) == 23
    assert all(e.codigo.startswith("C") for e in roster)
