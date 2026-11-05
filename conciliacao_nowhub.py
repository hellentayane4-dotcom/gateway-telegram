"""Conciliação conservadora de transações NowHub já vinculadas.

Não cria PIX nem envia saques. Saques de status incerto não têm a reserva
liberada automaticamente. Saques sem ID são apenas sinalizados em logs.
"""
import asyncio
import logging
import os
from decimal import Decimal, InvalidOperation

from config import settings
from database import conectar, confirmar_deposito_pago, concluir_saque
from nowhub import client

log = logging.getLogger('smokepay.conciliacao')

# Evita repetir um mesmo alerta em cada ciclo de 30 segundos.
# O conjunto reinicia ao reiniciar o processo; nenhum dado financeiro é alterado.
_saques_sem_id_alertados = set()


def alertar_saques_sem_id():
    """Registra alertas de saques com reserva antiga e sem ID do provedor.

    Consulta exclusivamente o banco e escreve nos logs; não libera reservas,
    não cria saques e não chama a API da NowHub.
    """
    conn = conectar()
    try:
        rows = conn.execute("""
            SELECT identificador, status
            FROM saques_api
            WHERE status IN ('reservado', 'processando')
              AND (provider_withdraw_id IS NULL OR TRIM(provider_withdraw_id) = '')
              AND datetime(criado_em) <= datetime('now', '-5 minutes')
        """).fetchall()
    finally:
        conn.close()

    atuais = {row['identificador']: row['status'] for row in rows}
    _saques_sem_id_alertados.intersection_update(atuais)

    for identificador in sorted(set(atuais) - _saques_sem_id_alertados):
        log.error(
            'ALERTA SEGURANCA: saque sem ID da NowHub ha mais de 5 minutos: '
            'ref=%s status=%s. Reserva mantida; revisao manual necessaria.',
            identificador,
            atuais[identificador],
        )
        _saques_sem_id_alertados.add(identificador)

    return len(atuais)

_saques_sem_id_notificados = set()


async def notificar_saques_sem_id(bot):
    if bot is None or not settings.admin_telegram_id:
        return

    conn = conectar()
    try:
        rows = conn.execute("""
            SELECT identificador, status
            FROM saques_api
            WHERE status IN ('reservado', 'processando')
              AND (provider_withdraw_id IS NULL
                   OR TRIM(provider_withdraw_id) = '')
              AND datetime(criado_em) <= datetime('now', '-5 minutes')
        """).fetchall()
    finally:
        conn.close()

    atuais = {row["identificador"]: row["status"] for row in rows}
    _saques_sem_id_notificados.intersection_update(atuais)

    for ref, status in atuais.items():
        if ref in _saques_sem_id_notificados:
            continue
        try:
            await bot.send_message(
                chat_id=settings.admin_telegram_id,
                text=(
                    "🚨 SMOKEPAY - SAQUE SEM ID\n\n"
                    f"Referência: {ref}\n"
                    f"Status: {status}\n"
                    "Tempo: mais de 5 minutos\n\n"
                    "Saldo permanece reservado.\n"
                    "Verifique a transferência na NowHub "
                    "antes de liberar ou reenviar."
                ),
            )
            _saques_sem_id_notificados.add(ref)
        except Exception:
            log.exception("Falha ao notificar saque sem ID: %s", ref)
def _cents(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount * 100 != (amount * 100).to_integral_value():
            return None
        return int(amount * 100)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _rows():
    conn = conectar()
    try:
        deposits = [dict(x) for x in conn.execute("""
            SELECT * FROM depositos_api WHERE provider='nowhub'
            AND status='pendente' AND provider_transaction_id IS NOT NULL
            ORDER BY id LIMIT 100
        """).fetchall()]
        withdrawals = [dict(x) for x in conn.execute("""
            SELECT * FROM saques_api WHERE status='processando'
            AND provider_withdraw_id IS NOT NULL
            ORDER BY id LIMIT 100
        """).fetchall()]
        return deposits, withdrawals
    finally:
        conn.close()


async def conciliar_uma_vez(bot=None):
    # Independent fail-closed switches: no financial state changes in simulation.
    if os.getenv('NOWHUB_DEPOSIT_SIMULATION', 'true').lower() not in {'false', '0', 'no'}:
        return {'bloqueado': 'simulacao'}
    if not settings.nowhub_hmac_verified or settings.deposit_provider != 'nowhub':
        return {'bloqueado': 'configuracao'}
    deposits, withdrawals = _rows()
    results = {'depositos_conferidos': 0, 'saques_conferidos': 0, 'alertas': 0}
    for kind, items in (('deposit', deposits), ('withdraw', withdrawals)):
        for row in items:
            ref = row['provider_transaction_id'] if kind == 'deposit' else row['provider_withdraw_id']
            try:
                tx = await client.transaction(ref)
                if not isinstance(tx, dict) or str(tx.get('transaction_id')) != ref:
                    raise ValueError('ID da transacao divergente')
                if str(tx.get('external_id') or '') != row['identificador']:
                    raise ValueError('external_id divergente')
                expected = row['valor_bruto_centavos'] if kind == 'deposit' else row['valor_envio_centavos']
                if _cents(tx.get('amount')) != expected:
                    raise ValueError('valor divergente')
                typ = str(tx.get('type') or '').lower()
                if kind == 'deposit' and typ not in {'deposit', '0'}:
                    raise ValueError('tipo divergente')
                if kind == 'withdraw' and typ not in {'withdraw', 'withdrawal'}:
                    raise ValueError('tipo divergente')
                status = str(tx.get('status') or '').upper()
                if kind == 'deposit':
                    results['depositos_conferidos'] += 1
                    if status in {'COMPLETED', 'PAID'}:
                        _, resultado = confirmar_deposito_pago(row['identificador'], expected)
                        if resultado == 'creditado':
                            results.setdefault('depositos_creditados', 0)
                            results['depositos_creditados'] += 1
                            log.info('Depósito conciliado e creditado: %s', row['identificador'])
                            if bot is not None:
                                try:
                                    from notificacoes import formatar_valor, notificar_canal, notificar_usuario
                                    ref_curta = str(row.get('provider_transaction_id') or row['identificador'])
                                    if len(ref_curta) > 16:
                                        ref_curta = f"{ref_curta[:8]}…{ref_curta[-6:]}"
                                    await notificar_usuario(
                                        bot,
                                        row['telegram_id'],
                                        "✅ <b>PIX RECEBIDO</b>\n\n"
                                        f"💰 Crédito: <b>{formatar_valor(row['valor_liquido_centavos'])}</b>\n"
                                        f"🆔 ID: <code>{ref_curta}</code>\n\n"
                                        "Seu saldo já está disponível.",
                                        imagem='pix_recebido.png',
                                    )
                                    await notificar_canal(
                                        bot,
                                        "✅ <b>PIX RECEBIDO</b>\n\n"
                                        f"🆔 Conta: <code>{str(row['telegram_id'])[-6:]}</code>\n"
                                        f"💰 Valor: <b>{formatar_valor(row['valor_bruto_centavos'])}</b>\n"
                                        f"🧾 Crédito: {formatar_valor(row['valor_liquido_centavos'])}",
                                        imagem='pix_recebido.png',
                                    )
                                except Exception:
                                    # Falha de notificação não desfaz um crédito já conciliado.
                                    log.exception('Falha ao notificar depósito conciliado: %s', row['identificador'])
                    elif status in {'DISPUTED', 'RETIDO', 'REFUNDED', 'CHARGEBACK'}:
                        log.error('Depósito requer revisão manual: %s, status=%s', row['identificador'], status)
                        results['alertas'] += 1
                else:
                    results['saques_conferidos'] += 1
                    if status in {'COMPLETED', 'PAID'} and settings.enable_real_withdrawals:
                        saque, resultado = concluir_saque(row['identificador'])
                        if saque is not None and resultado == 'concluido' and bot is not None:
                            try:
                                from notificacoes import formatar_valor, notificar_canal, notificar_usuario
                                await notificar_usuario(
                                    bot,
                                    saque['telegram_id'],
                                    "✅ <b>SAQUE EFETUADO</b>\n\n"
                                    f"💰 Valor enviado: <b>{formatar_valor(saque['valor_envio_centavos'])}</b>\n"
                                    "A transferência foi concluída.",
                                    imagem='saque_efetuado.png',
                                )
                                await notificar_canal(
                                    bot,
                                    "✅ <b>SAQUE EFETUADO</b>\n\n"
                                    f"🆔 Conta: <code>{str(saque['telegram_id'])[-6:]}</code>\n"
                                    f"💰 Valor: <b>{formatar_valor(saque['valor_envio_centavos'])}</b>",
                                    imagem='saque_efetuado.png',
                                )
                            except Exception:
                                log.exception('Falha ao notificar saque conciliado: %s', row['identificador'])
                    elif status in {'FAILED', 'REJECTED', 'CANCELED', 'DISPUTED'}:
                        # Não devolver reserva automaticamente sem evidência adicional.
                        log.error('Saque exige revisão manual: %s, status=%s', row['identificador'], status)
                        results['alertas'] += 1
            except Exception as exc:
                log.warning('Conciliação não concluída para %s %s: %s', kind, ref, type(exc).__name__)
                results['alertas'] += 1
    return results



async def executar_conciliacao_periodica(bot=None):
    while True:
        log.info('Conciliação NowHub: iniciando ciclo')
        try:
            alertar_saques_sem_id()
                        await notificar_saques_sem_id(bot)
        except Exception:
            # O diagnostico nunca pode interromper a conciliacao dos depositos.
            log.exception('Falha ao monitorar saques sem ID; conciliacao continua')
        try:
            result = await conciliar_uma_vez(bot)
            log.info(
                'Conciliação NowHub: ciclo concluído | depósitos=%s | saques=%s | alertas=%s | bloqueado=%s',
                result.get('depositos_conferidos', 0),
                result.get('saques_conferidos', 0),
                result.get('alertas', 0),
                result.get('bloqueado', 'não'),
            )
            if result.get('alertas'):
                log.warning('Conciliação requer atenção: %s', result)
        except Exception:
            log.exception('Falha no ciclo de conciliação')
        await asyncio.sleep(30)
