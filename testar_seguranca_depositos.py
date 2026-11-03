"""Testes locais do banco SmokePay. Nao acessa API, Telegram ou banco real."""
import tempfile
from pathlib import Path
from unittest.mock import patch

import database


def checar(nome, condicao):
    print(f"{'APROVADO' if condicao else 'FALHOU'} - {nome}")
    return bool(condicao)


def executar():
    resultados = []
    with tempfile.TemporaryDirectory(prefix="smokepay_teste_") as pasta:
        with patch.object(database, "BANCO", Path(pasta) / "teste_isolado.db"):
            database.criar_banco()
            usuario = 987654321
            database.cadastrar_usuario(usuario, "Usuario de teste")
            ref = "teste_deposito_seguro"
            database.criar_deposito(usuario, ref, 1000, 289, 711, provider="nowhub")
            database.atualizar_deposito_criado(ref, "txn_teste_isolado", None, None, None)

            resultados.append(checar("PIX pendente nao credita", database.consultar_saldo(usuario)["disponivel_centavos"] == 0))
            _, estado = database.confirmar_deposito_pago(ref, 999)
            resultados.append(checar("Valor divergente rejeitado", estado == "valor_divergente" and database.consultar_saldo(usuario)["disponivel_centavos"] == 0))
            _, estado = database.confirmar_deposito_pago("transacao_inexistente", 1000)
            resultados.append(checar("Transacao desconhecida rejeitada", estado == "nao_encontrado" and database.consultar_saldo(usuario)["disponivel_centavos"] == 0))
            _, estado = database.confirmar_deposito_pago(ref, 1000)
            resultados.append(checar("Confirmacao credita R$ 7,11", estado == "creditado" and database.consultar_saldo(usuario)["disponivel_centavos"] == 711))
            _, estado = database.confirmar_deposito_pago(ref, 1000)
            resultados.append(checar("Confirmacao duplicada nao credita novamente", estado == "ja_processado" and database.consultar_saldo(usuario)["disponivel_centavos"] == 711))

            ref_cancelada = "teste_cancelado"
            database.criar_deposito(usuario, ref_cancelada, 1000, 289, 711, provider="nowhub")
            database.marcar_deposito_status(ref_cancelada, "cancelado")
            saldo_antes = database.consultar_saldo(usuario)["disponivel_centavos"]
            _, estado = database.confirmar_deposito_pago(ref_cancelada, 1000)
            saldo_depois = database.consultar_saldo(usuario)["disponivel_centavos"]
            resultados.append(checar("Deposito cancelado permanece bloqueado", saldo_depois == saldo_antes))

    print("\nRESUMO:", sum(resultados), "de", len(resultados), "testes aprovados")
    print("O banco real nao foi aberto nem modificado por este script.")
    if not all(resultados):
        print("ATENCAO: nao habilite processamento financeiro em producao.")


if __name__ == "__main__":
    executar()
