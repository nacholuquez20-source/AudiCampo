import hashlib
import hmac
import logging
from functools import lru_cache
from typing import Any, Optional

import httpx

from app.models import WhatsAppMessage

logger = logging.getLogger(__name__)


class WhatsAppClient:
    async def send_text(self, telefono: str, text: str) -> None:
        raise NotImplementedError


class LocalWhatsAppClient(WhatsAppClient):
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, str]] = []

    async def send_text(self, telefono: str, text: str) -> None:
        self.sent_messages.append((telefono, text))


def _to_whatsapp_send_format(telefono: str) -> str:
    """Argentine mobile numbers carry a '9' after the country code (54) in the
    'from' field of received messages, but the Cloud API rejects that '9' when
    used as the 'to' recipient - it must be sent without it."""
    if telefono.startswith("549"):
        return "54" + telefono[3:]
    return telefono


class WhatsAppRealClient(WhatsAppClient):
    def __init__(self, access_token: str, phone_number_id: str) -> None:
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"

    async def send_text(self, telefono: str, text: str) -> None:
        """Send text via WhatsApp Cloud API."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": _to_whatsapp_send_format(telefono),
            "type": "text",
            "text": {"body": text},
        }
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to send WhatsApp message to {telefono}: {e} | response body: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to send WhatsApp message to {telefono}: {e}")
            raise


def verify_signature(raw_body: bytes, signature_header: Optional[str], app_secret: str) -> bool:
    if signature_header is None:
        return False
    signature = signature_header.removeprefix("sha256=")
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def parse_webhook_messages(payload: dict[str, Any]) -> list[WhatsAppMessage]:
    messages: list[WhatsAppMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for raw in value.get("messages", []):
                telefono = raw.get("from")
                message_id = raw.get("id")
                if not telefono or not message_id:
                    continue
                audio = raw.get("audio") or {}
                text = raw.get("text") or {}
                messages.append(
                    WhatsAppMessage(
                        message_id=message_id,
                        telefono=telefono,
                        audio_id=audio.get("id"),
                        text=text.get("body"),
                    )
                )
    return messages


@lru_cache
def get_whatsapp_client(
    access_token: Optional[str] = None, phone_number_id: Optional[str] = None
) -> WhatsAppClient:
    """Factory for WhatsApp client.

    Returns LocalWhatsAppClient if access_token or phone_number_id is None,
    otherwise WhatsAppRealClient. Uses lru_cache to ensure same instance on multiple calls.
    """
    if access_token is None or phone_number_id is None:
        return LocalWhatsAppClient()
    return WhatsAppRealClient(access_token, phone_number_id)


whatsapp_client = LocalWhatsAppClient()
