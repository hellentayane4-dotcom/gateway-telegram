
import os
import hmac
import hashlib

from dotenv import load_dotenv

load_dotenv()

secret = os.getenv("NOWHUB_WEBHOOK_SECRET")

print("INICIANDO TESTE HMAC NOWHUB")

if not secret:
    print("ERRO: NOWHUB_WEBHOOK_SECRET nao encontrado no .env")
else:
    payload = b'{"id":"evt_teste","type":"deposit.updated"}'

    assinatura = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()

    assinatura_correta = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()

    assinatura_falsa = "0" * 64

    print(
        "Assinatura valida:",
        hmac.compare_digest(assinatura, assinatura_correta)
    )

    print(
        "Assinatura falsa rejeitada:",
        not hmac.compare_digest(assinatura, assinatura_falsa)
    )

    print("Teste concluido sem enviar requisicoes.")
