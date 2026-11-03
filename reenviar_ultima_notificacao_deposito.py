"""Reenvia ao canal a notificação do último depósito NowHub já pago.

Não altera saldo, status, transações ou saques. Use uma única vez se o PIX já foi
creditado antes da correção das notificações.
"""
import asyncio

from telegram import Bot

from config import settings
from database import conectar
from notificacoes import formatar_valor, notificar_canal


async def main():
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN não configurado")
    if not settings.notification_channel_id:
        raise SystemExit("CANAL_NOTIFICACOES_ID não configurado")

    conn = conectar()
    try:
        dep = conn.execute(
            """
            SELECT * FROM depositos_api
            WHERE provider='nowhub' AND status='pago'
            ORDER BY atualizado_em DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    if dep is None:
        raise SystemExit("Nenhum depósito NowHub pago encontrado")

    bot = Bot(token=settings.telegram_bot_token)
    ok = await notificar_canal(
        bot,
        "✅ <b>PIX RECEBIDO</b>\n\n"
        f"🆔 Conta: <code>{str(dep['telegram_id'])[-6:]}</code>\n"
        f"💰 Valor: <b>{formatar_valor(dep['valor_bruto_centavos'])}</b>\n"
        f"🧾 Crédito: {formatar_valor(dep['valor_liquido_centavos'])}",
        imagem="pix_recebido.png",
    )
    print("NOTIFICACAO ENVIADA" if ok else "FALHA AO ENVIAR NOTIFICACAO")


if __name__ == "__main__":
    asyncio.run(main())
