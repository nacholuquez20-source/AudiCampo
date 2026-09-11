import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from google.cloud import firestore
from pydantic import ValidationError

from app.config import get_settings
from app.models import EstadoProceso, EstadoTecnico, ReporteExtraido, ValidationErrorItem
from app.state_machine import ensure_transition

logger = logging.getLogger(__name__)


class EstadoNoEncontrado(Exception):
    """El documento no existe o no calza con el esquema actual.

    Puede pasar si Cloud Tasks reintenta una tarea vieja después de que ese
    registro ya se borró o cambió de forma (por un cambio de esquema). No hay
    nada que actualizar en ese caso - es un reintento huérfano, no un error del
    reporte en curso.
    """


def _try_parse(raw: dict, message_id: str) -> Optional[EstadoTecnico]:
    """Un cambio de esquema (como sacar variedad/fuente_nitrogenada) puede dejar
    documentos viejos que ya no calzan con el modelo actual. Tratarlos como si no
    existieran es mejor que romper la petición entera: el peor caso es que se le
    vuelva a preguntar un dato a alguien, no que el bot deje de contestar."""
    try:
        return EstadoTecnico.model_validate(raw)
    except ValidationError:
        logger.warning("Documento %s no calza con el esquema actual, se ignora", message_id, exc_info=True)
        return None


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
        return _try_parse(snapshot.to_dict(), message_id)

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
        return _try_parse(docs[0].to_dict(), docs[0].id)

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
            if not snapshot.exists:
                raise EstadoNoEncontrado(f"No existe el documento {message_id}")
            try:
                item = EstadoTecnico.model_validate(snapshot.to_dict())
            except ValidationError as exc:
                raise EstadoNoEncontrado(f"El documento {message_id} no calza con el esquema actual") from exc

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


class MessageDedupRepository:
    """Recuerda qué message_id de WhatsApp ya se procesaron.

    WhatsApp reenvía el mismo mensaje si no contestamos a tiempo (o por cualquier
    otro reintento de su lado). Sin esto, un "sí" reenviado guarda el reporte dos
    veces en la planilla.
    """

    def claim(self, message_id: str) -> bool:
        """True la primera vez que se ve este message_id; False si ya se había visto."""
        raise NotImplementedError


class InMemoryMessageDedupRepository(MessageDedupRepository):
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def claim(self, message_id: str) -> bool:
        if message_id in self._seen:
            return False
        self._seen.add(message_id)
        return True


class FirestoreMessageDedupRepository(MessageDedupRepository):
    def __init__(self) -> None:
        settings = get_settings()
        self._client = firestore.Client(project=settings.gcp_project_id)
        self._collection_name = "mensajes_recibidos"

    def claim(self, message_id: str) -> bool:
        doc_ref = self._client.collection(self._collection_name).document(message_id)

        @firestore.transactional
        def _claim_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            if snapshot.exists:
                return False
            # "recibido_en" queda para poder configurar en la consola de Firestore una
            # política de TTL sobre esta colección y que se auto-limpie sola.
            transaction.set(doc_ref, {"recibido_en": datetime.now(timezone.utc)})
            return True

        transaction = self._client.transaction()
        return _claim_transaction(transaction)


@lru_cache
def get_message_dedup_repository() -> MessageDedupRepository:
    if get_settings().environment == "local":
        return InMemoryMessageDedupRepository()
    return FirestoreMessageDedupRepository()
