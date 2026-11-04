import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from telegram import Bot

from config import settings
from database import (
    aplicar_comissao_afiliado,
    buscar_saque_por_referencia,
    vincular_saque_webhook_antecipado,
    confirmar_deposito_pago,
    buscar_deposito_por_referencia,
    concluir_saque,
    finalizar_webhook,
    liberar_saque,
    marcar_deposito_status,
    registrar_webhook,
    reverter_deposito,
    salvar_ip_capturado,
)
from notificacoes import formatar_valor, notificar_canal, notificar_usuario

logger = logging.getLogger("smokepay.webhook")


def _centavos(valor: Any) -> int | None:
    if valor is None:
        return None
    try:
        dec = Decimal(str(valor))
        if not dec.is_finite():
            return None
        return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def _ref_transacao(payload: dict[str, Any]) -> str | None:
    tx = payload.get("transaction") or {}
    return str(tx.get("id") or tx.get("identifier") or payload.get("identifier") or "") or None


def _ref_transferencia(payload: dict[str, Any]) -> str | None:
    wd = payload.get("withdraw") or {}
    return str(wd.get("clientIdentifier") or wd.get("id") or "") or None


def criar_web_app(bot: Bot) -> FastAPI:
    app = FastAPI(title="Smoke Pay Webhooks", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "smokepay"}

    @app.get("/capturar-ip", response_class=HTMLResponse)
    async def capturar_ip(request: Request, token: str):
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        raw_ip = forwarded or (request.client.host if request.client else "")
        try:
            ip = str(ipaddress.ip_address(raw_ip))
        except ValueError:
            return HTMLResponse("<h2>Não foi possível identificar seu IP.</h2>", status_code=400)
        if ":" in ip:
            return HTMLResponse(
                "<h2>Foi detectado IPv6.</h2><p>A API de transferência exige IPv4. Tente outra rede e abra o link novamente.</p>",
                status_code=400,
            )
        if not salvar_ip_capturado(token, ip):
            return HTMLResponse("<h2>Link inválido ou expirado.</h2>", status_code=400)
        return HTMLResponse(
            "<h2>IP confirmado ✅</h2><p>Volte ao Telegram e toque em <b>Já confirmei</b>.</p>"
        )

    @app.post("/webhooks/botapix")
    async def webhook_botapix(request: Request):
        # Etapa de validação controlada. A BotaPix envia o digest em
        # X-BotaPix-Signature no formato sha256=<hex>. Para não alterar os bytes
        # assinados, validamos o HMAC sobre o corpo HTTP bruto antes do JSON parse.
        raw_body = await request.body()
        assinatura = (request.headers.get("x-botapix-signature") or "").strip()
        event_id = (request.headers.get("x-botapix-event-id") or "").strip()
        timestamp = (request.headers.get("x-botapix-timestamp") or "").strip()

        if not settings.botapix_webhook_secret:
            logger.error("Webhook BotaPix recebido sem BOTAPIX_WEBHOOK_SECRET configurado")
            raise HTTPException(status_code=503, detail="Webhook BotaPix não configurado")
        if not assinatura.startswith("sha256="):
            logger.warning("Webhook BotaPix rejeitado: assinatura ausente ou formato inválido")
            raise HTTPException(status_code=401, detail="Assinatura inválida")
        if not event_id or not timestamp:
            logger.warning("Webhook BotaPix rejeitado: event_id/timestamp ausente")
            raise HTTPException(status_code=400, detail="Headers BotaPix incompletos")

        esperado = "sha256=" + hmac.new(
            settings.botapix_webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(assinatura.lower(), esperado.lower()):
            logger.warning("Webhook BotaPix rejeitado: HMAC não confere event_id=%s", event_id)
            raise HTTPException(status_code=401, detail="Assinatura HMAC inválida")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="JSON inválido") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload inválido")

        evento = str(payload.get("event") or "")
        payload_event_id = str(payload.get("event_id") or "")
        if payload_event_id and not hmac.compare_digest(payload_event_id, event_id):
            logger.warning("Webhook BotaPix rejeitado: event_id do body diverge do header")
            raise HTTPException(status_code=400, detail="Event ID divergente")

        # O evento de teste nunca altera saldo. Ele serve apenas para provar que
        # o endpoint e a assinatura estão corretos antes de ativar pagamentos.
        if evento == "webhook.test":
            logger.info("Webhook BotaPix de teste validado event_id=%s", event_id)
            return {"ok": True, "event": evento, "signature": "valid"}

        # Nesta atualização, eventos financeiros continuam bloqueados. Depois de
        # confirmar o HMAC com o botão 'Enviar teste', habilitaremos payment.confirmed.
        logger.warning("Webhook BotaPix autenticado, mas evento financeiro ainda bloqueado evento=%s event_id=%s", evento or "-", event_id)
        raise HTTPException(status_code=503, detail="Evento BotaPix financeiro ainda em validação")

    @app.post("/webhooks/provider")
    async def webhook_provider_final(request: Request):
        # Ponto reservado para a API definitiva. Mantemos separado para que a
        # troca de provedor não altere carteira, histórico, afiliados ou admin.
        raise HTTPException(status_code=503, detail="Provedor financeiro definitivo ainda não configurado")

    @app.post("/webhooks/nowhub")
    async def webhook_nowhub(request: Request):
        # Verifica a assinatura mesmo no modo de diagnostico, sem liberar
        # qualquer operacao financeira. O corpo bruto e usado no HMAC.
        if not settings.nowhub_webhook_secret or settings.nowhub_hmac_format not in {"sha256-hex", "sha256-prefix-hex"}:
            raise HTTPException(status_code=503, detail="HMAC NowHub nao configurado")
        raw = await request.body()
        if len(raw) > 256 * 1024:
            raise HTTPException(status_code=413, detail="Evento muito grande")
        signature = (request.headers.get("x-signature") or "").strip().lower()
        expected = hmac.new(settings.nowhub_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        if settings.nowhub_hmac_format == "sha256-prefix-hex":
            expected = "sha256=" + expected
        if not signature or not hmac.compare_digest(signature, expected):
            logger.warning("NowHub: assinatura ausente ou invalida")
            raise HTTPException(status_code=401, detail="Assinatura invalida")
        # Bloqueio independente: validar HMAC não habilita pagamentos reais.
        import os
        if os.getenv("NOWHUB_DEPOSIT_SIMULATION", "true").strip().lower() not in {"false", "0", "no"}:
            raise HTTPException(status_code=503, detail="Operações financeiras em simulação")
        if not settings.nowhub_hmac_verified:
            logger.info("NowHub: assinatura HMAC valida; processamento financeiro bloqueado")
            raise HTTPException(status_code=503, detail="Assinatura HMAC valida; processamento financeiro bloqueado")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="JSON inválido") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise HTTPException(status_code=400, detail="Evento inválido")
        event = str(payload.get("type") or "")
        data = payload["data"]
        ref = str(data.get("transaction_id") or "")
        if event not in {"deposit.updated", "withdraw.updated"}:
            # Outside the two supported operations; never mutate wallet state.
            return {"ok": True, "ignored": True}
        if not ref:
            raise HTTPException(status_code=400, detail="Referência ausente")
        from nowhub import client, NowHubError
        # A consulta pode ficar indisponível por alguns instantes logo após o webhook.
        # Como é um GET idempotente, podemos repetir somente a leitura sem criar
        # depósito ou saque duplicado.
        tx = None
        ultimo_erro = None
        for tentativa, atraso in enumerate((0.0, 0.75, 2.0), start=1):
            if atraso:
                await asyncio.sleep(atraso)
            try:
                tx = await client.transaction(ref)
                break
            except NowHubError as exc:
                ultimo_erro = exc
                logger.warning("NowHub: consulta da transação falhou tentativa=%s ref=%s", tentativa, ref)
        if tx is None:
            raise HTTPException(status_code=503, detail="Consulta de transação indisponível") from ultimo_erro
        if not isinstance(tx, dict) or str(tx.get("transaction_id")) != ref:
            raise HTTPException(status_code=409, detail="Identificador divergente")
        status = str(tx.get("status") or "").upper()
        external = str(tx.get("external_id") or "")
        amount = _centavos(tx.get("amount"))
        if not external or amount is None:
            raise HTTPException(status_code=409, detail="Transação incompleta")
        if event == "deposit.updated":
            dep = buscar_deposito_por_referencia(external)
            # A API de produção observada retornou type=0 para depósito, embora a
            # documentação também mostre "deposit". Mantemos a validação estrita
            # aceitando apenas essas duas representações, além de ID, referência e valor.
            tipo_tx = str(tx.get("type", "")).strip().lower()
            if dep is None or dep["provider"] != "nowhub" or dep["provider_transaction_id"] != ref or dep["valor_bruto_centavos"] != amount or tipo_tx not in {"deposit", "0"}:
                raise HTTPException(status_code=409, detail="Depósito não corresponde")
            # A API de produção observada retornou PAID para PIX liquidado.
            # COMPLETED continua aceito para compatibilidade com a documentação.
            if status in {"COMPLETED", "PAID"}:
                dep, result = confirmar_deposito_pago(external, amount)
                if result == "creditado":
                    ref_curta = str(dep["provider_transaction_id"] or dep["identificador"])
                    if len(ref_curta) > 16:
                        ref_curta = f"{ref_curta[:8]}…{ref_curta[-6:]}"
                    await notificar_usuario(
                        bot,
                        dep["telegram_id"],
                        "✅ <b>PIX RECEBIDO</b>\n\n"
                        f"💰 Crédito: <b>{formatar_valor(dep['valor_liquido_centavos'])}</b>\n"
                        f"🆔 ID: <code>{ref_curta}</code>\n\n"
                        "Seu saldo já está disponível.",
                        imagem="pix_recebido.png",
                    )
                    await notificar_canal(
                        bot,
                        "✅ <b>PIX RECEBIDO</b>\n\n"
                        f"🆔 Conta: <code>{str(dep['telegram_id'])[-6:]}</code>\n"
                        f"💰 Valor: <b>{formatar_valor(dep['valor_bruto_centavos'])}</b>\n"
                        f"🧾 Crédito: {formatar_valor(dep['valor_liquido_centavos'])}",
                        imagem="pix_recebido.png",
                    )
            elif status in {"FAILED", "CANCELED", "REJECTED"}:
                dep, result = marcar_deposito_status(external, "cancelado")
            elif status in {"RETIDO", "DISPUTED"}:
                # Do not silently treat a retained/contested transaction as paid.
                # Existing paid funds require manual reconciliation before payouts.
                logger.error("NowHub deposito exige conciliacao: ref=%s status=%s", ref, status)
                result = "conciliacao_necessaria"
            else:
                result = "pendente"
        else:
            saque = buscar_saque_por_referencia(external)
            if saque is None or (saque["provider_withdraw_id"] and saque["provider_withdraw_id"] != ref) or saque["valor_envio_centavos"] != amount:
                raise HTTPException(status_code=409, detail="Saque não corresponde")
            if not settings.enable_real_withdrawals or str(tx.get("type", "")).lower() not in {"withdraw", "withdrawal"}:
                raise HTTPException(status_code=409, detail="Saque não autorizado ou divergente")
            if not saque["provider_withdraw_id"]:
                # O webhook pode chegar antes da resposta HTTP do saque.
                # O vínculo é atômico e somente após consulta autenticada ao provedor.
                vinculo = vincular_saque_webhook_antecipado(external, ref)
                if vinculo not in {"vinculado", "ja_vinculado"}:
                    raise HTTPException(status_code=409, detail="Saque exige conciliação: " + vinculo)
            elif saque["provider_withdraw_id"] != ref:
                raise HTTPException(status_code=409, detail="Saque não autorizado ou divergente")
            if status in {"COMPLETED", "PAID"}:
                saque, result = concluir_saque(external)
                if saque is not None and result == "concluido":
                    await notificar_usuario(
                        bot,
                        saque["telegram_id"],
                        "✅ <b>SAQUE EFETUADO</b>\n\n"
                        f"💰 Valor enviado: <b>{formatar_valor(saque['valor_envio_centavos'])}</b>\n"
                        "A transferência foi concluída.",
                        imagem="saque_efetuado.png",
                    )
                    await notificar_canal(
                        bot,
                        "✅ <b>SAQUE EFETUADO</b>\n\n"
                        f"🆔 Conta: <code>{str(saque['telegram_id'])[-6:]}</code>\n"
                        f"💰 Valor: <b>{formatar_valor(saque['valor_envio_centavos'])}</b>",
                        imagem="saque_efetuado.png",
                    )
            
            elif status in {"FAILED", "CANCELED", "REJECTED", "DISPUTED"}:
                # Manter reserva ate revisao segura da transferencia.
                logger.error(
                    "Saque NowHub exige revisao manual: ref=%s status=%s",
                    ref, status,
                )
                result = "revisao_manual"

            else:
                result = "pendente"
        return {"ok": True, "result": result}

    @app.post("/webhooks/sigilopay")
    async def webhook_sigilopay(request: Request):
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="JSON inválido") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload inválido")

        evento = str(payload.get("event") or "").upper()
        ref = _ref_transferencia(payload) if evento.startswith("TRANSFER_") else _ref_transacao(payload)
        recebido = str(payload.get("token") or "")

        if evento.startswith("TRANSFER_"):
            saque = buscar_saque_por_referencia(ref) if ref else None
            esperado_hash = saque["webhook_token_hash"] if saque is not None else None
            recebido_hash = hashlib.sha256(recebido.encode("utf-8")).hexdigest() if recebido else ""
            if not esperado_hash or not hmac.compare_digest(recebido_hash, esperado_hash):
                logger.warning("Webhook de transferência rejeitado por token inválido ref=%s", ref)
                raise HTTPException(status_code=401, detail="Token inválido")
        else:
            esperado = settings.sigilopay_webhook_token
            if not esperado:
                logger.error("Webhook de transação recebido, mas SIGILOPAY_WEBHOOK_TOKEN não está configurado")
                raise HTTPException(status_code=503, detail="Webhook não configurado")
            if not recebido or not hmac.compare_digest(recebido, esperado):
                logger.warning("Webhook de transação rejeitado por token inválido")
                raise HTTPException(status_code=401, detail="Token inválido")
        _, duplicado = registrar_webhook(payload, evento, ref)
        if duplicado:
            return {"ok": True, "duplicate": True}

        resultado = "ignorado"
        try:
            if evento == "TRANSACTION_PAID":
                tx = payload.get("transaction") or {}
                valor = _centavos(tx.get("amount"))
                if not ref:
                    resultado = "sem_referencia"
                else:
                    dep, estado = confirmar_deposito_pago(ref, valor)
                    resultado = estado
                    if dep is not None and estado == "creditado":
                        ref_curta = str(dep["provider_transaction_id"] or dep["identificador"])
                        if len(ref_curta) > 16:
                            ref_curta = f"{ref_curta[:8]}…{ref_curta[-6:]}"
                        await notificar_usuario(
                            bot,
                            dep["telegram_id"],
                            "✅ <b>PIX RECEBIDO</b>\n\n"
                            f"💰 Crédito: <b>{formatar_valor(dep['valor_liquido_centavos'])}</b>\n"
                            f"🆔 ID: <code>{ref_curta}</code>\n\n"
                            "Seu saldo já está disponível.",
                            imagem="pix_recebido.png",
                        )
                        await notificar_canal(
                            bot,
                            "✅ <b>PIX RECEBIDO</b>\n\n"
                            f"🆔 Conta: <code>{str(dep['telegram_id'])[-6:]}</code>\n"
                            f"💰 Valor: <b>{formatar_valor(dep['valor_bruto_centavos'])}</b>\n"
                            f"🧾 Crédito: {formatar_valor(dep['valor_liquido_centavos'])}",
                            imagem="pix_recebido.png",
                        )
                        comissao, comissao_estado = aplicar_comissao_afiliado(dep["id"], settings.referral_commission_bp)
                        if comissao is not None and comissao_estado == "creditada":
                            await notificar_usuario(
                                bot,
                                comissao["beneficiario_id"],
                                "🤝 <b>COMISSÃO PAGA</b>\n\n"
                                f"💰 Valor: <b>{formatar_valor(comissao['valor_centavos'])}</b>\n"
                                "A comissão foi adicionada ao seu saldo.",
                                imagem="comissao_paga.png",
                            )
                            await notificar_canal(
                                bot,
                                "🤝 <b>COMISSÃO PAGA</b>\n\n"
                                f"💰 Valor: <b>{formatar_valor(comissao['valor_centavos'])}</b>\n"
                                f"👥 Nível: {comissao['nivel']}",
                                imagem="comissao_paga.png",
                            )
            elif evento in {"TRANSACTION_CANCELED", "TRANSACTION_CANCELLED"} and ref:
                _, resultado = marcar_deposito_status(ref, "cancelado")
            elif evento in {"TRANSACTION_REFUNDED", "TRANSACTION_CHARGED_BACK", "TRANSACTION_CHARGEBACK"} and ref:
                status = "estornado" if evento == "TRANSACTION_REFUNDED" else "chargeback"
                dep, resultado = reverter_deposito(ref, status)
                if dep is not None and resultado == "revertido":
                    await notificar_usuario(
                        bot,
                        dep["telegram_id"],
                        "⚠️ SMOKE PAY\n\nUm depósito anteriormente creditado foi revertido pela operadora. "
                        "O saldo correspondente foi ajustado.",
                    )
            elif evento == "TRANSFER_CREATED" and ref:
                resultado = "transferencia_processando"
            elif evento == "TRANSFER_COMPLETED" and ref:
                saque, resultado = concluir_saque(ref)
                if saque is not None and resultado == "concluido":
                    await notificar_usuario(
                        bot,
                        saque["telegram_id"],
                        "✅ <b>SAQUE EFETUADO</b>\n\n"
                        f"💰 Valor enviado: <b>{formatar_valor(saque['valor_envio_centavos'])}</b>\n"
                        "A transferência foi concluída pelo provedor.",
                        imagem="saque_efetuado.png",
                    )
                    await notificar_canal(
                        bot,
                        "✅ <b>SAQUE EFETUADO</b>\n\n"
                        f"🆔 Conta: <code>{str(saque['telegram_id'])[-6:]}</code>\n"
                        f"💰 Valor: <b>{formatar_valor(saque['valor_envio_centavos'])}</b>",
                        imagem="saque_efetuado.png",
                    )
            elif evento == "TRANSFER_FAILED" and ref:
                wd = payload.get("withdraw") or {}
                saque, resultado = liberar_saque(ref, str(wd.get("message") or "Transferência falhou"))
                if saque is not None and resultado == "liberado":
                    await notificar_usuario(
                        bot,
                        saque["telegram_id"],
                        "⚠️ SMOKE PAY\n\nO saque falhou e o valor reservado foi devolvido ao seu saldo.",
                    )
        except Exception:
            logger.exception("Erro ao processar webhook %s ref=%s", evento, ref)
            resultado = "erro_interno"
            finalizar_webhook(payload, resultado)
            raise HTTPException(status_code=500, detail="Erro interno")

        finalizar_webhook(payload, resultado)
        logger.info("Webhook processado evento=%s ref=%s resultado=%s", evento, ref, resultado)
        return {"ok": True, "result": resultado}

    return app
