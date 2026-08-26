import pytest

from app.validators import normalize_quantity


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8 horas", "8 horas"),
        ("8 hs", "8 horas"),
        ("25 hectáreas", "25 hectáreas"),
        ("25 has", "25 hectáreas"),
        ("14 surcos", "14 surcos"),
        ("6 viajes", "6 viajes"),
        ("6 cantidad de viajes", "6 viajes"),
        ("3,5 hs", "3.5 horas"),
    ],
)
def test_normalize_quantity_accepts_valid_units(raw, expected):
    assert normalize_quantity(raw) == expected


@pytest.mark.parametrize("raw", ["25", "10 metros", "12 toneladas", "5 kilos", "muchas hectáreas", "dos viajes"])
def test_normalize_quantity_rejects_invalid_quantities(raw):
    assert normalize_quantity(raw) is None
