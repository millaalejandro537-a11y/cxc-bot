import pytest

from cxc_bot.catalog import RouteNotAssignedError, VendorCatalog, VendorNotFoundError


@pytest.fixture
def catalog():
    return VendorCatalog.load()


def test_valid_vendor_route(catalog):
    catalog.validate("Erick Brizuela", "Cortés")  # no debe lanzar


def test_normalized_matching_ignores_accents_and_case(catalog):
    catalog.validate("erick brizuela", "CORTES")
    catalog.validate("ERICK BRIZUELA", "cortes")


def test_unknown_vendor_raises(catalog):
    with pytest.raises(VendorNotFoundError):
        catalog.validate("Vendedor Fantasma", "Cortés")


def test_route_not_belonging_to_vendor_raises(catalog):
    """Cortes es de Erick Brizuela, no de Jose Carpio: no confiar en texto
    suelto del Excel, validar contra el catalogo oficial."""
    with pytest.raises(RouteNotAssignedError):
        catalog.validate("José Carpio", "Cortés")


def test_nonexistent_route_raises(catalog):
    with pytest.raises(RouteNotAssignedError):
        catalog.validate("Erick Brizuela", "Ruta Que No Existe")


def test_canonical_forms(catalog):
    assert catalog.canonical_vendor("erick brizuela") == "Erick Brizuela"
    assert catalog.canonical_route("erick brizuela", "las vegas") == "Las Vegas"


def test_all_vendors_lists_the_three_official_vendors(catalog):
    vendors = catalog.all_vendors()
    assert len(vendors) == 3
    assert any("Carpio" in v for v in vendors)
    assert any("Milla" in v for v in vendors)
    assert any("Brizuela" in v for v in vendors)
