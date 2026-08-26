import json

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.config import Settings, get_settings
from app.firestore_state import get_state_repository
from app.models import EstadoProceso, EstadoTecnico
from app.sheets_writer import get_sheets_writer
from app.storage import LocalAudioStorage
from app.tasks import processor
from app.whatsapp import parse_webhook_messages, verify_signature

app = FastAPI(title="AudiCampo MVP")
audio_storage = LocalAudioStorage()


@app.get("/", response_class=HTMLResponse)
async def local_tester() -> str:
    return """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AudiCampo MVP</title>
  <style>
    :root { color-scheme: light; font-family: Arial, sans-serif; }
    body { margin: 0; background: #f6f7f9; color: #1f2937; }
    main { max-width: 1040px; margin: 0 auto; padding: 28px 18px 44px; }
    h1 { margin: 0 0 6px; font-size: 30px; }
    p { margin: 0 0 18px; color: #4b5563; }
    section { background: white; border: 1px solid #d8dee8; border-radius: 8px; padding: 18px; margin-top: 16px; }
    button { border: 0; border-radius: 6px; padding: 10px 14px; margin: 0 8px 8px 0; cursor: pointer; font-weight: 700; }
    .primary { background: #0f766e; color: white; }
    .secondary { background: #334155; color: white; }
    .ghost { background: #e5e7eb; color: #111827; }
    pre { white-space: pre-wrap; background: #111827; color: #f9fafb; padding: 14px; border-radius: 8px; overflow: auto; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }
    th, td { border-bottom: 1px solid #e5e7eb; padding: 8px; text-align: left; vertical-align: top; }
    th { background: #f8fafc; }
  </style>
</head>
<body>
  <main>
    <h1>AudiCampo MVP</h1>
    <p>Prueba local del flujo: audio simulado, validación, confirmación humana y guardado.</p>

    <section>
      <button class="primary" onclick="sendAudio()">Simular audio válido</button>
      <button class="secondary" onclick="confirmReport()">Confirmar reporte</button>
      <button class="ghost" onclick="refreshData()">Actualizar vista</button>
      <pre id="status">Listo para probar.</pre>
    </section>

    <section>
      <h2>Estado técnico</h2>
      <pre id="state">Sin datos todavía.</pre>
    </section>

    <section>
      <h2>Filas guardadas</h2>
      <div id="sheet">Sin filas todavía.</div>
    </section>
  </main>
  <script>
    const phone = "5491111111111";
    const headers = ["Fecha", "Lote", "Sección", "Código Tarea", "Descripción Tarea", "Cantidad", "Variedad", "Fuente Nitrogenada", "Contratista", "Nombre del capataz"];

    function setStatus(text) {
      document.getElementById("status").textContent = text;
    }

    async function postWebhook(message) {
      const body = { entry: [{ changes: [{ value: { messages: [message] } }] }] };
      const response = await fetch("/webhook/whatsapp", {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify(body)
      });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }

    async function sendAudio() {
      const id = "wamid.web-" + Date.now();
      const payload = {
        fecha: "2026-06-18",
        lote: "20",
        seccion: "3",
        codigo_tarea: "145",
        descripcion_tarea: "Fertilización",
        cantidad: "25 has",
        variedad: "ACA 603",
        fuente_nitrogenada: "Urea",
        contratista: "Trabajo propio",
        nombre_capataz: "Juan Pérez"
      };
      setStatus("Enviando audio simulado...");
      await postWebhook({ id, from: phone, audio: { id: "json://" + JSON.stringify(payload) } });
      setStatus("Audio aceptado. Ahora tocá Confirmar reporte.");
      await refreshData();
    }

    async function confirmReport() {
      setStatus("Enviando CONFIRMAR...");
      await postWebhook({ id: "wamid.confirm-" + Date.now(), from: phone, text: { body: "CONFIRMAR" } });
      setStatus("Confirmación enviada. Si había un reporte pendiente, quedó guardado.");
      await refreshData();
    }

    async function refreshData() {
      const state = await fetch("/dev/state").then(r => r.json());
      const sheets = await fetch("/dev/sheets").then(r => r.json());
      document.getElementById("state").textContent = JSON.stringify(state, null, 2);
      renderRows(sheets.rows || []);
    }

    function renderRows(rows) {
      const target = document.getElementById("sheet");
      if (!rows.length) {
        target.textContent = "Sin filas todavía.";
        return;
      }
      target.innerHTML = "<table><thead><tr>" + headers.map(h => `<th>${h}</th>`).join("") + "</tr></thead><tbody>" +
        rows.map(row => "<tr>" + row.map(cell => `<td>${cell}</td>`).join("") + "</tr>").join("") +
        "</tbody></table>";
    }

    refreshData().catch(error => setStatus("Error: " + error.message));
  </script>
</body>
</html>
"""


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/webhook/whatsapp")
async def verify_whatsapp_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> Response:
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Invalid verify token")


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request, background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    raw_body = await request.body()

    # Validate signature based on environment
    if settings.environment != "local":
        if not settings.whatsapp_app_secret:
            raise HTTPException(status_code=500, detail="WHATSAPP_APP_SECRET no configurado")
        signature_header = request.headers.get("x-hub-signature-256")
        if not verify_signature(raw_body, signature_header, settings.whatsapp_app_secret):
            raise HTTPException(status_code=403, detail="Firma inválida")
    elif settings.whatsapp_app_secret:
        signature_header = request.headers.get("x-hub-signature-256")
        if not verify_signature(raw_body, signature_header, settings.whatsapp_app_secret):
            raise HTTPException(status_code=403, detail="Firma inválida")

    payload = json.loads(raw_body)
    repo = get_state_repository()

    for message in parse_webhook_messages(payload):
        if message.audio_id:
            technical_state = EstadoTecnico(
                message_id=message.message_id,
                telefono=message.telefono,
                estado=EstadoProceso.RECIBIDO,
            )
            state, created = repo.create_if_absent(technical_state)
            if created:
                audio_uri = await audio_storage.save_whatsapp_audio(message.audio_id, message.message_id)
                repo.update(state.message_id, ruta_audio=audio_uri)
                background_tasks.add_task(processor.process_audio, state.message_id)
        elif message.text:
            background_tasks.add_task(processor.handle_text, message.telefono, message.text)

    return {"status": "accepted"}


@app.post("/tasks/process-audio")
async def process_audio_task(payload: dict[str, str]) -> dict[str, str]:
    message_id = payload.get("message_id")
    if not message_id:
        raise HTTPException(status_code=400, detail="message_id is required")
    await processor.process_audio(message_id)
    return {"status": "processed"}


@app.post("/tasks/delete-audio")
async def delete_audio_task(payload: dict[str, str]) -> dict[str, str]:
    # Real Cloud Storage deletion is wired here in production.
    if not payload.get("message_id") and not payload.get("ruta_audio"):
        raise HTTPException(status_code=400, detail="message_id or ruta_audio is required")
    return {"status": "accepted"}


@app.get("/dev/state")
async def dev_state(settings: Settings = Depends(get_settings)) -> list[dict]:
    if settings.environment != "local":
        raise HTTPException(status_code=404, detail="Not found")
    repo = get_state_repository()
    return [item.model_dump(mode="json") for item in repo._items.values()]


@app.get("/dev/sheets")
async def dev_sheets(settings: Settings = Depends(get_settings)) -> dict[str, list[list[str]]]:
    if settings.environment != "local":
        raise HTTPException(status_code=404, detail="Not found")
    sheets_writer = get_sheets_writer()
    if hasattr(sheets_writer, "rows"):
        return {"rows": sheets_writer.rows}
    return {"rows": []}
