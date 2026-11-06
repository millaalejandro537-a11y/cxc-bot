from cxc_portal import discrepancias_fuente as d

FIN = {"C1": {"nombre": "A", "vendedor_codigo": "7", "vendedor_nombre": "Juan Pérez López", "zona": "01", "activo": True},
       "C2": {"nombre": "B", "vendedor_codigo": "7", "vendedor_nombre": "Juan Pérez López", "zona": "01", "activo": True},
       "C3": {"nombre": "C", "vendedor_codigo": "9", "vendedor_nombre": "Otra Persona", "zona": "03", "activo": False}}


def _ruta(vend="JUAN PEREZ", clientes=("C1", "C2", "C3", "C4")):
    return {"route_id": "R", "vendedor": vend, "clientes": [{"codigo": c, "zona": "Z", "nombre": c} for c in clientes]}


def test_detecta_vendedor_zona_inactivos_y_ausentes_sin_tocar_nada():
    rutas = [_ruta()]
    antes = repr(rutas)
    r = d.comparar(rutas, FIN)["rutas"][0]
    assert repr(rutas) == antes                                                        # nada se modifica
    assert r["no_en_fuente"] == ["C4"] and r["con_discrepancia"] is True
    assert r["vendedor"]["predominante_codigo"] == "7" and r["vendedor"]["coincide_con_vendedor_de_la_ruta"] is True
    assert [x["codigo"] for x in r["vendedor"]["discrepantes"]] == ["C3"]
    assert r["zonas"][0]["zona_financiera_predominante"] == "01" and [x["codigo"] for x in r["zonas"][0]["discrepantes"]] == ["C3"]
    assert [x["codigo"] for x in r["inactivos_en_fuente"]] == ["C3"]


def test_ruta_coherente_no_marca_discrepancia_y_el_nombre_del_vendedor_se_compara_sin_acentos():
    r = d.comparar([_ruta("juan perez lopez", ("C1", "C2"))], FIN)
    assert r["rutas_con_discrepancia"] == 0 and r["rutas"][0]["vendedor"]["coincide_con_vendedor_de_la_ruta"] is True


def test_vendedor_de_la_ruta_distinto_del_financiero_se_marca():
    r = d.comparar([_ruta("María Gómez", ("C1", "C2"))], FIN)["rutas"][0]
    assert r["vendedor"]["coincide_con_vendedor_de_la_ruta"] is False and r["con_discrepancia"] is True
