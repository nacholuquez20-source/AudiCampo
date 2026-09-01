import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Return a test client."""
    return TestClient(app)


def _create_signature(app_secret: str, body: bytes) -> str:
    """Helper to create a valid signature."""
    digest = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_local_no_secret_accepts_webhook_without_signature(client, monkeypatch):
    """Test that local env without secret configured accepts webhooks without signature."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "")
    # Clear the cache
    from app.config import get_settings

    get_settings.cache_clear()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.test1",
                                    "from": "5491111111111",
                                    "text": {"body": "test"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post("/webhook/whatsapp", content=body, headers={"Content-Type": "application/json"})

    assert response.status_code == 200


def test_local_with_secret_requires_valid_signature(client, monkeypatch):
    """Test that local env with secret requires valid signature."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-secret")
    from app.config import get_settings
    from app.firestore_state import get_state_repository

    get_settings.cache_clear()
    get_state_repository.cache_clear()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.test2",
                                    "from": "5491111111111",
                                    "text": {"body": "test"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")
    signature = _create_signature("test-secret", body)

    response = client.post(
        "/webhook/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "x-hub-signature-256": signature},
    )

    assert response.status_code == 200


def test_local_with_secret_rejects_invalid_signature(client, monkeypatch):
    """Test that local env with secret rejects invalid signature."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-secret")
    from app.config import get_settings
    from app.firestore_state import get_state_repository

    get_settings.cache_clear()
    get_state_repository.cache_clear()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.test3",
                                    "from": "5491111111111",
                                    "text": {"body": "test"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post(
        "/webhook/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "x-hub-signature-256": "sha256=invalid"},
    )

    assert response.status_code == 403


def test_webhook_sends_immediate_ack_for_new_audio(client, monkeypatch):
    """A brand new audio message should get an immediate 'received' reply."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    from app.config import get_settings
    from app.firestore_state import get_state_repository
    from app.tasks import processor

    get_settings.cache_clear()
    get_state_repository.cache_clear()

    telefono = "5491199999999"
    audio_payload = (
        'json://{"fecha":"2026-06-18","lote":"20","seccion":"3","codigo_tarea":"145",'
        '"descripcion_tarea":"Fertilización","cantidad":"25 has","variedad":"ACA 603",'
        '"fuente_nitrogenada":"Urea","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.ack-test",
                                    "from": telefono,
                                    "audio": {"id": audio_payload},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post("/webhook/whatsapp", content=body, headers={"Content-Type": "application/json"})

    assert response.status_code == 200
    messages_to_phone = [text for (tel, text) in processor.whatsapp.sent_messages if tel == telefono]
    assert any("Recibí tu audio" in m for m in messages_to_phone)


def test_webhook_replies_to_unsupported_message_type(client, monkeypatch):
    """An image, video, or other unsupported message type should get a friendly reply."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    from app.config import get_settings
    from app.firestore_state import get_state_repository
    from app.tasks import processor

    get_settings.cache_clear()
    get_state_repository.cache_clear()

    telefono = "5491188887777"
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.image-test",
                                    "from": telefono,
                                    "type": "image",
                                    "image": {"id": "some-image-id", "mime_type": "image/jpeg"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post("/webhook/whatsapp", content=body, headers={"Content-Type": "application/json"})

    assert response.status_code == 200
    messages_to_phone = [text for (tel, text) in processor.whatsapp.sent_messages if tel == telefono]
    assert any("solo entiendo audios" in m for m in messages_to_phone)


def test_local_without_signature_header_when_no_secret(client, monkeypatch):
    """Test that local env without secret works without signature header (regression)."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    from app.config import get_settings
    from app.firestore_state import get_state_repository

    get_settings.cache_clear()
    get_state_repository.cache_clear()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.test4",
                                    "from": "5491111111111",
                                    "text": {"body": "test"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")

    response = client.post("/webhook/whatsapp", content=body, headers={"Content-Type": "application/json"})

    assert response.status_code == 200
