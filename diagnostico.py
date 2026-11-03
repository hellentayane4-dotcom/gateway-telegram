from config import settings
from database import criar_banco, estatisticas_admin, verificar_integridade


def ok(v):
    return "OK" if v else "FALTA"


if __name__ == "__main__":
    criar_banco()
    stats = estatisticas_admin()
    print("SMOKE PAY - DIAGNOSTICO")
    print("Banco SQLite:", "OK" if verificar_integridade() else "ERRO")
    print("Usuarios preservados:", stats["usuarios"])
    print("TELEGRAM_BOT_TOKEN:", ok(bool(settings.telegram_bot_token)))
    print("ADMIN_TELEGRAM_ID:", ok(bool(settings.admin_telegram_id)))
    print("PUBLIC_BASE_URL:", ok(bool(settings.public_base_url)))
    print("DEPOSIT_PROVIDER:", settings.deposit_provider)
    print("Depositos prontos:", "SIM" if settings.ready_for_deposits else "NAO - aguardando API")
    print("WITHDRAWAL_PROVIDER:", settings.withdrawal_provider)
    print("Saques reais:", "ATIVOS" if settings.ready_for_withdrawals else "DESATIVADOS (seguro)")
    print("Comissao afiliado:", f"{settings.referral_commission_bp/100:.2f}%")
    print("\nNenhum segredo foi exibido.")
