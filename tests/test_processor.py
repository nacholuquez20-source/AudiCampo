import pytest

from app.firestore_state import InMemoryStateRepository
from app.gemini_extractor import LocalGeminiExtractor
from app.models import EstadoProceso, EstadoTecnico
from app.sheets_writer import LocalSheetsWriter
from app.tasks import ReportProcessor
from app.whatsapp import LocalWhatsAppClient


@pytest.mark.asyncio
async def test_processor_does_not_write_until_confirmed():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","lote":"20","seccion":"3","codigo_tarea":"145",'
        '"descripcion_tarea":"Fertilización","cantidad":"25 has","variedad":"ACA 603",'
        '"fuente_nitrogenada":"Urea","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.1",
            telefono="5491111111111",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )

    await processor.process_audio("wamid.1")

    assert repo.get("wamid.1").estado == EstadoProceso.PENDIENTE_CONFIRMACION
    assert sheets.rows == []

    await processor.handle_text("5491111111111", "CONFIRMAR")

    assert repo.get("wamid.1").estado == EstadoProceso.GUARDADO
    assert len(sheets.rows) == 1


@pytest.mark.asyncio
async def test_processor_requests_missing_data():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.2",
            telefono="5492222222222",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio='json://{"fecha":null}',
        )
    )

    await processor.process_audio("wamid.2")

    assert repo.get("wamid.2").estado == EstadoProceso.PENDIENTE_DATOS
    assert sheets.rows == []
    assert "falta: fecha" in whats_app.sent_messages[-1][1]


@pytest.mark.asyncio
async def test_handle_text_sends_welcome_when_nothing_pending():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)

    await processor.handle_text("5493333333333", "hola")

    assert len(whats_app.sent_messages) == 1
    assert "audio de voz" in whats_app.sent_messages[-1][1]


@pytest.mark.asyncio
async def test_handle_text_sends_reminder_for_unrecognized_text_when_pending():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","lote":"20","seccion":"3","codigo_tarea":"145",'
        '"descripcion_tarea":"Fertilización","cantidad":"25 has","variedad":"ACA 603",'
        '"fuente_nitrogenada":"Urea","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.3",
            telefono="5494444444444",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )
    await processor.process_audio("wamid.3")

    await processor.handle_text("5494444444444", "gracias")

    assert "pendiente de confirmar" in whats_app.sent_messages[-1][1]


@pytest.mark.asyncio
async def test_handle_text_sends_format_hint_for_malformed_correction():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","lote":"20","seccion":"3","codigo_tarea":"145",'
        '"descripcion_tarea":"Fertilización","cantidad":"25 has","variedad":"ACA 603",'
        '"fuente_nitrogenada":"Urea","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.4",
            telefono="5495555555555",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )
    await processor.process_audio("wamid.4")

    await processor.handle_text("5495555555555", "corregir lote")

    assert "CORREGIR campo: valor" in whats_app.sent_messages[-1][1]
