import logging
from decimal import Decimal
from typing import Any

import httpx

from config import settings

logger = logging.getLogger("smokepay.sigilopay")


class SigiloPayError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    if not settings.sigilopay_public_key or not settings.sigilopay_secret_key:
        raise SigiloPayError("Credenciais da SigiloPay não configuradas.")
    return {
        "x-public-key": settings.sigilopay_public_key,
        "x-secret-key": settings.sigilopay_secret_key,
        "Content-Type": "application/json",
    }


def _money(cents: int) -> float:
    return float((Decimal(cents) / Decimal(100)).quantize(Decimal("0.01")))


async def ping() -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.sigilopay_base_url}/ping")
            return resp.is_success
    except httpx.HTTPError:
        return False


async def criar_deposito_pix(identifier: str, amount_cents: int, callback_url: str) -> dict[str, Any]:
    if not settings.sigilopay_enabled:
        raise SigiloPayError("Integração SigiloPay desativada.")
    payload = {
        "identifier": identifier,
        "amount": _money(amount_cents),
        "callbackUrl": callback_url,
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                f"{settings.sigilopay_base_url}/gateway/pix/deposit",
                headers=_headers(),
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise SigiloPayError("Falha de conexão com a SigiloPay.") from exc

    if not resp.is_success:
        logger.warning("SigiloPay depósito recusado HTTP %s", resp.status_code)
        raise SigiloPayError(f"SigiloPay recusou a criação do PIX (HTTP {resp.status_code}).")
    try:
        data = resp.json()
    except ValueError as exc:
        raise SigiloPayError("Resposta inválida da SigiloPay.") from exc
    if not data.get("transactionId"):
        raise SigiloPayError("SigiloPay não retornou transactionId.")
    return data


async def criar_transferencia(
    *,
    identifier: str,
    amount_cents: int,
    pix_type: str,
    pix_key: str,
    owner_ip: str,
    owner_name: str,
    document_type: str,
    document_number: str,
    callback_url: str,
) -> dict[str, Any]:
    if not settings.ready_for_withdrawals:
        raise SigiloPayError("Saques reais estão desativados.")
    payload = {
        "identifier": identifier,
        "amount": _money(amount_cents),
        "discountFeeOfReceiver": settings.discount_fee_of_receiver,
        "pix": {"type": pix_type, "key": pix_key},
        "owner": {
            "ip": owner_ip,
            "name": owner_name,
            "document": {"type": document_type, "number": document_number},
        },
        "callbackUrl": callback_url,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.sigilopay_base_url}/gateway/transfers",
                headers=_headers(),
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise SigiloPayError("Falha de conexão ao solicitar a transferência.") from exc

    if not resp.is_success:
        logger.warning("SigiloPay transferência recusada HTTP %s", resp.status_code)
        try:
            body = resp.json()
            message = str(body.get("message") or body.get("errorCode") or "erro da API")
        except ValueError:
            message = "erro da API"
        raise SigiloPayError(f"Transferência recusada pela SigiloPay: {message}")
    try:
        return resp.json()
    except ValueError as exc:
        raise SigiloPayError("Resposta inválida da SigiloPay na transferência.") from exc
