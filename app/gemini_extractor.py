import asyncio
import json
import logging
import re
from functools import lru_cache
from typing import Optional

from app.models import ReporteExtraido
from app.storage import download_gcs_audio

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


EXTRACTOR_PROMPT = """Sos un sistema de extracción de reportes de campo.

Analizá el audio y extraé exclusivamente los siguientes campos:
1. Fecha
2. Lote
3. Sección
4. Código Tarea
5. Descripción Tarea
6. Cantidad
7. Variedad
8. Fuente Nitrogenada
9. Contratista
10. Nombre del capataz

Reglas:
- No inventes ningún dato.
- No agregues otros campos.
- Si un dato no está presente o no se comprende con seguridad, devolvé null.
- Normalizá la fecha al formato AAAA-MM-DD.
- Cantidad debe contener un valor numérico y una unidad.
- Las únicas unidades válidas son: horas, hectáreas, surcos o viajes.
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
    def __init__(self, api_key: str) -> None:
        import google.generativeai as genai
        self.api_key = api_key
        genai.configure(api_key=api_key)

    async def extract_from_audio(self, audio_uri: str) -> ReporteExtraido:
        """Call Gemini API to extract report from audio URI.

        For testing, if audio_uri starts with json://, parse it locally.
        Otherwise, download the audio from GCS and send it to Gemini as audio content.
        """
        if audio_uri.startswith("json://"):
            try:
                return ReporteExtraido.model_validate(json.loads(audio_uri.removeprefix("json://")))
            except Exception:
                return ReporteExtraido()

        try:
            audio_bytes, mime_type = await asyncio.to_thread(download_gcs_audio, audio_uri)

            import google.generativeai as genai
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = await model.generate_content_async(
                [
                    EXTRACTOR_PROMPT,
                    {"mime_type": mime_type, "data": audio_bytes},
                ],
                generation_config=genai.GenerationConfig(response_mime_type="application/json"),
            )
            data = json.loads(_strip_json_fence(response.text))
            return ReporteExtraido.model_validate(data)
        except Exception:
            logger.exception("Fallo la extracción de Gemini para %s", audio_uri)
            return ReporteExtraido()


@lru_cache
def get_gemini_extractor(api_key: Optional[str] = None) -> GeminiExtractor:
    """Factory for Gemini extractor.

    Returns LocalGeminiExtractor if api_key is None, otherwise GeminiRealExtractor.
    Uses lru_cache to ensure same instance is returned on multiple calls.
    """
    if api_key is None:
        return LocalGeminiExtractor()
    return GeminiRealExtractor(api_key)
