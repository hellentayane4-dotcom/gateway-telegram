from dataclasses import dataclass

from botapix import BotaPixError, criar_cobranca_pix
from config import settings
from provider_final import ProviderFinalError, criar_deposito as criar_deposito_final, criar_saque as criar_saque_final
from sigilopay import SigiloPayError, criar_deposito_pix, criar_transferencia


class PaymentProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class DepositCharge:
    provider: str
    reference: str
    status: str
    payment_url: str | None = None
    pix_code: str | None = None
    pix_image_url: str | None = None
    expires_at: str | None = None


async def create_deposit_charge(identifier: str, amount_cents: int) -> DepositCharge:
    provider = settings.deposit_provider
    if provider == "nowhub":
        from nowhub import client, NowHubError
        try:
            data = await client.deposit(identifier, amount_cents)
            ref = str(data["transaction_id"])
            if not ref or ref == "None":
                raise NowHubError("Resposta sem transaction_id")
            return DepositCharge(provider="nowhub", reference=ref,
                status=str(data.get("status") or "PENDING"),
                pix_code=data.get("pix_copy_paste"),
                pix_image_url=None)
        except (NowHubError, KeyError, TypeError) as exc:
            raise PaymentProviderError("Falha ao criar depósito NowHub; confira o status antes de repetir") from exc
    if provider == "botapix":
        try:
            data = await criar_cobranca_pix(identifier, amount_cents, settings.botapix_webhook_url or None)
        except BotaPixError as exc:
            raise PaymentProviderError(str(exc)) from exc
        return DepositCharge(
            provider="botapix",
            reference=str(data["reference"]),
            status=str(data.get("status") or "pending"),
            payment_url=str(data.get("payment_url") or "") or None,
            expires_at=str(data.get("expires_at") or "") or None,
        )

    if provider == "sigilopay":
        try:
            data = await criar_deposito_pix(identifier, amount_cents, settings.sigilopay_webhook_url)
        except SigiloPayError as exc:
            raise PaymentProviderError(str(exc)) from exc
        pix = data.get("pix") or {}
        return DepositCharge(
            provider="sigilopay",
            reference=str(data["transactionId"]),
            status=str(data.get("status") or "pending"),
            pix_code=pix.get("code"),
            pix_image_url=pix.get("image"),
            expires_at=pix.get("expiresAt"),
        )

    if provider in {"final", "custom", "provider"}:
        try:
            data = await criar_deposito_final(identifier, amount_cents, f"{settings.public_base_url}/webhooks/provider" if settings.public_base_url else None)
        except ProviderFinalError as exc:
            raise PaymentProviderError(str(exc)) from exc
        return DepositCharge(
            provider="final",
            reference=str(data["reference"]),
            status=str(data.get("status") or "pending"),
            payment_url=data.get("payment_url"),
            pix_code=data.get("pix_code"),
            pix_image_url=data.get("pix_image_url"),
            expires_at=data.get("expires_at"),
        )

    raise PaymentProviderError("Nenhum provedor de depósito está ativo.")


async def create_withdrawal(**kwargs):
    provider = settings.withdrawal_provider
    if provider == "nowhub":
        from nowhub import client, NowHubError
        try:
            return await client.withdraw(kwargs['identifier'], kwargs['amount_cents'],
                kwargs['pix_key'], kwargs['pix_type'], kwargs['owner_name'], kwargs['document_number'])
        except NowHubError as exc:
            raise PaymentProviderError(str(exc)) from exc
    if provider == "sigilopay":
        try:
            return await criar_transferencia(**kwargs)
        except SigiloPayError as exc:
            raise PaymentProviderError(str(exc)) from exc
    if provider in {"final", "custom", "provider"}:
        try:
            return await criar_saque_final(**kwargs)
        except ProviderFinalError as exc:
            raise PaymentProviderError(str(exc)) from exc
    raise PaymentProviderError("Nenhum provedor de saque está ativo.")
