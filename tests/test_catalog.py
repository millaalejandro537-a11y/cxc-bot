import pytest

from cxc_bot.catalog import RouteNotAssignedError, VendorCatalog, VendorNotFoundError


@pytest.fixture
def catalog():
    return VendorCatalog.load()


def test_valid_vendor_route(catalog):
    catalog.validate("Eric Brizuela", "Cortés")  # no debe lanzar


def test_normalized_matching_ignores_accents_and_case(catalog):
    catalog.validate("eric brizuela", "CORTES")
    catalog.validate("ERIC BRIZUELA", "cortes")


def test_unknown_vendor_raises(catalog):
    with pytest.raises(VendorNotFoundError):
        catalog.validate("Vendedor Fantasma", "Cortés")


def test_route_not_belonging_to_vendor_raises(catalog):
    """Cortes es de Eric Brizuela, no de Jose Carpio: no confiar en texto
    suelto del Excel, validar contra el catalogo oficial."""
    with pytest.raises(RouteNotAssignedError):
        catalog.validate("José Carpio", "Cortés")


def test_nonexistent_route_raises(catalog):
    with pytest.raises(RouteNotAssignedError):
        catalog.validate("Eric Brizuela", "Ruta Que No Existe")


def test_canonical_forms(catalog):
    assert catalog.canonical_vendor("eric brizuela") == "Eric Brizuela"
    assert catalog.canonical_route("eric brizuela", "las vegas") == "Las Vegas"


def test_all_vendors_lists_the_three_official_vendors(catalog):
    vendors = catalog.all_vendors()
    assert len(vendors) == 3
    assert any("Carpio" in v for v in vendors)
    assert any("Milla" in v for v in vendors)
    assert any("Brizuela" in v for v in vendors)
