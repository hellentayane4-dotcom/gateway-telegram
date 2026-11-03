import logging

from telegram import Bot
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


async def notificar_canal(bot: Bot, mensagem: str, imagem: str | None = None) -> bool:
    if not settings.notification_channel_id:
        return False
    try:
        caminho = ASSETS_DIR / imagem if imagem else None
        if caminho and caminho.exists():
            with caminho.open("rb") as foto:
                await bot.send_photo(
                    chat_id=settings.notification_channel_id,
                    photo=foto,
                    caption=mensagem,
                    parse_mode=ParseMode.HTML,
                )
        else:
            await bot.send_message(
                chat_id=settings.notification_channel_id,
                text=mensagem,
                parse_mode=ParseMode.HTML,
            )
        return True
    except (TelegramError, OSError, AttributeError):
        logger.exception("Falha ao notificar canal")
        return False
