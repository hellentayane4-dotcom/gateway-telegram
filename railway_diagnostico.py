import os
from database import BANCO
from config import settings

print("RAILWAY:", bool(os.getenv("RAILWAY_ENVIRONMENT_ID")))
print("HOST:", settings.web_host)
print("PORT:", settings.web_port)
print("PUBLIC_BASE_URL_CONFIGURADA:", bool(settings.public_base_url))
print("BANCO:", BANCO)
print("VOLUME:", os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "nao-montado"))
print("DEPOSIT_PROVIDER:", settings.deposit_provider)
print("WITHDRAWAL_PROVIDER:", settings.withdrawal_provider)
print("SAQUE_REAL:", settings.enable_real_withdrawals)
