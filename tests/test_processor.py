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


class RecordingWhatsAppClient(LocalWhatsAppClient):
    """Como LocalWhatsAppClient, pero además guarda las filas de cada lista enviada
    - LocalWhatsAppClient solo guarda el cuerpo del mensaje, no las opciones."""

    def __init__(self) -> None:
        super().__init__()
        self.sent_lists: list[tuple[str, str, list[tuple[str, str]]]] = []

    async def send_list(self, telefono, body, button_label, rows):
        await super().send_list(telefono, body, button_label, rows)
        self.sent_lists.append((telefono, body, rows))


class FailingListWhatsAppClient(LocalWhatsAppClient):
    """send_list falla (por ejemplo la API de WhatsApp la rechaza); send_text sigue
    andando, para probar que el mensaje de texto se manda igual como respaldo."""

    async def send_list(self, telefono, body, button_label, rows):
        raise RuntimeError("simulated WhatsApp list rejection")


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
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":null,"seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.2",
            telefono="5492222222222",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )

    await processor.process_audio("wamid.2")

    assert repo.get("wamid.2").estado == EstadoProceso.PENDIENTE_DATOS
    assert sheets.rows == []
    assert "me falta el lote" in whats_app.sent_messages[-1][1]


@pytest.mark.asyncio
async def test_codigo_tarea_is_derived_from_the_spoken_task():
    """Un capataz dice 'fertilización', nunca '145': el código sale del catálogo."""
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":null,"descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    telefono = "5490066667777"
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.13", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )

    await processor.process_audio("wamid.13")

    assert repo.get("wamid.13").estado == EstadoProceso.PENDIENTE_CONFIRMACION
    await processor.handle_text(telefono, "sí")
    assert sheets.rows[0][5] == "145"  # código deducido de "Fertilización"


@pytest.mark.asyncio
async def test_missing_field_message_uses_plain_language():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":null,"seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.14",
            telefono="5490088889999",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )

    await processor.process_audio("wamid.14")

    ultimo = whats_app.sent_messages[-1][1]
    assert "el lote" in ultimo
    assert "codigo_tarea" not in ultimo  # nunca jerga técnica


@pytest.mark.asyncio
async def test_missing_fecha_is_autocompleted_instead_of_requested():
    from datetime import datetime, timedelta, timezone

    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":null,"finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.11", telefono="5490022223333", estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )

    await processor.process_audio("wamid.11")

    assert repo.get("wamid.11").estado == EstadoProceso.PENDIENTE_CONFIRMACION
    today_ar = datetime.now(timezone(timedelta(hours=-3))).date().isoformat()
    assert f"Fecha: {today_ar}" in whats_app.sent_messages[-1][1]

    await processor.handle_text("5490022223333", "sí")
    assert sheets.rows[0][0] == today_ar


@pytest.mark.asyncio
async def test_second_audio_is_treated_as_voice_correction_to_pending_report():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    telefono = "5496666666666"
    audio_payload_a = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    correction_payload = 'json://{"trabajador":"Raúl Soria"}'
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
    assert updated.reporte_extraido.trabajador == "Raúl Soria"
    assert updated.reporte_extraido.lote == "20"  # el resto de los datos no se pierde

    await processor.handle_text(telefono, "dale")
    assert repo.get("wamid.a").estado == EstadoProceso.GUARDADO
    assert sheets.rows[0][4] == "Raúl Soria"


@pytest.mark.asyncio
async def test_voice_correction_with_unintelligible_audio_asks_to_retry():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    telefono = "5498888888888"
    audio_payload_a = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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
async def test_complete_report_by_text_goes_straight_to_confirmation():
    """Un reporte completo escrito (no hablado) tiene que funcionar igual que uno
    por audio: sin nada pendiente antes, va directo a pedir confirmación."""
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    telefono = "5490088880000"
    payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )

    await processor.handle_text(telefono, payload, "wamid.text1")

    pending = repo.find_pending_by_phone(telefono)
    assert pending is not None
    assert pending.estado == EstadoProceso.PENDIENTE_CONFIRMACION
    assert "¿Está todo bien?" in whats_app.sent_messages[-1][1]

    await processor.handle_text(telefono, "CONFIRMAR")
    assert len(sheets.rows) == 1


@pytest.mark.asyncio
async def test_incomplete_report_by_text_asks_for_the_missing_field():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    telefono = "5490099991111"
    payload = 'json://{"lote":"20","seccion":"3"}'

    await processor.handle_text(telefono, payload, "wamid.text2")

    pending = repo.find_pending_by_phone(telefono)
    assert pending is not None
    assert pending.estado == EstadoProceso.PENDIENTE_DATOS
    assert "me falta" in whats_app.sent_messages[-1][1]


@pytest.mark.asyncio
async def test_a_retried_text_message_does_not_start_a_second_report():
    """Si el mismo message_id de WhatsApp llega dos veces (reintento), no debe
    crear un segundo reporte pendiente."""
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    telefono = "5490022223333"
    payload = 'json://{"lote":"20","seccion":"3"}'

    await processor.handle_text(telefono, payload, "wamid.text3")
    await processor.handle_text(telefono, payload, "wamid.text3")

    assert repo.get("wamid.text3") is not None
    # No hay un segundo documento con otro id para el mismo teléfono.
    assert sum(1 for i in repo._items.values() if i.telefono == telefono) == 1


@pytest.mark.asyncio
async def test_text_that_extracts_nothing_gets_the_welcome_message_not_an_error():
    """Distinto de un audio ininteligible (que sí es un error técnico): un texto que
    no parece un reporte ("gracias", "hola") es simplemente otra cosa, no una falla."""
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)

    await processor.handle_text("5490044445555", "muchas gracias!", "wamid.text4")

    assert "audio de voz" in whats_app.sent_messages[-1][1]
    assert repo.find_pending_by_phone("5490044445555") is None


@pytest.mark.asyncio
async def test_save_failure_reverts_to_pending_and_can_be_retried():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = FlakySheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    telefono = "5490099990000"
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.10", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )
    await processor.process_audio("wamid.10")

    await processor.handle_text(telefono, "corregir clima: soleado")

    assert "CORREGIR campo: valor" in whats_app.sent_messages[-1][1]


class UnavailableExtractor(LocalGeminiExtractor):
    async def extract_from_audio(self, audio_uri: str):
        raise RuntimeError("Gemini caído")


@pytest.mark.asyncio
async def test_ai_outage_reports_a_technical_problem_not_a_missing_field():
    """Si la IA falla, el bot no debe decir 'falta: fecha' y mandar a grabar de nuevo."""
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, UnavailableExtractor(), whats_app, sheets)
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.12",
            telefono="5490044445555",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio="gs://bucket/audio.ogg",
        )
    )

    await processor.process_audio("wamid.12")

    ultimo = whats_app.sent_messages[-1][1]
    assert "problema técnico" in ultimo
    assert "porque falta:" not in ultimo  # no debe culpar al usuario por un dato faltante
    assert sheets.rows == []


@pytest.mark.asyncio
async def test_catalogs_outage_notifies_instead_of_crashing():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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
async def test_plain_text_answers_the_single_field_the_bot_asked_for():
    """Regresión: alguien escribía la respuesta directo ("20") en vez de
    CORREGIR lote: 20, y el bot la ignoraba. Si solo falta un dato, no hay
    ambigüedad sobre a cuál responde, así que se toma directo."""
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":null,"codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    telefono = "5490011112222"
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.plain1", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )
    await processor.process_audio("wamid.plain1")
    assert "me falta el nombre de quién hizo la tarea" in whats_app.sent_messages[-1][1]

    await processor.handle_text(telefono, "Raúl Soria")

    assert repo.get("wamid.plain1").estado == EstadoProceso.PENDIENTE_CONFIRMACION
    assert repo.get("wamid.plain1").reporte_extraido.trabajador == "Raúl Soria"

    await processor.handle_text(telefono, "CONFIRMAR")
    assert sheets.rows[0][4] == "Raúl Soria"


@pytest.mark.asyncio
async def test_labeled_answer_is_recognized_without_the_word_corregir():
    """Regresión real: un capataz contestó "Seccion: 4" (repitiendo el nombre del
    dato, sin la palabra CORREGIR) y el bot lo ignoró. Nombrar el campo alcanza,
    incluso si además falta más de un dato a la vez."""
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    # A propósito faltan dos datos (seccion y trabajador) para probar que "campo:
    # valor" funciona aunque el atajo de "un solo dato faltante" no aplique.
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":null,'
        '"trabajador":null,"codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    telefono = "5490044445555"
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.labeled1", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )
    await processor.process_audio("wamid.labeled1")

    await processor.handle_text(telefono, "Seccion: 4")

    assert repo.get("wamid.labeled1").reporte_extraido.seccion == "4"
    # Sigue pendiente porque falta trabajador, pero seccion sí quedó aplicada.
    assert repo.get("wamid.labeled1").estado == EstadoProceso.PENDIENTE_DATOS


@pytest.mark.asyncio
async def test_plain_text_is_not_guessed_when_more_than_one_field_is_missing():
    """Si faltan varios datos, un texto suelto no se aplica a ninguno - no hay
    forma de saber a cuál responde sin arriesgarse a pisar el campo equivocado."""
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    telefono = "5490033334444"
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.plain2",
            telefono=telefono,
            estado=EstadoProceso.RECIBIDO,
            ruta_audio='json://{"lote":"20"}',
        )
    )
    await processor.process_audio("wamid.plain2")

    await processor.handle_text(telefono, "20")

    assert "pendiente de confirmar" in whats_app.sent_messages[-1][1]
    assert repo.get("wamid.plain2").reporte_extraido.finca is None


@pytest.mark.asyncio
async def test_missing_contratista_is_offered_as_a_tappable_list():
    """El catálogo de contratistas es chico (entra en el tope de 10 de WhatsApp):
    en vez de pedirlo por texto/audio, se manda para tocar."""
    repo = InMemoryStateRepository()
    whats_app = RecordingWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":null,"nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.list1", telefono="5490055556666", estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )

    await processor.process_audio("wamid.list1")

    assert len(whats_app.sent_lists) == 1
    telefono, body, rows = whats_app.sent_lists[0]
    assert "contratista" in body
    assert rows == [
        ("contratista_sel::Servicios Norte", "Servicios Norte"),
        ("contratista_sel::Trabajo propio", "Trabajo propio"),
    ]


@pytest.mark.asyncio
async def test_tapping_a_contratista_list_option_fills_the_field():
    repo = InMemoryStateRepository()
    whats_app = RecordingWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":null,"nombre_capataz":"Juan Pérez"}'
    )
    telefono = "5490066667777"
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.list2", telefono=telefono, estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )
    await processor.process_audio("wamid.list2")

    await processor.handle_text(telefono, "contratista_sel::Trabajo propio")

    assert repo.get("wamid.list2").estado == EstadoProceso.PENDIENTE_CONFIRMACION
    assert repo.get("wamid.list2").reporte_extraido.contratista == "Trabajo propio"

    await processor.handle_text(telefono, "CONFIRMAR")
    assert sheets.rows[0][8] == "Trabajo propio"


@pytest.mark.asyncio
async def test_falls_back_to_text_when_sending_the_list_fails():
    repo = InMemoryStateRepository()
    whats_app = FailingListWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":null,"nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(message_id="wamid.list3", telefono="5490077778888", estado=EstadoProceso.RECIBIDO, ruta_audio=audio_payload)
    )

    await processor.process_audio("wamid.list3")  # no debe lanzar aunque falle el envío de la lista

    assert "me falta el contratista" in whats_app.sent_messages[-1][1]


@pytest.mark.asyncio
async def test_handle_text_sends_format_hint_for_malformed_correction():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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


class RecordingAudioStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete_audio(self, ruta_audio: str) -> None:
        self.deleted.append(ruta_audio)


class RecordingTaskQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict]] = []

    async def enqueue(self, path: str, body: dict) -> None:
        self.enqueued.append((path, body))


class FailingAudioStorage:
    async def delete_audio(self, ruta_audio: str) -> None:
        raise RuntimeError("simulated GCS delete failure")


@pytest.mark.asyncio
async def test_confirming_schedules_audio_deletion_via_storage_when_no_queue():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    storage = RecordingAudioStorage()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets, storage=storage)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.delete-1",
            telefono="5491111111111",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )
    await processor.process_audio("wamid.delete-1")
    # El audio real (no el fixture json://) es el que hay que borrar una vez guardado.
    repo.update("wamid.delete-1", ruta_audio="gs://bucket/audios/2026/06/18/wamid.delete-1.ogg")

    await processor.handle_text("5491111111111", "CONFIRMAR")

    assert repo.get("wamid.delete-1").estado == EstadoProceso.GUARDADO
    assert storage.deleted == ["gs://bucket/audios/2026/06/18/wamid.delete-1.ogg"]


@pytest.mark.asyncio
async def test_confirming_enqueues_audio_deletion_when_task_queue_configured():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    storage = RecordingAudioStorage()
    queue = RecordingTaskQueue()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets, storage=storage, task_queue=queue)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.delete-3",
            telefono="5493333333333",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )
    await processor.process_audio("wamid.delete-3")
    repo.update("wamid.delete-3", ruta_audio="gs://bucket/audios/2026/06/18/wamid.delete-3.ogg")

    await processor.handle_text("5493333333333", "CONFIRMAR")

    assert queue.enqueued == [
        ("/tasks/delete-audio", {"ruta_audio": "gs://bucket/audios/2026/06/18/wamid.delete-3.ogg"})
    ]
    assert storage.deleted == []  # se usó la cola, no el borrado directo


@pytest.mark.asyncio
async def test_confirming_does_not_schedule_deletion_for_dev_json_payload():
    """El audio 'json://' es un fixture de desarrollo, no un archivo real: no hay nada que borrar."""
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    storage = RecordingAudioStorage()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets, storage=storage)
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.delete-4",
            telefono="5494444444444",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )
    await processor.process_audio("wamid.delete-4")

    await processor.handle_text("5494444444444", "CONFIRMAR")

    assert storage.deleted == []


@pytest.mark.asyncio
async def test_audio_deletion_failure_does_not_break_confirmation():
    repo = InMemoryStateRepository()
    whats_app = LocalWhatsAppClient()
    sheets = LocalSheetsWriter()
    processor = ReportProcessor(repo, LocalGeminiExtractor(), whats_app, sheets, storage=FailingAudioStorage())
    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    repo.create_if_absent(
        EstadoTecnico(
            message_id="wamid.delete-5",
            telefono="5495555555555",
            estado=EstadoProceso.RECIBIDO,
            ruta_audio=audio_payload,
        )
    )
    await processor.process_audio("wamid.delete-5")
    repo.update("wamid.delete-5", ruta_audio="gs://bucket/audios/2026/06/18/wamid.delete-5.ogg")

    await processor.handle_text("5495555555555", "CONFIRMAR")  # no debe lanzar

    assert repo.get("wamid.delete-5").estado == EstadoProceso.GUARDADO
    assert len(sheets.rows) == 1
