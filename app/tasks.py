import logging

from app.catalogs import load_catalogs
from app.config import get_settings
from app.firestore_state import StateRepository, get_state_repository
from app.gemini_extractor import GeminiExtractor, get_gemini_extractor
from app.message_templates import (
    confirmation_summary,
    correction_format_hint,
    correction_understanding_failed_message,
    missing_field_message,
    pending_reminder_message,
    retry_exhausted_message,
    saved_message,
    welcome_message,
)
from app.models import BUSINESS_FIELDS, EstadoProceso, EstadoTecnico, ReporteExtraido
from app.sheets_writer import SheetsWriter, get_sheets_writer
from app.validators import validate_report
from app.whatsapp import WhatsAppClient, get_whatsapp_client

logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 3

CONFIRM_WORDS = {"confirmar", "confirmo", "si", "sí", "dale", "listo", "ok", "okay", "correcto"}


class ReportProcessor:
    def __init__(
        self,
        state_repo: StateRepository,
        extractor: GeminiExtractor,
        whats_app: WhatsAppClient,
        sheets: SheetsWriter,
    ) -> None:
        self.state_repo = state_repo
        self.extractor = extractor
        self.whatsapp = whats_app
        self.sheets = sheets

    async def _notify(self, telefono: str, text: str) -> None:
        """Best-effort WhatsApp send: a delivery failure should never break processing."""
        try:
            await self.whatsapp.send_text(telefono, text)
        except Exception:
            logger.exception("No se pudo enviar el mensaje de WhatsApp a %s", telefono)

    async def process_audio(self, message_id: str) -> None:
        item = self.state_repo.get(message_id)
        if not item or not item.ruta_audio:
            return

        existing_pending = self.state_repo.find_pending_by_phone(item.telefono)
        if existing_pending and existing_pending.message_id != message_id:
            await self._apply_voice_correction(existing_pending, item)
            return

        self.state_repo.update(message_id, estado=EstadoProceso.PROCESANDO, increment_attempts=True)
        try:
            extracted = await self.extractor.extract_from_audio(item.ruta_audio)
        except Exception:
            await self._fail_or_review(message_id, EstadoProceso.ERROR_IA)
            return

        catalogs = load_catalogs()
        validated, errors = validate_report(extracted, catalogs, telefono=item.telefono)
        if errors:
            self.state_repo.update(
                message_id,
                estado=EstadoProceso.PENDIENTE_DATOS,
                reporte_extraido=extracted,
                errores_validacion=errors,
            )
            await self._notify(item.telefono, missing_field_message(errors[0].campo))
            return

        self.state_repo.update(
            message_id,
            estado=EstadoProceso.PENDIENTE_CONFIRMACION,
            reporte_extraido=extracted,
            errores_validacion=[],
        )
        await self._notify(item.telefono, confirmation_summary(validated))

    async def _apply_voice_correction(self, pending: EstadoTecnico, new_item: EstadoTecnico) -> None:
        """Treat a new audio arriving while a report is pending as a spoken correction to it."""
        try:
            correction = await self.extractor.extract_from_audio(new_item.ruta_audio)
        except Exception:
            await self._notify(pending.telefono, correction_understanding_failed_message())
            return

        correction_data = correction.model_dump()
        if not any(correction_data.get(field) for field in BUSINESS_FIELDS):
            await self._notify(pending.telefono, correction_understanding_failed_message())
            return

        merged_data = pending.reporte_extraido.model_dump()
        for field in BUSINESS_FIELDS:
            if correction_data.get(field):
                merged_data[field] = correction_data[field]
        merged = ReporteExtraido(**merged_data)

        catalogs = load_catalogs()
        validated, errors = validate_report(merged, catalogs, telefono=pending.telefono)
        next_state = EstadoProceso.PENDIENTE_DATOS if errors else EstadoProceso.PENDIENTE_CONFIRMACION
        self.state_repo.update(
            pending.message_id,
            estado=next_state,
            reporte_extraido=merged,
            errores_validacion=errors,
        )
        if errors:
            await self._notify(pending.telefono, missing_field_message(errors[0].campo))
        else:
            await self._notify(pending.telefono, confirmation_summary(validated))

    async def handle_text(self, telefono: str, text: str) -> None:
        pending = self.state_repo.find_pending_by_phone(telefono)
        normalized = text.strip()

        if not pending or not pending.reporte_extraido:
            await self._notify(telefono, welcome_message())
            return

        if normalized.casefold() in CONFIRM_WORDS:
            catalogs = load_catalogs()
            validated, errors = validate_report(pending.reporte_extraido, catalogs, telefono=telefono)
            if errors:
                self.state_repo.update(
                    pending.message_id,
                    estado=EstadoProceso.PENDIENTE_DATOS,
                    errores_validacion=errors,
                )
                await self._notify(telefono, missing_field_message(errors[0].campo))
                return

            self.state_repo.update(pending.message_id, estado=EstadoProceso.CONFIRMADO)
            try:
                await self.sheets.append_report(validated)
            except Exception:
                await self._fail_or_review(pending.message_id, EstadoProceso.ERROR_ESCRITURA)
                return
            self.state_repo.update(pending.message_id, estado=EstadoProceso.GUARDADO)
            await self._notify(telefono, saved_message())
            return

        if normalized.casefold().startswith("corregir "):
            field_value = normalized[len("corregir ") :]
            if ":" not in field_value:
                await self._notify(telefono, correction_format_hint())
                return
            field, value = [part.strip() for part in field_value.split(":", 1)]
            await self._apply_correction(pending.message_id, telefono, field, value)
            return

        await self._notify(telefono, pending_reminder_message())

    async def _apply_correction(self, message_id: str, telefono: str, field: str, value: str) -> None:
        item = self.state_repo.get(message_id)
        if not item or not item.reporte_extraido:
            return

        field_map = {
            "fecha": "fecha",
            "lote": "lote",
            "seccion": "seccion",
            "sección": "seccion",
            "codigo tarea": "codigo_tarea",
            "código tarea": "codigo_tarea",
            "descripcion tarea": "descripcion_tarea",
            "descripción tarea": "descripcion_tarea",
            "cantidad": "cantidad",
            "variedad": "variedad",
            "fuente nitrogenada": "fuente_nitrogenada",
            "contratista": "contratista",
            "nombre del capataz": "nombre_capataz",
        }
        model_field = field_map.get(field.casefold())
        if not model_field:
            return

        updated = item.reporte_extraido.model_copy(update={model_field: value})
        catalogs = load_catalogs()
        validated, errors = validate_report(updated, catalogs, telefono=telefono)
        next_state = EstadoProceso.PENDIENTE_DATOS if errors else EstadoProceso.PENDIENTE_CONFIRMACION
        self.state_repo.update(
            message_id,
            estado=next_state,
            reporte_extraido=updated,
            errores_validacion=errors,
        )
        if errors:
            await self._notify(telefono, missing_field_message(errors[0].campo))
        elif validated:
            await self._notify(telefono, confirmation_summary(validated))

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
        get_gemini_extractor(settings.google_genai_api_key),
        get_whatsapp_client(settings.whatsapp_access_token, settings.whatsapp_phone_number_id),
        get_sheets_writer(),
    )


processor = _get_processor()
