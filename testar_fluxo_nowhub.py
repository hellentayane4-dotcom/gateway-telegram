"""Teste local isolado do fluxo NowHub; nunca abre smokepay.db nem chama a API."""
import asyncio
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from dotenv import load_dotenv

import database
import nowhub
import webserver


async def main():
    load_dotenv()
    secret = os.getenv('NOWHUB_WEBHOOK_SECRET')
    if not secret:
        raise SystemExit('ERRO: NOWHUB_WEBHOOK_SECRET ausente no .env')

    with tempfile.TemporaryDirectory(prefix='smokepay_nowhub_teste_') as pasta:
        with patch.object(database, 'BANCO', Path(pasta) / 'teste.db'):
            database.criar_banco()
            database.cadastrar_usuario(987654321, 'Usuario de teste')
            database.criar_deposito(987654321, 'sp_teste_001', 1000, 100, 900, provider='nowhub')
            database.atualizar_deposito_criado('sp_teste_001', 'txn_teste_001', None, None, None, provider='nowhub')
            database.criar_deposito(987654321, 'sp_cancelado', 1000, 100, 900, provider='nowhub')
            database.atualizar_deposito_criado('sp_cancelado', 'txn_cancelado', None, None, None, provider='nowhub')
            database.marcar_deposito_status('sp_cancelado', 'cancelado')

            settings = SimpleNamespace(nowhub_hmac_verified=True, nowhub_webhook_secret=secret, nowhub_hmac_format='sha256-hex')
            transacao = AsyncMock()
            notificar = AsyncMock()
            app = webserver.criar_web_app(bot=None)

            async def enviar(http, event_id, ref, signature_valid=True):
                body = json.dumps({'id': event_id, 'type': 'deposit.updated', 'data': {'transaction_id': ref, 'status': 'COMPLETED'}}, separators=(',', ':')).encode()
                sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if signature_valid else '0' * 64
                return await http.post('/webhooks/nowhub', content=body, headers={'X-Signature': sig, 'Content-Type': 'application/json'})

            with patch.object(webserver, 'settings', settings), patch.object(nowhub.client, 'transaction', transacao), patch.object(webserver, 'notificar_usuario', notificar):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://teste.local') as http:
                    tx = {'transaction_id': 'txn_teste_001', 'external_id': 'sp_teste_001', 'amount': 10.0, 'status': 'PENDING', 'type': 'deposit'}
                    transacao.return_value = tx
                    r = await enviar(http, 'evt_pending', 'txn_teste_001')
                    print('APROVADO' if r.status_code == 200 and database.consultar_saldo(987654321)['disponivel_centavos'] == 0 else 'FALHOU', '- PIX pendente sem credito')

                    tx['status'] = 'COMPLETED'
                    r = await enviar(http, 'evt_pago', 'txn_teste_001')
                    print('APROVADO' if r.status_code == 200 and database.consultar_saldo(987654321)['disponivel_centavos'] == 900 else 'FALHOU', '- PIX confirmado credita R$ 9,00')

                    r = await enviar(http, 'evt_pago_duplicado', 'txn_teste_001')
                    print('APROVADO' if r.status_code == 200 and database.consultar_saldo(987654321)['disponivel_centavos'] == 900 else 'FALHOU', '- Webhook repetido nao duplica credito')

                    r = await enviar(http, 'evt_falso', 'txn_teste_001', False)
                    print('APROVADO' if r.status_code == 401 and database.consultar_saldo(987654321)['disponivel_centavos'] == 900 else 'FALHOU', '- Assinatura falsa rejeitada')

                    transacao.return_value = {'transaction_id': 'txn_cancelado', 'external_id': 'sp_cancelado', 'amount': 10.0, 'status': 'COMPLETED', 'type': 'deposit'}
                    r = await enviar(http, 'evt_cancelado', 'txn_cancelado')
                    print('APROVADO' if database.consultar_saldo(987654321)['disponivel_centavos'] == 900 and r.status_code == 200 else 'FALHOU', '- Cancelado permanece sem credito')

            print('Banco real nao foi aberto. API NowHub nao foi chamada.')


if __name__ == '__main__':
    asyncio.run(main())
