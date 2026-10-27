import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cxc_bot.config import load_route_config  # noqa: E402
from cxc_bot.data_source import LocalMockDataSource  # noqa: E402

from synthetic_fixtures import build_cortes_base  # noqa: E402


@pytest.fixture(scope="session")
def ensure_fixture_copy(tmp_path_factory):
    """Libro base SINTETICO (ver tests/synthetic_fixtures.py): la suite no necesita ni lee datos SAP privados."""
    return build_cortes_base(tmp_path_factory.mktemp("base_sintetica") / "CORTES_septiembre_sintetico.xlsx")


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
    """Los registros SAP sinteticos embebidos en Hoja2 del libro base,
    usados como punto de partida realista para las pruebas."""
    return LocalMockDataSource(ensure_fixture_copy, sheet_name="Hoja2").fetch_clients()
