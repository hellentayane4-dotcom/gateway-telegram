"""Teste offline do adaptador NowHub. Nao acessa API, banco, Telegram ou .env."""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

BASE = Path(__file__).resolve().parent

def modulo(nome, **atributos):
    m = types.ModuleType(nome)
    for chave, valor in atributos.items():
        setattr(m, chave, valor)
    sys.modules[nome] = m
    return m

async def main():
    modulo('botapix', BotaPixError=type('BotaPixError', (Exception,), {}), criar_cobranca_pix=None)
    modulo('provider_final', ProviderFinalError=type('ProviderFinalError', (Exception,), {}), criar_deposito=None, criar_saque=None)
    modulo('sigilopay', SigiloPayError=type('SigiloPayError', (Exception,), {}), criar_deposito_pix=None, criar_transferencia=None)
    modulo('config', settings=types.SimpleNamespace(deposit_provider='nowhub'))
    fake = AsyncMock()
    modulo('nowhub', client=types.SimpleNamespace(deposit=fake), NowHubError=type('NowHubError', (Exception,), {}))
    spec = importlib.util.spec_from_file_location('payment_providers_test', BASE / 'payment_providers.py')
    adapter = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = adapter
    spec.loader.exec_module(adapter)

    fake.return_value = {'transaction_id': 'txn_offline_001', 'status': 'PENDING', 'pix_copy_paste': 'PIX_FICTICIO_NAO_PAGAVEL'}
    charge = await adapter.create_deposit_charge('teste-offline', 900)
    assert charge.provider == 'nowhub' and charge.reference == 'txn_offline_001'
    assert charge.pix_code == 'PIX_FICTICIO_NAO_PAGAVEL' and charge.status == 'PENDING'
    fake.assert_awaited_once_with('teste-offline', 900)
    print('APROVADO - resposta esperada cria DepositCharge com codigo PIX ficticio')

    fake.return_value = {'transaction_id': 'txn_offline_002', 'status': 'PENDING'}
    try:
        await adapter.create_deposit_charge('teste-offline-2', 900)
    except adapter.PaymentProviderError:
        print('APROVADO - resposta sem codigo PIX rejeitada com erro controlado')
    else:
        raise AssertionError('Resposta sem codigo PIX deveria ser rejeitada')

    fake.return_value = {'id': 'txn_outro_formato', 'pix': {'code': 'PIX_FICTICIO'}}
    try:
        await adapter.create_deposit_charge('teste-offline-3', 900)
    except adapter.PaymentProviderError:
        print('APROVADO - resposta com identificador diferente e rejeitada com erro controlado')
    else:
        raise AssertionError('Resposta sem transaction_id deveria ser rejeitada')

    assert fake.await_count == 3
    print('RESULTADO: 3/3 testes offline aprovados. Nenhuma chamada real a NowHub, Telegram ou banco.')

if __name__ == '__main__':
    asyncio.run(main())
