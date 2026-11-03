"""Teste do webhook antecipado; usa exclusivamente SQLite temporário e API simulada."""
import unittest
from unittest.mock import patch
from testar_webhooks_controlados import TestarWebhooksControlados
import webserver


class WebhookAntecipado(TestarWebhooksControlados):
    def test_webhook_antes_resposta_api_conclui_uma_vez(self):
        # Volta ao estado anterior à confirmação da resposta HTTP da API.
        with self.db.transacao_imediata() as conn:
            conn.execute("UPDATE saques_api SET provider_withdraw_id=NULL, status='reservado' WHERE identificador=?", (self.ref,))
        with patch.object(webserver, 'vincular_saque_webhook_antecipado', self.db.vincular_saque_webhook_antecipado):
            r1, _ = self.post()
            r2, _ = self.post()
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(r1.json()['result'], 'concluido')
        self.assertEqual(r2.json()['result'], 'ja_processado')
        self.assertEqual(self.db.buscar_saque_por_referencia(self.ref)['status'], 'concluido')
        self.assertEqual(self.saldo()['disponivel_centavos'], 3000)
        self.assertEqual(self.saldo()['reservado_centavos'], 0)
        # Resposta tardia do POST não pode reabrir transação concluída.
        self.db.marcar_saque_enviado(self.ref, self.provider_ref, None)
        self.assertEqual(self.db.buscar_saque_por_referencia(self.ref)['status'], 'concluido')

    def test_webhook_antecipado_com_referencia_errada_bloqueado(self):
        with self.db.transacao_imediata() as conn:
            conn.execute("UPDATE saques_api SET provider_withdraw_id=NULL, status='reservado' WHERE identificador=?", (self.ref,))
        self.tx['amount'] = '68.00'
        with patch.object(webserver, 'vincular_saque_webhook_antecipado', self.db.vincular_saque_webhook_antecipado):
            r, _ = self.post()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.db.buscar_saque_por_referencia(self.ref)['status'], 'reservado')
        self.assertEqual(self.saldo()['reservado_centavos'], 7000)


if __name__ == '__main__':
    unittest.main()
