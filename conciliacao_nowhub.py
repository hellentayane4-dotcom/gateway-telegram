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
