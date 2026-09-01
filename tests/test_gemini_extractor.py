import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from google.genai import errors

from app.gemini_extractor import (
    RESPONSE_SCHEMA,
    ExtractionUnavailable,
    GeminiRealExtractor,
    LocalGeminiExtractor,
    get_gemini_extractor,
)
from app.models import BUSINESS_FIELDS, ReporteExtraido


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
        extractor = GeminiRealExtractor("test-api-key", "gemini-2.5-flash")
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
        extractor = GeminiRealExtractor("test-api-key", "gemini-2.5-flash")
        audio_uri = "json://{invalid json"
        result = await extractor.extract_from_audio(audio_uri)
        assert result.fecha is None

    @pytest.mark.asyncio
    async def test_real_extractor_calls_gemini_api(self):
        """GeminiRealExtractor should download the audio and call Gemini API for real URIs."""
        with patch("app.gemini_extractor.download_gcs_audio") as mock_download:
            mock_download.return_value = (b"fake-audio-bytes", "audio/ogg")
            with patch("google.genai.Client") as mock_client_class:
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

                mock_client = MagicMock()
                mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                extractor = GeminiRealExtractor("test-api-key", "gemini-2.5-flash")
                result = await extractor.extract_from_audio("gs://bucket/audio.ogg")

                # Verify the audio was downloaded and the API was called with its bytes
                mock_download.assert_called_once_with("gs://bucket/audio.ogg")
                mock_client_class.assert_called_once_with(api_key="test-api-key")
                call_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
                assert call_kwargs["model"] == "gemini-2.5-flash"
                audio_part = call_kwargs["contents"][1]
                assert audio_part.inline_data.data == b"fake-audio-bytes"
                assert audio_part.inline_data.mime_type == "audio/ogg"
                assert result.fecha == "2026-06-18"
                assert result.lote == "20"

    @pytest.mark.asyncio
    async def test_real_extractor_raises_when_gemini_is_unreachable(self):
        """A technical failure must be distinguishable from 'listened but understood nothing'."""
        with patch("app.gemini_extractor.download_gcs_audio") as mock_download:
            mock_download.return_value = (b"fake-audio-bytes", "audio/ogg")
            with patch("google.genai.Client") as mock_client_class:
                mock_client = MagicMock()
                mock_client.aio.models.generate_content = AsyncMock(side_effect=Exception("API error"))
                mock_client_class.return_value = mock_client

                extractor = GeminiRealExtractor("test-api-key", "gemini-3.7-flash")

                with pytest.raises(ExtractionUnavailable):
                    await extractor.extract_from_audio("gs://bucket/audio.ogg")

    @pytest.mark.asyncio
    async def test_real_extractor_retries_transient_errors_then_falls_back(self):
        """A 503 (model saturated) should retry, then try the fallback model."""
        transient = errors.ServerError(503, {"error": {"message": "high demand"}})
        ok_response = MagicMock()
        ok_response.text = json.dumps({"fecha": "2026-06-18"})

        with patch("app.gemini_extractor.download_gcs_audio") as mock_download:
            mock_download.return_value = (b"fake-audio-bytes", "audio/ogg")
            with patch("google.genai.Client") as mock_client_class:
                with patch("app.gemini_extractor.RETRY_BACKOFF_SECONDS", 0):
                    mock_client = MagicMock()
                    # El modelo principal falla sus 3 intentos; el de respaldo responde bien.
                    mock_client.aio.models.generate_content = AsyncMock(
                        side_effect=[transient, transient, transient, ok_response]
                    )
                    mock_client_class.return_value = mock_client

                    extractor = GeminiRealExtractor("test-api-key", "gemini-3.7-flash")
                    result = await extractor.extract_from_audio("gs://bucket/audio.ogg")

                    assert result.fecha == "2026-06-18"
                    assert mock_client.aio.models.generate_content.await_count == 4
                    modelos = [c.kwargs["model"] for c in mock_client.aio.models.generate_content.await_args_list]
                    assert modelos == ["gemini-3.7-flash"] * 3 + ["gemini-3.6-flash"]

    @pytest.mark.asyncio
    async def test_real_extractor_accepts_spanish_label_keys(self):
        """Regresión: Gemini contestaba con las etiquetas del prompt ("Código Tarea",
        "Sección") en vez de las claves del modelo, y se descartaba el reporte entero."""
        with patch("app.gemini_extractor.download_gcs_audio") as mock_download:
            mock_download.return_value = (b"fake-audio-bytes", "audio/ogg")
            with patch("google.genai.Client") as mock_client_class:
                mock_response = MagicMock()
                mock_response.text = json.dumps({
                    "Fecha": None,
                    "Lote": "15",
                    "Sección": "20",
                    "Código Tarea": None,
                    "Descripción Tarea": "fumigó",
                    "Cantidad": "10 hectáreas",
                    "Variedad": None,
                    "Fuente Nitrogenada": "urea",
                    "Contratista": "López",
                    "Nombre del capataz": "Gino",
                })
                mock_client = MagicMock()
                mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                extractor = GeminiRealExtractor("test-api-key", "gemini-3.5-flash")
                result = await extractor.extract_from_audio("gs://bucket/audio.ogg")

                assert result.lote == "15"
                assert result.seccion == "20"
                assert result.descripcion_tarea == "fumigó"
                assert result.cantidad == "10 hectáreas"
                assert result.fuente_nitrogenada == "urea"
                assert result.contratista == "López"
                assert result.nombre_capataz == "Gino"

    def test_response_schema_has_no_additional_properties(self):
        """Regresión: la API de Gemini rechaza la request entera si el esquema trae
        'additionalProperties' (que es lo que genera pydantic con extra='forbid')."""
        assert "additionalProperties" not in json.dumps(RESPONSE_SCHEMA)
        assert "additional_properties" not in json.dumps(RESPONSE_SCHEMA)
        assert set(RESPONSE_SCHEMA["properties"]) == set(BUSINESS_FIELDS)

    @pytest.mark.asyncio
    async def test_real_extractor_retries_without_schema_if_api_rejects_it(self):
        """Si la API rechaza el esquema, hay que reintentar sin él y no perder el reporte."""
        schema_error = errors.ClientError(
            400,
            {"error": {"message": "Unknown name \"additional_properties\" at 'generation_config.response_schema'"}},
        )
        ok_response = MagicMock()
        ok_response.text = json.dumps({"lote": "15"})

        with patch("app.gemini_extractor.download_gcs_audio") as mock_download:
            mock_download.return_value = (b"fake-audio-bytes", "audio/ogg")
            with patch("google.genai.Client") as mock_client_class:
                mock_client = MagicMock()
                # Ambos modelos rechazan el esquema; sin esquema el principal responde.
                mock_client.aio.models.generate_content = AsyncMock(
                    side_effect=[schema_error, schema_error, ok_response]
                )
                mock_client_class.return_value = mock_client

                extractor = GeminiRealExtractor("test-api-key", "gemini-3.5-flash")
                result = await extractor.extract_from_audio("gs://bucket/audio.ogg")

                assert result.lote == "15"
                ultimo_config = mock_client.aio.models.generate_content.await_args_list[-1].kwargs["config"]
                assert ultimo_config.response_schema is None

    @pytest.mark.asyncio
    async def test_real_extractor_strips_markdown_json_fence(self):
        """GeminiRealExtractor should tolerate a ```json fenced response."""
        with patch("app.gemini_extractor.download_gcs_audio") as mock_download:
            mock_download.return_value = (b"fake-audio-bytes", "audio/ogg")
            with patch("google.genai.Client") as mock_client_class:
                mock_response = MagicMock()
                mock_response.text = "```json\n" + json.dumps({"fecha": "2026-06-18"}) + "\n```"
                mock_client = MagicMock()
                mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                extractor = GeminiRealExtractor("test-api-key", "gemini-2.5-flash")
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
