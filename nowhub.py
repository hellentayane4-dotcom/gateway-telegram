"""NowHub API adapter. Never log credentials, bearer tokens or PIX details."""
import asyncio
import time
from decimal import Decimal
import httpx
from config import settings

class NowHubError(RuntimeError):
    pass

class NowHubUncertainError(NowHubError):
    """Request may have reached the provider; never automatically retry a payout."""

class NowHubClient:
    def __init__(self):
        self.token = None
        self.expires = 0
        self.lock = asyncio.Lock()

    async def auth(self):
        async with self.lock:
            if self.token and time.monotonic() < self.expires:
                return self.token
            if not settings.nowhub_client_id or not settings.nowhub_client_secret:
                raise NowHubError('Credenciais NowHub ausentes')
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.post('https://api.nowhubglobal.com/v1/auth/login', json={
                        'client_id': settings.nowhub_client_id,
                        'client_secret': settings.nowhub_client_secret,
                    })
                r.raise_for_status()
                data = r.json()
                self.token = data['access_token']
                self.expires = time.monotonic() + max(1, int(data.get('expires_in', 3600))-60)
                return self.token
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise NowHubError('Falha na autenticação NowHub') from exc

    async def request(self, method, path, *, payload=None):
        token = await self.auth()
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.request(method, 'https://api.nowhubglobal.com/v1'+path,
                    headers={'Authorization': f'Bearer {token}'}, json=payload)
            if r.status_code >= 500 and method.upper() == 'POST':
                raise NowHubUncertainError('Falha do provedor após envio; concilie antes de tentar novamente')
            if r.status_code >= 400:
                # A resposta HTTP isolada não comprova que um saque não foi executado.
                # Nunca libere a reserva automaticamente sem conciliação confiável.
                if method.upper() == 'POST' and path.startswith('/withdrawals/'):
                    raise NowHubUncertainError(
                        f'NowHub retornou HTTP {r.status_code} durante o saque; '
                        'mantenha a reserva até a conciliação'
                    )
                raise NowHubError(f'NowHub retornou HTTP {r.status_code}')
            return r.json()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if method.upper() == 'POST':
                raise NowHubUncertainError('Resultado incerto; concilie a transação antes de tentar novamente') from exc
            raise NowHubError('Falha de comunicação com NowHub') from exc
        except (ValueError, KeyError) as exc:
            if method.upper() == "POST":
                raise NowHubUncertainError(
                    "Resposta inválida após envio; "
                    "mantenha o saldo reservado até a conciliação"
                ) from exc

            raise NowHubError(
                "Resposta inválida da NowHub"
            ) from exc

    async def deposit(self, identifier, cents):
        return await self.request('POST', '/payments/deposit', payload={
            'amount': float(Decimal(cents)/100), 'external_id': identifier,
            'clientCallbackUrl': settings.nowhub_webhook_url,
        })

    async def transaction(self, transaction_id):
        if not transaction_id or '/' in transaction_id or '?' in transaction_id:
            raise NowHubError('ID de transação inválido')
        return await self.request('GET', '/transactions/'+transaction_id)

    async def withdraw(self, identifier, cents, pix_key, pix_type, name, document):
        types = {'cpf':'CPF','cnpj':'CNPJ','phone':'PHONE','email':'EMAIL','evp':'EVP','key':'EVP'}
        key_type = types.get(pix_type.lower())
        if pix_type.lower() == 'key':
            key_type = 'EMAIL' if '@' in pix_key else 'EVP'
        if not key_type:
            raise NowHubError('Tipo PIX não suportado')
        return await self.request('POST', '/withdrawals/withdraw', payload={
            'external_id': identifier, 'amount': float(Decimal(cents)/100),
            'pix_key': pix_key, 'pix_key_type': key_type,
            'beneficiary': {'name': name, 'document': document},
        })

    async def balance(self):
        return await self.request('GET', '/balance')

client = NowHubClient()
