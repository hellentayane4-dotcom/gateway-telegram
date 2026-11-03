import logging
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config import settings
from database import config_bool, criar_deposito, marcar_deposito_erro, atualizar_deposito_criado
from notificacoes import formatar_valor, notificar_canal
from payment_providers import PaymentProviderError, create_deposit_charge
from ui import abreviar, editar_painel, enviar_painel, esc

logger = logging.getLogger("smokepay.deposito")


def _parse_centavos(texto: str) -> int:
    s = texto.strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    valor = Decimal(s)
    if not valor.is_finite() or valor <= 0:
        raise InvalidOperation
    return int((valor * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _calcular_taxa(centavos: int) -> tuple[int, int]:
    percentual = (centavos * settings.deposit_fee_percent_bp + 5000) // 10000
    taxa = percentual + settings.deposit_fee_fixed_cents
    return taxa, centavos - taxa


async def iniciar_deposito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if config_bool("maintenance_mode", False) or not config_bool("deposits_enabled", True):
        await editar_painel(
            query,
            "💠 <b>DEPÓSITOS TEMPORARIAMENTE INDISPONÍVEIS</b>\n\nO sistema está em manutenção. Tente novamente mais tarde.",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ VOLTAR", callback_data="menu")]]),
        )
        return
    context.user_data.clear()
    context.user_data["fluxo"] = "deposito_valor"
    await editar_painel(
        query,
        "💠 <b>DEPÓSITO VIA PIX</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Informações</b>\n"
        f"• Mínimo: <b>{formatar_valor(settings.deposit_min_cents)}</b>\n"
        f"• Máximo: <b>{formatar_valor(settings.deposit_max_cents)}</b>\n"
        "• Use ponto ou vírgula para decimais.\n\n"
        "🧾 Exemplo: <code>50,00</code>\n\n"
        "👇 <b>Digite o valor que deseja depositar:</b>",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCELAR", callback_data="cancelar_deposito")]]),
    )


async def cancelar_deposito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await editar_painel(
        query,
        "❌ <b>DEPÓSITO CANCELADO</b>\n\nNenhuma cobrança foi criada e nenhum saldo foi alterado.",
        InlineKeyboardMarkup([[InlineKeyboardButton("🏠 MENU", callback_data="menu")]]),
    )


async def receber_texto_deposito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("fluxo") != "deposito_valor" or not update.message or not update.message.text:
        return
    try:
        bruto = _parse_centavos(update.message.text)
    except (InvalidOperation, ValueError):
        await update.message.reply_text("⚠️ <b>Valor inválido.</b>\nExemplo: <code>50,00</code>", parse_mode="HTML")
        return
    if not settings.deposit_min_cents <= bruto <= settings.deposit_max_cents:
        await update.message.reply_text(
            f"⚠️ Digite um valor entre <b>{formatar_valor(settings.deposit_min_cents)}</b> e <b>{formatar_valor(settings.deposit_max_cents)}</b>.",
            parse_mode="HTML",
        )
        return
    taxa, liquido = _calcular_taxa(bruto)
    if liquido <= 0:
        await update.message.reply_text("⚠️ O valor informado não cobre as taxas.")
        return
    context.user_data["deposito"] = {"bruto": bruto, "taxa": taxa, "liquido": liquido}
    context.user_data["fluxo"] = "deposito_confirmacao"
    await update.message.reply_text(
        "🧾 <b>RESUMO DO DEPÓSITO</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Valor: <b>{formatar_valor(bruto)}</b>\n"
        f"💳 Taxas: {formatar_valor(taxa)}\n"
        f"✅ Crédito previsto: <b>{formatar_valor(liquido)}</b>\n\n"
        "🔐 O saldo só será liberado após confirmação válida do provedor.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ GERAR PIX", callback_data="confirmar_deposito")],
            [InlineKeyboardButton("❌ CANCELAR", callback_data="cancelar_deposito")],
        ]),
    )


async def confirmar_deposito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dados = context.user_data.get("deposito")
    if not dados or context.user_data.get("fluxo") != "deposito_confirmacao":
        await editar_painel(query, "⚠️ Esta solicitação expirou. Inicie um novo depósito.", InlineKeyboardMarkup([[InlineKeyboardButton("🏠 MENU", callback_data="menu")]]))
        return

    if not settings.ready_for_deposits:
        context.user_data.clear()
        await editar_painel(
            query,
            "💠 <b>PIX AINDA NÃO CONECTADO</b>\n\n"
            "A interface do depósito está pronta, mas a <b>API financeira definitiva ainda não foi ativada</b>.\n\n"
            "Nenhuma cobrança foi criada e nenhum saldo foi alterado.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🏠 MENU", callback_data="menu")]]),
        )
        return

    telegram_id = query.from_user.id
    identificador = f"smokepay-dep-{telegram_id}-{uuid.uuid4().hex[:16]}"
    criar_deposito(
        telegram_id, identificador, dados["bruto"], dados["taxa"], dados["liquido"], provider=settings.deposit_provider
    )
    try:
        cobranca = await create_deposit_charge(identificador, dados["bruto"])
        atualizar_deposito_criado(
            identificador,
            cobranca.reference,
            cobranca.pix_code,
            cobranca.pix_image_url,
            cobranca.expires_at,
            payment_url=cobranca.payment_url,
            provider=cobranca.provider,
        )
    except PaymentProviderError as exc:
        marcar_deposito_erro(identificador, str(exc))
        logger.warning("Falha ao criar depósito provider=%s: %s", settings.deposit_provider, exc)
        context.user_data.clear()
        await editar_painel(
            query,
            f"⚠️ <b>NÃO FOI POSSÍVEL GERAR O PIX</b>\n\n{esc(exc)}\n\nNenhum saldo foi alterado.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🏠 MENU", callback_data="menu")]]),
        )
        return

    ref = cobranca.reference
    caption = (
        "🏦 <b>PIX GERADO COM SUCESSO</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Valor: <b>{formatar_valor(dados['bruto'])}</b>\n"
        f"🆔 ID: <code>{esc(abreviar(ref, 8, 6))}</code>\n"
        "⏳ Status: <b>AGUARDANDO PAGAMENTO</b>\n\n"
        "✅ Após a confirmação segura, o saldo será atualizado automaticamente."
    )
    botoes = []
    if cobranca.payment_url:
        botoes.append([InlineKeyboardButton("💠 ABRIR PAGAMENTO PIX", url=cobranca.payment_url)])
    botoes.append([InlineKeyboardButton("🏠 MENU", callback_data="menu")])

    await enviar_painel(update, caption, InlineKeyboardMarkup(botoes), "pix_gerado.png")
    if cobranca.pix_image_url:
        try:
            await context.bot.send_photo(chat_id=telegram_id, photo=cobranca.pix_image_url, caption="QR Code PIX — Smoke Pay")
        except Exception:
            logger.exception("Falha ao enviar QR Code")
    if cobranca.pix_code:
        await context.bot.send_message(
            chat_id=telegram_id,
            text=f"💠 <b>PIX COPIA E COLA</b>\n\n<code>{esc(cobranca.pix_code)}</code>",
            parse_mode="HTML",
        )

    await notificar_canal(
        context.bot,
        "💠 <b>PIX GERADO</b>\n\n"
        f"💰 Valor: <b>{formatar_valor(dados['bruto'])}</b>\n"
        f"🆔 ID: <code>{esc(abreviar(ref, 8, 6))}</code>\n"
        "⏳ Status: <b>PENDENTE</b>",
        imagem="pix_gerado.png",
    )
    context.user_data.clear()


def registrar_handlers_deposito(app: Application):
    app.add_handler(CallbackQueryHandler(iniciar_deposito, pattern="^depositar$"), group=0)
    app.add_handler(CallbackQueryHandler(cancelar_deposito, pattern="^cancelar_deposito$"), group=0)
    app.add_handler(CallbackQueryHandler(confirmar_deposito, pattern="^confirmar_deposito$"), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto_deposito), group=1)
