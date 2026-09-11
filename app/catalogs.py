import logging
import time
from typing import Optional

import gspread
from google.auth import default

from app.config import get_settings
from app.models import CapatazInfo, Catalogs

# Cache TTL in seconds
CATALOGS_TTL_SECONDS = 60
_cached_catalogs: Optional[Catalogs] = None
_cached_at: Optional[float] = None


def _seed_catalogs() -> Catalogs:
    """Local seed catalogs for development and tests.

    Production should load these values from Google Sheets auxiliary tabs or
    another controlled catalog source owned by the field team.
    """
    return Catalogs(
        capataces_por_telefono={
            "5491111111111": CapatazInfo(nombre="Juan Pérez", contratista="Trabajo propio", finca="Fronterita"),
        },
        lotes_secciones={("20", "3"), ("21", "1")},
        tareas={"145": "Fertilización", "201": "Pulverización"},
        contratistas={"Trabajo propio", "Servicios Norte"},
    )


def _cell(row: dict, key: str) -> str:
    """Read a worksheet cell as text. Sheets/gspread can hand back int/float/bool
    for cells that look numeric (e.g. a phone number), regardless of how they were
    written, so never assume the value is already a string."""
    value = row.get(key, "")
    return str(value).strip() if value is not None else ""


def _parse_capataces(rows: list[dict]) -> dict[str, CapatazInfo]:
    """Parse capataces from worksheet rows.

    "contratista" y "finca" son columnas opcionales en la hoja: si están, el
    reporte se completa solo con esos datos y no hace falta que la persona los diga.
    """
    result = {}
    for row in rows:
        telefono = _cell(row, "telefono")
        nombre = _cell(row, "nombre")
        if telefono and nombre:
            result[telefono] = CapatazInfo(
                nombre=nombre,
                contratista=_cell(row, "contratista") or None,
                finca=_cell(row, "finca") or None,
            )
    return result


def _parse_lotes_secciones(rows: list[dict]) -> set[tuple[str, str]]:
    """Parse lotes and secciones from worksheet rows."""
    result = set()
    for row in rows:
        lote = _cell(row, "lote")
        seccion = _cell(row, "seccion")
        if lote and seccion:
            result.add((lote, seccion))
    return result


def _parse_tareas(rows: list[dict]) -> dict[str, str]:
    """Parse tareas from worksheet rows."""
    result = {}
    for row in rows:
        codigo = _cell(row, "codigo")
        descripcion = _cell(row, "descripcion")
        if codigo and descripcion:
            result[codigo] = descripcion
    return result


def _parse_nombre_set(rows: list[dict], key: str = "nombre") -> set[str]:
    """Parse a simple set of names from worksheet rows."""
    result = set()
    for row in rows:
        nombre = _cell(row, key)
        if nombre:
            result.add(nombre)
    return result


class SheetsCatalogSource:
    """Load catalogs from Google Sheets."""

    def _get_client(self):
        """Get an authenticated gspread client."""
        credentials, _ = default()
        return gspread.authorize(credentials)

    def _load_from_sheets(self) -> Catalogs:
        """Load catalogs from Google Sheets."""
        settings = get_settings()
        client = self._get_client()
        sheet = client.open_by_key(settings.google_sheet_id)

        capataces_rows = sheet.worksheet("Capataces").get_all_records()
        lotes_secciones_rows = sheet.worksheet("LotesSecciones").get_all_records()
        tareas_rows = sheet.worksheet("Tareas").get_all_records()
        contratistas_rows = sheet.worksheet("Contratistas").get_all_records()

        return Catalogs(
            capataces_por_telefono=_parse_capataces(capataces_rows),
            lotes_secciones=_parse_lotes_secciones(lotes_secciones_rows),
            tareas=_parse_tareas(tareas_rows),
            contratistas=_parse_nombre_set(contratistas_rows, "nombre"),
        )

    def load(self) -> Catalogs:
        """Load catalogs with caching."""
        global _cached_catalogs, _cached_at
        now = time.monotonic()

        if (
            _cached_catalogs is not None
            and _cached_at is not None
            and (now - _cached_at) < CATALOGS_TTL_SECONDS
        ):
            return _cached_catalogs

        try:
            catalogs = self._load_from_sheets()
        except Exception:
            logging.warning("No se pudo refrescar catálogos desde Sheets", exc_info=True)
            if _cached_catalogs is not None:
                return _cached_catalogs
            raise

        _cached_catalogs = catalogs
        _cached_at = now
        return catalogs


def load_catalogs() -> Catalogs:
    """Load catalogs based on environment.

    Local environment uses seed catalogs; production uses Google Sheets.
    """
    if get_settings().environment == "local":
        return _seed_catalogs()
    return SheetsCatalogSource().load()
