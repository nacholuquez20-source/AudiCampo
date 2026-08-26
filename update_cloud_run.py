#!/usr/bin/env python3
"""
Script para actualizar variables de entorno en Cloud Run sin necesidad de gcloud CLI.
"""

import os
from google.cloud import run_v2
from google.cloud.run_v2 import UpdateServiceRequest

def update_cloud_run_service():
    """Actualiza el servicio de Cloud Run con el WHATSAPP_APP_SECRET."""

    # Configuración
    project_id = "audio-campo"
    service_name = "audicamp"
    region = "us-central1"

    # Crear cliente de Cloud Run
    client = run_v2.ServicesClient()

    # Path del servicio
    service_path = f"projects/{project_id}/locations/{region}/services/{service_name}"

    try:
        # Obtener el servicio actual
        service = client.get_service(request={"name": service_path})
        print(f"✓ Servicio encontrado: {service.name}")

        # Actualizar variable de entorno en el container
        if service.template.containers:
            for container in service.template.containers:
                # Inicializar lista de env vars si no existe
                if not container.env:
                    from google.cloud.run_v2 import EnvVar
                    container.env = []

                # Buscar o crear WHATSAPP_APP_SECRET (leído de variable de entorno local)
                whatsapp_app_secret = os.environ["WHATSAPP_APP_SECRET"]
                secret_found = False
                for env_var in container.env:
                    if env_var.name == "WHATSAPP_APP_SECRET":
                        env_var.value = whatsapp_app_secret
                        secret_found = True
                        break

                if not secret_found:
                    from google.cloud.run_v2 import EnvVar
                    container.env.append(
                        EnvVar(name="WHATSAPP_APP_SECRET", value=whatsapp_app_secret)
                    )

        # Actualizar el servicio
        update_request = UpdateServiceRequest(service=service)
        operation = client.update_service(request=update_request)

        print("⏳ Actualizando Cloud Run... esto puede tardar 15-30 segundos...")

        # Esperar a que complete
        result = operation.result(timeout=120)

        print(f"✓ Servicio actualizado exitosamente: {result.name}")
        print(f"✓ WHATSAPP_APP_SECRET configurado")
        print(f"\n✓ MVP listo para Meta/WhatsApp!")
        print(f"URL pública: https://audicamp-142517467995.us-central1.run.app")

        return True

    except Exception as e:
        print(f"✗ Error al actualizar: {e}")
        return False

if __name__ == "__main__":
    update_cloud_run_service()
