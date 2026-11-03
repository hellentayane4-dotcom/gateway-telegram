"""Testes do handler real com mocks; não envia PIX nem usa banco de produção."""
import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import saque
from config import settings
from payment_providers import PaymentProviderError
from nowhub import NowHubUncertainError

class TestarHandlerSaqueControlado(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_data = {'fluxo':'saque_confirmacao', 'saque':{
            'valor':7000,'taxa':200,'envio':6800,'pix_type':'cpf',
            'pix_key':'12345678901','owner_ip':'192.0.2.10',
            'owner_name':'Teste Seguro','document_type':'cpf',
            'document_number':'12345678901'}}
        self.query=SimpleNamespace(from_user=SimpleNamespace(id=42))
        self.update=SimpleNamespace(callback_query=self.query)
        self.ctx=SimpleNamespace(user_data=self.user_data,bot=object())
        self.patches=[
            patch.object(saque,'settings',replace(settings,withdrawal_provider='nowhub',
                enable_real_withdrawals=True,nowhub_client_id='fake',nowhub_client_secret='fake',
                public_base_url='https://example.invalid',nowhub_webhook_secret='fake',
                nowhub_hmac_verified=True,nowhub_hmac_format='sha256-hex')),
            patch.object(saque,'_simulacao_saque',return_value=False),
            patch.object(saque,'config_bool',return_value=True),
            patch.object(saque,'responder_callback',new_callable=AsyncMock,return_value=True),
            patch.object(saque,'editar_painel',new_callable=AsyncMock),
            patch.object(saque,'notificar_canal',new_callable=AsyncMock),
            patch.object(saque,'criar_saque_reservando',return_value=(1,'reservado')),
            patch.object(saque,'marcar_saque_enviado'),
            patch.object(saque,'liberar_saque'),
            patch.object(saque,'consumir_token_ip'),
            patch.object(saque,'create_withdrawal',new_callable=AsyncMock,return_value={'transaction_id':'provider-test-1'})
        ]
        self.mocks=[p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.reserve=self.mocks[6];self.mark=self.mocks[7];self.release=self.mocks[8];self.provider=self.mocks[10]

    async def test_sucesso_reserva_uma_vez_e_url_correta(self):
        await saque.confirmar_saque(self.update,self.ctx)
        self.reserve.assert_called_once();self.provider.assert_awaited_once()
        self.assertEqual(self.provider.await_args.kwargs['callback_url'],'https://example.invalid/webhooks/nowhub')
        self.mark.assert_called_once();self.release.assert_not_called()
        self.assertFalse(self.user_data)

    async def test_duplo_clique_enquanto_api_aguarda(self):
        started=asyncio.Event(); finish=asyncio.Event()
        async def pending(**kwargs):
            started.set();await finish.wait();return {'transaction_id':'provider-test-1'}
        self.provider.side_effect=pending
        first=asyncio.create_task(saque.confirmar_saque(self.update,self.ctx))
        await started.wait()
        await saque.confirmar_saque(self.update,self.ctx)
        finish.set();await first
        self.reserve.assert_called_once();self.provider.assert_awaited_once()

    async def test_timeout_incerto_mantem_reserva(self):
        self.provider.side_effect=PaymentProviderError('incerto')
        self.provider.side_effect.__cause__=NowHubUncertainError('timeout')
        await saque.confirmar_saque(self.update,self.ctx)
        self.release.assert_not_called();self.mark.assert_not_called();self.assertFalse(self.user_data)

    async def test_resposta_sem_id_mantem_reserva(self):
        self.provider.return_value={}
        await saque.confirmar_saque(self.update,self.ctx)
        self.release.assert_not_called();self.mark.assert_not_called()

    async def test_notificacao_falha_nao_reenvia(self):
        self.mocks[5].side_effect=RuntimeError('canal indisponivel')
        await saque.confirmar_saque(self.update,self.ctx)
        self.provider.assert_awaited_once();self.release.assert_not_called()
        self.assertFalse(self.user_data)

    async def test_bloqueio_configuracao_nao_reserva(self):
        self.mocks[2].return_value=False
        await saque.confirmar_saque(self.update,self.ctx)
        self.reserve.assert_not_called();self.provider.assert_not_awaited()

if __name__=='__main__':unittest.main()
