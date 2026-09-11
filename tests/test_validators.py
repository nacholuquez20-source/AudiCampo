import pytest
from pydantic import ValidationError

from app.catalogs import load_catalogs
from app.models import ReporteExtraido
from app.validators import validate_report


def valid_report() -> ReporteExtraido:
    return ReporteExtraido(
        fecha="2026-06-18",
        finca="Fronterita",
        lote="20",
        seccion="3",
        trabajador="Aragón Martín",
        codigo_tarea="145",
        descripcion_tarea="Fertilización",
        cantidad="25 has",
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
        "Fronterita",
        "20",
        "3",
        "Aragón Martín",
        "145",
        "Fertilización",
        "25 hectáreas",
        "Trabajo propio",
        "Juan Pérez",
    ]


def test_finca_and_contratista_are_filled_from_catalog_when_not_spoken():
    """No hace falta decir la finca ni el contratista si ya están cargados para ese
    teléfono en la hoja de capataces - se completan solos."""
    reporte = valid_report().model_copy(update={"finca": None, "contratista": None})

    validated, errors = validate_report(reporte, load_catalogs(), telefono="5491111111111")

    assert errors == []
    assert validated is not None
    assert validated.finca == "Fronterita"
    assert validated.contratista == "Trabajo propio"


def test_finca_is_still_asked_when_catalog_has_no_match():
    """Un teléfono no cargado en la hoja (o sin finca asignada) sigue el flujo
    normal: si no lo dice, se le pide, en vez de quedar vacío."""
    reporte = valid_report().model_copy(update={"finca": None})

    validated, errors = validate_report(reporte, load_catalogs(), telefono="5490000000000")

    assert validated is None
    assert any(error.campo == "finca" for error in errors)


def test_validate_report_rejects_missing_fields():
    reporte = valid_report().model_copy(update={"trabajador": None})

    validated, errors = validate_report(reporte, load_catalogs())

    assert validated is None
    assert errors[0].campo == "trabajador"


def test_rudimentary_task_is_registered_verbatim_without_a_code():
    """La gente dice la tarea como le sale. Aunque no coincida con ningún código del
    catálogo, el reporte se guarda y la descripción queda tal cual la dijo."""
    reporte = valid_report().model_copy(
        update={"codigo_tarea": None, "descripcion_tarea": "carpí lo de arriba"}
    )

    validated, errors = validate_report(reporte, load_catalogs(), telefono="5491111111111")

    assert errors == []
    assert validated is not None
    assert validated.descripcion_tarea == "carpí lo de arriba"
    assert validated.codigo_tarea == ""  # lo completa la oficina


def test_task_description_is_never_rejected_for_disagreeing_with_the_code():
    reporte = valid_report().model_copy(update={"descripcion_tarea": "Cosecha"})

    validated, errors = validate_report(reporte, load_catalogs(), telefono="5491111111111")

    assert errors == []
    assert validated is not None
    assert validated.descripcion_tarea == "Cosecha"


def test_unrecognized_contratista_is_registered_verbatim_not_rejected():
    """Piloto sin catálogo completo: un contratista real que la empresa usa pero que
    todavía no está cargado en la hoja se guarda igual, no se bloquea el reporte."""
    reporte = valid_report().model_copy(update={"contratista": "Folker Simón"})

    validated, errors = validate_report(reporte, load_catalogs(), telefono="5490000000000")

    assert errors == []
    assert validated is not None
    assert validated.contratista == "Folker Simón"


def test_unrecognized_lote_seccion_is_registered_verbatim_not_rejected():
    reporte = valid_report().model_copy(update={"lote": "16", "seccion": "16"})

    validated, errors = validate_report(reporte, load_catalogs(), telefono="5490000000000")

    assert errors == []
    assert validated is not None
    assert validated.lote == "16"
    assert validated.seccion == "16"


def test_task_code_is_still_derived_when_the_description_matches_the_catalog():
    reporte = valid_report().model_copy(update={"codigo_tarea": None})

    validated, errors = validate_report(reporte, load_catalogs(), telefono="5491111111111")

    assert errors == []
    assert validated is not None
    assert validated.codigo_tarea == "145"


def test_extracted_report_forbids_extra_fields():
    with pytest.raises(ValidationError):
        ReporteExtraido.model_validate(
            {
                "fecha": "2026-06-18",
                "finca": "Fronterita",
                "lote": "20",
                "seccion": "3",
                "trabajador": "Aragón Martín",
                "codigo_tarea": "145",
                "descripcion_tarea": "Fertilización",
                "cantidad": "25 hectáreas",
                "contratista": "Trabajo propio",
                "nombre_capataz": "Juan Pérez",
                "maquina": "tractor",
            }
        )
