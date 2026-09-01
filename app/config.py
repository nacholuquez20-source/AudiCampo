import os
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str
    environment: str
    whatsapp_verify_token: str
    whatsapp_access_token: Optional[str]
    whatsapp_phone_number_id: Optional[str]
    whatsapp_app_secret: Optional[str]
    gcp_project_id: Optional[str]
    gcs_bucket_name: Optional[str]
    firestore_collection: str
    cloud_tasks_queue: Optional[str]
    cloud_tasks_location: Optional[str]
    process_audio_url: Optional[str]
    gemini_model: str
    gemini_fallback_model: str
    google_genai_api_key: Optional[str]
    google_sheet_id: Optional[str]
    google_sheet_tab: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "AudiCampo"),
        environment=os.getenv("ENVIRONMENT", "local"),
        whatsapp_verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", "dev-verify-token"),
        whatsapp_access_token=os.getenv("WHATSAPP_ACCESS_TOKEN"),
        whatsapp_phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_app_secret=os.getenv("WHATSAPP_APP_SECRET"),
        gcp_project_id=os.getenv("GCP_PROJECT_ID"),
        gcs_bucket_name=os.getenv("GCS_BUCKET_NAME"),
        firestore_collection=os.getenv("FIRESTORE_COLLECTION", "reportes_estado"),
        cloud_tasks_queue=os.getenv("CLOUD_TASKS_QUEUE"),
        cloud_tasks_location=os.getenv("CLOUD_TASKS_LOCATION"),
        process_audio_url=os.getenv("PROCESS_AUDIO_URL"),
        # Se usa un modelo estable ya asentado como principal: los recién lanzados
        # (3.6/3.7) vienen devolviendo 503 por saturación durante sus primeras semanas.
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        gemini_fallback_model=os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash"),
        google_genai_api_key=os.getenv("GOOGLE_GENAI_API_KEY"),
        google_sheet_id=os.getenv("GOOGLE_SHEET_ID"),
        google_sheet_tab=os.getenv("GOOGLE_SHEET_TAB", "Reportes"),
    )
