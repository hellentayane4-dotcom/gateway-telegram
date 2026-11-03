"""Testes isolados de deposito e saque; sem rede, Telegram ou banco real."""
import tempfile
from pathlib import Path
from unittest.mock import patch
import database


def executar():
    with tempfile.TemporaryDirectory(prefix='smokepay_transicoes_') as pasta:
        with patch.object(database, 'BANCO', Path(pasta) / 'isolado.db'):
            database.criar_banco()
            usuario = 123456789
            database.cadastrar_usuario(usuario, 'Teste')
            database.criar_deposito(usuario, 'dep1', 1000, 100, 900, provider='nowhub')
            database.atualizar_deposito_criado('dep1', 'txn1', 'PIX_FICTICIO', None, None)
            assert database.confirmar_deposito_pago('dep1', 1000)[1] == 'creditado'
            assert database.marcar_deposito_status('dep1', 'cancelado')[1] == 'terminal'
            database.atualizar_deposito_criado('dep1', 'txn2', 'PIX_FICTICIO', None, None)
            dep = database.buscar_deposito_por_referencia('dep1')
            assert dep['status'] == 'pago' and dep['provider_transaction_id'] == 'txn1'
            assert database.consultar_saldo(usuario)['disponivel_centavos'] == 900
            print('APROVADO - deposito pago nao e reaberto nem cancelado')
            database.criar_deposito(usuario, 'dep2', 1000, 100, 900, provider='nowhub')
            assert database.marcar_deposito_status('dep2', 'cancelado')[1] == 'terminal'  # criando nao vira cancelado
            database.marcar_deposito_erro('dep2', 'erro simulado')
            database.atualizar_deposito_criado('dep2', 'txn2', None, None, None)
            assert database.buscar_deposito_por_referencia('dep2')['status'] == 'erro'
            print('APROVADO - deposito com erro nao e reaberto')
            saque_id, result = database.criar_saque_reservando(usuario, 'saq1', 500, 50, 450, 'evp', '***')
            assert saque_id and result == 'reservado'
            assert database.concluir_saque('saq1')[1] == 'terminal'
            assert database.consultar_saldo(usuario) == {'disponivel_centavos': 400, 'reservado_centavos': 500}
            database.marcar_saque_enviado('saq1', 'wd1', None)
            assert database.concluir_saque('saq1')[1] == 'concluido'
            assert database.concluir_saque('saq1')[1] == 'ja_processado'
            assert database.liberar_saque('saq1')[1] == 'terminal'
            assert database.consultar_saldo(usuario) == {'disponivel_centavos': 400, 'reservado_centavos': 0}
            print('APROVADO - saque so conclui apos envio e nao duplica movimentos')
            saque_id, result = database.criar_saque_reservando(usuario, 'saq2', 200, 20, 180, 'evp', '***')
            assert result == 'reservado'
            assert database.liberar_saque('saq2')[1] == 'liberado'
            assert database.liberar_saque('saq2')[1] == 'ja_processado'
            assert database.concluir_saque('saq2')[1] == 'terminal'
            assert database.consultar_saldo(usuario) == {'disponivel_centavos': 400, 'reservado_centavos': 0}
            print('APROVADO - falha de saque libera reserva apenas uma vez')
    print('RESULTADO: 4/4 testes aprovados. Banco real nao foi aberto.')

if __name__ == '__main__':
    executar()
