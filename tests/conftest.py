import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cxc_bot.config import load_route_config  # noqa: E402
from cxc_bot.data_source import LocalMockDataSource  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_BASE = FIXTURES_DIR / "CORTES_septiembre_original.xlsx"


@pytest.fixture(scope="session", autouse=True)
def ensure_fixture_copy():
    """Usa el fixture local; nunca accede a archivos personales del host."""
    if not FIXTURE_BASE.is_file():
        pytest.fail("Falta tests/fixtures/CORTES_septiembre_original.xlsx; restaurar el fixture de pruebas.")
    yield FIXTURE_BASE


@pytest.fixture
def base_file(ensure_fixture_copy, tmp_path):
    """Copia de trabajo aislada por test, para no compartir estado entre tests."""
    dest = tmp_path / "CORTES_base.xlsx"
    shutil.copy2(ensure_fixture_copy, dest)
    return dest


@pytest.fixture
def cortes_config():
    return load_route_config("CORTES")


@pytest.fixture
def baseline_sap_records(ensure_fixture_copy):
    """Los registros SAP reales embebidos en Hoja2 del archivo de referencia,
    usados como punto de partida realista para las pruebas."""
    return LocalMockDataSource(ensure_fixture_copy, sheet_name="Hoja2").fetch_clients()
