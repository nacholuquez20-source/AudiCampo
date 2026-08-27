import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gemini_extractor import GeminiRealExtractor, LocalGeminiExtractor, get_gemini_extractor
from app.models import ReporteExtraido


class TestLocalGeminiExtractor:
    @pytest.mark.asyncio
    async def test_local_extractor_with_json_payload(self):
        extractor = LocalGeminiExtractor()
        payload = {
            "fecha": "2026-06-18",
            "lote": "20",
            "seccion": "3",
            "codigo_tarea": "145",
            "descripcion_tarea": "Fertilización",
            "cantidad": "25 has",
            "variedad": "ACA 603",
            "fuente_nitrogenada": "Urea",
            "contratista": "Trabajo propio",
            "nombre_capataz": "Juan Pérez",
        }
        audio_uri = f"json://{json.dumps(payload)}"
        result = await extractor.extract_from_audio(audio_uri)
        assert result.fecha == "2026-06-18"
        assert result.lote == "20"

    @pytest.mark.asyncio
    async def test_local_extractor_with_invalid_uri(self):
        extractor = LocalGeminiExtractor()
        result = await extractor.extract_from_audio("gs://bucket/audio.ogg")
        assert result.fecha is None
        assert result.lote is None


class TestGeminiRealExtractor:
    @pytest.mark.asyncio
    async def test_real_extractor_with_json_payload(self):
        """GeminiRealExtractor should handle json:// URIs for testing."""
        extractor = GeminiRealExtractor("test-api-key")
        payload = {
            "fecha": "2026-06-18",
            "lote": "20",
            "seccion": "3",
            "codigo_tarea": "145",
            "descripcion_tarea": "Fertilización",
            "cantidad": "25 has",
            "variedad": "ACA 603",
            "fuente_nitrogenada": "Urea",
            "contratista": "Trabajo propio",
            "nombre_capataz": "Juan Pérez",
        }
        audio_uri = f"json://{json.dumps(payload)}"
        result = await extractor.extract_from_audio(audio_uri)
        assert result.fecha == "2026-06-18"
        assert result.lote == "20"

    @pytest.mark.asyncio
    async def test_real_extractor_with_invalid_json(self):
        """GeminiRealExtractor should return empty on invalid JSON."""
        extractor = GeminiRealExtractor("test-api-key")
        audio_uri = "json://{invalid json"
        result = await extractor.extract_from_audio(audio_uri)
        assert result.fecha is None

    @pytest.mark.asyncio
    async def test_real_extractor_calls_gemini_api(self):
        """GeminiRealExtractor should download the audio and call Gemini API for real URIs."""
        with patch("app.gemini_extractor.download_gcs_audio") as mock_download:
            mock_download.return_value = (b"fake-audio-bytes", "audio/ogg")
            with patch("google.generativeai.GenerativeModel") as mock_model_class:
                with patch("google.generativeai.configure") as mock_configure:
                    # Mock the async response
                    mock_response = MagicMock()
                    mock_response.text = json.dumps({
                        "fecha": "2026-06-18",
                        "lote": "20",
                        "seccion": "3",
                        "codigo_tarea": "145",
                        "descripcion_tarea": "Fertilización",
                        "cantidad": "25 has",
                        "variedad": "ACA 603",
                        "fuente_nitrogenada": "Urea",
                        "contratista": "Trabajo propio",
                        "nombre_capataz": "Juan Pérez",
                    })

                    # Mock the model instance
                    mock_model = AsyncMock()
                    mock_model.generate_content_async = AsyncMock(return_value=mock_response)
                    mock_model_class.return_value = mock_model

                    extractor = GeminiRealExtractor("test-api-key")
                    result = await extractor.extract_from_audio("gs://bucket/audio.ogg")

                    # Verify the audio was downloaded and the API was called with its bytes
                    mock_download.assert_called_once_with("gs://bucket/audio.ogg")
                    mock_model_class.assert_called_once_with("gemini-1.5-flash")
                    call_args = mock_model.generate_content_async.call_args
                    contents = call_args.args[0]
                    assert contents[1] == {"mime_type": "audio/ogg", "data": b"fake-audio-bytes"}
                    assert result.fecha == "2026-06-18"
                    assert result.lote == "20"

    @pytest.mark.asyncio
    async def test_real_extractor_fallback_on_error(self):
        """GeminiRealExtractor should return empty on API error."""
        with patch("app.gemini_extractor.download_gcs_audio") as mock_download:
            mock_download.return_value = (b"fake-audio-bytes", "audio/ogg")
            with patch("google.generativeai.GenerativeModel") as mock_model_class:
                with patch("google.generativeai.configure") as mock_configure:
                    mock_model = AsyncMock()
                    mock_model.generate_content_async = AsyncMock(side_effect=Exception("API error"))
                    mock_model_class.return_value = mock_model

                    extractor = GeminiRealExtractor("test-api-key")
                    result = await extractor.extract_from_audio("gs://bucket/audio.ogg")

                    assert result.fecha is None

    @pytest.mark.asyncio
    async def test_real_extractor_strips_markdown_json_fence(self):
        """GeminiRealExtractor should tolerate a ```json fenced response."""
        with patch("app.gemini_extractor.download_gcs_audio") as mock_download:
            mock_download.return_value = (b"fake-audio-bytes", "audio/ogg")
            with patch("google.generativeai.GenerativeModel") as mock_model_class:
                with patch("google.generativeai.configure"):
                    mock_response = MagicMock()
                    mock_response.text = "```json\n" + json.dumps({"fecha": "2026-06-18"}) + "\n```"
                    mock_model = AsyncMock()
                    mock_model.generate_content_async = AsyncMock(return_value=mock_response)
                    mock_model_class.return_value = mock_model

                    extractor = GeminiRealExtractor("test-api-key")
                    result = await extractor.extract_from_audio("gs://bucket/audio.ogg")

                    assert result.fecha == "2026-06-18"


class TestGetGeminiExtractor:
    def test_factory_returns_local_for_none_api_key(self):
        extractor = get_gemini_extractor(None)
        assert isinstance(extractor, LocalGeminiExtractor)

    def test_factory_returns_real_for_api_key(self):
        extractor = get_gemini_extractor("test-api-key")
        assert isinstance(extractor, GeminiRealExtractor)

    def test_factory_caches_instances(self):
        extractor1 = get_gemini_extractor("test-api-key")
        extractor2 = get_gemini_extractor("test-api-key")
        assert extractor1 is extractor2

    def test_factory_different_keys_return_different_instances(self):
        extractor1 = get_gemini_extractor("key1")
        extractor2 = get_gemini_extractor("key2")
        assert extractor1 is not extractor2
