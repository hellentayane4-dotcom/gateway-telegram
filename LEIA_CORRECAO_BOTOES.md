# Correção dos botões Telegram

- Todos os callbacks agora usam a mesma prioridade (grupo 0). O handler genérico de `main.py` não responde novamente a callbacks já processados pelos módulos de depósito, saque ou admin.
- Callbacks expirados são ignorados sem interromper o bot.
- Teste: reinicie o bot, envie `/start` novo e clique em Depositar, Cancelar, Sacar e Voltar. Evite usar botões de mensagens muito antigas.
- Preserve seu `.env` e `smokepay.db` existentes; o ZIP não os contém.
- Depósitos reais e saques reais permanecem desabilitados para testes.
- Revogue o token do bot exposto nos logs e atualize seu `.env` localmente.
