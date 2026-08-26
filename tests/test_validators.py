import pytest
from pydantic import ValidationError

from app.catalogs import load_catalogs
from app.models import ReporteExtraido
from app.validators import validate_report


def valid_report() -> ReporteExtraido:
    return ReporteExtraido(
        fecha="2026-06-18",
        lote="20",
        seccion="3",
        codigo_tarea="145",
        descripcion_tarea="Fertilización",
        cantidad="25 has",
        variedad="ACA 603",
        fuente_nitrogenada="Urea",
        contratista="Trabajo propio",
        nombre_capataz="Juan Pérez",
    )


def test_validate_report_returns_clean_non_null_business_record():
    validated, errors = validate_report(valid_report(), load_catalogs(), telefono="5491111111111")

    assert errors == []
    assert validated is not None
    assert validated.cantidad == "25 hectáreas"
    assert validated.to_sheet_row() == [
        "2026-06-18",
        "20",
        "3",
        "145",
        "Fertilización",
        "25 hectáreas",
        "ACA 603",
        "Urea",
        "Trabajo propio",
        "Juan Pérez",
    ]


def test_validate_report_rejects_missing_fields():
    reporte = valid_report().model_copy(update={"variedad": None})

    validated, errors = validate_report(reporte, load_catalogs())

    assert validated is None
    assert errors[0].campo == "variedad"


def test_validate_report_rejects_task_description_contradiction():
    reporte = valid_report().model_copy(update={"descripcion_tarea": "Cosecha"})

    validated, errors = validate_report(reporte, load_catalogs())

    assert validated is None
    assert any(error.campo == "descripcion_tarea" for error in errors)


def test_extracted_report_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ReporteExtraido.model_validate(
            {
                "fecha": "2026-06-18",
                "lote": "20",
                "seccion": "3",
                "codigo_tarea": "145",
                "descripcion_tarea": "Fertilización",
                "cantidad": "25 hectáreas",
                "variedad": "ACA 603",
                "fuente_nitrogenada": "Urea",
                "contratista": "Trabajo propio",
                "nombre_capataz": "Juan Pérez",
                "maquina": "tractor",
            }
        )
