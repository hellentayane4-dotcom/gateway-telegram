# SmokePay — versão revisada para testes (02/11/2026)

Esta entrega **não está homologada para transações reais**. Não contém credenciais nem banco de produção.

## Correções
- Banco: impede reabrir depósito pago/erro e impede cancelar depósito já pago sem conciliação.
- Banco: saque só conclui depois de entrar em `processando`; bloqueia segunda conclusão/liberação.
- Webhook NowHub: só processa os eventos `deposit.updated` e `withdraw.updated`; outros eventos são ignorados sem alterar o saldo. Status `RETIDO`/`DISPUTED` são sinalizados para conciliação, sem crédito automático.
- Painel: exibe PIX como SIMULAÇÃO enquanto o modo seguro estiver ativo.

## Instalação no Windows
1. Faça backup da sua pasta original e do banco `smokepay.db`.
2. Extraia o ZIP em uma pasta separada.
3. Copie seu `.env` **localmente** para a nova pasta, sem compartilhá-lo.
4. No PowerShell: `python -m pip install -r requirements.txt`
5. Rode: `python testar_integracao_nowhub_offline.py`, `python testar_seguranca_depositos.py` e `python testar_transicoes_seguras.py`.
6. Inicie: `python main.py` (com o ngrok/webhook configurados se necessário).

## Travas que devem permanecer
```
NOWHUB_HMAC_VERIFIED=false
NOWHUB_DEPOSIT_SIMULATION=true
ENABLE_REAL_WITHDRAWALS=false
NOWHUB_HMAC_FORMAT=sha256-hex
```

Não altere essas travas antes de validar ponta a ponta com ambiente de testes, revisar a conciliação de estados/saques e verificar o webhook de produção. A API real e o Telegram **não foram executados** nesta revisão.

## Pendências antes de produção
- Testar inicialização e botões com a biblioteca `python-telegram-bot` no Windows.
- Testar webhook HMAC com FastAPI e assinatura real em ambiente isolado.
- Implementar conciliação operacional para depósitos retidos/contestados, sem interface de MED ou estorno, e bloquear saques quando houver risco de saldo indisponível.
- Revisar fluxo de payout incerto e regras de saldo negativo/chargebacks com provedor.
- Verificar o QR Code base64 retornado pela NowHub; o PIX copia e cola está implementado.
