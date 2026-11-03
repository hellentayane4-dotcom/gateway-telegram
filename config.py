import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} deve conter apenas números inteiros.") from exc


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    admin_telegram_id: int | None = _int("ADMIN_TELEGRAM_ID")
    notification_channel_id: int | None = _int("CANAL_NOTIFICACOES_ID")
    support_username: str = os.getenv("SUPPORT_USERNAME", "smokepaysuporte").strip().lstrip("@")

    # A API definitiva entra apenas nestes roteadores.
    deposit_provider: str = os.getenv("DEPOSIT_PROVIDER", "disabled").strip().lower()
    withdrawal_provider: str = os.getenv("WITHDRAWAL_PROVIDER", "disabled").strip().lower()

    # Espaço reservado para a API definitiva. As variáveis ficam prontas para
    # preencher sem alterar a interface do bot.
    final_provider_enabled: bool = _bool("FINAL_PROVIDER_ENABLED", False)
    final_api_base_url: str = os.getenv("FINAL_API_BASE_URL", "").strip().rstrip("/")
    final_api_key: str = os.getenv("FINAL_API_KEY", "").strip()
    final_api_secret: str = os.getenv("FINAL_API_SECRET", "").strip()
    final_webhook_secret: str = os.getenv("FINAL_WEBHOOK_SECRET", "").strip()

    # BotaPix fica como adaptador opcional/teste.
    botapix_enabled: bool = _bool("BOTAPIX_ENABLED", False)
    botapix_base_url: str = os.getenv("BOTAPIX_BASE_URL", "https://botapix.com/api/v1").strip().rstrip("/")
    botapix_api_key: str = os.getenv("BOTAPIX_API_KEY", "").strip()
    botapix_webhook_secret: str = os.getenv("BOTAPIX_WEBHOOK_SECRET", "").strip()
    botapix_charge_expiration: int = _int("BOTAPIX_CHARGE_EXPIRATION", 1800) or 1800

    # SigiloPay preservada como adaptador legado/opcional.
    sigilopay_base_url: str = os.getenv("SIGILOPAY_BASE_URL", "https://app.sigilopay.com.br/api/v1").strip().rstrip("/")
    sigilopay_public_key: str = os.getenv("SIGILOPAY_PUBLIC_KEY", "").strip()
    sigilopay_secret_key: str = os.getenv("SIGILOPAY_SECRET_KEY", "").strip()
    sigilopay_webhook_token: str = os.getenv("SIGILOPAY_WEBHOOK_TOKEN", "").strip()
    sigilopay_enabled: bool = _bool("SIGILOPAY_ENABLED", False)
    discount_fee_of_receiver: bool = _bool("SIGILOPAY_DISCOUNT_FEE_OF_RECEIVER", False)

    # Railway injeta RAILWAY_PUBLIC_DOMAIN e PORT em tempo de execução.
    # Em desenvolvimento local, PUBLIC_BASE_URL/WEB_HOST/WEB_PORT continuam funcionando.
    _railway_public_domain: str = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    public_base_url: str = (
        os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        or (f"https://{_railway_public_domain}" if _railway_public_domain else "")
    )
    web_host: str = os.getenv(
        "WEB_HOST",
        "0.0.0.0" if os.getenv("RAILWAY_ENVIRONMENT_ID") else "127.0.0.1",
    ).strip()
    web_port: int = _int("PORT", _int("WEB_PORT", 8000) or 8000) or 8000

    nowhub_client_id: str = os.getenv("NOWHUB_CLIENT_ID", "").strip()
    nowhub_client_secret: str = os.getenv("NOWHUB_CLIENT_SECRET", "").strip()
    nowhub_webhook_secret: str = os.getenv("NOWHUB_WEBHOOK_SECRET", "").strip()
    # Pending confirmation of NowHub's exact HMAC signature encoding.
    nowhub_hmac_format: str = os.getenv("NOWHUB_HMAC_FORMAT", "").strip().lower()
    nowhub_hmac_verified: bool = _bool("NOWHUB_HMAC_VERIFIED", False)

    @property
    def nowhub_webhook_url(self) -> str:
        return f"{self.public_base_url}/webhooks/nowhub" if self.public_base_url else ""

    enable_real_withdrawals: bool = _bool("ENABLE_REAL_WITHDRAWALS", False)

    deposit_min_cents: int = _int("DEPOSIT_MIN_CENTS", 500) or 500
    deposit_max_cents: int = _int("DEPOSIT_MAX_CENTS", 100000000) or 100000000
    deposit_fee_percent_bp: int = _int("DEPOSIT_FEE_PERCENT_BP", 0) or 0
    deposit_fee_fixed_cents: int = _int("DEPOSIT_FEE_FIXED_CENTS", 0) or 0

    withdrawal_min_cents: int = _int("WITHDRAWAL_MIN_CENTS", 1000) or 1000
    withdrawal_fee_percent_bp: int = _int("WITHDRAWAL_FEE_PERCENT_BP", 0) or 0
    withdrawal_fee_fixed_cents: int = _int("WITHDRAWAL_FEE_FIXED_CENTS", 0) or 0

    # Comissão direta. Vem desligada até a regra comercial ser definida.
    referral_commission_bp: int = _int("REFERRAL_COMMISSION_BP", 0) or 0

    @property
    def sigilopay_webhook_url(self) -> str:
        return f"{self.public_base_url}/webhooks/sigilopay" if self.public_base_url else ""

    @property
    def botapix_webhook_url(self) -> str:
        return f"{self.public_base_url}/webhooks/botapix" if self.public_base_url else ""

    @property
    def webhook_url(self) -> str:
        return self.nowhub_webhook_url if self.withdrawal_provider == "nowhub" else self.sigilopay_webhook_url

    @property
    def ready_for_deposits(self) -> bool:
        if self.deposit_provider == "nowhub":
            return bool(self.nowhub_client_id and self.nowhub_client_secret and self.nowhub_webhook_url.startswith("https://") and self.nowhub_webhook_secret and self.nowhub_hmac_verified and self.nowhub_hmac_format in {"sha256-hex", "sha256-prefix-hex"})
        if self.deposit_provider in {"final", "custom", "provider"}:
            return bool(self.final_provider_enabled and self.final_api_base_url and self.final_api_key)
        if self.deposit_provider == "botapix":
            return bool(self.botapix_enabled and self.botapix_api_key)
        if self.deposit_provider == "sigilopay":
            return bool(
                self.sigilopay_enabled
                and self.sigilopay_public_key
                and self.sigilopay_secret_key
                and self.public_base_url
            )
        return False

    @property
    def ready_for_withdrawals(self) -> bool:
        if not self.enable_real_withdrawals or self.withdrawal_provider == "disabled":
            return False
        if self.withdrawal_provider == "nowhub":
            return bool(self.nowhub_client_id and self.nowhub_client_secret and
                self.nowhub_webhook_url.startswith("https://") and
                self.nowhub_webhook_secret and self.nowhub_hmac_verified and
                self.nowhub_hmac_format in {"sha256-hex", "sha256-prefix-hex"})
        if self.withdrawal_provider in {"final", "custom", "provider"}:
            return bool(self.final_provider_enabled and self.final_api_base_url and self.final_api_key and self.public_base_url)
        if self.withdrawal_provider == "sigilopay":
            return bool(
                self.sigilopay_enabled
                and self.sigilopay_public_key
                and self.sigilopay_secret_key
                and self.public_base_url
            )
        return False

    @property
    def botapix_key_mode(self) -> str:
        if self.botapix_api_key.startswith("btpx_test_"):
            return "TESTE"
        if self.botapix_api_key.startswith("btpx_live_"):
            return "PRODUÇÃO"
        return "DESCONHECIDO" if self.botapix_api_key else "NÃO CONFIGURADA"


settings = Settings()
