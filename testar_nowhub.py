
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
        try:
            resposta = await client.post(
                "https://api.nowhubglobal.com/v1/auth/login",
                json={
                    "client_id": client_id,
                    "client_secret": client_secret
                }
            )

            if resposta.status_code == 200:
                dados = resposta.json()
                if dados.get("access_token"):
                    print("SUCESSO: Autenticacao NowHub funcionando!")
                    print("Token JWT recebido com seguranca.")
                    print("Validade:", dados.get("expires_in"), "segundos")
                else:
                    print("Resposta sem access_token.")
            else:
                print("Falha na autenticacao.")
                print("HTTP:", resposta.status_code)

        except httpx.RequestError as erro:
            print("Erro de conexao:", type(erro).__name__)

asyncio.run(testar())
