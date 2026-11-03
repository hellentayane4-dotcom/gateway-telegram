
import asyncio
import os

import httpx
from dotenv import load_dotenv

load_dotenv()


async def testar():
    client_id = os.getenv("NOWHUB_CLIENT_ID")
    client_secret = os.getenv("NOWHUB_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("ERRO: Credenciais NowHub ausentes no .env")
        return

    async with httpx.AsyncClient(timeout=20) as client:
        login = await client.post(
            "https://api.nowhubglobal.com/v1/auth/login",
            json={
                "client_id": client_id,
                "client_secret": client_secret
            }
        )
        login.raise_for_status()

        token = login.json()["access_token"]

        saldo = await client.get(
            "https://api.nowhubglobal.com/v1/balance",
            headers={
                "Authorization": f"Bearer {token}"
            }
        )
        saldo.raise_for_status()

        dados = saldo.json()

        print("\nCONSULTA NOWHUB FUNCIONANDO!")
        print(f"Saldo disponivel: R$ {dados['saldo_disponivel']:.2f}")
        print(f"PIX pagos: {dados['pix_pago']}")
        print(f"PIX gerados: {dados['pix_gerados']}")
        print(f"Volume total: {dados['volume_total']}")
        print(
            f"Saldo a liberar cartao: "
            f"R$ {dados['saldo_a_liberar_cartao']:.2f}"
        )
        print(
            f"Proxima liberacao: "
            f"{dados.get('proxima_liberacao_cartao')}"
        )


try:
    asyncio.run(testar())
except httpx.HTTPStatusError as erro:
    print("Erro HTTP:", erro.response.status_code)
except httpx.RequestError as erro:
    print("Erro de conexao:", type(erro).__name__)
except Exception as erro:
    print("Erro:", type(erro).__name__, str(erro))
