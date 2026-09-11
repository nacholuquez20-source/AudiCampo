from app.message_templates import missing_field_message, pending_reminder_message
from app.models import BUSINESS_FIELDS


def test_pending_reminder_mentions_the_text_correction_command():
    """Regresión: alguien escribía la respuesta directo (\"20\") en vez de
    CORREGIR campo: valor, y el bot la ignoraba sin avisar que había otra forma."""
    mensaje = pending_reminder_message()

    assert "CORREGIR" in mensaje


def test_missing_field_message_mentions_the_text_correction_command_for_every_field():
    for campo in BUSINESS_FIELDS:
        mensaje = missing_field_message(campo)
        assert "CORREGIR" in mensaje, f"falta la opción de texto para el campo {campo!r}"
