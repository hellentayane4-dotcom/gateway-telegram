"""Testes isolados da trava persistente de saque (sem rede e sem banco real)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import database


class TestarTravaPersistente(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='smokepay_trava_')
        self.patcher = patch.object(database, 'BANCO', Path(self.tmp.name) / 'teste.db')
        self.patcher.start()
        database.criar_banco()
        database.cadastrar_usuario(90001, 'Teste')
        with database.transacao_imediata() as conn:
            conn.execute('UPDATE saldos SET disponivel_centavos=10000 WHERE telegram_id=90001')

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def reservar(self, ref):
        return database.criar_saque_reservando(90001, ref, 2000, 100, 1900, 'cpf', '***')

    def test_segundo_saque_bloqueado_e_saldo_preservado(self):
        self.assertEqual(self.reservar('primeiro')[1], 'reservado')
        self.assertEqual(self.reservar('segundo')[1], 'saque_pendente')
        self.assertEqual(database.consultar_saldo(90001), {'disponivel_centavos':8000,'reservado_centavos':2000})

    def test_reinicio_logico_mantem_bloqueio(self):
        self.reservar('primeiro')
        database.marcar_saque_enviado('primeiro', 'provider-1', None)
        self.assertEqual(self.reservar('segundo')[1], 'saque_pendente')

    def test_saque_terminal_libera_nova_solicitacao(self):
        self.reservar('primeiro')
        database.marcar_saque_enviado('primeiro', 'provider-1', None)
        self.assertEqual(database.concluir_saque('primeiro')[1], 'concluido')
        self.assertEqual(self.reservar('segundo')[1], 'reservado')

    def test_falha_confirmada_libera_nova_solicitacao(self):
        self.reservar('primeiro')
        self.assertEqual(database.liberar_saque('primeiro', 'falha confirmada')[1], 'liberado')
        self.assertEqual(self.reservar('segundo')[1], 'reservado')


if __name__ == '__main__':
    unittest.main()
