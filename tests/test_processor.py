import pytest

from app.firestore_state import InMemoryStateRepository
from app.gemini_extractor import LocalGeminiExtractor
from app.models import EstadoProceso, EstadoTecnico
from app.sheets_writer import LocalSheetsWriter
from app.tasks import ReportProcessor
from app.whatsapp import LocalWhatsAppClient


class FailingWhatsAppClient:
    async def send_text(self, telefono: str, text: str) -> None:
        raise RuntimeError("simulated WhatsApp delivery failure")


@pytest.mark.asyncio
async def test_notify_failure_does_not_break_audio_processing():
    repo = InMemoryStateRepository()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), FailingWhatsAppClient(), sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","lote":"20","seccion":"3","codigo_tarea":"145",'
        '"descripcion_tarea":"Fertilización","cantidad":"25 has","variedad":"ACA 603",'
        '"fuente_nitrogenada":"Urea","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.fail",
            telefono="5490000000000",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )

    await processor.process_audio("wamid.fail")  # no debe lanzar aunque el envío de WhatsApp falle

    assert repo.get("wamid.fail").estado == EstadoProceso.PENDIENTE_CONFIRMACION


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
async def test_second_audio_is_treated_as_voice_correction_to_pending_report():
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
    correction_payload = 'json://{"variedad":"DM 46R18"}'
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.a", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload_a)
    )
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.b", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=correction_payload)
    )

    await processor.process_audio("wamid.a")
    assert repo.get("wamid.a").estado == EstadoProceso.PENDIENTE_CONFIRMACION

    await processor.process_audio("wamid.b")

    # El segundo audio no crea un reporte nuevo: corrige el pendiente y lo deja listo para confirmar.
    assert repo.get("wamid.b").estado == EstadoProceso.RECIBIDO
    updated = repo.get("wamid.a")
    assert updated.estado == EstadoProceso.PENDIENTE_CONFIRMACION
    assert updated.reporte_extraido.variedad == "DM 46R18"
    assert updated.reporte_extraido.lote == "20"  # el resto de los datos no se pierde

    await processor.handle_text(telefono, "dale")
    assert repo.get("wamid.a").estado == EstadoProceso.GUARDADO
    assert sheets.rows[0][6] == "DM 46R18"


@pytest.mark.asyncio
async def test_voice_correction_with_unintelligible_audio_asks_to_retry():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    telefono = "5498888888888"
    audio_payload_a = (
        'json://{"fecha":"2026-06-18","lote":"20","seccion":"3","codigo_tarea":"145",'
        '"descripcion_tarea":"Fertilización","cantidad":"25 has","variedad":"ACA 603",'
        '"fuente_nitrogenada":"Urea","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.c", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload_a)
    )
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.d", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio="json://{}")
    )

    await processor.process_audio("wamid.c")
    await processor.process_audio("wamid.d")

    assert "No entendí" in whats_app.sent_messages[-1][1]
    # El reporte pendiente sigue intacto, listo para confirmar.
    assert repo.get("wamid.c").estado == EstadoProceso.PENDIENTE_CONFIRMACION


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
