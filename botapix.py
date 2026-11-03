import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger("smokepay.botapix")


class BotaPixError(RuntimeError):
    pass


def _headers(idempotency_key: str | None = None) -> dict[str, str]:
    if not settings.botapix_api_key:
        raise BotaPixError("BOTAPIX_API_KEY não configurada.")
    headers = {
        "Authorization": f"Bearer {settings.botapix_api_key}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def criar_cobranca_pix(
    identifier: str,
    amount_cents: int,
    notification_url: str | None = None,
) -> dict[str, Any]:
    """Cria cobrança BotaPix.

    O valor é enviado em centavos, como exige a API. A mesma identifier também é
    usada como Idempotency-Key e external_reference, evitando cobranças duplicadas.
    """
    if not settings.botapix_enabled:
        raise BotaPixError("Integração BotaPix está desativada.")
    if amount_cents < 100:
        raise BotaPixError("A BotaPix exige cobrança mínima de R$ 1,00.")

    payload: dict[str, Any] = {
        "amount": int(amount_cents),
        "external_reference": identifier,
        "description": "Depósito Smoke Pay",
        "expiration": max(300, min(int(settings.botapix_charge_expiration), 86400)),
        "metadata": {"app": "smokepay", "canal": "telegram"},
    }

    # A documentação exige Webhook Secret quando notification_url é informado.
    # Só enviamos essa URL quando o segredo estiver configurado. A validação HMAC
    # continuará bloqueada até confirmarmos, na documentação/painel, o formato
    # exato do cabeçalho e da mensagem assinada.
    if notification_url and settings.botapix_webhook_secret:
        payload["notification_url"] = notification_url

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                f"{settings.botapix_base_url}/charges",
                headers=_headers(identifier),
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise BotaPixError("Falha de conexão com a BotaPix.") from exc

    try:
        body = resp.json()
    except ValueError:
        body = {}

    if not resp.is_success:
        code = str(body.get("error") or "") if isinstance(body, dict) else ""
        if resp.status_code == 401:
            message = "API Key BotaPix inválida ou ausente."
        elif resp.status_code == 403 and code == "kyc_required":
            message = "A BotaPix informou que a conta ainda não está liberada para recebimentos (KYC)."
        elif resp.status_code == 422 and code == "webhook_secret_required":
            message = "A BotaPix exige Webhook Secret para usar notification_url."
        elif resp.status_code == 429:
            message = "Limite temporário de requisições da BotaPix atingido."
        else:
            message = f"BotaPix recusou a criação do PIX (HTTP {resp.status_code})."
        logger.warning("BotaPix charge error status=%s code=%s", resp.status_code, code or "-")
        raise BotaPixError(message)

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise BotaPixError("Resposta inválida da BotaPix: campo data ausente.")
    if not data.get("reference") or not data.get("payment_url"):
        raise BotaPixError("Resposta da BotaPix sem reference/payment_url.")
    return data
