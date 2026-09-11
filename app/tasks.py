import asyncio
import logging
from typing import Optional
from uuid import uuid4

from app.catalogs import load_catalogs
from app.config import get_settings
from app.firestore_state import StateRepository, get_state_repository
from app.gemini_extractor import GeminiExtractor, get_gemini_extractor
from app.message_templates import (
    CONFIRMATION_BUTTONS,
    ai_unavailable_message,
    catalogs_unavailable_message,
    confirmation_summary,
    correction_format_hint,
    correction_understanding_failed_message,
    missing_field_message,
    pending_reminder_message,
    retry_exhausted_message,
    save_failed_message,
    saved_message,
    welcome_message,
)
from app.models import BUSINESS_FIELDS, Catalogs, EstadoProceso, EstadoTecnico, ReporteExtraido, ReporteValidado
from app.sheets_writer import SheetsWriter, get_sheets_writer
from app.storage import AudioStorage, LocalAudioStorage, get_audio_storage
from app.task_queue import TaskQueue, get_task_queue
from app.validators import validate_report
from app.whatsapp import WhatsAppClient, get_whatsapp_client

logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 3

CONFIRM_WORDS = {"confirmar", "confirmo", "si", "sí", "dale", "listo", "ok", "okay", "correcto"}

# Prefijo del id de fila que identifica una respuesta de la lista de contratista -
# nunca algo que alguien escribiría a mano, así no hay forma de confundirlo con un
# dato real.
CONTRATISTA_LIST_PREFIX = "contratista_sel::"
LIST_MAX_ROWS = 10
LIST_ROW_TITLE_MAX_LEN = 24

# Cómo se nombra cada campo al escribir "campo: valor" - con o sin el CORREGIR
# adelante. La gente naturalmente contesta repitiendo el nombre del dato que se le
# pidió ("Sección: 4"), no necesariamente con la palabra CORREGIR.
FIELD_NAME_MAP = {
    "fecha": "fecha",
    "finca": "finca",
    "lote": "lote",
    "seccion": "seccion",
    "sección": "seccion",
    "trabajador": "trabajador",
    "codigo tarea": "codigo_tarea",
    "código tarea": "codigo_tarea",
    "descripcion tarea": "descripcion_tarea",
    "descripción tarea": "descripcion_tarea",
    "cantidad": "cantidad",
    "contratista": "contratista",
    "nombre del capataz": "nombre_capataz",
    "nombre capataz": "nombre_capataz",
    "capataz": "nombre_capataz",
}


class ReportProcessor:
    def __init__(
        self,
        state_repo: StateRepository,
        extractor: GeminiExtractor,
        whats_app: WhatsAppClient,
        sheets: SheetsWriter,
        *,
        storage: Optional[AudioStorage] = None,
        task_queue: Optional[TaskQueue] = None,
    ) -> None:
        self.state_repo = state_repo
        self.extractor = extractor
        self.whatsapp = whats_app
        self.sheets = sheets
        self.storage = storage or LocalAudioStorage()
        self.task_queue = task_queue

    async def _notify(self, telefono: str, text: str) -> None:
        """Best-effort WhatsApp send: a delivery failure should never break processing."""
        try:
            await self.whatsapp.send_text(telefono, text)
        except Exception:
            logger.exception("No se pudo enviar el mensaje de WhatsApp a %s", telefono)

    async def _notify_confirmation(self, telefono: str, validated: ReporteValidado) -> None:
        """Send the report summary with tappable Confirmar/Corregir buttons."""
        try:
            await self.whatsapp.send_buttons(telefono, confirmation_summary(validated), CONFIRMATION_BUTTONS)
        except Exception:
            logger.exception("No se pudo enviar los botones de confirmación a %s", telefono)

    async def _load_catalogs_or_notify(self, telefono: str) -> Optional[Catalogs]:
        """Catalogs live in Google Sheets - a transient outage there should never crash processing."""
        try:
            return await asyncio.to_thread(load_catalogs)
        except Exception:
            logger.exception("No se pudieron cargar los catálogos")
            await self._notify(telefono, catalogs_unavailable_message())
            return None

    async def process_audio(self, message_id: str) -> None:
        item = self.state_repo.get(message_id)
        if not item or not item.ruta_audio:
            return

        existing_pending = self.state_repo.find_pending_by_phone(item.telefono)
        if existing_pending and existing_pending.message_id != message_id:
            await self._apply_voice_correction(existing_pending, item)
            return

        self.state_repo.update(message_id, estado=EstadoProceso.PROCESANDO, increment_attempts=True)

        # Escuchar el audio y leer la planilla no dependen entre sí: los corremos en paralelo.
        extracted, catalogs = await asyncio.gather(
            self.extractor.extract_from_audio(item.ruta_audio),
            asyncio.to_thread(load_catalogs),
            return_exceptions=True,
        )
        if isinstance(extracted, BaseException):
            logger.error("Falló la extracción del audio %s", message_id, exc_info=extracted)
            await self._notify(item.telefono, ai_unavailable_message())
            await self._fail_or_review(message_id, EstadoProceso.ERROR_IA)
            return
        if isinstance(catalogs, BaseException):
            logger.error("No se pudieron cargar los catálogos", exc_info=catalogs)
            await self._notify(item.telefono, catalogs_unavailable_message())
            return

        await self._save_new_extraction(message_id, item.telefono, extracted, catalogs)

    async def _save_new_extraction(
        self, message_id: str, telefono: str, extracted: ReporteExtraido, catalogs: Catalogs
    ) -> None:
        """Valida un reporte recién extraído (de audio o de texto) y avanza el estado
        según corresponda. Común a ambos orígenes: de acá para adelante ya no importa
        si el dato vino hablado o escrito."""
        validated, errors = validate_report(extracted, catalogs, telefono=telefono)
        if errors:
            self.state_repo.update(
                message_id,
                estado=EstadoProceso.PENDIENTE_DATOS,
                reporte_extraido=extracted,
                errores_validacion=errors,
            )
            await self._notify_missing_field(telefono, errors[0].campo, catalogs)
            return

        self.state_repo.update(
            message_id,
            estado=EstadoProceso.PENDIENTE_CONFIRMACION,
            reporte_extraido=extracted,
            errores_validacion=[],
        )
        await self._notify_confirmation(telefono, validated)

    async def _start_report_from_text(self, telefono: str, text: str, message_id: Optional[str]) -> None:
        """Un texto sin nada pendiente puede ser un reporte nuevo escrito a mano, no
        solo un saludo. Se intenta extraer igual que un audio; si Gemini no encuentra
        ningún dato de reporte ahí, se asume que era otra cosa (un saludo, una
        pregunta) y se manda la bienvenida en vez de un error confuso."""
        if not text:
            await self._notify(telefono, welcome_message())
            return

        extracted, catalogs = await asyncio.gather(
            self.extractor.extract_from_text(text),
            asyncio.to_thread(load_catalogs),
            return_exceptions=True,
        )
        if isinstance(extracted, BaseException):
            logger.error("Falló la extracción de texto para %s", telefono, exc_info=extracted)
            await self._notify(telefono, ai_unavailable_message())
            return

        extracted_data = extracted.model_dump()
        if not any(extracted_data.get(field) for field in BUSINESS_FIELDS):
            await self._notify(telefono, welcome_message())
            return

        if isinstance(catalogs, BaseException):
            logger.error("No se pudieron cargar los catálogos", exc_info=catalogs)
            await self._notify(telefono, catalogs_unavailable_message())
            return

        resolved_id = message_id or f"text-{uuid4()}"
        state, created = self.state_repo.create_if_absent(
            EstadoTecnico(message_id=resolved_id, telefono=telefono, estado=EstadoProceso.RECIBIDO)
        )
        if not created:
            return  # reintento del mismo mensaje de WhatsApp: ya se está procesando o se procesó

        self.state_repo.update(resolved_id, estado=EstadoProceso.PROCESANDO, increment_attempts=True)
        await self._save_new_extraction(resolved_id, telefono, extracted, catalogs)

    async def _apply_voice_correction(self, pending: EstadoTecnico, new_item: EstadoTecnico) -> None:
        """Treat a new audio arriving while a report is pending as a spoken correction to it."""
        correction, catalogs = await asyncio.gather(
            self.extractor.extract_from_audio(new_item.ruta_audio),
            asyncio.to_thread(load_catalogs),
            return_exceptions=True,
        )
        if isinstance(correction, BaseException):
            logger.error("Falló la extracción del audio de corrección", exc_info=correction)
            await self._notify(pending.telefono, ai_unavailable_message())
            return

        correction_data = correction.model_dump()
        if not any(correction_data.get(field) for field in BUSINESS_FIELDS):
            await self._notify(pending.telefono, correction_understanding_failed_message())
            return

        if isinstance(catalogs, BaseException):
            logger.error("No se pudieron cargar los catálogos", exc_info=catalogs)
            await self._notify(pending.telefono, catalogs_unavailable_message())
            return

        merged_data = pending.reporte_extraido.model_dump()
        for field in BUSINESS_FIELDS:
            if correction_data.get(field):
                merged_data[field] = correction_data[field]
        merged = ReporteExtraido(**merged_data)

        validated, errors = validate_report(merged, catalogs, telefono=pending.telefono)
        next_state = EstadoProceso.PENDIENTE_DATOS if errors else EstadoProceso.PENDIENTE_CONFIRMACION
        self.state_repo.update(
            pending.message_id,
            estado=next_state,
            reporte_extraido=merged,
            errores_validacion=errors,
        )
        if errors:
            await self._notify_missing_field(pending.telefono, errors[0].campo, catalogs)
        else:
            await self._notify_confirmation(pending.telefono, validated)

    async def handle_text(self, telefono: str, text: str, message_id: Optional[str] = None) -> None:
        pending = self.state_repo.find_pending_by_phone(telefono)
        normalized = text.strip()

        if not pending or not pending.reporte_extraido:
            await self._start_report_from_text(telefono, normalized, message_id)
            return

        if normalized.startswith(CONTRATISTA_LIST_PREFIX):
            valor = normalized[len(CONTRATISTA_LIST_PREFIX):]
            await self._apply_field_value(pending, telefono, "contratista", valor)
            return

        if normalized.casefold() in CONFIRM_WORDS:
            catalogs = await self._load_catalogs_or_notify(telefono)
            if catalogs is None:
                return
            validated, errors = validate_report(pending.reporte_extraido, catalogs, telefono=telefono)
            if errors:
                self.state_repo.update(
                    pending.message_id,
                    estado=EstadoProceso.PENDIENTE_DATOS,
                    errores_validacion=errors,
                )
                await self._notify_missing_field(telefono, errors[0].campo, catalogs)
                return

            self.state_repo.update(pending.message_id, estado=EstadoProceso.CONFIRMADO)
            try:
                await self.sheets.append_report(validated)
            except Exception:
                logger.exception("No se pudo guardar el reporte %s en Sheets", pending.message_id)
                # Volvemos a dejarlo como pendiente de confirmar para que un futuro "sí" reintente
                # el guardado, en vez de dejarlo trabado sin que nadie se entere.
                self.state_repo.update(pending.message_id, estado=EstadoProceso.PENDIENTE_CONFIRMACION)
                await self._notify(telefono, save_failed_message())
                return
            self.state_repo.update(pending.message_id, estado=EstadoProceso.GUARDADO)
            await self._notify(telefono, saved_message())
            await self._schedule_audio_deletion(pending)
            return

        explicit_correccion = normalized.casefold().startswith("corregir ")
        if explicit_correccion:
            normalized_sin_corregir = normalized[len("corregir ") :]
        else:
            normalized_sin_corregir = normalized

        # La gente contesta de forma natural repitiendo el nombre del dato ("Sección:
        # 4"), no necesariamente con la palabra CORREGIR adelante - se reconoce igual,
        # con o sin ella, siempre que el nombre del campo sea uno que existe.
        if ":" in normalized_sin_corregir:
            field, value = [part.strip() for part in normalized_sin_corregir.split(":", 1)]
            if field.casefold() in FIELD_NAME_MAP:
                await self._apply_correction(pending.message_id, telefono, field, value)
                return
            if explicit_correccion:
                await self._notify(telefono, correction_format_hint())
                return

        # El bot está esperando puntualmente un dato (le acaba de decir a la persona
        # "me falta X"): un texto suelto ("20", "Juan") se toma como la respuesta a
        # ESE dato, no se descarta. Solo aplica cuando falta un único campo - si
        # faltara más de uno no habría forma de saber a cuál responde.
        if pending.estado == EstadoProceso.PENDIENTE_DATOS and len(pending.errores_validacion) == 1 and normalized:
            campo = pending.errores_validacion[0].campo
            await self._apply_field_value(pending, telefono, campo, normalized)
            return

        await self._notify(telefono, pending_reminder_message())

    async def _apply_correction(self, message_id: str, telefono: str, field: str, value: str) -> None:
        item = self.state_repo.get(message_id)
        if not item or not item.reporte_extraido:
            return

        model_field = FIELD_NAME_MAP.get(field.casefold())
        if not model_field:
            await self._notify(telefono, correction_format_hint())
            return

        await self._apply_field_value(item, telefono, model_field, value)

    async def _apply_field_value(self, item: EstadoTecnico, telefono: str, model_field: str, value: str) -> None:
        """Set one field on the pending report and re-validate. Shared by the
        explicit `CORREGIR campo: valor` command and by a plain text reply that
        answers whatever single field the bot just asked for."""
        updated = item.reporte_extraido.model_copy(update={model_field: value})
        catalogs = await self._load_catalogs_or_notify(telefono)
        if catalogs is None:
            return
        validated, errors = validate_report(updated, catalogs, telefono=telefono)
        next_state = EstadoProceso.PENDIENTE_DATOS if errors else EstadoProceso.PENDIENTE_CONFIRMACION
        self.state_repo.update(
            item.message_id,
            estado=next_state,
            reporte_extraido=updated,
            errores_validacion=errors,
        )
        if errors:
            await self._notify_missing_field(telefono, errors[0].campo, catalogs)
        elif validated:
            await self._notify_confirmation(telefono, validated)

    async def _notify_missing_field(self, telefono: str, campo: str, catalogs: Catalogs) -> None:
        """Avisa qué dato falta. Si es el contratista y el catálogo es chico, se manda
        como una lista para tocar en vez de tener que escribirlo o decirlo - para
        cualquier otro campo (o un catálogo demasiado grande para una lista de
        WhatsApp, que tiene tope de 10 opciones) se sigue pidiendo como texto/audio."""
        if campo == "contratista":
            opciones = sorted(catalogs.contratistas)
            cabe_en_lista = 0 < len(opciones) <= LIST_MAX_ROWS and all(
                len(o) <= LIST_ROW_TITLE_MAX_LEN for o in opciones
            )
            if cabe_en_lista:
                rows = [(f"{CONTRATISTA_LIST_PREFIX}{o}", o) for o in opciones]
                try:
                    await self.whatsapp.send_list(telefono, missing_field_message(campo), "Elegir", rows)
                    return
                except Exception:
                    logger.exception("No se pudo enviar la lista de contratistas a %s", telefono)
                    # Sigue al mensaje de texto normal en vez de dejar a la persona sin respuesta.
        await self._notify(telefono, missing_field_message(campo))

    async def _schedule_audio_deletion(self, item: EstadoTecnico) -> None:
        """Ya se guardó el reporte: el audio no hace falta conservarlo. Best-effort:
        si falla, el reporte igual quedó guardado y el audio se borra en un intento
        posterior (no vale la pena que el capataz vea un error acá)."""
        if not item.ruta_audio or item.ruta_audio.startswith("json://"):
            return
        try:
            if self.task_queue:
                await self.task_queue.enqueue("/tasks/delete-audio", {"ruta_audio": item.ruta_audio})
            else:
                await self.storage.delete_audio(item.ruta_audio)
        except Exception:
            logger.exception("No se pudo borrar el audio de %s", item.message_id)

    async def _fail_or_review(self, message_id: str, error_state: EstadoProceso) -> None:
        item = self.state_repo.get(message_id)
        if not item:
            return
        if item.intentos >= MAX_ATTEMPTS:
            self.state_repo.update(message_id, estado=EstadoProceso.PENDIENTE_REVISION)
            await self._notify(item.telefono, retry_exhausted_message())
        else:
            self.state_repo.update(message_id, estado=error_state)


def _get_processor() -> ReportProcessor:
    settings = get_settings()
    return ReportProcessor(
        get_state_repository(),
        get_gemini_extractor(
            settings.google_genai_api_key, settings.gemini_model, settings.gemini_fallback_model
        ),
        get_whatsapp_client(settings.whatsapp_access_token, settings.whatsapp_phone_number_id),
        get_sheets_writer(),
        storage=get_audio_storage(settings.whatsapp_access_token, settings.gcs_bucket_name),
        task_queue=get_task_queue(),
    )


processor = _get_processor()
