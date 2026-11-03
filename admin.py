from callback_seguro import responder_callback
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import settings
from database import config_bool, definir_config, estatisticas_admin, verificar_integridade
from notificacoes import formatar_valor
from ui import editar_painel


def _admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and settings.admin_telegram_id and user.id == settings.admin_telegram_id)


def _teclado_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 ATUALIZAR", callback_data="admin_resumo")],
        [InlineKeyboardButton("🛠 MANUTENÇÃO", callback_data="admin_toggle_maintenance")],
        [InlineKeyboardButton("💠 DEPÓSITOS ON/OFF", callback_data="admin_toggle_deposits")],
        [InlineKeyboardButton("💸 SAQUES ON/OFF", callback_data="admin_toggle_withdrawals")],
    ])


def _texto_admin() -> str:
    s = estatisticas_admin()
    manut = config_bool("maintenance_mode", False)
    deps = config_bool("deposits_enabled", True)
    saqs = config_bool("withdrawals_enabled", False)
    return (
        "🛡️ <b>PAINEL ADMIN — SMOKE PAY</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Usuários: <b>{s['usuarios']}</b>\n"
        f"💰 Saldos: <b>{formatar_valor(s['saldo_centavos'])}</b>\n"
        f"🔒 Reservado: {formatar_valor(s['reservado_centavos'])}\n\n"
        f"📥 Volume depósitos: <b>{formatar_valor(s['volume_dep_centavos'])}</b>\n"
        f"📤 Volume saques: <b>{formatar_valor(s['volume_saq_centavos'])}</b>\n"
        f"⏳ Depósitos pendentes: {s['depositos_pendentes']}\n"
        f"⏳ Saques pendentes: {s['saques_pendentes']}\n\n"
        f"🤖 Manutenção: <b>{'ON' if manut else 'OFF'}</b>\n"
        f"💠 Depósitos: <b>{'ON' if deps else 'OFF'}</b>\n"
        f"💸 Saques: <b>{'ON' if saqs else 'OFF'}</b>\n"
        f"🔐 Banco: <b>{'OK' if verificar_integridade() else 'ERRO'}</b>"
    )


async def painel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin(update):
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return
    await update.message.reply_text(_texto_admin(), parse_mode="HTML", reply_markup=_teclado_admin())


async def status_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin(update):
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return
    await update.message.reply_text(
        "🔌 <b>INTEGRAÇÕES</b>\n\n"
        f"📥 Provedor depósito: <b>{settings.deposit_provider.upper()}</b>\n"
        f"📥 Pronto: <b>{'SIM' if settings.ready_for_deposits else 'NÃO'}</b>\n"
        f"📤 Provedor saque: <b>{settings.withdrawal_provider.upper()}</b>\n"
        f"📤 Pronto: <b>{'SIM' if settings.ready_for_withdrawals else 'NÃO'}</b>\n\n"
        "As chaves secretas nunca são exibidas neste painel.",
        parse_mode="HTML",
    )


async def admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not _admin(update):
        await query.answer("Acesso não autorizado.", show_alert=True)
        return
    if not await responder_callback(query):
        return
    if query.data == "admin_toggle_maintenance":
        definir_config("maintenance_mode", "0" if config_bool("maintenance_mode", False) else "1")
    elif query.data == "admin_toggle_deposits":
        definir_config("deposits_enabled", "0" if config_bool("deposits_enabled", True) else "1")
    elif query.data == "admin_toggle_withdrawals":
        definir_config("withdrawals_enabled", "0" if config_bool("withdrawals_enabled", False) else "1")
    await editar_painel(query, _texto_admin(), _teclado_admin())


def registrar_handlers_admin(app: Application):
    app.add_handler(CommandHandler("painel", painel), group=0)
    app.add_handler(CommandHandler("statusapi", status_api), group=0)
    app.add_handler(CallbackQueryHandler(admin_callbacks, pattern="^admin_"), group=0)
