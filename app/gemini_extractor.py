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
- Si un dato no está presente o no se comprende con seguridad, devolvé null. Es mejor
  devolver null que adivinar.
- Normalizá la fecha al formato AAAA-MM-DD.
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
    def __init__(self, api_key: str, model: str) -> None:
        from google import genai

        self.model = model
        self.client = genai.Client(api_key=api_key)

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

            from google.genai import types

            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=[
                    EXTRACTOR_PROMPT,
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            data = json.loads(_strip_json_fence(response.text))
            return ReporteExtraido.model_validate(data)
        except Exception:
            logger.exception("Fallo la extracción de Gemini para %s", audio_uri)
            return ReporteExtraido()


@lru_cache
def get_gemini_extractor(api_key: Optional[str] = None, model: str = "gemini-2.5-flash") -> GeminiExtractor:
    """Factory for Gemini extractor.

    Returns LocalGeminiExtractor if api_key is None, otherwise GeminiRealExtractor.
    Uses lru_cache to ensure same instance is returned on multiple calls.
    """
    if api_key is None:
        return LocalGeminiExtractor()
    return GeminiRealExtractor(api_key, model)
