# Etapa 3 — SmokePay

Versão para testes offline. **Não habilite dinheiro real.**

Arquivos revisados: `database.py` e `webserver.py`.

Correções: a confirmação NowHub exige referência de provedor vinculada; o webhook financeiro permanece bloqueado quando `NOWHUB_DEPOSIT_SIMULATION=true`, mesmo com HMAC verificado; saques exigem autorização explícita, referência vinculada e tipo correspondente; saques concluídos não podem ser reabertos pelo registro de envio; valores de depósito são validados.

Execute: `python -m unittest -v testar_etapa3_offline.py`. Usa banco temporário, sem alterar `smokepay.db`.

Atenção: os testes HTTP de integração real com NowHub e recuperação de falhas de rede ainda não foram homologados. Não desative o modo de simulação.
