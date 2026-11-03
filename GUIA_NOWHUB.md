## Teste seguro do botão Depositar (sem criar PIX)

Adicione `NOWHUB_DEPOSIT_SIMULATION=true` ao seu `.env` local. É o padrão mesmo se a variável não existir. Inicie o bot normalmente, abra **Depositar**, digite o valor e pressione **SIMULAR PIX**. O bot exibirá um resumo fictício, sem gerar código PIX, sem chamar a NowHub, sem gravar depósito e sem creditar saldo. Não é um teste de integração de cobrança real.

**Não altere** `NOWHUB_DEPOSIT_SIMULATION` para `false` até que o webhook HMAC e a conciliação estejam validados. `ENABLE_REAL_WITHDRAWALS=false` deve permanecer. Para o modo real, a configuração `ready_for_deposits` continua exigindo HTTPS, segredo e formato HMAC verificado.

# SmokePay + NowHub — integração preparada

## Para configurar

1. Instale Python 3.11+ e `pip install -r requirements.txt`.
2. Copie `.env.example` para `.env`, mantendo seu `.env` original em backup se já existir.
3. Preencha `TELEGRAM_BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `NOWHUB_CLIENT_ID`, `NOWHUB_CLIENT_SECRET`, `NOWHUB_WEBHOOK_SECRET` e `PUBLIC_BASE_URL` (HTTPS público, sem barra final). Configure a URL `https://SEU_DOMINIO/webhooks/nowhub` no painel NowHub.
4. **Confirme com o suporte NowHub** a fórmula de `X-Signature`: se é HMAC-SHA256 do corpo bruto em hex, se há prefixo `sha256=`, timestamp, ou outro esquema. A documentação fornecida NÃO especifica esses detalhes. `NOWHUB_HMAC_FORMAT` aceita apenas `sha256-hex` e `sha256-prefix-hex`, após validação com amostra real. Se o esquema for diferente, o código precisará ser ajustado. Só depois configure `NOWHUB_HMAC_VERIFIED=true`.
5. Deixe `ENABLE_REAL_WITHDRAWALS=false` até fazer testes ponta a ponta e conciliação de saldo. Após validação em ambiente controlado, configure `ENABLE_REAL_WITHDRAWALS=true` para liberar saques.
6. Inicie o servidor e o bot conforme os scripts originais. Em desenvolvimento, o ngrok pode oferecer HTTPS, mas em produção use domínio e proxy HTTPS estáveis.

## Funcionalidades

- JWT com renovação antecipada; depósito PIX, consulta de transação e saldo; saque PIX por chave CPF/CNPJ/telefone/email/aleatória.
- Webhook `/webhooks/nowhub` valida HMAC (somente após formato confirmado), consulta o status na API e confere IDs, valor e tipo antes de mexer no saldo.
- Crédito idempotente de depósito, confirmação/liberação idempotente de reserva de saque, reversão de depósito contestado (`DISPUTED`).
- MED: alerta em log para análise manual; **não** há débito automático de MED porque o evento pode representar apenas retenção temporária.
- Saques com resultado de envio incerto preservam a reserva; **não** faça novo envio antes de conciliar.

## Limitações importantes

- Não foi possível testar a API NowHub ao vivo sem credenciais nem confirmar o formato HMAC. A integração não deve ser tratada como produção validada.
- O fluxo legado pede confirmação de IP do usuário para saques; foi mantido para evitar mudanças desnecessárias na experiência, embora a NowHub não o exija no payload.
- Taxas do provedor NowHub e do SmokePay podem divergir: confirme o modelo comercial e a reserva necessária antes de ativar saques reais.
- O código mantém suporte a provedores legados. Guarde o `.env` e o banco original fora do ZIP; nenhum segredo real está incluído.
