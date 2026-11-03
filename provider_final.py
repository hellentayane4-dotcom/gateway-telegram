"""Adaptador reservado para a API financeira definitiva da Smoke Pay.

Quando a documentação chegar, este é o arquivo principal a ser preenchido.
O restante do bot não precisa ser reconstruído.
"""
from typing import Any


class ProviderFinalError(RuntimeError):
    pass


async def criar_deposito(identifier: str, amount_cents: int, callback_url: str | None) -> dict[str, Any]:
    raise ProviderFinalError("API definitiva de depósito ainda não configurada.")


async def criar_saque(**kwargs) -> dict[str, Any]:
    raise ProviderFinalError("API definitiva de saque ainda não configurada.")
