import asyncio
import logging
from functools import lru_cache

import gspread
from google.auth import default

from app.config import get_settings
from app.models import ReporteValidado, SHEETS_HEADERS

logger = logging.getLogger(__name__)


class SheetsWriter:
    async def append_report(self, reporte: ReporteValidado) -> None:
        raise NotImplementedError


class LocalSheetsWriter(SheetsWriter):
    def __init__(self) -> None:
        self.rows: list[list[str]] = []

    async def append_report(self, reporte: ReporteValidado) -> None:
        row = reporte.to_sheet_row()
        if len(row) != len(SHEETS_HEADERS):
            raise ValueError("La fila final debe tener exactamente 10 columnas.")
        self.rows.append(row)


class GoogleSheetsWriter(SheetsWriter):
    def __init__(self, spreadsheet_id: str) -> None:
        self.spreadsheet_id = spreadsheet_id
        self._client = None

    def _get_client(self) -> gspread.Client:
        if self._client is None:
            creds, _ = default()
            self._client = gspread.authorize(creds)
        return self._client

    async def append_report(self, reporte: ReporteValidado) -> None:
        row = reporte.to_sheet_row()
        if len(row) != len(SHEETS_HEADERS):
            raise ValueError("La fila final debe tener exactamente 10 columnas.")

        try:
            settings = get_settings()
            loop = asyncio.get_event_loop()

            def _append_to_sheet():
                client = self._get_client()
                spreadsheet = client.open_by_key(self.spreadsheet_id)
                worksheet = spreadsheet.worksheet(settings.google_sheet_tab)
                worksheet.append_row(row)

            await loop.run_in_executor(None, _append_to_sheet)
        except Exception as e:
            logger.error(f"Error al agregar fila a Google Sheets: {e}")
            raise


@lru_cache(maxsize=1)
def get_sheets_writer() -> SheetsWriter:
    settings = get_settings()
    if settings.environment == "local":
        return LocalSheetsWriter()
    elif settings.google_sheet_id:
        return GoogleSheetsWriter(settings.google_sheet_id)
    else:
        raise ValueError("GOOGLE_SHEET_ID no está configurado en ambiente no-local")
