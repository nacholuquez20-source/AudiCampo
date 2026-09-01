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
async def test_second_audio_is_blocked_while_first_report_is_unconfirmed():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    telefono = "5496666666666"
    audio_payload_a = (
        'json://{"fecha":"2026-06-18","lote":"20","seccion":"3","codigo_tarea":"145",'
        '"descripcion_tarea":"Fertilización","cantidad":"25 has","variedad":"ACA 603",'
        '"fuente_nitrogenada":"Urea","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    audio_payload_b = (
        'json://{"fecha":"2026-06-19","lote":"21","seccion":"1","codigo_tarea":"201",'
        '"descripcion_tarea":"Pulverización","cantidad":"10 has","variedad":"DM 46R18",'
        '"fuente_nitrogenada":"UAN","contratista":"Servicios Norte","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.a", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload_a)
    )
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.b", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload_b)
    )

    await processor.process_audio("wamid.a")
    assert repo.get("wamid.a").estado == EstadoProceso.PENDIENTE_CONFIRMACION

    await processor.process_audio("wamid.b")

    # El segundo audio no se procesó: sigue en RECIBIDO y no se llamó a Gemini para nada.
    assert repo.get("wamid.b").estado == EstadoProceso.RECIBIDO
    assert "sin confirmar" in whats_app.sent_messages[-1][1]

    # El primer reporte sigue disponible para confirmar/corregir.
    await processor.handle_text(telefono, "CONFIRMAR")
    assert repo.get("wamid.a").estado == EstadoProceso.GUARDADO
    assert len(sheets.rows) == 1


@pytest.mark.asyncio
async def test_handle_text_accepts_flexible_confirmation_words():
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
        EstadoTecnico(message_id="wamid.5", telefono="5497777777777", estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )
    await processor.process_audio("wamid.5")

    await processor.handle_text("5497777777777", "dale")

    assert repo.get("wamid.5").estado == EstadoProceso.GUARDADO
    assert len(sheets.rows) == 1


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
