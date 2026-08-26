#!/usr/bin/env python3
"""
Script para agregar WHATSAPP_VERIFY_TOKEN a Cloud Run.
"""

import os
from google.cloud import run_v2
from google.cloud.run_v2 import UpdateServiceRequest, EnvVar

def add_verify_token():
    """Agrega WHATSAPP_VERIFY_TOKEN a Cloud Run."""

    project_id = "audio-campo"
    service_name = "audicamp"
    region = "us-central1"

    client = run_v2.ServicesClient()
    service_path = f"projects/{project_id}/locations/{region}/services/{service_name}"

    try:
        service = client.get_service(request={"name": service_path})
        print(f"✓ Servicio encontrado")

        if service.template.containers:
            for container in service.template.containers:
                if not container.env:
                    container.env = []

                # Buscar o crear WHATSAPP_VERIFY_TOKEN
                token_found = False
                for env_var in container.env:
                    if env_var.name == "WHATSAPP_VERIFY_TOKEN":
                        env_var.value = "dev-verify-token"
                        token_found = True
                        break

                if not token_found:
                    container.env.append(
                        EnvVar(name="WHATSAPP_VERIFY_TOKEN", value="dev-verify-token")
                    )

        update_request = UpdateServiceRequest(service=service)
        operation = client.update_service(request=update_request)

        print("⏳ Actualizando Cloud Run...")
        result = operation.result(timeout=120)

        print(f"✓ WHATSAPP_VERIFY_TOKEN agregado correctamente")
        print(f"✓ Ya podés registrar el webhook en Meta")

        return True

    except Exception as e:
        print(f"✗ Error: {e}")
        return False

if __name__ == "__main__":
    add_verify_token()
