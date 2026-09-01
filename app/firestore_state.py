from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from google.cloud import firestore

from app.config import get_settings
from app.models import EstadoProceso, EstadoTecnico, ReporteExtraido, ValidationErrorItem
from app.state_machine import ensure_transition


class StateRepository:
    def create_if_absent(self, estado: EstadoTecnico) -> tuple[EstadoTecnico, bool]:
        raise NotImplementedError

    def get(self, message_id: str) -> Optional[EstadoTecnico]:
        raise NotImplementedError

    def find_pending_by_phone(self, telefono: str) -> Optional[EstadoTecnico]:
        raise NotImplementedError

    def update(
        self,
        message_id: str,
        *,
        estado: Optional[EstadoProceso] = None,
        reporte_extraido: Optional[ReporteExtraido] = None,
        errores_validacion: Optional[list[ValidationErrorItem]] = None,
        ruta_audio: Optional[str] = None,
        increment_attempts: bool = False,
    ) -> EstadoTecnico:
        raise NotImplementedError


class InMemoryStateRepository(StateRepository):
    def __init__(self) -> None:
        self._items: dict[str, EstadoTecnico] = {}

    def create_if_absent(self, estado: EstadoTecnico) -> tuple[EstadoTecnico, bool]:
        existing = self._items.get(estado.message_id)
        if existing:
            return existing, False
        self._items[estado.message_id] = estado
        return estado, True

    def get(self, message_id: str) -> Optional[EstadoTecnico]:
        return self._items.get(message_id)

    def find_pending_by_phone(self, telefono: str) -> Optional[EstadoTecnico]:
        for item in sorted(self._items.values(), key=lambda x: x.fecha_actualizacion, reverse=True):
            if item.telefono == telefono and item.estado in {
                EstadoProceso.PENDIENTE_DATOS,
                EstadoProceso.PENDIENTE_CONFIRMACION,
            }:
                return item
        return None

    def update(
        self,
        message_id: str,
        *,
        estado: Optional[EstadoProceso] = None,
        reporte_extraido: Optional[ReporteExtraido] = None,
        errores_validacion: Optional[list[ValidationErrorItem]] = None,
        ruta_audio: Optional[str] = None,
        increment_attempts: bool = False,
    ) -> EstadoTecnico:
        item = self._items[message_id]
        if estado:
            ensure_transition(item.estado, estado)
            item.estado = estado
        if reporte_extraido is not None:
            item.reporte_extraido = reporte_extraido
        if errores_validacion is not None:
            item.errores_validacion = errores_validacion
        if ruta_audio is not None:
            item.ruta_audio = ruta_audio
        if increment_attempts:
            item.intentos += 1
        item.fecha_actualizacion = datetime.now(timezone.utc)
        self._items[message_id] = item
        return item


class FirestoreStateRepository(StateRepository):
    def __init__(self) -> None:
        settings = get_settings()
        self._client = firestore.Client(project=settings.gcp_project_id)
        self._collection_name = settings.firestore_collection

    def _doc_ref(self, message_id: str):
        return self._client.collection(self._collection_name).document(message_id)

    def create_if_absent(self, estado: EstadoTecnico) -> tuple[EstadoTecnico, bool]:
        doc_ref = self._doc_ref(estado.message_id)
        data = estado.model_dump(mode="json")

        @firestore.transactional
        def _create_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            if snapshot.exists:
                return False
            transaction.set(doc_ref, data)
            return True

        transaction = self._client.transaction()
        created = _create_transaction(transaction)

        if created:
            return estado, True
        else:
            # Fetch the existing document
            snapshot = doc_ref.get()
            existing = EstadoTecnico.model_validate(snapshot.to_dict())
            return existing, False

    def get(self, message_id: str) -> Optional[EstadoTecnico]:
        doc_ref = self._doc_ref(message_id)
        snapshot = doc_ref.get()
        if not snapshot.exists:
            return None
        return EstadoTecnico.model_validate(snapshot.to_dict())

    def find_pending_by_phone(self, telefono: str) -> Optional[EstadoTecnico]:
        query = (
            self._client.collection(self._collection_name)
            .where(filter=firestore.FieldFilter("telefono", "==", telefono))
            .where(
                filter=firestore.FieldFilter(
                    "estado",
                    "in",
                    [EstadoProceso.PENDIENTE_DATOS.value, EstadoProceso.PENDIENTE_CONFIRMACION.value],
                )
            )
            .order_by("fecha_actualizacion", direction=firestore.Query.DESCENDING)
            .limit(1)
        )
        docs = list(query.stream())
        if not docs:
            return None
        return EstadoTecnico.model_validate(docs[0].to_dict())

    def update(
        self,
        message_id: str,
        *,
        estado: Optional[EstadoProceso] = None,
        reporte_extraido: Optional[ReporteExtraido] = None,
        errores_validacion: Optional[list[ValidationErrorItem]] = None,
        ruta_audio: Optional[str] = None,
        increment_attempts: bool = False,
    ) -> EstadoTecnico:
        doc_ref = self._doc_ref(message_id)

        @firestore.transactional
        def _update_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            item = EstadoTecnico.model_validate(snapshot.to_dict())

            if estado:
                ensure_transition(item.estado, estado)
                item = item.model_copy(update={"estado": estado})
            if reporte_extraido is not None:
                item = item.model_copy(update={"reporte_extraido": reporte_extraido})
            if errores_validacion is not None:
                item = item.model_copy(update={"errores_validacion": errores_validacion})
            if ruta_audio is not None:
                item = item.model_copy(update={"ruta_audio": ruta_audio})
            if increment_attempts:
                item = item.model_copy(update={"intentos": item.intentos + 1})

            item = item.model_copy(update={"fecha_actualizacion": datetime.now(timezone.utc)})
            transaction.set(doc_ref, item.model_dump(mode="json"), merge=True)
            return item

        transaction = self._client.transaction()
        return _update_transaction(transaction)


@lru_cache
def get_state_repository() -> StateRepository:
    if get_settings().environment == "local":
        return InMemoryStateRepository()
    return FirestoreStateRepository()


state_repository = InMemoryStateRepository()
