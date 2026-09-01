from unittest.mock import patch

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


class FlakySheetsWriter(LocalSheetsWriter):
    """Fails the first append_report call, then succeeds on subsequent ones."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def append_report(self, reporte) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated Sheets outage")
        await super().append_report(reporte)


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
async def test_save_failure_reverts_to_pending_and_can_be_retried():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = FlakySheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","lote":"20","seccion":"3","codigo_tarea":"145",'
        '"descripcion_tarea":"Fertilización","cantidad":"25 has","variedad":"ACA 603",'
        '"fuente_nitrogenada":"Urea","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    telefono = "5490077778888"
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.9", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )
    await processor.process_audio("wamid.9")

    await processor.handle_text(telefono, "sí")  # falla la primera vez (simulado)

    assert "problema técnico" in whats_app.sent_messages[-1][1]
    assert repo.get("wamid.9").estado == EstadoProceso.PENDIENTE_CONFIRMACION
    assert sheets.rows == []

    await processor.handle_text(telefono, "sí")  # reintento: ahora sí guarda

    assert repo.get("wamid.9").estado == EstadoProceso.GUARDADO
    assert len(sheets.rows) == 1


@pytest.mark.asyncio
async def test_correction_with_unknown_field_gets_a_hint():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","lote":"20","seccion":"3","codigo_tarea":"145",'
        '"descripcion_tarea":"Fertilización","cantidad":"25 has","variedad":"ACA 603",'
        '"fuente_nitrogenada":"Urea","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    telefono = "5490099990000"
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.10", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )
    await processor.process_audio("wamid.10")

    await processor.handle_text(telefono, "corregir clima: soleado")

    assert "CORREGIR campo: valor" in whats_app.sent_messages[-1][1]


@pytest.mark.asyncio
async def test_catalogs_outage_notifies_instead_of_crashing():
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
        EstadoTecnico(message_id="wamid.8", telefono="5490055556666", estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )

    with patch("app.tasks.load_catalogs", side_effect=RuntimeError("Sheets is down")):
        await processor.process_audio("wamid.8")  # no debe lanzar aunque Sheets esté caído

    assert "problema técnico" in whats_app.sent_messages[-1][1]
    assert repo.get("wamid.8").estado == EstadoProceso.PROCESANDO
    assert sheets.rows == []


@pytest.mark.asyncio
async def test_successful_audio_sends_confirmation_as_buttons():
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
        EstadoTecnico(message_id="wamid.6", telefono="5490011112222", estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )

    await processor.process_audio("wamid.6")

    assert "¿Está todo bien?" in whats_app.sent_messages[-1][1]


@pytest.mark.asyncio
async def test_tapping_corregir_button_prompts_for_a_voice_correction():
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
        EstadoTecnico(message_id="wamid.7", telefono="5490033334444", estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )
    await processor.process_audio("wamid.7")

    # el tap del boton "Corregir" llega como el texto "corregir" (el id del boton)
    await processor.handle_text("5490033334444", "corregir")

    assert "pendiente de confirmar" in whats_app.sent_messages[-1][1]
    assert repo.get("wamid.7").estado == EstadoProceso.PENDIENTE_CONFIRMACION


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
