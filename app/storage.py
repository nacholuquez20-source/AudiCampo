import asyncio
import logging
from datetime import date
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

import httpx
from google.cloud import storage as gcs_storage

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v18.0"
DEFAULT_MIME_TYPE = "audio/ogg"


class AudioStorage:
    async def save_whatsapp_audio(self, audio_id: str, message_id: str) -> str:
        raise NotImplementedError


class LocalAudioStorage(AudioStorage):
    async def save_whatsapp_audio(self, audio_id: str, message_id: str) -> str:
        if audio_id.startswith("json://"):
            return audio_id
        today = date.today()
        return f"gs://local-dev/audios/{today:%Y/%m/%d}/{message_id}.ogg"


class GcsAudioStorage(AudioStorage):
    """Descarga el audio real desde WhatsApp Cloud API y lo archiva en GCS."""

    def __init__(self, access_token: str, bucket_name: str) -> None:
        self.access_token = access_token
        self.bucket_name = bucket_name

    async def save_whatsapp_audio(self, audio_id: str, message_id: str) -> str:
        if audio_id.startswith("json://"):
            return audio_id

        audio_bytes, mime_type = await self._download_from_whatsapp(audio_id)
        blob_path = self._blob_path(message_id, mime_type)
        await asyncio.to_thread(self._upload_to_gcs, blob_path, audio_bytes, mime_type)
        return f"gs://{self.bucket_name}/{blob_path}"

    async def _download_from_whatsapp(self, audio_id: str) -> tuple[bytes, str]:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient() as client:
            meta_response = await client.get(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{audio_id}", headers=headers
            )
            meta_response.raise_for_status()
            media_url = meta_response.json()["url"]

            media_response = await client.get(media_url, headers=headers)
            media_response.raise_for_status()
            mime_type = media_response.headers.get("content-type", DEFAULT_MIME_TYPE).split(";")[0]
            return media_response.content, mime_type

    def _blob_path(self, message_id: str, mime_type: str) -> str:
        extension = mime_type.split("/")[-1] if "/" in mime_type else "ogg"
        today = date.today()
        return f"audios/{today:%Y/%m/%d}/{message_id}.{extension}"

    def _upload_to_gcs(self, blob_path: str, audio_bytes: bytes, mime_type: str) -> None:
        client = gcs_storage.Client()
        bucket = client.bucket(self.bucket_name)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(audio_bytes, content_type=mime_type)


def download_gcs_audio(gs_uri: str) -> tuple[bytes, str]:
    """Descarga bytes de audio desde una URI gs://bucket/path. Devuelve (bytes, mime_type)."""
    parsed = urlparse(gs_uri)
    bucket_name = parsed.netloc
    blob_path = parsed.path.lstrip("/")

    client = gcs_storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    audio_bytes = blob.download_as_bytes()
    return audio_bytes, blob.content_type or DEFAULT_MIME_TYPE


@lru_cache
def get_audio_storage(access_token: Optional[str] = None, bucket_name: Optional[str] = None) -> AudioStorage:
    """Factory de almacenamiento de audio.

    Devuelve LocalAudioStorage si falta el token o el bucket, sino GcsAudioStorage.
    """
    if access_token is None or bucket_name is None:
        return LocalAudioStorage()
    return GcsAudioStorage(access_token, bucket_name)
