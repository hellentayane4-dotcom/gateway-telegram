
import asyncio
import hashlib
import hmac
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from dotenv import load_dotenv

import webserver

load_dotenv()


async def main():
    secret = os.getenv("NOWHUB_WEBHOOK_SECRET")

    if not secret:
        print("ERRO: segredo do webhook nao configurado.")
        return

    # Configuracao apenas para esta instancia de teste.
    configuracao_teste = SimpleNamespace(
        nowhub_hmac_verified=True,
        nowhub_webhook_secret=secret,
        nowhub_hmac_format="sha256-hex",
    )

    # Evento desconhecido: nunca entra na rotina financeira.
    payload = {
        "id": "evt_teste_local",
        "type": "smokepay.security_test",
        "data": {}
    }

    corpo = json.dumps(
        payload, separators=(",", ":")
    ).encode("utf-8")

    assinatura = hmac.new(
        secret.encode("utf-8"),
        corpo,
        hashlib.sha256
    ).hexdigest()

    with patch.object(
        webserver, "settings", configuracao_teste
    ):
        app = webserver.criar_web_app(bot=None)

        transporte = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transporte,
            base_url="http://teste.local"
        ) as client:

            falsa = await client.post(
                "/webhooks/nowhub",
                content=corpo,
                headers={
                    "X-Signature": "0" * 64,
                    "Content-Type": "application/json"
                }
            )

            valida = await client.post(
                "/webhooks/nowhub",
                content=corpo,
                headers={
                    "X-Signature": assinatura,
                    "Content-Type": "application/json"
                }
            )

    print("TESTE DO ENDPOINT NOWHUB")
    print("Assinatura falsa:", falsa.status_code)
    print("Assinatura valida:", valida.status_code)
    print("Resposta valida:", valida.json())

    passou = (
        falsa.status_code == 401
        and valida.status_code == 200
        and valida.json().get("ignored") is True
    )

    print("RESULTADO:", "APROVADO" if passou else "REVISAR")
    print("Nenhuma cobranca foi criada.")


if __name__ == "__main__":
    asyncio.run(main())
