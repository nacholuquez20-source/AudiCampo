# AudiCampo MVP

Backend FastAPI para reportes de campo por audio de WhatsApp con IA.

## Principios del MVP

- La IA solo interpreta audio y devuelve JSON.
- La validación, confirmación, estado y escritura son deterministas.
- Nunca se guarda un reporte sin `CONFIRMAR`.
- La fila final de Google Sheets tiene exactamente 10 columnas de negocio.
- Los campos fuera de alcance son rechazados por los modelos internos.

## Ejecutar localmente

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

## Endpoints

- `GET /webhook/whatsapp`: verificación de Meta.
- `POST /webhook/whatsapp`: eventos entrantes. Descarta reintentos de WhatsApp por `message_id`
  ya visto, responde rápido y encola el procesamiento del audio en Cloud Tasks (si está
  configurado; si no, lo corre en background local, como antes).
- `POST /tasks/process-audio`: descarga el audio y lo procesa. Lo llama Cloud Tasks, con
  reintento automático si la instancia se cae a mitad de camino. Protegido por
  `TASKS_SHARED_SECRET`.
- `POST /tasks/delete-audio`: borra el audio ya procesado de Cloud Storage. Se dispara solo
  después de que el capataz confirma el reporte. Protegido por `TASKS_SHARED_SECRET`.
- `GET /health`: estado básico.

### Variables de entorno para Cloud Tasks (producción)

- `CLOUD_TASKS_QUEUE`, `CLOUD_TASKS_LOCATION`: la cola ya prevista en `config.py`.
- `SERVICE_BASE_URL`: URL pública del propio servicio de Cloud Run, para que Cloud Tasks
  sepa a dónde volver a llamar. Si no se define, se deriva de `PROCESS_AUDIO_URL`.
- `TASKS_SHARED_SECRET`: secreto compartido que Cloud Tasks manda en el header
  `X-Tasks-Secret`. Sin esta variable, en cualquier ambiente que no sea `local` los
  endpoints `/tasks/*` rechazan todos los pedidos (fail closed).

Si `CLOUD_TASKS_QUEUE`/`CLOUD_TASKS_LOCATION`/`SERVICE_BASE_URL` no están completos, el
servicio sigue funcionando como antes (procesa en background local) — no rompe nada, pero
vuelve a quedar expuesto al riesgo de perder un audio si Cloud Run apaga la instancia a
mitad de camino.

## Desarrollo local sin servicios externos

El adaptador local de Gemini acepta `audio_id` con prefijo `json://` para simular una extracción:

```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              {
                "id": "wamid.dev1",
                "from": "5491111111111",
                "audio": {
                  "id": "json://{\"fecha\":\"2026-06-18\",\"finca\":\"Fronterita\",\"lote\":\"20\",\"seccion\":\"3\",\"trabajador\":\"Aragón Martín\",\"codigo_tarea\":\"145\",\"descripcion_tarea\":\"Fertilización\",\"cantidad\":\"25 has\",\"contratista\":\"Trabajo propio\",\"nombre_capataz\":\"Juan Pérez\"}"
                }
              }
            ]
          }
        }
      ]
    }
  ]
}
```

Luego enviar un mensaje de texto `CONFIRMAR` desde el mismo teléfono para guardar la fila en el writer local.

## Configuración de producción

### Variables de entorno requeridas

- `WHATSAPP_APP_SECRET`: secreto para validar firmas de webhooks desde Meta. Requerido en `environment != "local"`.
- `GOOGLE_SHEET_ID`: ID de la hoja de Google Sheets con catálogos en pestañas (`Capataces`, `LotesSecciones`, `Tareas`, `Contratistas`). `Capataces` necesita las columnas `telefono` y `nombre`; `contratista` y `finca` son opcionales - si están cargadas, el capataz no tiene que decir esos datos en el audio.
- `GOOGLE_GENAI_API_KEY`: clave de API de Google Generative AI (Gemini). Requerida para activar extracción real en producción.
- `WHATSAPP_ACCESS_TOKEN`: token de acceso de WhatsApp Cloud API. Requerido para envío real y para descargar audio entrante en producción.
- `WHATSAPP_PHONE_NUMBER_ID`: ID de número de teléfono de WhatsApp Business. Requerido con `WHATSAPP_ACCESS_TOKEN`.
- `GCS_BUCKET_NAME`: bucket de Cloud Storage donde se archiva el audio descargado de WhatsApp. Requerido junto con `WHATSAPP_ACCESS_TOKEN` para activar la descarga real en producción.

### Firestore (Estado)

El módulo `firestore_state.py` proporciona `FirestoreStateRepository` para persistir estado en Firestore usando Application Default Credentials (ADC). En ambiente local (`ENVIRONMENT=local`), la app usa `InMemoryStateRepository` para desarrollo sin servicios externos.

Para ejecutar tests de Firestore contra el emulador:

```bash
gcloud emulators firestore start
export FIRESTORE_EMULATOR_HOST=localhost:8080
pytest tests/test_firestore_state.py -v
```

### Catálogos (Google Sheets)

El módulo `catalogs.py` carga catálogos desde Google Sheets en producción (`ENVIRONMENT != "local"`). Usa cache TTL de 60 segundos y Application Default Credentials (ADC). En ambiente local, carga catálogos seed en memoria.

## Adaptadores productivos

- ✓ `firestore_state.py`: Firestore con dedupe por `message_id`.
- ✓ Verificación de firma del webhook: validar `x-hub-signature-256` en `POST /webhook/whatsapp`.
- ✓ `catalogs.py`: Google Sheets con cache TTL.
- ✓ `gemini_extractor.py`: Gemini con audio real (descarga desde GCS + `generate_content_async` multimodal), con fallback local.
- ✓ `whatsapp.py`: WhatsApp Cloud API con fallback local.
- ✓ `sheets_writer.py`: Google Sheets `Reportes` con 10 columnas exactas (A-J).
- ✓ `storage.py`: descarga el audio desde la Graph API de WhatsApp y lo archiva en Cloud Storage (`GcsAudioStorage`), con fallback local.

## Futuro

- `storage.py`: aplicar TTL de 7 días a los audios archivados en Cloud Storage (lifecycle rule del bucket).
