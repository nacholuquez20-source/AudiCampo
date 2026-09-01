import asyncio
import json
import logging
import re
import unicodedata
from functools import lru_cache
from typing import Optional

from app.models import BUSINESS_FIELDS, ReporteExtraido
from app.storage import download_gcs_audio
from app.validators import today_in_argentina

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Códigos que indican una falla pasajera de Gemini (saturación, cuota, caída momentánea).
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_GEMINI_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2
FALLBACK_MODEL = "gemini-3.6-flash"

# Esquema explícito, no derivado del modelo pydantic: ReporteExtraido usa
# extra="forbid", que pydantic traduce a "additionalProperties", y la API de Gemini
# (subconjunto de OpenAPI 3.0) rechaza la request entera si ese campo aparece.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {field: {"type": "string", "nullable": True} for field in BUSINESS_FIELDS},
}


class ExtractionUnavailable(Exception):
    """No se pudo llegar a la IA (saturación, cuota, red).

    Es distinto de que la IA haya escuchado el audio y no haya encontrado datos:
    esto no se soluciona grabando de nuevo, así que hay que avisarlo como problema
    técnico y no como "no te entendí".
    """


def _is_transient(exc: Exception) -> bool:
    return getattr(exc, "code", None) in TRANSIENT_STATUS_CODES


def _is_schema_rejection(exc: Optional[Exception]) -> bool:
    """A 400 naming response_schema means the schema shape isn't accepted by the API."""
    if exc is None or getattr(exc, "code", None) != 400:
        return False
    return "response_schema" in str(exc)


def _canonical_key(key: str) -> str:
    """Map a JSON key back to the model's field name.

    Gemini tends to answer using the field labels as written in the prompt
    ("Código Tarea", "Sección"), not the snake_case names the model expects.
    """
    normalized = unicodedata.normalize("NFKD", key)
    without_accents = "".join(c for c in normalized if not unicodedata.combining(c))
    return without_accents.strip().lower().replace(" ", "_").replace("-", "_")


_FIELD_ALIASES = {
    "nombre_del_capataz": "nombre_capataz",
}


def _normalize_keys(data: dict) -> dict:
    result = {}
    for key, value in data.items():
        canonical = _canonical_key(key)
        result[_FIELD_ALIASES.get(canonical, canonical)] = value
    return result


EXTRACTOR_PROMPT = """Sos un sistema de extracción de reportes de campo. El audio es de un
capataz o peón de campo del norte argentino, hablando de forma espontánea y coloquial,
no leyendo un formulario.

Tené en cuenta al escuchar:
- Va a haber muletillas y relleno ("che", "viste", "y bueno", "digamos", "este...",
  "o sea"). Ignoralos, no son parte del dato.
- Puede arrancar una frase, cortarse y corregirse a mitad de camino ("en el lote... no,
  esperá, en el lote 20"). Quedate siempre con la versión corregida/final que dice la
  persona, no con el primer intento.
- Los números pueden decirse de forma natural ("veinticinco", "un cuarto de hora", "media
  hectárea", "unas diez hectáreas más o menos") y no como cifras prolijas. Convertilos al
  formato numérico igual.
- El orden en que menciona los datos puede no seguir la lista de abajo, y puede repetir
  o aclarar un dato más adelante en el mismo audio.

Analizá el audio y extraé exclusivamente los siguientes campos. Usá EXACTAMENTE estas
claves en el JSON (en minúscula, sin acentos ni espacios), no las etiquetas descriptivas:
- "fecha" (Fecha)
- "lote" (Lote)
- "seccion" (Sección)
- "codigo_tarea" (Código de tarea)
- "descripcion_tarea" (Descripción de la tarea)
- "cantidad" (Cantidad)
- "variedad" (Variedad)
- "fuente_nitrogenada" (Fuente nitrogenada)
- "contratista" (Contratista)
- "nombre_capataz" (Nombre del capataz)

Reglas:
- No inventes ningún dato.
- No agregues otros campos.
- Si un dato no está presente o no se comprende con seguridad, devolvé null. Es mejor
  devolver null que adivinar.
- Normalizá la fecha al formato AAAA-MM-DD. Si la persona dice "hoy", "ayer",
  "anteayer" u otra referencia relativa, calculala usando la fecha de referencia que se
  te da más abajo. Si no menciona ninguna fecha, devolvé null (no asumas "hoy") - el
  sistema completa la fecha de hoy automáticamente cuando esto pasa.
- Cantidad debe contener un valor numérico y una unidad.
- Las únicas unidades válidas son: horas, hectáreas, surcos o viajes. Si la persona usa
  otra unidad (por ejemplo "cuadras" u otra medida local) y no estás seguro de a cuál de
  estas cuatro corresponde, devolvé null en vez de convertirla vos - una conversión
  incorrecta puede arruinar el dato.
- Normalizá "cantidad de viajes" como "viajes".
- Conservá los códigos de tarea como texto.
- No deduzcas el código a partir de la descripción.
- No deduzcas la descripción a partir del código.
- No completes variedad, fuente nitrogenada ni contratista usando conocimiento general.
- Respondé únicamente con el JSON solicitado.
"""


def _strip_json_fence(text: str) -> str:
    return _JSON_FENCE_RE.sub("", text.strip()).strip()


class GeminiExtractor:
    async def extract_from_audio(self, audio_uri: str) -> ReporteExtraido:
        raise NotImplementedError


class LocalGeminiExtractor(GeminiExtractor):
    async def extract_from_audio(self, audio_uri: str) -> ReporteExtraido:
        """Development shim.

        If audio_uri starts with json://, parse that payload. Otherwise return an
        empty extraction so the deterministic missing-field flow can be tested.
        """
        if audio_uri.startswith("json://"):
            return ReporteExtraido.model_validate(json.loads(audio_uri.removeprefix("json://")))
        return ReporteExtraido()


class GeminiRealExtractor(GeminiExtractor):
    def __init__(self, api_key: str, model: str, fallback_model: str = FALLBACK_MODEL) -> None:
        from google import genai

        self.model = model
        self.fallback_model = fallback_model
        self.client = genai.Client(api_key=api_key)

    async def extract_from_audio(self, audio_uri: str) -> ReporteExtraido:
        """Call Gemini API to extract report from audio URI.

        For testing, if audio_uri starts with json://, parse it locally.
        Otherwise, download the audio from GCS and send it to Gemini as audio content.

        Raises ExtractionUnavailable if Gemini could not be reached at all, so the
        caller can tell the difference between a technical failure and an audio the
        model listened to but found nothing usable in.
        """
        if audio_uri.startswith("json://"):
            try:
                return ReporteExtraido.model_validate(json.loads(audio_uri.removeprefix("json://")))
            except Exception:
                return ReporteExtraido()

        audio_bytes, mime_type = await asyncio.to_thread(download_gcs_audio, audio_uri)
        response_text = await self._generate(audio_bytes, mime_type)

        try:
            data = json.loads(_strip_json_fence(response_text))
            return ReporteExtraido.model_validate(_normalize_keys(data))
        except Exception:
            # La IA contestó pero no en el formato esperado: eso sí es "no te entendí".
            logger.exception("Respuesta de Gemini no interpretable para %s", audio_uri)
            return ReporteExtraido()

    async def _generate(self, audio_bytes: bytes, mime_type: str) -> str:
        """Try the primary model with retries, then the fallback model.

        The response schema pins the JSON keys, but it is an optimization, not a
        requirement: if the API rejects the schema itself we retry without it rather
        than losing the report, since the prompt and key normalization already cover
        the naming.
        """
        from google.genai import types

        prompt = f"{EXTRACTOR_PROMPT}\n\nFecha de referencia (hoy): {today_in_argentina()}"
        contents = [prompt, types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)]
        configs = (
            types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=RESPONSE_SCHEMA
            ),
            types.GenerateContentConfig(response_mime_type="application/json"),
        )

        last_error: Optional[Exception] = None
        for config in configs:
            for model in (self.model, self.fallback_model):
                for attempt in range(MAX_GEMINI_ATTEMPTS):
                    try:
                        response = await self.client.aio.models.generate_content(
                            model=model, contents=contents, config=config
                        )
                        return response.text
                    except Exception as exc:
                        last_error = exc
                        if not _is_transient(exc):
                            break
                        if attempt + 1 < MAX_GEMINI_ATTEMPTS:
                            await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                logger.warning("Gemini no respondió con el modelo %s: %s", model, last_error)
            if not _is_schema_rejection(last_error):
                break
            logger.warning("Gemini rechazó el esquema de respuesta, reintentando sin él")

        raise ExtractionUnavailable("No se pudo contactar a Gemini") from last_error


@lru_cache
def get_gemini_extractor(
    api_key: Optional[str] = None,
    model: str = "gemini-3.5-flash",
    fallback_model: str = FALLBACK_MODEL,
) -> GeminiExtractor:
    """Factory for Gemini extractor.

    Returns LocalGeminiExtractor if api_key is None, otherwise GeminiRealExtractor.
    Uses lru_cache to ensure same instance is returned on multiple calls.
    """
    if api_key is None:
        return LocalGeminiExtractor()
    return GeminiRealExtractor(api_key, model, fallback_model)
