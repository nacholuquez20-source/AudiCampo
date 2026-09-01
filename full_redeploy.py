#!/usr/bin/env python3
"""
Full redeploy de Cloud Run con código nuevo.
Esto rebuildea la imagen Docker e instancia el servicio.
"""

import subprocess
import os
import shutil

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = os.path.expanduser('~/.google/audicamp-key.json')

print("🔨 Iniciando redeploy completo de Cloud Run...")
print("Esto puede tardar 3-5 minutos...\n")

KNOWN_GCLOUD_PATH = r"C:\Users\usuario\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
gcloud_path = shutil.which("gcloud") or KNOWN_GCLOUD_PATH

cmd = [
    gcloud_path, "run", "deploy", "audicamp",
    "--source", ".",
    "--region", "us-central1",
    "--project", "audio-campo",
    "--allow-unauthenticated"
]

try:
    result = subprocess.run(cmd, cwd=r"C:\Users\usuario\Desktop\AudiCampo", capture_output=False, shell=True)

    if result.returncode == 0:
        print("\n✓ Redeploy completado exitosamente!")
        print("✓ El webhook debería funcionar ahora con text/plain")
        print("\nIntenta registrar en Meta de nuevo.")
    else:
        print(f"\n✗ Error en redeploy (exit code: {result.returncode})")

except Exception as e:
    print(f"✗ Error: {e}")
    print("\nAlternativa: ejecuta manualmente en PowerShell:")
    print("gcloud run deploy audicamp --source . --region us-central1 --project audio-campo --allow-unauthenticated")
