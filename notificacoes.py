import logging
import re

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import ASSETS_DIR, settings

logger = logging.getLogger("smokepay.notificacoes")


def formatar_valor(valor_centavos: int) -> str:
    sinal = "-" if valor_centavos < 0 else ""
    valor_centavos = abs(int(valor_centavos))
    reais, centavos = divmod(valor_centavos, 100)
    texto = f"{reais:,}".replace(",", ".")
    return f"{sinal}R$ {texto},{centavos:02d}"


async def notificar_usuario(bot: Bot, telegram_id: int, mensagem: str, imagem: str | None = None) -> bool:
    try:
        caminho = ASSETS_DIR / imagem if imagem else None
        if caminho and caminho.exists():
            with caminho.open("rb") as foto:
                await bot.send_photo(chat_id=telegram_id, photo=foto, caption=mensagem, parse_mode=ParseMode.HTML)
        else:
            await bot.send_message(chat_id=telegram_id, text=mensagem, parse_mode=ParseMode.HTML)
        return True
    except (TelegramError, OSError, AttributeError):
        logger.exception("Falha ao notificar usuário %s", telegram_id)
        return False


async def notificar_canal(
    bot: Bot, mensagem: str, imagem: str | None = None, *, usuario_id: int | None = None
) -> bool:
    if not settings.notification_channel_id:
        return False

    # Exibe somente os quatro primeiros dígitos do ID do Telegram.
    usuario = str(usuario_id).strip() if usuario_id is not None else ""
    usuario_curto = f"{usuario[:4]}****" if usuario.isdecimal() else "indisponível"
    mensagem = f"🆔 <b>Usuário:</b> <code>{usuario_curto}</code>\n\n{mensagem}"

    # Usa o username real do bot, quando fornecido pelo Telegram.
    nome_bot = (getattr(bot, "username", None) or "Smokepaybank_bot").lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", nome_bot):
        nome_bot = "Smokepaybank_bot"
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("SMOKEPAY - BOT", url=f"https://t.me/{nome_bot}")]
    ])
    try:
        caminho = ASSETS_DIR / imagem if imagem else None
        if caminho and caminho.exists():
            with caminho.open("rb") as foto:
                await bot.send_photo(
                    chat_id=settings.notification_channel_id,
                    photo=foto,
                    caption=mensagem,
                    parse_mode=ParseMode.HTML,
                    reply_markup=teclado,
                )
        else:
            await bot.send_message(
                chat_id=settings.notification_channel_id,
                text=mensagem,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado,
            )
        return True
    except (TelegramError, OSError, AttributeError):
        logger.exception("Falha ao notificar canal")
        return False
