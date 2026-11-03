"""Testes isolados: nenhum pagamento real ou banco de produção."""
import tempfile
import unittest
from pathlib import Path
import database as db

class TesteSeguranca(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = db.BANCO
        db.BANCO = Path(self.temp.name) / 'teste.db'
        db.criar_banco()
        db.cadastrar_usuario(123456, 'Conta teste')

    def tearDown(self):
        db.BANCO = self.old
        self.temp.cleanup()

    def dep(self):
        db.criar_deposito(123456,'teste-dep',1000,100,900,provider='nowhub')
        db.atualizar_deposito_criado('teste-dep','tx-teste','codigo',None,None)

    def test_credito_uma_vez(self):
        self.dep()
        self.assertEqual(db.confirmar_deposito_pago('teste-dep',1000)[1], 'creditado')
        self.assertEqual(db.confirmar_deposito_pago('teste-dep',1000)[1], 'ja_processado')
        self.assertEqual(db.consultar_saldo(123456)['disponivel_centavos'],900)

    def test_valor_divergente(self):
        self.dep()
        self.assertEqual(db.confirmar_deposito_pago('teste-dep',2000)[1], 'valor_divergente')
        self.assertEqual(db.consultar_saldo(123456)['disponivel_centavos'],0)

    def test_cancelamento_nao_reabre(self):
        self.dep()
        db.marcar_deposito_status('teste-dep','cancelado')
        self.assertEqual(db.confirmar_deposito_pago('teste-dep',1000)[1], 'terminal')

    def test_pago_nao_cancela(self):
        self.dep()
        db.confirmar_deposito_pago('teste-dep',1000)
        self.assertEqual(db.marcar_deposito_status('teste-dep','cancelado')[1],'terminal')
        self.assertEqual(db.consultar_saldo(123456)['disponivel_centavos'],900)

    def test_criacao_nao_reabre_pago(self):
        self.dep()
        db.confirmar_deposito_pago('teste-dep',1000)
        db.atualizar_deposito_criado('teste-dep','tx-outra','codigo',None,None)
        self.assertEqual(db.buscar_deposito_por_referencia('teste-dep')['status'],'pago')

    def test_valores_invalidos(self):
        with self.assertRaises(ValueError):
            db.criar_deposito(123456,'invalido',1000,200,900)

    def test_sem_credito_antes_pendente(self):
        db.criar_deposito(123456,'teste-dep',1000,100,900,provider='nowhub')
        self.assertEqual(db.confirmar_deposito_pago('teste-dep',1000)[1], 'terminal')

    def test_reinicio_persistencia(self):
        self.dep()
        db.confirmar_deposito_pago('teste-dep',1000)
        self.assertEqual(db.confirmar_deposito_pago('tx-teste',1000)[1], 'ja_processado')
        self.assertEqual(db.consultar_saldo(123456)['disponivel_centavos'],900)

if __name__ == '__main__':
    unittest.main()
