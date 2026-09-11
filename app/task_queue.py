import asyncio
import json
import logging
from functools import lru_cache
from typing import Optional

from google.cloud import tasks_v2

from app.config import get_settings

logger = logging.getLogger(__name__)


class TaskQueue:
    """Encola un llamado HTTP de vuelta a nuestros propios endpoints /tasks/*.

    Cloud Tasks reintenta solo si el llamado falla, y cada intento entra como un
    pedido HTTP nuevo a Cloud Run - eso evita que se pierda un reporte si la
    instancia se apaga a mitad de camino (ver README, "riesgo en el código").
    Es la misma cola que ya estaba prevista en la configuración pero sin usar.
    """

    def __init__(
        self,
        project_id: str,
        location: str,
        queue: str,
        service_base_url: str,
        shared_secret: Optional[str],
    ) -> None:
        self.project_id = project_id
        self.location = location
        self.queue = queue
        self.service_base_url = service_base_url
        self.shared_secret = shared_secret
        self._client: Optional[tasks_v2.CloudTasksClient] = None

    def _get_client(self) -> tasks_v2.CloudTasksClient:
        if self._client is None:
            self._client = tasks_v2.CloudTasksClient()
        return self._client

    async def enqueue(self, path: str, body: dict) -> None:
        await asyncio.to_thread(self._enqueue_sync, path, body)

    def _enqueue_sync(self, path: str, body: dict) -> None:
        client = self._get_client()
        parent = client.queue_path(self.project_id, self.location, self.queue)
        headers = {"Content-Type": "application/json"}
        if self.shared_secret:
            headers["X-Tasks-Secret"] = self.shared_secret
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{self.service_base_url}{path}",
                "headers": headers,
                "body": json.dumps(body).encode("utf-8"),
            }
        }
        client.create_task(request={"parent": parent, "task": task})


@lru_cache
def get_task_queue() -> Optional[TaskQueue]:
    """None cuando falta configurar Cloud Tasks: quien llama debe hacer el trabajo al toque.

    Eso mantiene el comportamiento local (sin Cloud Tasks real) y no rompe nada si
    todavía no se creó la cola en GCP.
    """
    settings = get_settings()
    if settings.environment == "local":
        return None
    if not (
        settings.gcp_project_id
        and settings.cloud_tasks_location
        and settings.cloud_tasks_queue
        and settings.service_base_url
    ):
        logger.warning(
            "Cloud Tasks no está configurado del todo (falta project/location/queue/service_base_url); "
            "se va a procesar todo en el momento en vez de encolarlo."
        )
        return None
    return TaskQueue(
        settings.gcp_project_id,
        settings.cloud_tasks_location,
        settings.cloud_tasks_queue,
        settings.service_base_url,
        settings.tasks_shared_secret,
    )
