from callback_seguro import responder_callback
import asyncio
import logging
from datetime import datetime

import uvicorn
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from admin import registrar_handlers_admin
from config import settings
from database import (
    cadastrar_usuario,
    codigo_afiliado,
    config_bool,
    consultar_saldo,
    criar_banco,
    listar_depositos,
    listar_saques,
    resumo_financeiro,
    resumo_mensal,
    resumo_rede,
    vincular_indicacao,
    verificar_integridade,
)
from deposito import registrar_handlers_deposito
from notificacoes import formatar_valor
from saque import registrar_handlers_saque
from ui import abreviar, editar_painel, enviar_painel, esc
from webserver import criar_web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# Impede que URLs com o token do Telegram apareçam nos logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger("smokepay")

def menu_principal() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💠 DEPOSITAR", callback_data="depositar"), InlineKeyboardButton("💸 SACAR", callback_data="sacar")],
        [InlineKeyboardButton("💼 CARTEIRA", callback_data="carteira"), InlineKeyboardButton("🌐 MINHA REDE", callback_data="rede")],
        [InlineKeyboardButton("📋 HISTÓRICO", callback_data="historico"), InlineKeyboardButton("💳 TAXAS", callback_data="taxas")],
        [InlineKeyboardButton("🟢 STATUS", callback_data="status"), InlineKeyboardButton("🎧 SUPORTE", callback_data="suporte")],
        [InlineKeyboardButton("🔄 ATUALIZAR", callback_data="menu")],
    ])


def voltar_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLTAR", callback_data="menu")]])


def _status_pix() -> str:
    from deposito import _modo_simulacao
    if _modo_simulacao():
        return "🧪 SIMULAÇÃO"
    if not config_bool("deposits_enabled", True):
        return "🔴 DESATIVADO"
    return "🟢 ONLINE" if settings.ready_for_deposits else "🟡 AGUARDANDO API"


def _status_saque() -> str:
    if not config_bool("withdrawals_enabled", False):
        return "⚪ DESATIVADO"
    return "🟢 ONLINE" if settings.ready_for_withdrawals else "🟡 AGUARDANDO API"


def _tela_inicial(usuario_id: int, nome: str) -> str:
    saldo = consultar_saldo(usuario_id)
    manutencao = config_bool("maintenance_mode", False)
    sistema = "🟠 MANUTENÇÃO" if manutencao else "🟢 ONLINE"
    return (
        "🏦 <b>SMOKE PAY — SUA GATEWAY DIGITAL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Olá, <b>{esc(nome)}</b>!\n"
        f"🆔 Conta: <code>{abreviar(usuario_id, 4, 3)}</code>\n"
        f"💰 <b>Saldo: {formatar_valor(saldo['disponivel_centavos'])}</b>\n\n"
        f"⚙️ Sistema: <b>{sistema}</b>\n"
        f"🔐 Segurança: <b>ATIVA</b>\n"
        f"💠 Pix: <b>{_status_pix()}</b>\n"
        f"💸 Saques: <b>{_status_saque()}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>Escolha uma opção:</b>"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    cadastrar_usuario(user.id, user.first_name or "Cliente", user.username)
    if context.args:
        vincular_indicacao(user.id, context.args[0])
    nome = user.username or user.first_name or "Cliente"
    await enviar_painel(update, _tela_inicial(user.id, nome), menu_principal(), "home.png")


async def comando_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    cadastrar_usuario(user.id, user.first_name or "Cliente", user.username)
    saldo = consultar_saldo(user.id)
    await update.message.reply_text(
        f"💼 <b>CARTEIRA SMOKE PAY</b>\n\n"
        f"💰 Disponível: <b>{formatar_valor(saldo['disponivel_centavos'])}</b>\n"
        f"🔒 Reservado: <b>{formatar_valor(saldo['reservado_centavos'])}</b>",
        parse_mode="HTML",
    )


def _linha_dep(row) -> str:
    icone = {
        "pago": "✅", "pendente": "⏳", "criando": "⏳", "cancelado": "❌",
        "estornado": "↩️", "chargeback": "⚠️", "erro": "❌"
    }.get(row["status"], "•")
    ref = row["provider_transaction_id"] or row["identificador"]
    return f"{icone} <b>{formatar_valor(row['valor_bruto_centavos'])}</b> · <code>{abreviar(ref)}</code> · {esc(row['status'].upper())}"


def _linha_saque(row) -> str:
    icone = {"concluido": "✅", "processando": "⏳", "reservado": "🔒", "falhou": "❌"}.get(row["status"], "•")
    ref = row["provider_withdraw_id"] or row["identificador"]
    return f"{icone} <b>{formatar_valor(row['valor_solicitado_centavos'])}</b> · <code>{abreviar(ref)}</code> · {esc(row['status'].upper())}"


async def botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not await responder_callback(query):
        return
    user = query.from_user
    cadastrar_usuario(user.id, user.first_name or "Cliente", user.username)

    if query.data == "menu":
        context.user_data.clear()
        nome = user.username or user.first_name or "Cliente"
        await editar_painel(query, _tela_inicial(user.id, nome), menu_principal())
        return

    if query.data == "carteira":
        saldo = consultar_saldo(user.id)
        resumo = resumo_financeiro(user.id)
        meses = resumo_mensal(user.id)
        mensal = ""
        if meses:
            linhas = []
            nomes = {"01":"Jan", "02":"Fev", "03":"Mar", "04":"Abr", "05":"Mai", "06":"Jun", "07":"Jul", "08":"Ago", "09":"Set", "10":"Out", "11":"Nov", "12":"Dez"}
            for r in meses[:4]:
                ano, mes = str(r["mes"]).split("-") if r["mes"] else ("", "")
                linhas.append(f"{nomes.get(mes, mes)}/{ano[-2:]}  📥 {formatar_valor(int(r['depositos'] or 0))}  📤 {formatar_valor(int(r['saques'] or 0))}")
            mensal = "\n\n📅 <b>Resumo recente</b>\n" + "\n".join(linhas)
        texto = (
            "💼 <b>SUA CARTEIRA DIGITAL</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Saldo: <b>{formatar_valor(saldo['disponivel_centavos'])}</b>\n"
            f"🔒 Reservado: {formatar_valor(saldo['reservado_centavos'])}\n\n"
            "📊 <b>Resumo financeiro</b>\n"
            f"📥 Depositado: {formatar_valor(resumo['depositado_centavos'])}\n"
            f"📤 Sacado: {formatar_valor(resumo['sacado_centavos'])}\n"
            f"🤝 Comissões: {formatar_valor(resumo['comissoes_centavos'])}\n"
            f"⏳ Pendências: {resumo['pendentes']}"
            f"{mensal}"
        )
        await editar_painel(query, texto, InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 HISTÓRICO", callback_data="historico"), InlineKeyboardButton("🌐 AFILIADOS", callback_data="rede")],
            [InlineKeyboardButton("💳 VER TAXAS", callback_data="taxas")],
            [InlineKeyboardButton("⬅️ VOLTAR", callback_data="menu")],
        ]))
        return

    if query.data == "historico":
        deps = listar_depositos(user.id, 6)
        saqs = listar_saques(user.id, 6)
        dep_text = "\n".join(_linha_dep(r) for r in deps) if deps else "Nenhum depósito registrado."
        saq_text = "\n".join(_linha_saque(r) for r in saqs) if saqs else "Nenhum saque registrado."
        texto = (
            "📋 <b>HISTÓRICO</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📥 <b>Depósitos recentes</b>\n{dep_text}\n\n"
            f"📤 <b>Saques recentes</b>\n{saq_text}"
        )
        await editar_painel(query, texto, voltar_menu())
        return

    if query.data == "rede":
        rede = resumo_rede(user.id)
        codigo = codigo_afiliado(user.id)
        bot_username = context.bot.username or "SmokePayBot"
        convite = f"https://t.me/{bot_username}?start={codigo}"
        niveis = "\n".join(f"{n}º nível: <b>{rede['niveis'].get(n, 0)}</b>" for n in range(1, 6))
        pct = settings.referral_commission_bp / 100
        texto = (
            "🌐 <b>MINHA REDE SMOKE PAY</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Rede total: <b>{rede['total']}</b>\n"
            f"💵 Comissões: <b>{formatar_valor(rede['comissao_centavos'])}</b>\n"
            f"⚙️ Comissão direta atual: <b>{pct:.2f}%</b>\n\n"
            f"📊 <b>Distribuição</b>\n{niveis}\n\n"
            "🔗 <b>Seu link de convite</b>\n"
            f"<code>{esc(convite)}</code>\n\n"
            "Compartilhe seu link. A comissão só é creditada quando a regra estiver ativada pelo administrador."
        )
        await editar_painel(query, texto, voltar_menu())
        return

    if query.data == "taxas":
        dep_pct = settings.deposit_fee_percent_bp / 100
        saq_pct = settings.withdrawal_fee_percent_bp / 100
        texto = (
            "💳 <b>TAXAS E LIMITES</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "💠 <b>Depósitos</b>\n"
            f"• Mínimo: {formatar_valor(settings.deposit_min_cents)}\n"
            f"• Máximo: {formatar_valor(settings.deposit_max_cents)}\n"
            f"• Taxa: {dep_pct:.2f}% + {formatar_valor(settings.deposit_fee_fixed_cents)}\n\n"
            "💸 <b>Saques</b>\n"
            f"• Mínimo: {formatar_valor(settings.withdrawal_min_cents)}\n"
            f"• Taxa: {saq_pct:.2f}% + {formatar_valor(settings.withdrawal_fee_fixed_cents)}\n\n"
            "ℹ️ As regras podem ser ajustadas antes da integração financeira definitiva."
        )
        await editar_painel(query, texto, voltar_menu())
        return

    if query.data == "status":
        manutencao = config_bool("maintenance_mode", False)
        texto = (
            "🟢 <b>STATUS DO SISTEMA</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🤖 Bot: <b>{'🟠 MANUTENÇÃO' if manutencao else '🟢 ONLINE'}</b>\n"
            f"💠 PIX: <b>{_status_pix()}</b>\n"
            f"💸 Saques: <b>{_status_saque()}</b>\n"
            f"💼 Carteira: <b>🟢 ONLINE</b>\n"
            f"🔐 Banco: <b>{'🟢 ÍNTEGRO' if verificar_integridade() else '🔴 ERRO'}</b>\n\n"
            f"🕒 Atualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        await editar_painel(query, texto, voltar_menu())
        return

    if query.data == "suporte":
        suporte = settings.support_username
        teclado = [[InlineKeyboardButton("💬 ABRIR SUPORTE", url=f"https://t.me/{suporte}")]] if suporte else []
        teclado.append([InlineKeyboardButton("⬅️ VOLTAR", callback_data="menu")])
        await editar_painel(
            query,
            "🎧 <b>SUPORTE SMOKE PAY</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "Precisa de ajuda com sua conta ou uma transação?\n\n"
            f"Atendimento: <b>@{esc(suporte)}</b>" if suporte else "🎧 <b>SUPORTE SMOKE PAY</b>\n\nCanal de suporte ainda não configurado.",
            InlineKeyboardMarkup(teclado),
        )
        return


async def configurar_menu(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "🏠 Início"),
        BotCommand("saldo", "💰 Consultar saldo"),
        BotCommand("painel", "🛡️ Painel administrativo"),
        BotCommand("statusapi", "🔌 Status das integrações"),
    ])


async def executar():
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado no .env")
    criar_banco()
    if not verificar_integridade():
        raise RuntimeError("Falha na verificação de integridade do banco SQLite")

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start), group=0)
    app.add_handler(CommandHandler("saldo", comando_saldo), group=0)
    registrar_handlers_deposito(app)
    registrar_handlers_saque(app)
    registrar_handlers_admin(app)
    app.add_handler(CallbackQueryHandler(botoes), group=0)

    web_app = criar_web_app(app.bot)
    server = uvicorn.Server(uvicorn.Config(web_app, host=settings.web_host, port=settings.web_port, log_level="info"))

    await app.initialize()
    await configurar_menu(app)
    await app.start()
    if app.updater is None:
        raise RuntimeError("Updater do Telegram não disponível")
    await app.updater.start_polling(drop_pending_updates=False)
    logger.info("SMOKE PAY ONLINE | web=http://%s:%s", settings.web_host, settings.web_port)
    from conciliacao_nowhub import executar_conciliacao_periodica
    tarefa_conciliacao = asyncio.create_task(executar_conciliacao_periodica(app.bot))
    try:
        await server.serve()
    finally:
        tarefa_conciliacao.cancel()
        try:
            await tarefa_conciliacao
        except asyncio.CancelledError:
            pass
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    asyncio.run(executar())


if __name__ == "__main__":
    main()
