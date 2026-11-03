from callback_seguro import responder_callback
import ipaddress
import logging
import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from config import settings
from database import (
    config_bool,
    consultar_ip_capturado,
    consultar_saldo,
    consumir_token_ip,
    criar_saque_reservando,
    criar_token_captura_ip,
    liberar_saque,
    marcar_saque_enviado,
)
from notificacoes import formatar_valor, notificar_canal
from ui import editar_painel
from payment_providers import PaymentProviderError, create_withdrawal

logger = logging.getLogger("smokepay.saque")


def _parse_centavos(texto: str) -> int:
    s = texto.strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    valor = Decimal(s)
    if not valor.is_finite() or valor <= 0:
        raise InvalidOperation
    return int((valor * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _calcular_taxa(centavos: int) -> tuple[int, int]:
    percentual = (centavos * settings.withdrawal_fee_percent_bp + 5000) // 10000
    taxa = percentual + settings.withdrawal_fee_fixed_cents
    return taxa, centavos - taxa


def _normalizar_nome(nome: str) -> str:
    texto = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^A-Za-z ]+", "", texto)
    return re.sub(r"\s+", " ", texto).strip()[:100]


def _mascarar(chave: str) -> str:
    if len(chave) <= 6:
        return "*" * len(chave)
    return f"{chave[:3]}{'*' * min(12, len(chave)-6)}{chave[-3:]}"


async def abrir_saque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await responder_callback(query):
        return
    if config_bool("maintenance_mode", False) or (not config_bool("withdrawals_enabled", False)) or (not settings.ready_for_withdrawals):
        context.user_data.clear()
        await editar_painel(query, 
            "💸 <b>CENTRAL DE SAQUES</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            "⚪ Saques: <b>AGUARDANDO API</b>\n\n"
            "A estrutura de saldo, reserva e confirmação já está pronta. Quando o provedor definitivo de payout for conectado, este menu será liberado sem refazer o bot.",
            teclado=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]),
        )
        return
    saldo = consultar_saldo(query.from_user.id)["disponivel_centavos"]
    context.user_data.clear()
    context.user_data["fluxo"] = "saque_valor"
    await editar_painel(query, 
        "💜 SMOKE PAY — SAQUE PIX\n\n"
        f"Saldo disponível: {formatar_valor(saldo)}\n"
        f"Saque mínimo: {formatar_valor(settings.withdrawal_min_cents)}\n\n"
        "Digite o valor que deseja sacar:",
        teclado=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_saque")]]),
    )


async def cancelar_saque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await responder_callback(query):
        return
    context.user_data.clear()
    await editar_painel(query, 
        "Saque cancelado.",
        teclado=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]),
    )


async def receber_texto_saque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fluxo = context.user_data.get("fluxo")
    if not fluxo or not fluxo.startswith("saque_") or not update.message or not update.message.text:
        return
    texto = update.message.text.strip()

    if fluxo == "saque_valor":
        try:
            valor = _parse_centavos(texto)
        except (InvalidOperation, ValueError):
            await update.message.reply_text("❌ Valor inválido. Exemplo: 50,00")
            return
        if valor < settings.withdrawal_min_cents:
            await update.message.reply_text(f"❌ O saque mínimo é {formatar_valor(settings.withdrawal_min_cents)}.")
            return
        saldo = consultar_saldo(update.effective_user.id)["disponivel_centavos"]
        if valor > saldo:
            await update.message.reply_text("❌ Saldo insuficiente.")
            return
        taxa, envio = _calcular_taxa(valor)
        if envio <= 0:
            await update.message.reply_text("❌ O valor não cobre as taxas.")
            return
        context.user_data["saque"] = {"valor": valor, "taxa": taxa, "envio": envio}
        context.user_data["fluxo"] = "saque_tipo_pix"
        await update.message.reply_text(
            "Escolha o tipo da chave PIX:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("CPF", callback_data="saque_pix_cpf"), InlineKeyboardButton("CNPJ", callback_data="saque_pix_cnpj")],
                [InlineKeyboardButton("Telefone", callback_data="saque_pix_phone"), InlineKeyboardButton("Aleatória / e-mail", callback_data="saque_pix_key")],
                [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_saque")],
            ]),
        )
        return

    if fluxo == "saque_chave":
        if len(texto) < 3 or len(texto) > 180:
            await update.message.reply_text("❌ Chave PIX inválida.")
            return
        context.user_data["saque"]["pix_key"] = texto
        context.user_data["fluxo"] = "saque_nome"
        await update.message.reply_text("Digite o nome completo do titular da chave PIX:")
        return

    if fluxo == "saque_nome":
        nome = _normalizar_nome(texto)
        if len(nome) < 3:
            await update.message.reply_text("❌ Nome inválido. Digite o nome completo.")
            return
        context.user_data["saque"]["owner_name"] = nome
        context.user_data["fluxo"] = "saque_tipo_documento"
        await update.message.reply_text(
            "Documento do titular:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("CPF", callback_data="saque_doc_cpf"), InlineKeyboardButton("CNPJ", callback_data="saque_doc_cnpj")],
                [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_saque")],
            ]),
        )
        return

    if fluxo == "saque_documento":
        numero = re.sub(r"\D", "", texto)
        tipo = context.user_data["saque"].get("document_type")
        esperado = 11 if tipo == "cpf" else 14
        if len(numero) != esperado:
            await update.message.reply_text(f"❌ {tipo.upper()} inválido. Digite somente os {esperado} números.")
            return
        context.user_data["saque"]["document_number"] = numero
        if not settings.public_base_url:
            await update.message.reply_text(
                "⚠️ Falta configurar a URL pública do Smoke Pay. O saque não será enviado enquanto isso."
            )
            context.user_data.clear()
            return
        token = criar_token_captura_ip(update.effective_user.id)
        context.user_data["saque"]["ip_token"] = token
        context.user_data["fluxo"] = "saque_ip"
        url = f"{settings.public_base_url}/capturar-ip?token={token}"
        await update.message.reply_text(
            "O provedor de saque configurado exige o IPv4 do solicitante.\n\n"
            "Toque no botão abaixo pelo aparelho/rede que está solicitando o saque. O Smoke Pay captura somente o IP necessário para esta solicitação e o token expira em 15 minutos.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Confirmar meu IP", url=url)],
                [InlineKeyboardButton("✅ Já confirmei", callback_data="saque_verificar_ip")],
                [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_saque")],
            ]),
        )


async def escolher_tipo_pix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await responder_callback(query):
        return
    mapa = {
        "saque_pix_cpf": "cpf",
        "saque_pix_cnpj": "cnpj",
        "saque_pix_phone": "phone",
        "saque_pix_key": "key",
    }
    if context.user_data.get("fluxo") != "saque_tipo_pix":
        return
    context.user_data["saque"]["pix_type"] = mapa[query.data]
    context.user_data["fluxo"] = "saque_chave"
    await editar_painel(query, "Digite a chave PIX que receberá a transferência:")


async def escolher_documento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await responder_callback(query):
        return
    if context.user_data.get("fluxo") != "saque_tipo_documento":
        return
    context.user_data["saque"]["document_type"] = "cpf" if query.data.endswith("cpf") else "cnpj"
    context.user_data["fluxo"] = "saque_documento"
    await editar_painel(query, f"Digite somente os números do {context.user_data['saque']['document_type'].upper()}:")


async def verificar_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await responder_callback(query):
        return
    dados = context.user_data.get("saque") or {}
    token = dados.get("ip_token")
    if context.user_data.get("fluxo") != "saque_ip" or not token:
        await editar_painel(query, "Esta solicitação expirou. Inicie um novo saque.")
        return
    ip = consultar_ip_capturado(token, query.from_user.id)
    if not ip:
        await query.message.reply_text("⚠️ IP ainda não foi capturado. Abra primeiro o botão Confirmar meu IP.")
        return
    try:
        ipaddress.IPv4Address(ip)
    except ipaddress.AddressValueError:
        await editar_painel(query, "⚠️ Foi detectado um endereço que não é IPv4. Tente novamente em uma rede com IPv4.")
        return
    dados["owner_ip"] = ip
    context.user_data["fluxo"] = "saque_confirmacao"
    await editar_painel(query, 
        "💜 CONFIRMAR SAQUE\n\n"
        f"Valor solicitado: {formatar_valor(dados['valor'])}\n"
        f"Taxas Smoke Pay: {formatar_valor(dados['taxa'])}\n"
        f"Valor enviado pela transferência: {formatar_valor(dados['envio'])}\n"
        f"Chave PIX: {_mascarar(dados['pix_key'])}\n\n"
        "Ao confirmar, o valor solicitado será reservado até o provedor concluir ou falhar a transferência.",
        teclado=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirmar saque", callback_data="saque_confirmar")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_saque")],
        ]),
    )


async def confirmar_saque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await responder_callback(query):
        return
    dados = context.user_data.get("saque") or {}
    if context.user_data.get("fluxo") != "saque_confirmacao":
        await editar_painel(query, "Esta solicitação expirou. Inicie um novo saque.")
        return
    if not settings.ready_for_withdrawals:
        await editar_painel(query, 
            "⚠️ Saques reais ainda estão desativados na configuração de segurança. Nenhum valor foi reservado ou enviado.",
            teclado=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]),
        )
        context.user_data.clear()
        return

    telegram_id = query.from_user.id
    identificador = f"smokepay-out-{telegram_id}-{uuid.uuid4().hex[:16]}"
    saque_id, status = criar_saque_reservando(
        telegram_id,
        identificador,
        dados["valor"],
        dados["taxa"],
        dados["envio"],
        dados["pix_type"],
        _mascarar(dados["pix_key"]),
    )
    if status != "reservado":
        await editar_painel(query, "❌ Saldo insuficiente ou alterado. Nenhum saque foi enviado.")
        context.user_data.clear()
        return
    try:
        resposta = await create_withdrawal(
            identifier=identificador,
            amount_cents=dados["envio"],
            pix_type=dados["pix_type"],
            pix_key=dados["pix_key"],
            owner_ip=dados["owner_ip"],
            owner_name=dados["owner_name"],
            document_type=dados["document_type"],
            document_number=dados["document_number"],
            callback_url=settings.webhook_url,
        )
        if settings.withdrawal_provider == "nowhub" and not resposta.get("transaction_id"):
            raise PaymentProviderError("Saque enviado, mas resposta sem ID: conciliação manual necessária")
        withdraw = resposta.get("withdraw") or {}
        provider_id = resposta.get("transaction_id") if settings.withdrawal_provider == "nowhub" else withdraw.get("id")
        marcar_saque_enviado(
            identificador,
            str(provider_id) if provider_id else None,
            resposta.get("receiptUrl"),
            str(resposta.get("webhookToken")) if resposta.get("webhookToken") else None,
        )
    except PaymentProviderError as exc:
        # An HTTP timeout can mean NowHub accepted the payout. Never refund
        # an uncertain payout automatically: it requires reconciliation.
        from nowhub import NowHubUncertainError
        if settings.withdrawal_provider == "nowhub" and (isinstance(exc.__cause__, NowHubUncertainError) or "conciliação manual" in str(exc)):
            logger.error("Saque incerto, reserva mantida: %s", identificador)
            await editar_painel(query, "⚠️ Saque aguardando conciliação. Valor reservado; não tente novamente. Contate o suporte.")
            context.user_data.clear()
            return
        liberar_saque(identificador, str(exc))
        logger.warning("Transferência não iniciada: %s", exc)
        await editar_painel(query, 
            "⚠️ A transferência não foi iniciada. O valor reservado foi devolvido ao saldo.",
            teclado=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]),
        )
        if dados.get("ip_token"):
            consumir_token_ip(dados["ip_token"], telegram_id)
        context.user_data.clear()
        return

    if dados.get("ip_token"):
        consumir_token_ip(dados["ip_token"], telegram_id)
    await editar_painel(query, 
        "✅ Saque enviado para processamento.\n\n"
        f"Valor solicitado: {formatar_valor(dados['valor'])}\n"
        f"Valor da transferência: {formatar_valor(dados['envio'])}\n"
        "Status: PROCESSANDO\n\n"
        "Se o provedor informar falha terminal, a reserva será liberada automaticamente uma única vez.",
        teclado=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu")]]),
    )
    await notificar_canal(
        context.bot,
        f"💜 SMOKE PAY | SAQUE\n\nUsuário: {telegram_id}\nSolicitado: {formatar_valor(dados['valor'])}\nEnvio: {formatar_valor(dados['envio'])}\nStatus: PROCESSANDO",
    )
    context.user_data.clear()


def registrar_handlers_saque(app: Application):
    app.add_handler(CallbackQueryHandler(abrir_saque, pattern="^sacar$"), group=0)
    app.add_handler(CallbackQueryHandler(cancelar_saque, pattern="^cancelar_saque$"), group=0)
    app.add_handler(CallbackQueryHandler(escolher_tipo_pix, pattern="^saque_pix_(cpf|cnpj|phone|key)$"), group=0)
    app.add_handler(CallbackQueryHandler(escolher_documento, pattern="^saque_doc_(cpf|cnpj)$"), group=0)
    app.add_handler(CallbackQueryHandler(verificar_ip, pattern="^saque_verificar_ip$"), group=0)
    app.add_handler(CallbackQueryHandler(confirmar_saque, pattern="^saque_confirmar$"), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto_saque), group=2)
