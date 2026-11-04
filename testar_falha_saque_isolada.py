"""Teste isolado da rota de falha em saque.py.

Nao importa o bot, nao abre banco, nao acessa Telegram e nao chama a NowHub.
Extrai somente o tratamento da excecao com AST e usa objetos falsos.
Execute na mesma pasta de saque.py com: python testar_falha_saque_isolada.py
"""
import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace


def localizar_tratamento():
    arquivo = Path(__file__).resolve().with_name('saque.py')
    arvore = ast.parse(arquivo.read_text(encoding='utf-8'), filename=str(arquivo))
    funcao = next((n for n in arvore.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'confirmar_saque'), None)
    assert funcao is not None, 'Funcao confirmar_saque nao encontrada'
    candidatos = [n for n in ast.walk(funcao) if isinstance(n, ast.ExceptHandler)
                  and isinstance(n.type, ast.Name) and n.type.id == 'Exception']
    handler = next((n for n in candidatos if any(isinstance(x, ast.Await) and
                   isinstance(x.value, ast.Call) and isinstance(x.value.func, ast.Name) and
                   x.value.func.id == 'editar_painel' for linha in n.body for x in ast.walk(linha))), None)
    assert handler is not None, 'Tratamento de falha do saque nao encontrado'
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and
                   n.func.id == 'liberar_saque' for linha in handler.body for n in ast.walk(linha)), \
           'FALHA: tratamento pode liberar saldo'
    return handler.body


async def executar():
    corpo = localizar_tratamento()
    saidas = []
    class FalsoContexto:
        user_data = {'fluxo': 'saque_processando', 'saque': {'valor': 1000}}

    async def editar_painel(query, texto, teclado=None):
        saidas.append((texto, teclado))

    def liberar_saque(*args, **kwargs):
        raise AssertionError('FALHA: tentou liberar saldo reservado')

    class InlineKeyboardButton:
        def __init__(self, *args, **kwargs):
            pass

    class InlineKeyboardMarkup:
        def __init__(self, *args, **kwargs):
            pass

    teste = ast.AsyncFunctionDef(
        name='executar_handler', args=ast.arguments(posonlyargs=[], args=[], vararg=None,
            kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
        body=corpo, decorator_list=[], returns=None, type_comment=None)
    modulo = ast.fix_missing_locations(ast.Module(body=[teste], type_ignores=[]))
    ambiente = {
        'logger': logging.getLogger('teste_saque_isolado'),
        'identificador': 'REFERENCIA_FICTICIA',
        'context': FalsoContexto(),
        'query': object(),
        'editar_painel': editar_painel,
        'InlineKeyboardButton': InlineKeyboardButton,
        'InlineKeyboardMarkup': InlineKeyboardMarkup,
        'liberar_saque': liberar_saque,
    }
    exec(compile(modulo, '<tratamento_saque_isolado>', 'exec'), ambiente)
    await ambiente['executar_handler']()
    assert ambiente['context'].user_data == {}, 'FALHA: fluxo nao foi limpo'
    assert len(saidas) == 1, 'FALHA: resposta inesperada ao usuario'
    assert 'reservado' in saidas[0][0].lower(), 'FALHA: nao explica que saldo ficou reservado'
    assert 'concilia' in saidas[0][0].lower(), 'FALHA: nao indica conciliacao'
    print('APROVADO - falha simulada manteve reserva (sem chamar liberacao)')
    print('APROVADO - fluxo de saque foi encerrado')
    print('APROVADO - usuario recebe orientacao de conciliacao')
    print('RESULTADO: 3/3 verificacoes aprovadas; sem banco, Telegram ou NowHub')


if __name__ == '__main__':
    asyncio.run(executar())
