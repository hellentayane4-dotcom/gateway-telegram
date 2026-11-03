# SmokePay — deploy na Railway

Esta versão detecta automaticamente:

- `PORT` fornecida pela Railway;
- `RAILWAY_PUBLIC_DOMAIN` para montar o webhook público;
- `RAILWAY_VOLUME_MOUNT_PATH` para armazenar `smokepay.db` de forma persistente.

## Estrutura recomendada

Um serviço Railway chamado `smokepay` + um Volume montado em `/data`.

> Não suba `.env`, `smokepay.db`, backups, tokens ou segredos para o GitHub.

## Primeiro deploy seguro

1. Conecte o repositório GitHub ao serviço Railway.
2. Use `python main.py` como Start Command se a Railway não detectar automaticamente.
3. Anexe um **Volume** ao serviço e use o mount path `/data`.
4. Na aba **Variables**, cadastre as mesmas variáveis do seu `.env`, mas mantenha inicialmente:

```env
NOWHUB_DEPOSIT_SIMULATION=true
WITHDRAWAL_SIMULATION=true
ENABLE_REAL_WITHDRAWALS=false
```

5. Não precisa definir `PORT`, `WEB_PORT` ou `WEB_HOST` na Railway.
6. Gere um domínio em **Settings > Networking > Generate Domain**.
7. Confirme `https://SEU-DOMINIO/health` antes de liberar qualquer operação financeira.

## Migrar o banco atual

O banco atual contém o histórico e saldo da SmokePay. Não comece produção com um banco vazio.

Depois de anexar o Volume em `/data`, use a Railway CLI para enviar **o seu `smokepay.db` local diretamente ao Volume**, nunca ao GitHub:

```powershell
railway login
railway link
railway volume files upload "C:\caminho\para\smokepay.db" /smokepay.db
```

Como o Volume está montado em `/data`, `/smokepay.db` dentro do Volume fica disponível para o app como `/data/smokepay.db`.

Depois reinicie/redeploy o serviço e confira nos logs que o banco abriu normalmente.

## Webhook NowHub

Depois de gerar o domínio Railway, configure na NowHub:

```text
https://SEU-DOMINIO.up.railway.app/webhooks/nowhub
```

Mantenha o mesmo `NOWHUB_WEBHOOK_SECRET` em ambos os lados.

Teste primeiro o webhook/health. Somente depois, com o banco migrado e o bot estável, mude:

```env
NOWHUB_DEPOSIT_SIMULATION=false
```

Saques continuam bloqueados até o teste controlado específico:

```env
WITHDRAWAL_SIMULATION=true
ENABLE_REAL_WITHDRAWALS=false
```

## Corte do notebook para Railway

Antes de deixar a Railway rodar o Telegram em produção, pare `python main.py` no notebook. Duas instâncias usando o mesmo token causam `telegram.error.Conflict` no long polling.

## Healthcheck

A SmokePay expõe:

```text
GET /health
```

Resposta esperada:

```json
{"ok": true, "service": "smokepay"}
```
