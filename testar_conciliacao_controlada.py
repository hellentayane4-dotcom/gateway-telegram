"""Testes offline de saldo e conciliação; NÃO conecta à API e NÃO usa smokepay.db."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class ConciliacaoControlada(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='smokepay_conciliacao_')
        spec = importlib.util.spec_from_file_location('db_conciliacao_isolada', ROOT / 'database.py')
        self.db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.db)
        self.db.BANCO = Path(self.tmp.name) / 'somente_teste.db'
        self.db.criar_banco()
        self.user = 998877661
        self.db.cadastrar_usuario(self.user, 'Teste')

    def tearDown(self):
        self.tmp.cleanup()

    def saldo(self):
        s = self.db.consultar_saldo(self.user)
        return s['disponivel_centavos'], s['reservado_centavos']

    def deposito(self, ref='dep-teste'):
        self.db.criar_deposito(self.user, ref, 10000, 200, 9800, provider='nowhub')
        self.db.atualizar_deposito_criado(ref, 'provedor-'+ref, None, None, None)

    def saque(self, ref='saq-teste'):
        with self.db.transacao_imediata() as conn:
            conn.execute('UPDATE saldos SET disponivel_centavos=10000 WHERE telegram_id=?', (self.user,))
        _, status = self.db.criar_saque_reservando(self.user, ref, 7000, 100, 6900, 'evp', '***')
        self.assertEqual(status, 'reservado')

    def test_deposito_duplicado_credite_uma_vez(self):
        self.deposito()
        self.assertEqual(self.db.confirmar_deposito_pago('dep-teste', 10000)[1], 'creditado')
        self.assertEqual(self.db.confirmar_deposito_pago('dep-teste', 10000)[1], 'ja_processado')
        self.assertEqual(self.saldo(), (9800, 0))

    def test_deposito_valor_divergente_nao_credite(self):
        self.deposito()
        self.assertEqual(self.db.confirmar_deposito_pago('dep-teste', 9999)[1], 'valor_divergente')
        self.assertEqual(self.saldo(), (0, 0))

    def test_saque_duplicado_nao_debita_duas_vezes(self):
        self.saque()
        self.db.marcar_saque_enviado('saq-teste', 'prov-saq-teste', None)
        self.assertEqual(self.db.concluir_saque('saq-teste')[1], 'concluido')
        self.assertEqual(self.db.concluir_saque('saq-teste')[1], 'ja_processado')
        self.assertEqual(self.db.liberar_saque('saq-teste')[1], 'terminal')
        self.assertEqual(self.saldo(), (3000, 0))

    def test_saque_falhou_e_webhook_tardio_nao_debita(self):
        self.saque()
        self.db.marcar_saque_enviado('saq-teste', 'prov-saq-teste', None)
        self.assertEqual(self.db.liberar_saque('saq-teste')[1], 'liberado')
        self.assertEqual(self.db.concluir_saque('saq-teste')[1], 'terminal')
        self.assertEqual(self.saldo(), (10000, 0))

    def test_evento_antes_de_registrar_id_precisa_conciliacao(self):
        self.saque()
        # Documenta a lacuna atual: a conclusão antecipada NÃO é aplicada.
        self.assertEqual(self.db.concluir_saque('saq-teste')[1], 'terminal')
        self.assertEqual(self.saldo(), (3000, 7000))
        self.db.marcar_saque_enviado('saq-teste', 'prov-saq-teste', None)
        self.assertEqual(self.db.buscar_saque_por_referencia('saq-teste')['status'], 'processando')
        # Sem um novo webhook/conciliação, o saque permaneceria processando.

if __name__ == '__main__':
    unittest.main(verbosity=2)
