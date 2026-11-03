"""Confirma callback uma única vez e tolera expiração no Telegram."""
import logging
from telegram.error import BadRequest

logger = logging.getLogger("smokepay.callback")

async def responder_callback(query) -> bool:
    if query is None:
        return False
    try:
        await query.answer()
        return True
    except BadRequest as exc:
        msg = str(exc).lower()
        if "query is too old" in msg or "query id is invalid" in msg or "response timeout expired" in msg:
            logger.info("Callback expirado/invalidado; ignorando clique antigo")
            return False
        raise
