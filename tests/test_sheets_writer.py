import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import ReporteValidado
from app.sheets_writer import GoogleSheetsWriter, LocalSheetsWriter, get_sheets_writer


@pytest.fixture
def reporte_valido():
    return ReporteValidado(
        fecha="2025-01-15",
        lote="A1",
        seccion="1",
        codigo_tarea="T001",
        descripcion_tarea="Riego",
        cantidad="100",
        variedad="Maíz",
        fuente_nitrogenada="Urea",
        contratista="Contractor X",
        nombre_capataz="Juan",
    )


class TestLocalSheetsWriter:
    @pytest.mark.asyncio
    async def test_append_report_success(self, reporte_valido):
        writer = LocalSheetsWriter()
        await writer.append_report(reporte_valido)
        assert len(writer.rows) == 1
        assert writer.rows[0] == reporte_valido.to_sheet_row()

    @pytest.mark.asyncio
    async def test_append_multiple_reports(self, reporte_valido):
        writer = LocalSheetsWriter()
        await writer.append_report(reporte_valido)
        await writer.append_report(reporte_valido)
        assert len(writer.rows) == 2

    @pytest.mark.asyncio
    async def test_append_report_row_has_10_columns(self, reporte_valido):
        writer = LocalSheetsWriter()
        await writer.append_report(reporte_valido)
        assert len(writer.rows[0]) == 10


class TestGoogleSheetsWriter:
    @pytest.mark.asyncio
    async def test_append_report_success(self, reporte_valido):
        with patch("app.sheets_writer.gspread.authorize") as mock_auth, \
             patch("app.sheets_writer.default") as mock_default, \
             patch("app.sheets_writer.get_settings") as mock_settings:
            mock_creds = MagicMock()
            mock_default.return_value = (mock_creds, None)

            mock_client = MagicMock()
            mock_auth.return_value = mock_client

            mock_spreadsheet = MagicMock()
            mock_client.open_by_key.return_value = mock_spreadsheet

            mock_worksheet = MagicMock()
            mock_spreadsheet.worksheet.return_value = mock_worksheet

            mock_settings.return_value = MagicMock(google_sheet_tab="Reportes")

            writer = GoogleSheetsWriter("test-sheet-id")
            await writer.append_report(reporte_valido)

            mock_client.open_by_key.assert_called_once_with("test-sheet-id")
            mock_spreadsheet.worksheet.assert_called_once_with("Reportes")
            mock_worksheet.append_row.assert_called_once_with(reporte_valido.to_sheet_row())

    @pytest.mark.asyncio
    async def test_append_report_network_error(self, reporte_valido):
        with patch("app.sheets_writer.gspread.authorize") as mock_auth, \
             patch("app.sheets_writer.default") as mock_default, \
             patch("app.sheets_writer.get_settings") as mock_settings:
            mock_creds = MagicMock()
            mock_default.return_value = (mock_creds, None)

            mock_client = MagicMock()
            mock_auth.return_value = mock_client
            mock_client.open_by_key.side_effect = Exception("Network error")

            mock_settings.return_value = MagicMock(google_sheet_tab="Reportes")

            writer = GoogleSheetsWriter("test-sheet-id")
            with pytest.raises(Exception, match="Network error"):
                await writer.append_report(reporte_valido)

    @pytest.mark.asyncio
    async def test_append_report_worksheet_not_found(self, reporte_valido):
        with patch("app.sheets_writer.gspread.authorize") as mock_auth, \
             patch("app.sheets_writer.default") as mock_default, \
             patch("app.sheets_writer.get_settings") as mock_settings:
            mock_creds = MagicMock()
            mock_default.return_value = (mock_creds, None)

            mock_client = MagicMock()
            mock_auth.return_value = mock_client

            mock_spreadsheet = MagicMock()
            mock_client.open_by_key.return_value = mock_spreadsheet
            mock_spreadsheet.worksheet.side_effect = Exception("Worksheet not found")

            mock_settings.return_value = MagicMock(google_sheet_tab="Reportes")

            writer = GoogleSheetsWriter("test-sheet-id")
            with pytest.raises(Exception, match="Worksheet not found"):
                await writer.append_report(reporte_valido)

    @pytest.mark.asyncio
    async def test_append_report_row_has_10_columns(self, reporte_valido):
        with patch("app.sheets_writer.gspread.authorize") as mock_auth, \
             patch("app.sheets_writer.default") as mock_default, \
             patch("app.sheets_writer.get_settings") as mock_settings:
            mock_creds = MagicMock()
            mock_default.return_value = (mock_creds, None)

            mock_client = MagicMock()
            mock_auth.return_value = mock_client

            mock_spreadsheet = MagicMock()
            mock_client.open_by_key.return_value = mock_spreadsheet

            mock_worksheet = MagicMock()
            mock_spreadsheet.worksheet.return_value = mock_worksheet

            mock_settings.return_value = MagicMock(google_sheet_tab="Reportes")

            writer = GoogleSheetsWriter("test-sheet-id")
            await writer.append_report(reporte_valido)

            call_args = mock_worksheet.append_row.call_args
            assert len(call_args[0][0]) == 10


class TestGetSheetsWriter:
    def teardown_method(self):
        get_sheets_writer.cache_clear()

    def test_local_environment(self):
        with patch("app.sheets_writer.get_settings") as mock_settings:
            get_sheets_writer.cache_clear()
            mock_settings.return_value = MagicMock(environment="local", google_sheet_id=None)
            writer = get_sheets_writer()
            assert isinstance(writer, LocalSheetsWriter)

    def test_production_with_sheet_id(self):
        with patch("app.sheets_writer.get_settings") as mock_settings:
            get_sheets_writer.cache_clear()
            mock_settings.return_value = MagicMock(environment="production", google_sheet_id="sheet-123")
            writer = get_sheets_writer()
            assert isinstance(writer, GoogleSheetsWriter)
            assert writer.spreadsheet_id == "sheet-123"

    def test_production_without_sheet_id(self):
        with patch("app.sheets_writer.get_settings") as mock_settings:
            get_sheets_writer.cache_clear()
            mock_settings.return_value = MagicMock(environment="production", google_sheet_id=None)
            with pytest.raises(ValueError, match="GOOGLE_SHEET_ID"):
                get_sheets_writer()

    def test_factory_caching(self):
        with patch("app.sheets_writer.get_settings") as mock_settings:
            get_sheets_writer.cache_clear()
            mock_settings.return_value = MagicMock(environment="local", google_sheet_id=None)
            writer1 = get_sheets_writer()
            writer2 = get_sheets_writer()
            assert writer1 is writer2
