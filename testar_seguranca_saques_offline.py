"""Testes isolados: nunca abre smokepay.db do projeto."""
import importlib.util
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ARQUIVO = Path(__file__).with_name('database.py')

class TestesSaques(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='smokepay_saques_')
        spec = importlib.util.spec_from_file_location('smokepay_database_teste', ARQUIVO)
        self.db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.db)
        self.db.BANCO = Path(self.tmp.name) / 'banco_teste.db'
        self.db.criar_banco()
        self.user = 87654321
        self.db.cadastrar_usuario(self.user, 'Usuario Teste')
        with self.db.transacao_imediata() as c:
            c.execute('UPDATE saldos SET disponivel_centavos=10000 WHERE telegram_id=?', (self.user,))
            c.execute("INSERT INTO movimentos_saldo (telegram_id,tipo,valor_centavos,referencia) VALUES (?,'saldo_teste',10000,'teste:saldo')", (self.user,))

    def tearDown(self):
        self.tmp.cleanup()

    def reservar(self, ref, valor=7000):
        return self.db.criar_saque_reservando(self.user, ref, valor, 100, valor-100, 'cpf', '***')

    def saldo(self):
        return self.db.consultar_saldo(self.user)

    def test_01_saldo_insuficiente(self):
        self.assertEqual(self.reservar('saque-grande', 20000)[1], 'saldo_insuficiente')
        self.assertEqual(self.saldo(), {'disponivel_centavos':10000,'reservado_centavos':0})

    def test_02_reserva_correta(self):
        self.assertEqual(self.reservar('saque-1')[1], 'reservado')
        self.assertEqual(self.saldo(), {'disponivel_centavos':3000,'reservado_centavos':7000})

    def test_03_concorrencia_sem_saldo_duplo(self):
        barrier = threading.Barrier(2)
        def executar(i):
            barrier.wait(timeout=5)
            return self.reservar(f'concorrente-{i}')[1]
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(executar, (1,2)))
        self.assertCountEqual(resultados, ['reservado','saldo_insuficiente'])
        self.assertEqual(self.saldo(), {'disponivel_centavos':3000,'reservado_centavos':7000})

    def test_04_conclusao_uma_vez(self):
        self.reservar('saque-1')
        self.db.marcar_saque_enviado('saque-1','wd-1',None)
        self.assertEqual(self.db.concluir_saque('saque-1')[1], 'concluido')
        self.assertEqual(self.db.concluir_saque('saque-1')[1], 'ja_processado')
        self.assertEqual(self.saldo(), {'disponivel_centavos':3000,'reservado_centavos':0})

    def test_05_falha_libera_uma_vez(self):
        self.reservar('saque-1')
        self.db.marcar_saque_enviado('saque-1','wd-1',None)
        self.assertEqual(self.db.liberar_saque('saque-1','teste')[1], 'liberado')
        self.assertEqual(self.db.liberar_saque('saque-1','teste')[1], 'ja_processado')
        self.assertEqual(self.saldo(), {'disponivel_centavos':10000,'reservado_centavos':0})

    def test_06_conclusao_apos_falha_bloqueada(self):
        self.reservar('saque-1')
        self.db.liberar_saque('saque-1','teste')
        self.assertEqual(self.db.concluir_saque('saque-1')[1], 'terminal')
        self.assertEqual(self.saldo(), {'disponivel_centavos':10000,'reservado_centavos':0})

    def test_07_falha_apos_conclusao_bloqueada(self):
        self.reservar('saque-1')
        self.db.marcar_saque_enviado('saque-1','wd-1',None)
        self.db.concluir_saque('saque-1')
        self.assertEqual(self.db.liberar_saque('saque-1','teste')[1], 'terminal')
        self.assertEqual(self.saldo(), {'disponivel_centavos':3000,'reservado_centavos':0})

    def test_08_nao_concluir_antes_de_envio(self):
        self.reservar('saque-1')
        self.assertEqual(self.db.concluir_saque('saque-1')[1], 'terminal')
        self.assertEqual(self.saldo(), {'disponivel_centavos':3000,'reservado_centavos':7000})

    def test_09_envio_nao_reabre_terminal(self):
        self.reservar('saque-1')
        self.db.liberar_saque('saque-1','teste')
        self.db.marcar_saque_enviado('saque-1','wd-1',None)
        self.assertEqual(self.db.buscar_saque_por_referencia('saque-1')['status'], 'falhou')
        self.assertEqual(self.saldo(), {'disponivel_centavos':10000,'reservado_centavos':0})

if __name__ == '__main__':
    unittest.main(verbosity=2)
