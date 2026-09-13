"""Verificacion cruzada e independiente: en vez de confiar solo en que
compute_cached_values replica bien la logica de VLOOKUP/SUM, se usa un motor
de calculo de formulas de verdad (la libreria `formulas`) para evaluar el
archivo generado y comparar contra los valores que inyectamos.

Esto es solo para pruebas (dependencia pesada: numpy/scipy). El motor de
produccion NO depende de `formulas`; calcula VLOOKUP/SUM el mismo con
compute_cached_values, que es mas rapido y no necesita esas dependencias.
"""

from datetime import date

import openpyxl
import pytest

pytest.importorskip("formulas")
import formulas  # noqa: E402

from cxc_bot.excel_service import generate_route  # noqa: E402


def test_cached_values_match_independent_formula_engine(base_file, cortes_config, baseline_sap_records, tmp_path):
    output = tmp_path / "CORTES octubre.xlsx"
    generate_route(
        route_config=cortes_config,
        base_path=base_file,
        output_path=output,
        new_date=date(2026, 10, 12),
        sap_records=baseline_sap_records,
    )

    xl_model = formulas.ExcelModel().loads(str(output)).finish()
    solution = xl_model.calculate()

    def get(cell_ref):
        key = f"'[{output.name}]CORTES'!{cell_ref}"
        return solution[key].value[0, 0]

    wb = openpyxl.load_workbook(output, data_only=True)
    ws = wb["CORTES"]

    for row in range(8, 40):
        cached_name = ws[f"C{row}"].value
        cached_saldo = ws[f"E{row}"].value
        real_name = get(f"C{row}")
        real_saldo = get(f"E{row}")
        assert cached_name == real_name, f"C{row}: cache={cached_name!r} vs formulas={real_name!r}"
        assert cached_saldo == pytest.approx(real_saldo, abs=0.001), f"E{row}: cache={cached_saldo} vs formulas={real_saldo}"

    cached_total = ws["E40"].value
    real_total = get("E40")
    assert cached_total == pytest.approx(real_total, abs=0.001)
