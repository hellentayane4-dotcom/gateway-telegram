"""Testes isolados de webhook NowHub. Não usam rede nem smokepay.db."""
import asyncio
from contextlib import closing
import hashlib
import hmac
import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import nowhub
try:
    import telegram
except ImportError:
    # Apenas para ambientes de teste sem python-telegram-bot instalado.
    import sys, types
    telegram = types.ModuleType('telegram')
    telegram.Bot = type('Bot', (), {})
    constants = types.ModuleType('telegram.constants')
    constants.ParseMode = type('ParseMode', (), {'HTML': 'HTML'})
    errors = types.ModuleType('telegram.error')
    errors.TelegramError = type('TelegramError', (Exception,), {})
    sys.modules.update({'telegram': telegram, 'telegram.constants': constants, 'telegram.error': errors})
import webserver

HERE = Path(__file__).resolve().parent

class TestarWebhooksControlados(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='smokepay_webhook_')
        spec = importlib.util.spec_from_file_location('db_webhook_isolado', HERE / 'database.py')
        self.db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.db)
        self.db.BANCO = Path(self.tmp.name) / 'somente_teste.db'
        self.db.criar_banco()
        self.user = 87654321
        self.ref = 'teste-webhook-saque-01'
        self.provider_ref = 'nowhub-transacao-teste-01'
        self.db.cadastrar_usuario(self.user, 'Teste webhook')
        with self.db.transacao_imediata() as c:
            c.execute('UPDATE saldos SET disponivel_centavos=10000 WHERE telegram_id=?', (self.user,))
        _, status = self.db.criar_saque_reservando(self.user, self.ref, 7000, 100, 6900, 'evp', '***')
        self.assertEqual(status, 'reservado')
        self.db.marcar_saque_enviado(self.ref, self.provider_ref, None)
        self.tx = {'transaction_id': self.provider_ref, 'external_id': self.ref,
                   'amount': '69.00', 'type': 'withdrawal', 'status': 'COMPLETED'}
        self.event = {'type': 'withdraw.updated', 'data': {'transaction_id': self.provider_ref}}
        self.secret = 'segredo-exclusivo-do-teste'
        self.bot = object()
        self.app = webserver.criar_web_app(self.bot)

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, event=None, *, sign=True, simulation=False, verified=True, enabled=True):
        raw = json.dumps(event or self.event, separators=(',', ':')).encode()
        signature = hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest() if sign else 'invalida'
        cfg = replace(webserver.settings, nowhub_webhook_secret=self.secret,
                      nowhub_hmac_format='sha256-hex', nowhub_hmac_verified=verified,
                      enable_real_withdrawals=enabled)
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url='http://test.local') as c:
                return await c.post('/webhooks/nowhub', content=raw, headers={'X-Signature': signature})
        with patch.object(webserver, 'settings', cfg), \
             patch.dict('os.environ', {'NOWHUB_DEPOSIT_SIMULATION': 'true' if simulation else 'false'}), \
             patch.object(nowhub.client, 'transaction', new_callable=AsyncMock, return_value=self.tx.copy()) as lookup, \
             patch.object(webserver, 'buscar_saque_por_referencia', self.db.buscar_saque_por_referencia), \
             patch.object(webserver, 'concluir_saque', self.db.concluir_saque), \
             patch.object(webserver, 'liberar_saque', self.db.liberar_saque):
            response = asyncio.run(send())
        return response, lookup.await_count

    def saldo(self):
        return self.db.consultar_saldo(self.user)

    def test_assinatura_invalida_401_sem_consulta(self):
        r, n = self.post(sign=False)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(n, 0)
        self.assertEqual(self.saldo()['reservado_centavos'], 7000)

    def test_simulacao_bloqueia_mesmo_com_hmac_valido(self):
        r, n = self.post(simulation=True)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(n, 0)
        self.assertEqual(self.saldo()['reservado_centavos'], 7000)

    def test_hmac_nao_verificado_bloqueia(self):
        r, n = self.post(verified=False)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(n, 0)

    def test_saque_real_desativado_bloqueia(self):
        r, n = self.post(enabled=False)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(n, 1)
        self.assertEqual(self.saldo()['reservado_centavos'], 7000)

    def test_webhook_repetido_nao_duplica_conclusao(self):
        r1, _ = self.post()
        r2, _ = self.post()
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(self.saldo()['disponivel_centavos'], 3000)
        self.assertEqual(self.saldo()['reservado_centavos'], 0)
        self.assertEqual(self.db.buscar_saque_por_referencia(self.ref)['status'], 'concluido')
        with closing(self.db.conectar()) as c:
            n = c.execute("SELECT COUNT(*) FROM movimentos_saldo WHERE tipo='saque_concluido'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_webhook_atrasado_falha_nao_devolve_concluido(self):
        r, _ = self.post()
        self.assertEqual(r.status_code, 200)
        self.tx['status'] = 'FAILED'
        r, _ = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.saldo()['disponivel_centavos'], 3000)
        self.assertEqual(self.saldo()['reservado_centavos'], 0)
        self.assertEqual(self.db.buscar_saque_por_referencia(self.ref)['status'], 'concluido')

    def test_tipo_divergente_nao_altera_saldo(self):
        self.tx['type'] = 'deposit'
        r, _ = self.post()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.saldo()['reservado_centavos'], 7000)

    def test_valor_divergente_nao_altera_saldo(self):
        self.tx['amount'] = '68.00'
        r, _ = self.post()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.saldo()['reservado_centavos'], 7000)

if __name__ == '__main__':
    unittest.main(verbosity=2)
