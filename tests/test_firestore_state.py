import os
from datetime import datetime, timezone

import pytest

from app.firestore_state import (
    EstadoNoEncontrado,
    FirestoreMessageDedupRepository,
    FirestoreStateRepository,
    InMemoryMessageDedupRepository,
)
from app.models import EstadoProceso, EstadoTecnico, ValidationErrorItem


@pytest.mark.skipif(not os.getenv("FIRESTORE_EMULATOR_HOST"), reason="requires Firestore emulator")
class TestFirestoreStateRepository:
    @pytest.fixture
    def repo(self):
        """Create a FirestoreStateRepository for testing."""
        return FirestoreStateRepository()

    def test_create_if_absent_new_document(self, repo):
        """Test creating a new document."""
        estado = EstadoTecnico(
            message_id="test-msg-1",
            telefono="5491111111111",
            estado=EstadoProceso.RECIBIDO,
        )
        result_estado, created = repo.create_if_absent(estado)
        assert created is True
        assert result_estado.message_id == "test-msg-1"

    def test_create_if_absent_dedupe(self, repo):
        """Test that creating twice returns the existing document (dedupe)."""
        estado = EstadoTecnico(
            message_id="test-msg-2",
            telefono="5491111111111",
            estado=EstadoProceso.RECIBIDO,
        )
        result1, created1 = repo.create_if_absent(estado)
        result2, created2 = repo.create_if_absent(estado)

        assert created1 is True
        assert created2 is False
        assert result2.message_id == "test-msg-2"

    def test_get_existing_document(self, repo):
        """Test retrieving an existing document."""
        estado = EstadoTecnico(
            message_id="test-msg-3",
            telefono="5491111111111",
            estado=EstadoProceso.RECIBIDO,
        )
        repo.create_if_absent(estado)
        retrieved = repo.get("test-msg-3")

        assert retrieved is not None
        assert retrieved.message_id == "test-msg-3"
        assert retrieved.telefono == "5491111111111"

    def test_get_nonexistent_document(self, repo):
        """Test retrieving a non-existent document returns None."""
        retrieved = repo.get("nonexistent-id")
        assert retrieved is None

    def test_find_pending_by_phone(self, repo):
        """Test finding pending reports by phone."""
        # Create multiple states with different statuses and timestamps
        estado1 = EstadoTecnico(
            message_id="msg-pending-1",
            telefono="5491111111111",
            estado=EstadoProceso.PENDIENTE_CONFIRMACION,
        )
        estado2 = EstadoTecnico(
            message_id="msg-confirmed",
            telefono="5491111111111",
            estado=EstadoProceso.CONFIRMADO,
        )
        estado3 = EstadoTecnico(
            message_id="msg-pending-2",
            telefono="5491111111111",
            estado=EstadoProceso.PENDIENTE_DATOS,
        )

        repo.create_if_absent(estado1)
        repo.create_if_absent(estado2)
        repo.create_if_absent(estado3)

        found = repo.find_pending_by_phone("5491111111111")
        assert found is not None
        assert found.message_id in ["msg-pending-1", "msg-pending-2"]
        assert found.estado in {EstadoProceso.PENDIENTE_DATOS, EstadoProceso.PENDIENTE_CONFIRMACION}

    def test_find_pending_by_phone_no_results(self, repo):
        """Test finding pending reports when none exist."""
        found = repo.find_pending_by_phone("5492222222222")
        assert found is None

    def test_update_estado(self, repo):
        """Test updating the estado field."""
        estado = EstadoTecnico(
            message_id="msg-update-1",
            telefono="5491111111111",
            estado=EstadoProceso.RECIBIDO,
        )
        repo.create_if_absent(estado)
        updated = repo.update("msg-update-1", estado=EstadoProceso.PROCESANDO)

        assert updated.estado == EstadoProceso.PROCESANDO
        assert updated.fecha_actualizacion > estado.fecha_recepcion

    def test_update_reporte_extraido(self, repo):
        """Test updating the reporte_extraido field."""
        estado = EstadoTecnico(
            message_id="msg-update-2",
            telefono="5491111111111",
            estado=EstadoProceso.RECIBIDO,
        )
        repo.create_if_absent(estado)

        from app.models import ReporteExtraido

        reporte = ReporteExtraido(fecha="2026-06-18", lote="20")
        updated = repo.update("msg-update-2", reporte_extraido=reporte)

        assert updated.reporte_extraido is not None
        assert updated.reporte_extraido.fecha == "2026-06-18"

    def test_update_errores_validacion(self, repo):
        """Test updating the errores_validacion field."""
        estado = EstadoTecnico(
            message_id="msg-update-3",
            telefono="5491111111111",
            estado=EstadoProceso.RECIBIDO,
        )
        repo.create_if_absent(estado)

        errors = [ValidationErrorItem(campo="fecha", mensaje="falta: fecha")]
        updated = repo.update("msg-update-3", errores_validacion=errors)

        assert len(updated.errores_validacion) == 1
        assert updated.errores_validacion[0].campo == "fecha"

    def test_update_increment_attempts(self, repo):
        """Test incrementing the intentos field."""
        estado = EstadoTecnico(
            message_id="msg-update-4",
            telefono="5491111111111",
            estado=EstadoProceso.RECIBIDO,
        )
        repo.create_if_absent(estado)
        updated = repo.update("msg-update-4", increment_attempts=True)

        assert updated.intentos == 1

    def test_update_invalid_transition_raises_error(self, repo):
        """Test that invalid transitions raise ValueError."""
        estado = EstadoTecnico(
            message_id="msg-invalid-transition",
            telefono="5491111111111",
            estado=EstadoProceso.PENDIENTE_CONFIRMACION,
        )
        repo.create_if_absent(estado)

        # PENDIENTE_CONFIRMACION -> GUARDADO is not allowed
        with pytest.raises(ValueError):
            repo.update("msg-invalid-transition", estado=EstadoProceso.GUARDADO)

    def test_get_ignores_a_document_from_a_retired_schema_instead_of_crashing(self, repo):
        """Regresión: un cambio de campos (como sacar variedad/fuente_nitrogenada) dejó
        un documento viejo que rompía toda petición para ese teléfono con un 500."""
        doc_ref = repo._doc_ref("msg-old-schema")
        doc_ref.set(
            {
                "message_id": "msg-old-schema",
                "telefono": "5491111111111",
                "estado": "PENDIENTE_DATOS",
                "intentos": 1,
                "ruta_audio": None,
                "reporte_extraido": {"variedad": "ACA 603", "fuente_nitrogenada": "Urea"},
                "errores_validacion": [],
                "fecha_recepcion": datetime.now(timezone.utc).isoformat(),
                "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
            }
        )

        assert repo.get("msg-old-schema") is None

    def test_find_pending_by_phone_ignores_a_document_from_a_retired_schema(self, repo):
        doc_ref = repo._doc_ref("msg-old-schema-pending")
        doc_ref.set(
            {
                "message_id": "msg-old-schema-pending",
                "telefono": "5492222222222",
                "estado": "PENDIENTE_DATOS",
                "intentos": 1,
                "ruta_audio": None,
                "reporte_extraido": {"variedad": "ACA 603", "fuente_nitrogenada": "Urea"},
                "errores_validacion": [],
                "fecha_recepcion": datetime.now(timezone.utc).isoformat(),
                "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
            }
        )

        # No revienta: se lo trata como si no hubiera nada pendiente para ese teléfono.
        assert repo.find_pending_by_phone("5492222222222") is None

    def test_update_raises_a_clean_error_for_a_missing_document(self, repo):
        """Regresión: una tarea vieja de Cloud Tasks reintentando un message_id que
        ya no existe (por ejemplo, borrado a mano) tiraba un ValidationError crudo
        de pydantic en vez de un error claro que el llamador pueda reconocer."""
        with pytest.raises(EstadoNoEncontrado):
            repo.update("msg-que-no-existe", estado=EstadoProceso.PROCESANDO)

    def test_update_raises_a_clean_error_for_a_retired_schema_document(self, repo):
        doc_ref = repo._doc_ref("msg-old-schema-update")
        doc_ref.set(
            {
                "message_id": "msg-old-schema-update",
                "telefono": "5492222222222",
                "estado": "RECIBIDO",
                "intentos": 0,
                "ruta_audio": None,
                "reporte_extraido": {"variedad": "ACA 603", "fuente_nitrogenada": "Urea"},
                "errores_validacion": [],
                "fecha_recepcion": datetime.now(timezone.utc).isoformat(),
                "fecha_actualizacion": datetime.now(timezone.utc).isoformat(),
            }
        )

        with pytest.raises(EstadoNoEncontrado):
            repo.update("msg-old-schema-update", estado=EstadoProceso.PROCESANDO)


class TestInMemoryMessageDedupRepository:
    def test_claims_a_new_message_id(self):
        dedup = InMemoryMessageDedupRepository()
        assert dedup.claim("wamid.1") is True

    def test_rejects_a_message_id_already_claimed(self):
        """Un reintento de WhatsApp (mismo message_id) no debe procesarse dos veces."""
        dedup = InMemoryMessageDedupRepository()
        dedup.claim("wamid.1")
        assert dedup.claim("wamid.1") is False

    def test_different_message_ids_are_independent(self):
        dedup = InMemoryMessageDedupRepository()
        assert dedup.claim("wamid.1") is True
        assert dedup.claim("wamid.2") is True


@pytest.mark.skipif(not os.getenv("FIRESTORE_EMULATOR_HOST"), reason="requires Firestore emulator")
class TestFirestoreMessageDedupRepository:
    @pytest.fixture
    def dedup(self):
        return FirestoreMessageDedupRepository()

    def test_claims_a_new_message_id(self, dedup):
        assert dedup.claim("wamid-dedup-1") is True

    def test_rejects_a_message_id_already_claimed(self, dedup):
        dedup.claim("wamid-dedup-2")
        assert dedup.claim("wamid-dedup-2") is False
