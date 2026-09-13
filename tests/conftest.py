import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cxc_bot.config import load_route_config  # noqa: E402
from cxc_bot.data_source import LocalMockDataSource  # noqa: E402

REAL_SOURCE_FILE = Path("/Users/alejandrom/Desktop/CORTES septiembre.xlsx")
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_BASE = FIXTURES_DIR / "CORTES_septiembre_original.xlsx"


@pytest.fixture(scope="session", autouse=True)
def ensure_fixture_copy():
    """Copia el archivo real UNA vez a fixtures/ para que los tests nunca abran
    (ni muchos menos escriban) el original en el Escritorio del usuario."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    if not FIXTURE_BASE.exists():
        if not REAL_SOURCE_FILE.exists():
            pytest.skip(f"No se encontro el archivo real de referencia: {REAL_SOURCE_FILE}")
        shutil.copy2(REAL_SOURCE_FILE, FIXTURE_BASE)
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
