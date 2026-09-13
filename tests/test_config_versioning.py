from cxc_bot.config_versioning import ConfigVersionStore


def _write(path, content):
    path.write_text(content, encoding="utf-8")


def test_same_config_content_reuses_the_same_version_id(tmp_path):
    routes = tmp_path / "routes.json"
    catalog = tmp_path / "vendor_catalog.json"
    composition = tmp_path / "route_composition.json"
    _write(routes, '{"CORTES": {"vendor": "X"}}')
    _write(catalog, '{"X": ["CORTES"]}')
    _write(composition, "{}")

    store = ConfigVersionStore(
        db_path=tmp_path / "versions.sqlite3",
        routes_path=routes, vendor_catalog_path=catalog, route_composition_path=composition,
    )
    v1 = store.current_version_id()
    v2 = store.current_version_id()
    assert v1 == v2


def test_changed_config_content_gets_a_new_version_id(tmp_path):
    routes = tmp_path / "routes.json"
    catalog = tmp_path / "vendor_catalog.json"
    composition = tmp_path / "route_composition.json"
    _write(routes, '{"CORTES": {"vendor": "X"}}')
    _write(catalog, '{"X": ["CORTES"]}')
    _write(composition, "{}")

    store = ConfigVersionStore(
        db_path=tmp_path / "versions.sqlite3",
        routes_path=routes, vendor_catalog_path=catalog, route_composition_path=composition,
    )
    v1 = store.current_version_id()

    _write(routes, '{"CORTES": {"vendor": "Y"}}')  # cambia el contenido
    v2 = store.current_version_id()

    assert v1 != v2
    # ambas versiones quedan guardadas, ninguna se pierde
    assert store.get_version(v1).routes_json != store.get_version(v2).routes_json


def test_get_route_config_as_of_returns_historical_content(tmp_path):
    routes = tmp_path / "routes.json"
    catalog = tmp_path / "vendor_catalog.json"
    composition = tmp_path / "route_composition.json"
    _write(routes, '{"CORTES": {"vendor": "Original"}}')
    _write(catalog, "{}")
    _write(composition, "{}")

    store = ConfigVersionStore(
        db_path=tmp_path / "versions.sqlite3",
        routes_path=routes, vendor_catalog_path=catalog, route_composition_path=composition,
    )
    v1 = store.current_version_id()

    _write(routes, '{"CORTES": {"vendor": "Cambiado"}}')
    store.current_version_id()  # crea v2, pero v1 no se toca

    historical = store.get_route_config_as_of(v1, "CORTES")
    assert historical == {"vendor": "Original"}


def test_missing_config_files_do_not_crash(tmp_path):
    """Si algun archivo de config no existe (por ejemplo route_composition.json
    en un despliegue sin rutas compuestas), no debe fallar -- se trata como
    contenido vacio."""
    store = ConfigVersionStore(
        db_path=tmp_path / "versions.sqlite3",
        routes_path=tmp_path / "no_existe_routes.json",
        vendor_catalog_path=tmp_path / "no_existe_catalog.json",
        route_composition_path=tmp_path / "no_existe_composition.json",
    )
    version_id = store.current_version_id()
    assert version_id
