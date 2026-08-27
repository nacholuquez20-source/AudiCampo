from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.storage import GcsAudioStorage, LocalAudioStorage, download_gcs_audio, get_audio_storage


class TestLocalAudioStorage:
    @pytest.mark.asyncio
    async def test_passes_through_json_payload(self):
        storage = LocalAudioStorage()
        result = await storage.save_whatsapp_audio("json://{}", "wamid.1")
        assert result == "json://{}"

    @pytest.mark.asyncio
    async def test_fakes_gs_path_for_real_audio_id(self):
        storage = LocalAudioStorage()
        result = await storage.save_whatsapp_audio("wamid.audio-id", "wamid.1")
        assert result.startswith("gs://local-dev/audios/")


class TestGcsAudioStorage:
    @pytest.mark.asyncio
    async def test_passes_through_json_payload_without_network_calls(self):
        storage = GcsAudioStorage(access_token="token", bucket_name="bucket")
        result = await storage.save_whatsapp_audio("json://{}", "wamid.1")
        assert result == "json://{}"

    @pytest.mark.asyncio
    async def test_downloads_from_whatsapp_and_uploads_to_gcs(self):
        storage = GcsAudioStorage(access_token="token", bucket_name="my-bucket")

        def handler(request: httpx.Request) -> httpx.Response:
            if "graph.facebook.com" in str(request.url):
                assert request.headers["authorization"] == "Bearer token"
                return httpx.Response(200, json={"url": "https://media.example.com/audio"})
            assert request.headers["authorization"] == "Bearer token"
            return httpx.Response(200, content=b"raw-audio-bytes", headers={"content-type": "audio/ogg"})

        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient(transport=transport)

        with patch("httpx.AsyncClient", lambda: real_client):
            with patch("app.storage.gcs_storage.Client") as mock_client_class:
                mock_blob = MagicMock()
                mock_bucket = MagicMock()
                mock_bucket.blob.return_value = mock_blob
                mock_client_class.return_value.bucket.return_value = mock_bucket

                result = await storage.save_whatsapp_audio("wamid.audio-id", "wamid.1")

        assert result.startswith("gs://my-bucket/audios/")
        assert result.endswith(".ogg")
        mock_blob.upload_from_string.assert_called_once_with(b"raw-audio-bytes", content_type="audio/ogg")


class TestDownloadGcsAudio:
    def test_downloads_bytes_from_gs_uri(self):
        with patch("app.storage.gcs_storage.Client") as mock_client_class:
            mock_blob = MagicMock()
            mock_blob.download_as_bytes.return_value = b"audio-bytes"
            mock_blob.content_type = "audio/ogg"
            mock_bucket = MagicMock()
            mock_bucket.blob.return_value = mock_blob
            mock_client_class.return_value.bucket.return_value = mock_bucket

            audio_bytes, mime_type = download_gcs_audio("gs://my-bucket/audios/2026/06/18/wamid.1.ogg")

            mock_client_class.return_value.bucket.assert_called_once_with("my-bucket")
            mock_bucket.blob.assert_called_once_with("audios/2026/06/18/wamid.1.ogg")
            assert audio_bytes == b"audio-bytes"
            assert mime_type == "audio/ogg"


class TestGetAudioStorage:
    def test_factory_returns_local_when_missing_config(self):
        assert isinstance(get_audio_storage(None, None), LocalAudioStorage)
        assert isinstance(get_audio_storage("token", None), LocalAudioStorage)
        assert isinstance(get_audio_storage(None, "bucket"), LocalAudioStorage)

    def test_factory_returns_gcs_when_configured(self):
        storage = get_audio_storage("token", "bucket")
        assert isinstance(storage, GcsAudioStorage)

    def test_factory_caches_instances(self):
        first = get_audio_storage("token", "bucket")
        second = get_audio_storage("token", "bucket")
        assert first is second
