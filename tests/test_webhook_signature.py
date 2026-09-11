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
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
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


def test_webhook_ignores_a_retried_message_id(client, monkeypatch):
    """WhatsApp puede reenviar el mismo mensaje; no debe procesarse dos veces."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    from app.config import get_settings
    from app.firestore_state import get_message_dedup_repository, get_state_repository
    from app.tasks import processor

    get_settings.cache_clear()
    get_state_repository.cache_clear()
    get_message_dedup_repository.cache_clear()

    telefono = "5491166665555"
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.retry-test",
                                    "from": telefono,
                                    "text": {"body": "hola"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body = json.dumps(payload).encode("utf-8")

    first = client.post("/webhook/whatsapp", content=body, headers={"Content-Type": "application/json"})
    second = client.post("/webhook/whatsapp", content=body, headers={"Content-Type": "application/json"})

    assert first.status_code == 200
    assert second.status_code == 200
    messages_to_phone = [text for (tel, text) in processor.whatsapp.sent_messages if tel == telefono]
    assert len(messages_to_phone) == 1


def test_tasks_process_audio_requires_secret_when_configured(client, monkeypatch):
    """Con TASKS_SHARED_SECRET configurado, /tasks/process-audio rechaza pedidos sin el header."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TASKS_SHARED_SECRET", "top-secret")
    from app.config import get_settings

    get_settings.cache_clear()

    response = client.post("/tasks/process-audio", json={"message_id": "x", "telefono": "y", "audio_id": "z"})

    assert response.status_code == 403


def test_tasks_delete_audio_requires_secret_when_configured(client, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TASKS_SHARED_SECRET", "top-secret")
    from app.config import get_settings

    get_settings.cache_clear()

    response = client.post("/tasks/delete-audio", json={"ruta_audio": "gs://bucket/x.ogg"})

    assert response.status_code == 403


def test_tasks_endpoints_accept_the_correct_secret(client, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("TASKS_SHARED_SECRET", "top-secret")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    from app.config import get_settings
    from app.firestore_state import get_state_repository
    from app.models import EstadoProceso, EstadoTecnico

    get_settings.cache_clear()
    get_state_repository.cache_clear()

    audio_payload = (
        'json://{"fecha":"2026-06-18","finca":"Fronterita","lote":"20","seccion":"3",'
        '"trabajador":"Aragón Martín","codigo_tarea":"145","descripcion_tarea":"Fertilización",'
        '"cantidad":"25 has","contratista":"Trabajo propio","nombre_capataz":"Juan Pérez"}'
    )
    # En producción esta fila la crea el webhook antes de encolar la tarea.
    get_state_repository().create_if_absent(
        EstadoTecnico(message_id="wamid.secret-ok", telefono="5491111111111", estado=EstadoProceso.RECIBIDO)
    )

    response = client.post(
        "/tasks/process-audio",
        json={"message_id": "wamid.secret-ok", "telefono": "5491111111111", "audio_id": audio_payload},
        headers={"X-Tasks-Secret": "top-secret"},
    )

    assert response.status_code == 200


def test_webhook_text_report_is_saved_under_the_real_whatsapp_message_id(client, monkeypatch):
    """El webhook tiene que pasarle el message_id real de WhatsApp a handle_text,
    no que cada llamada se arme uno propio - si no, un reintento de WhatsApp podría
    terminar creando dos reportes en vez de reconocerse como el mismo mensaje."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    from app.config import get_settings
    from app.firestore_state import get_message_dedup_repository
    from app.tasks import processor

    get_settings.cache_clear()
    get_message_dedup_repository.cache_clear()

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.text-report-1",
                                    "from": "5491177778888",
                                    "text": {"body": 'json://{"lote":"20","seccion":"3"}'},
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
    # processor.state_repo, no get_state_repository(): el processor global guarda una
    # referencia fija tomada al importar el módulo, así que consultarlo de nuevo acá
    # (incluso con cache_clear) apuntaría a una instancia distinta y vacía.
    saved = processor.state_repo.get("wamid.text-report-1")
    assert saved is not None
    assert saved.telefono == "5491177778888"


def test_orphaned_task_for_a_gone_state_record_does_not_crash_the_endpoint(client, monkeypatch):
    """Regresión: una tarea vieja de Cloud Tasks reintentando un reporte cuyo
    registro ya no existe (o cambió de esquema) tiraba un 500 sin fin - Cloud Tasks
    la reintentaba para siempre. Ahora se corta en limpio."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("TASKS_SHARED_SECRET", "top-secret")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    from unittest.mock import MagicMock, patch

    from app.firestore_state import EstadoNoEncontrado

    with patch("app.main.get_state_repository") as mock_get_repo:
        mock_repo = MagicMock()
        mock_repo.update.side_effect = EstadoNoEncontrado("no existe")
        mock_get_repo.return_value = mock_repo

        response = client.post(
            "/tasks/process-audio",
            json={
                "message_id": "wamid.orphaned",
                "telefono": "5491111111111",
                "audio_id": "json://{}",
            },
            headers={"X-Tasks-Secret": "top-secret"},
        )

    assert response.status_code == 200
