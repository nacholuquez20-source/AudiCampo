#!/usr/bin/env python3
"""Test que Cloud Run está respondiendo correctamente."""

import requests

print("Testeando Cloud Run...\n")

# Test 1: Health check
try:
    r = requests.get("https://audicamp-142517467995.us-central1.run.app/health", timeout=10)
    print(f"✓ Health check: HTTP {r.status_code}")
    print(f"  Respuesta: {r.text}\n")
except Exception as e:
    print(f"✗ Health check falló: {e}\n")

# Test 2: Webhook GET (verificación de Meta)
try:
    r = requests.get(
        "https://audicamp-142517467995.us-central1.run.app/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "dev-verify-token",
            "hub.challenge": "test123"
        },
        timeout=10
    )
    print(f"✓ Webhook GET: HTTP {r.status_code}")
    print(f"  Respuesta: {r.text}\n")

    if r.status_code == 200 and r.text == "test123":
        print("✓✓✓ El webhook está funcionando correctamente para Meta!")
    else:
        print(f"⚠ Respuesta inesperada. Status: {r.status_code}, Body: {r.text}")

except Exception as e:
    print(f"✗ Webhook GET falló: {e}\n")
