import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.models import (
    BUSINESS_FIELDS,
    REQUIRED_FIELDS,
    Catalogs,
    ReporteExtraido,
    ReporteValidado,
    ValidationErrorItem,
)

ARGENTINA_TZ = timezone(timedelta(hours=-3))


def today_in_argentina() -> str:
    return datetime.now(ARGENTINA_TZ).date().isoformat()


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
    normalized = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _complete_tarea(data: dict, catalogs: Catalogs) -> None:
    """Resolve the task code from the catalog, without ever rejecting what was said.

    A capataz says the task however it comes out ("fumigué", "carpí el lote de
    arriba"), never by code: the code is an office-side concept. So the spoken
    description is always kept verbatim, and the code is only filled in when it can
    be looked up unambiguously. No match just means the code stays empty for the
    office to complete later - the report is never held back over it.
    """
    codigo = data.get("codigo_tarea")
    descripcion = data.get("descripcion_tarea")

    if catalogs.tareas:
        if _blank(codigo) and not _blank(descripcion):
            coincidencias = [c for c, d in catalogs.tareas.items() if _norm(d) == _norm(descripcion)]
            if len(coincidencias) == 1:  # ambiguo => mejor dejarlo vacío que elegir mal
                data["codigo_tarea"] = coincidencias[0]
        elif _blank(descripcion) and not _blank(codigo):
            desde_catalogo = catalogs.tareas.get(codigo.strip())
            if desde_catalogo:
                data["descripcion_tarea"] = desde_catalogo

    if _blank(data.get("codigo_tarea")):
        data["codigo_tarea"] = ""


def validate_report(
    reporte: ReporteExtraido,
    catalogs: Catalogs,
    telefono: Optional[str] = None,
) -> tuple[Optional[ReporteValidado], list[ValidationErrorItem]]:
    data = reporte.model_dump()
    errors: list[ValidationErrorItem] = []

    if telefono and telefono in catalogs.capataces_por_telefono:
        info = catalogs.capataces_por_telefono[telefono]
        data["nombre_capataz"] = info.nombre
        # Si la hoja de capataces ya tiene cargado el contratista o la finca de esta
        # persona, no hace falta que los diga: se completan solos. Si no están
        # cargados (puede trabajar para más de uno), sigue el flujo normal y se
        # los pide como cualquier otro dato.
        if info.contratista:
            data["contratista"] = info.contratista
        if info.finca:
            data["finca"] = info.finca

    if _blank(data.get("fecha")):
        data["fecha"] = today_in_argentina()

    _complete_tarea(data, catalogs)

    for field in REQUIRED_FIELDS:
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

    # Ni el lote/sección ni el contratista se validan contra el catálogo a
    # propósito, por la misma razón que la tarea: mientras las hojas no tengan
    # cargada la lista real de la empresa (estamos en piloto), rechazar por catálogo
    # significa rechazar datos reales que la persona sí dijo bien. Se registra tal
    # cual - la revisión de qué no matchea con ningún catálogo se hace después,
    # mirando la planilla, no frenando la carga en el momento.

    if errors:
        return None, errors

    cleaned = {field: str(data[field]).strip() for field in BUSINESS_FIELDS}
    return ReporteValidado(**cleaned), []
