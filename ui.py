import html
from pathlib import Path

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest

from config import ASSETS_DIR


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def abreviar(value: object, inicio: int = 6, fim: int = 4) -> str:
    s = str(value or "")
    if len(s) <= inicio + fim + 3:
        return s
    return f"{s[:inicio]}…{s[-fim:]}"


def asset(nome: str) -> Path:
    return ASSETS_DIR / nome


async def enviar_painel(update: Update, texto: str, teclado: InlineKeyboardMarkup | None = None, imagem: str | None = None):
    message = update.effective_message
    if not message:
        return
    if imagem and asset(imagem).exists():
        with asset(imagem).open("rb") as foto:
            return await message.reply_photo(
                photo=foto,
                caption=texto,
                reply_markup=teclado,
                parse_mode=ParseMode.HTML,
            )
    return await message.reply_text(text=texto, reply_markup=teclado, parse_mode=ParseMode.HTML)


async def editar_painel(query, texto: str, teclado: InlineKeyboardMarkup | None = None):
    """Edita a mesma mensagem, funcionando tanto em painel com foto quanto texto."""
    try:
        if query.message and query.message.photo:
            return await query.edit_message_caption(
                caption=texto,
                reply_markup=teclado,
                parse_mode=ParseMode.HTML,
            )
        return await query.edit_message_text(
            text=texto,
            reply_markup=teclado,
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return None
        raise
