import re
from datetime import date
from typing import Optional

from app.models import BUSINESS_FIELDS, Catalogs, ReporteExtraido, ReporteValidado, ValidationErrorItem


UNIT_ALIASES = {
    "horas": "horas",
    "hora": "horas",
    "hs": "horas",
    "hectareas": "hectáreas",
    "hectáreas": "hectáreas",
    "hectarea": "hectáreas",
    "hectárea": "hectáreas",
    "has": "hectáreas",
    "ha": "hectáreas",
    "surcos": "surcos",
    "surco": "surcos",
    "viajes": "viajes",
    "viaje": "viajes",
}

QUANTITY_RE = re.compile(
    r"^\s*(?P<number>\d+(?:[,.]\d+)?)\s*(?:cantidad\s+de\s+)?(?P<unit>[a-záéíóúñ]+)\s*$",
    re.IGNORECASE,
)


def normalize_quantity(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    match = QUANTITY_RE.match(raw.strip().lower())
    if not match:
        return None

    number = match.group("number").replace(",", ".")
    unit = UNIT_ALIASES.get(match.group("unit"))
    if not unit:
        return None

    if number.endswith(".0"):
        number = number[:-2]

    return f"{number} {unit}"


def _blank(value: Optional[str]) -> bool:
    return value is None or value.strip() == ""


def _norm(value: str) -> str:
    return value.strip().casefold()


def validate_report(
    reporte: ReporteExtraido,
    catalogs: Catalogs,
    telefono: Optional[str] = None,
) -> tuple[Optional[ReporteValidado], list[ValidationErrorItem]]:
    data = reporte.model_dump()
    errors: list[ValidationErrorItem] = []

    if telefono and telefono in catalogs.capataces_por_telefono:
        data["nombre_capataz"] = catalogs.capataces_por_telefono[telefono]

    for field in BUSINESS_FIELDS:
        if _blank(data.get(field)):
            errors.append(ValidationErrorItem(campo=field, mensaje="Campo obligatorio faltante."))

    if errors:
        return None, errors

    cantidad = normalize_quantity(data["cantidad"])
    if not cantidad:
        errors.append(
            ValidationErrorItem(
                campo="cantidad",
                mensaje="Cantidad debe incluir número y unidad válida: horas, hectáreas, surcos o viajes.",
            )
        )
    else:
        data["cantidad"] = cantidad

    try:
        date.fromisoformat(data["fecha"])
    except ValueError:
        errors.append(ValidationErrorItem(campo="fecha", mensaje="Fecha debe estar en formato AAAA-MM-DD."))

    lote_seccion = (data["lote"].strip(), data["seccion"].strip())
    if catalogs.lotes_secciones and lote_seccion not in catalogs.lotes_secciones:
        errors.append(ValidationErrorItem(campo="seccion", mensaje="Combinación lote/sección no reconocida."))

    codigo_tarea = data["codigo_tarea"].strip()
    expected_description = catalogs.tareas.get(codigo_tarea)
    if catalogs.tareas and expected_description is None:
        errors.append(ValidationErrorItem(campo="codigo_tarea", mensaje="Código de tarea no existe en catálogo."))
    elif expected_description and _norm(expected_description) != _norm(data["descripcion_tarea"]):
        errors.append(ValidationErrorItem(campo="descripcion_tarea", mensaje="Descripción contradice el código de tarea."))

    if catalogs.variedades and _norm(data["variedad"]) not in {_norm(v) for v in catalogs.variedades}:
        errors.append(ValidationErrorItem(campo="variedad", mensaje="Variedad no reconocida."))

    if catalogs.fuentes_nitrogenadas and _norm(data["fuente_nitrogenada"]) not in {
        _norm(v) for v in catalogs.fuentes_nitrogenadas
    }:
        errors.append(ValidationErrorItem(campo="fuente_nitrogenada", mensaje="Fuente nitrogenada no reconocida."))

    if catalogs.contratistas and _norm(data["contratista"]) not in {_norm(v) for v in catalogs.contratistas}:
        errors.append(ValidationErrorItem(campo="contratista", mensaje="Contratista no reconocido."))

    if errors:
        return None, errors

    cleaned = {field: str(data[field]).strip() for field in BUSINESS_FIELDS}
    return ReporteValidado(**cleaned), []
