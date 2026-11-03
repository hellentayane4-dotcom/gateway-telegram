import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
BANCO = BASE_DIR / "smokepay.db"


def conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(BANCO, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


@contextmanager
def transacao_imediata():
    conn = conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def criar_banco() -> None:
    conn = conectar()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                telegram_id INTEGER PRIMARY KEY,
                nome TEXT NOT NULL,
                username TEXT,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS transacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                tipo TEXT NOT NULL CHECK (tipo IN ('deposito', 'saque')),
                valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
                status TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN (
                    'pendente','pago','processando','concluido','recusado','cancelado'
                )),
                referencia_externa TEXT UNIQUE,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_id) REFERENCES usuarios(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS saldos (
                telegram_id INTEGER PRIMARY KEY,
                disponivel_centavos INTEGER NOT NULL DEFAULT 0,
                reservado_centavos INTEGER NOT NULL DEFAULT 0 CHECK (reservado_centavos >= 0),
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_id) REFERENCES usuarios(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS movimentos_saldo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                tipo TEXT NOT NULL,
                valor_centavos INTEGER NOT NULL,
                referencia TEXT NOT NULL UNIQUE,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_id) REFERENCES usuarios(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS depositos_api (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                identificador TEXT NOT NULL UNIQUE,
                provider_transaction_id TEXT UNIQUE,
                provider TEXT NOT NULL DEFAULT 'legacy',
                payment_url TEXT,
                valor_bruto_centavos INTEGER NOT NULL CHECK (valor_bruto_centavos > 0),
                taxa_centavos INTEGER NOT NULL CHECK (taxa_centavos >= 0),
                valor_liquido_centavos INTEGER NOT NULL CHECK (valor_liquido_centavos > 0),
                status TEXT NOT NULL DEFAULT 'criando',
                pix_code TEXT,
                pix_image_url TEXT,
                expires_at TEXT,
                erro TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_id) REFERENCES usuarios(telegram_id)
            );

            CREATE INDEX IF NOT EXISTS idx_depositos_usuario
            ON depositos_api(telegram_id, criado_em DESC);

            CREATE TABLE IF NOT EXISTS saques_api (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                identificador TEXT NOT NULL UNIQUE,
                provider_withdraw_id TEXT UNIQUE,
                valor_solicitado_centavos INTEGER NOT NULL CHECK (valor_solicitado_centavos > 0),
                taxa_centavos INTEGER NOT NULL CHECK (taxa_centavos >= 0),
                valor_envio_centavos INTEGER NOT NULL CHECK (valor_envio_centavos > 0),
                pix_tipo TEXT NOT NULL,
                pix_chave_mascarada TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reservado',
                receipt_url TEXT,
                webhook_token_hash TEXT,
                erro TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_id) REFERENCES usuarios(telegram_id)
            );

            CREATE INDEX IF NOT EXISTS idx_saques_usuario
            ON saques_api(telegram_id, criado_em DESC);

            CREATE TABLE IF NOT EXISTS webhook_eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_hash TEXT NOT NULL UNIQUE,
                evento TEXT,
                referencia TEXT,
                resultado TEXT,
                recebido_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processado_em TEXT
            );

            CREATE TABLE IF NOT EXISTS captura_ip (
                token TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                ip TEXT,
                expira_em TEXT NOT NULL,
                usado INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_id) REFERENCES usuarios(telegram_id)
            );

            CREATE INDEX IF NOT EXISTS indice_transacoes_usuario
            ON transacoes(telegram_id, criado_em);
            """
        )
        # Migração compatível com versões intermediárias deste refactor.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(saques_api)").fetchall()}
        if "webhook_token_hash" not in cols:
            conn.execute("ALTER TABLE saques_api ADD COLUMN webhook_token_hash TEXT")

        dep_cols = {row[1] for row in conn.execute("PRAGMA table_info(depositos_api)").fetchall()}
        if "provider" not in dep_cols:
            conn.execute("ALTER TABLE depositos_api ADD COLUMN provider TEXT NOT NULL DEFAULT 'legacy'")
        if "payment_url" not in dep_cols:
            conn.execute("ALTER TABLE depositos_api ADD COLUMN payment_url TEXT")

        # Recursos da interface final: afiliados, comissões e chaves de operação.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS afiliados (
                telegram_id INTEGER PRIMARY KEY,
                convidado_por INTEGER,
                codigo TEXT NOT NULL UNIQUE,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (telegram_id) REFERENCES usuarios(telegram_id),
                FOREIGN KEY (convidado_por) REFERENCES usuarios(telegram_id)
            );

            CREATE INDEX IF NOT EXISTS idx_afiliados_convidado_por
            ON afiliados(convidado_por);

            CREATE TABLE IF NOT EXISTS comissoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                beneficiario_id INTEGER NOT NULL,
                origem_telegram_id INTEGER NOT NULL,
                deposito_id INTEGER NOT NULL,
                nivel INTEGER NOT NULL DEFAULT 1,
                valor_centavos INTEGER NOT NULL CHECK (valor_centavos > 0),
                status TEXT NOT NULL DEFAULT 'creditada',
                referencia TEXT NOT NULL UNIQUE,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (beneficiario_id) REFERENCES usuarios(telegram_id),
                FOREIGN KEY (origem_telegram_id) REFERENCES usuarios(telegram_id),
                FOREIGN KEY (deposito_id) REFERENCES depositos_api(id)
            );

            CREATE INDEX IF NOT EXISTS idx_comissoes_beneficiario
            ON comissoes(beneficiario_id, criado_em DESC);

            CREATE TABLE IF NOT EXISTS configuracoes_sistema (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute("INSERT OR IGNORE INTO configuracoes_sistema (chave, valor) VALUES ('maintenance_mode', '0')")
        conn.execute("INSERT OR IGNORE INTO configuracoes_sistema (chave, valor) VALUES ('deposits_enabled', '1')")
        conn.execute("INSERT OR IGNORE INTO configuracoes_sistema (chave, valor) VALUES ('withdrawals_enabled', '0')")

        # Garante carteira e afiliado/código para usuários antigos sem alterar seus dados.
        for row in conn.execute("SELECT telegram_id FROM usuarios").fetchall():
            tid = int(row[0])
            conn.execute("INSERT OR IGNORE INTO saldos (telegram_id) VALUES (?)", (tid,))
            codigo = f"SP{tid:X}"
            conn.execute(
                "INSERT OR IGNORE INTO afiliados (telegram_id, codigo) VALUES (?, ?)",
                (tid, codigo),
            )

        # Migra somente uma vez os saldos de teste antigos, caso o saldo novo ainda
        # esteja zerado. Isso preserva o banco original sem somar valores repetidos.
        existe_teste = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teste_saldos'"
        ).fetchone()
        if existe_teste:
            for row in conn.execute("SELECT telegram_id, saldo_centavos FROM teste_saldos").fetchall():
                tid, saldo_antigo = int(row[0]), int(row[1])
                atual = conn.execute(
                    "SELECT disponivel_centavos FROM saldos WHERE telegram_id=?", (tid,)
                ).fetchone()
                if atual is not None and int(atual[0]) == 0 and saldo_antigo > 0:
                    conn.execute(
                        "UPDATE saldos SET disponivel_centavos=?, atualizado_em=CURRENT_TIMESTAMP WHERE telegram_id=?",
                        (saldo_antigo, tid),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO movimentos_saldo (telegram_id, tipo, valor_centavos, referencia) VALUES (?, 'migracao_teste', ?, ?)",
                        (tid, saldo_antigo, f"migracao-teste:{tid}"),
                    )
    finally:
        conn.close()


def cadastrar_usuario(telegram_id: int, nome: str, username: str | None = None) -> None:
    with transacao_imediata() as conn:
        conn.execute(
            """
            INSERT INTO usuarios (telegram_id, nome, username)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                nome = excluded.nome,
                username = excluded.username
            """,
            (telegram_id, nome, username),
        )
        conn.execute(
            "INSERT OR IGNORE INTO saldos (telegram_id) VALUES (?)",
            (telegram_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO afiliados (telegram_id, codigo) VALUES (?, ?)",
            (telegram_id, f"SP{telegram_id:X}"),
        )


def consultar_usuario(telegram_id: int):
    conn = conectar()
    try:
        return conn.execute(
            "SELECT * FROM usuarios WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    finally:
        conn.close()


def consultar_saldo(telegram_id: int) -> dict[str, int]:
    conn = conectar()
    try:
        row = conn.execute(
            "SELECT disponivel_centavos, reservado_centavos FROM saldos WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if not row:
            return {"disponivel_centavos": 0, "reservado_centavos": 0}
        return dict(row)
    finally:
        conn.close()


def criar_deposito(
    telegram_id: int,
    identificador: str,
    valor_bruto_centavos: int,
    taxa_centavos: int,
    valor_liquido_centavos: int,
    provider: str = "legacy",
) -> int:
    with transacao_imediata() as conn:
        cur = conn.execute(
            """
            INSERT INTO depositos_api (
                telegram_id, identificador, provider, valor_bruto_centavos,
                taxa_centavos, valor_liquido_centavos, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'criando')
            """,
            (
                telegram_id,
                identificador,
                provider,
                valor_bruto_centavos,
                taxa_centavos,
                valor_liquido_centavos,
            ),
        )
        return int(cur.lastrowid)


def atualizar_deposito_criado(
    identificador: str,
    provider_transaction_id: str,
    pix_code: str | None,
    pix_image_url: str | None,
    expires_at: str | None,
    payment_url: str | None = None,
    provider: str | None = None,
) -> None:
    with transacao_imediata() as conn:
        conn.execute(
            """
            UPDATE depositos_api
            SET provider_transaction_id = ?, pix_code = ?, pix_image_url = ?,
                expires_at = ?, payment_url = ?,
                provider = COALESCE(?, provider),
                status = 'pendente', atualizado_em = CURRENT_TIMESTAMP
            WHERE identificador = ?
            """,
            (provider_transaction_id, pix_code, pix_image_url, expires_at, payment_url, provider, identificador),
        )


def marcar_deposito_erro(identificador: str, erro: str) -> None:
    with transacao_imediata() as conn:
        conn.execute(
            """
            UPDATE depositos_api
            SET status='erro', erro=?, atualizado_em=CURRENT_TIMESTAMP
            WHERE identificador=?
            """,
            (erro[:500], identificador),
        )


def buscar_deposito_por_referencia(ref: str):
    conn = conectar()
    try:
        return conn.execute(
            """
            SELECT * FROM depositos_api
            WHERE provider_transaction_id = ? OR identificador = ?
            LIMIT 1
            """,
            (ref, ref),
        ).fetchone()
    finally:
        conn.close()


def confirmar_deposito_pago(ref: str, valor_evento_centavos: int | None = None):
    with transacao_imediata() as conn:
        dep = conn.execute(
            """
            SELECT * FROM depositos_api
            WHERE provider_transaction_id = ? OR identificador = ?
            LIMIT 1
            """,
            (ref, ref),
        ).fetchone()
        if dep is None:
            return None, "nao_encontrado"
        if valor_evento_centavos is not None and valor_evento_centavos != dep["valor_bruto_centavos"]:
            return dep, "valor_divergente"
        if dep["status"] == "pago":
            return dep, "ja_processado"
        if dep["status"] in {"estornado", "chargeback"}:
            return dep, "terminal"

        movimento_ref = f"deposito:{dep['id']}:credito"
        existe = conn.execute(
            "SELECT 1 FROM movimentos_saldo WHERE referencia = ?", (movimento_ref,)
        ).fetchone()
        if not existe:
            conn.execute(
                "INSERT OR IGNORE INTO saldos (telegram_id) VALUES (?)",
                (dep["telegram_id"],),
            )
            conn.execute(
                """
                UPDATE saldos
                SET disponivel_centavos = disponivel_centavos + ?, atualizado_em=CURRENT_TIMESTAMP
                WHERE telegram_id = ?
                """,
                (dep["valor_liquido_centavos"], dep["telegram_id"]),
            )
            conn.execute(
                """
                INSERT INTO movimentos_saldo (telegram_id, tipo, valor_centavos, referencia)
                VALUES (?, 'deposito_credito', ?, ?)
                """,
                (dep["telegram_id"], dep["valor_liquido_centavos"], movimento_ref),
            )
        conn.execute(
            """
            UPDATE depositos_api SET status='pago', atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (dep["id"],),
        )
        dep = conn.execute("SELECT * FROM depositos_api WHERE id=?", (dep["id"],)).fetchone()
        return dep, "creditado"


def marcar_deposito_status(ref: str, status: str):
    with transacao_imediata() as conn:
        dep = conn.execute(
            """
            SELECT * FROM depositos_api
            WHERE provider_transaction_id = ? OR identificador = ? LIMIT 1
            """,
            (ref, ref),
        ).fetchone()
        if dep is None:
            return None, "nao_encontrado"
        if dep["status"] == "pago" and status in {"cancelado", "recusado"}:
            return dep, "ignorado_pos_pagamento"
        conn.execute(
            "UPDATE depositos_api SET status=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
            (status, dep["id"]),
        )
        return conn.execute("SELECT * FROM depositos_api WHERE id=?", (dep["id"],)).fetchone(), "atualizado"


def reverter_deposito(ref: str, novo_status: str):
    with transacao_imediata() as conn:
        dep = conn.execute(
            """
            SELECT * FROM depositos_api
            WHERE provider_transaction_id = ? OR identificador = ? LIMIT 1
            """,
            (ref, ref),
        ).fetchone()
        if dep is None:
            return None, "nao_encontrado"
        if dep["status"] in {"estornado", "chargeback"}:
            return dep, "ja_processado"
        if dep["status"] != "pago":
            conn.execute(
                "UPDATE depositos_api SET status=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
                (novo_status, dep["id"]),
            )
            return dep, "sem_credito_para_reverter"

        movimento_ref = f"deposito:{dep['id']}:reversao"
        existe = conn.execute(
            "SELECT 1 FROM movimentos_saldo WHERE referencia=?", (movimento_ref,)
        ).fetchone()
        if not existe:
            conn.execute(
                """
                UPDATE saldos
                SET disponivel_centavos = disponivel_centavos - ?, atualizado_em=CURRENT_TIMESTAMP
                WHERE telegram_id=?
                """,
                (dep["valor_liquido_centavos"], dep["telegram_id"]),
            )
            conn.execute(
                """
                INSERT INTO movimentos_saldo (telegram_id, tipo, valor_centavos, referencia)
                VALUES (?, 'deposito_reversao', ?, ?)
                """,
                (dep["telegram_id"], -dep["valor_liquido_centavos"], movimento_ref),
            )
        # Reverte também comissões originadas por este depósito, uma única vez.
        comissoes = conn.execute(
            "SELECT * FROM comissoes WHERE deposito_id=? AND status='creditada'",
            (dep["id"],),
        ).fetchall()
        for comissao in comissoes:
            ref_comissao = f"{comissao['referencia']}:reversao"
            ja = conn.execute(
                "SELECT 1 FROM movimentos_saldo WHERE referencia=?", (ref_comissao,)
            ).fetchone()
            if not ja:
                conn.execute(
                    "UPDATE saldos SET disponivel_centavos=disponivel_centavos-?, atualizado_em=CURRENT_TIMESTAMP WHERE telegram_id=?",
                    (comissao["valor_centavos"], comissao["beneficiario_id"]),
                )
                conn.execute(
                    "INSERT INTO movimentos_saldo (telegram_id, tipo, valor_centavos, referencia) VALUES (?, 'comissao_reversao', ?, ?)",
                    (comissao["beneficiario_id"], -comissao["valor_centavos"], ref_comissao),
                )
            conn.execute(
                "UPDATE comissoes SET status='revertida' WHERE id=?", (comissao["id"],)
            )

        conn.execute(
            "UPDATE depositos_api SET status=?, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
            (novo_status, dep["id"]),
        )
        return conn.execute("SELECT * FROM depositos_api WHERE id=?", (dep["id"],)).fetchone(), "revertido"


def listar_depositos(telegram_id: int, limite: int = 10):
    conn = conectar()
    try:
        return conn.execute(
            """
            SELECT * FROM depositos_api WHERE telegram_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (telegram_id, limite),
        ).fetchall()
    finally:
        conn.close()


def criar_saque_reservando(
    telegram_id: int,
    identificador: str,
    valor_solicitado_centavos: int,
    taxa_centavos: int,
    valor_envio_centavos: int,
    pix_tipo: str,
    pix_chave_mascarada: str,
) -> tuple[int | None, str]:
    with transacao_imediata() as conn:
        saldo = conn.execute(
            "SELECT disponivel_centavos FROM saldos WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        disponivel = int(saldo[0]) if saldo else 0
        if disponivel < valor_solicitado_centavos:
            return None, "saldo_insuficiente"

        cur = conn.execute(
            """
            INSERT INTO saques_api (
                telegram_id, identificador, valor_solicitado_centavos,
                taxa_centavos, valor_envio_centavos, pix_tipo, pix_chave_mascarada, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'reservado')
            """,
            (
                telegram_id,
                identificador,
                valor_solicitado_centavos,
                taxa_centavos,
                valor_envio_centavos,
                pix_tipo,
                pix_chave_mascarada,
            ),
        )
        saque_id = int(cur.lastrowid)
        conn.execute(
            """
            UPDATE saldos SET
                disponivel_centavos = disponivel_centavos - ?,
                reservado_centavos = reservado_centavos + ?,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE telegram_id=?
            """,
            (valor_solicitado_centavos, valor_solicitado_centavos, telegram_id),
        )
        conn.execute(
            """
            INSERT INTO movimentos_saldo (telegram_id, tipo, valor_centavos, referencia)
            VALUES (?, 'saque_reserva', ?, ?)
            """,
            (telegram_id, -valor_solicitado_centavos, f"saque:{saque_id}:reserva"),
        )
        return saque_id, "reservado"


def marcar_saque_enviado(
    identificador: str,
    provider_withdraw_id: str | None,
    receipt_url: str | None,
    webhook_token: str | None = None,
) -> None:
    token_hash = hashlib.sha256(webhook_token.encode("utf-8")).hexdigest() if webhook_token else None
    with transacao_imediata() as conn:
        conn.execute(
            """
            UPDATE saques_api SET provider_withdraw_id=?, receipt_url=?, webhook_token_hash=?,
                status='processando', atualizado_em=CURRENT_TIMESTAMP
            WHERE identificador=?
            """,
            (provider_withdraw_id, receipt_url, token_hash, identificador),
        )


def buscar_saque_por_referencia(ref: str):
    conn = conectar()
    try:
        return conn.execute(
            """
            SELECT * FROM saques_api
            WHERE identificador=? OR provider_withdraw_id=? LIMIT 1
            """,
            (ref, ref),
        ).fetchone()
    finally:
        conn.close()


def concluir_saque(ref: str):
    with transacao_imediata() as conn:
        saque = conn.execute(
            """
            SELECT * FROM saques_api WHERE identificador=? OR provider_withdraw_id=? LIMIT 1
            """,
            (ref, ref),
        ).fetchone()
        if saque is None:
            return None, "nao_encontrado"
        if saque["status"] == "concluido":
            return saque, "ja_processado"
        if saque["status"] in {"falhou", "cancelado"}:
            return saque, "terminal"
        movimento_ref = f"saque:{saque['id']}:conclusao"
        existe = conn.execute(
            "SELECT 1 FROM movimentos_saldo WHERE referencia=?", (movimento_ref,)
        ).fetchone()
        if not existe:
            conn.execute(
                """
                UPDATE saldos SET reservado_centavos = reservado_centavos - ?,
                    atualizado_em=CURRENT_TIMESTAMP WHERE telegram_id=?
                """,
                (saque["valor_solicitado_centavos"], saque["telegram_id"]),
            )
            conn.execute(
                """
                INSERT INTO movimentos_saldo (telegram_id, tipo, valor_centavos, referencia)
                VALUES (?, 'saque_concluido', ?, ?)
                """,
                (saque["telegram_id"], -saque["valor_solicitado_centavos"], movimento_ref),
            )
        conn.execute(
            "UPDATE saques_api SET status='concluido', atualizado_em=CURRENT_TIMESTAMP WHERE id=?",
            (saque["id"],),
        )
        return conn.execute("SELECT * FROM saques_api WHERE id=?", (saque["id"],)).fetchone(), "concluido"


def liberar_saque(ref: str, motivo: str = ""):
    with transacao_imediata() as conn:
        saque = conn.execute(
            """
            SELECT * FROM saques_api WHERE identificador=? OR provider_withdraw_id=? LIMIT 1
            """,
            (ref, ref),
        ).fetchone()
        if saque is None:
            return None, "nao_encontrado"
        if saque["status"] in {"falhou", "cancelado"}:
            return saque, "ja_processado"
        if saque["status"] == "concluido":
            return saque, "terminal"
        movimento_ref = f"saque:{saque['id']}:liberacao"
        existe = conn.execute(
            "SELECT 1 FROM movimentos_saldo WHERE referencia=?", (movimento_ref,)
        ).fetchone()
        if not existe:
            conn.execute(
                """
                UPDATE saldos SET
                    disponivel_centavos = disponivel_centavos + ?,
                    reservado_centavos = reservado_centavos - ?,
                    atualizado_em=CURRENT_TIMESTAMP
                WHERE telegram_id=?
                """,
                (
                    saque["valor_solicitado_centavos"],
                    saque["valor_solicitado_centavos"],
                    saque["telegram_id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO movimentos_saldo (telegram_id, tipo, valor_centavos, referencia)
                VALUES (?, 'saque_liberado', ?, ?)
                """,
                (saque["telegram_id"], saque["valor_solicitado_centavos"], movimento_ref),
            )
        conn.execute(
            """
            UPDATE saques_api SET status='falhou', erro=?, atualizado_em=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (motivo[:500], saque["id"]),
        )
        return conn.execute("SELECT * FROM saques_api WHERE id=?", (saque["id"],)).fetchone(), "liberado"


def listar_saques(telegram_id: int, limite: int = 10):
    conn = conectar()
    try:
        return conn.execute(
            "SELECT * FROM saques_api WHERE telegram_id=? ORDER BY id DESC LIMIT ?",
            (telegram_id, limite),
        ).fetchall()
    finally:
        conn.close()


def resumo_usuario(telegram_id: int) -> dict[str, int]:
    conn = conectar()
    try:
        dep = conn.execute(
            "SELECT COUNT(*) FROM depositos_api WHERE telegram_id=?", (telegram_id,)
        ).fetchone()[0]
        saq = conn.execute(
            "SELECT COUNT(*) FROM saques_api WHERE telegram_id=?", (telegram_id,)
        ).fetchone()[0]
        return {"depositos": dep, "saques": saq}
    finally:
        conn.close()


def registrar_webhook(payload: dict[str, Any], evento: str | None, referencia: str | None):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with transacao_imediata() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO webhook_eventos (payload_hash, evento, referencia, resultado)
                VALUES (?, ?, ?, 'recebido')
                """,
                (digest, evento, referencia),
            )
            return int(cur.lastrowid), False
        except sqlite3.IntegrityError:
            return None, True


def finalizar_webhook(payload: dict[str, Any], resultado: str) -> None:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with transacao_imediata() as conn:
        conn.execute(
            """
            UPDATE webhook_eventos SET resultado=?, processado_em=CURRENT_TIMESTAMP
            WHERE payload_hash=?
            """,
            (resultado[:300], digest),
        )


def criar_token_captura_ip(telegram_id: int, minutos: int = 15) -> str:
    token = secrets.token_urlsafe(24)
    expira = datetime.now(timezone.utc) + timedelta(minutes=minutos)
    with transacao_imediata() as conn:
        conn.execute("DELETE FROM captura_ip WHERE telegram_id=? OR expira_em < ?", (telegram_id, datetime.now(timezone.utc).isoformat()))
        conn.execute(
            "INSERT INTO captura_ip (token, telegram_id, expira_em) VALUES (?, ?, ?)",
            (token, telegram_id, expira.isoformat()),
        )
    return token


def salvar_ip_capturado(token: str, ip: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with transacao_imediata() as conn:
        cur = conn.execute(
            """
            UPDATE captura_ip SET ip=?
            WHERE token=? AND usado=0 AND expira_em > ?
            """,
            (ip, token, now),
        )
        return cur.rowcount == 1


def consultar_ip_capturado(token: str, telegram_id: int) -> str | None:
    now = datetime.now(timezone.utc).isoformat()
    conn = conectar()
    try:
        row = conn.execute(
            """
            SELECT ip FROM captura_ip
            WHERE token=? AND telegram_id=? AND usado=0 AND expira_em > ?
            """,
            (token, telegram_id, now),
        ).fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def consumir_token_ip(token: str, telegram_id: int) -> None:
    with transacao_imediata() as conn:
        conn.execute(
            "DELETE FROM captura_ip WHERE token=? AND telegram_id=?", (token, telegram_id)
        )


def verificar_integridade() -> bool:
    conn = conectar()
    try:
        return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def vincular_indicacao(telegram_id: int, codigo_convite: str | None) -> bool:
    """Vincula o usuário ao primeiro indicador válido. Nunca troca um vínculo existente."""
    if not codigo_convite:
        return False
    codigo = codigo_convite.strip().upper()
    with transacao_imediata() as conn:
        proprio = conn.execute(
            "SELECT convidado_por FROM afiliados WHERE telegram_id=?", (telegram_id,)
        ).fetchone()
        if proprio is None or proprio[0] is not None:
            return False
        indicador = conn.execute(
            "SELECT telegram_id FROM afiliados WHERE codigo=?", (codigo,)
        ).fetchone()
        if not indicador or int(indicador[0]) == telegram_id:
            return False
        cur = conn.execute(
            "UPDATE afiliados SET convidado_por=? WHERE telegram_id=? AND convidado_por IS NULL",
            (int(indicador[0]), telegram_id),
        )
        return cur.rowcount == 1


def codigo_afiliado(telegram_id: int) -> str:
    conn = conectar()
    try:
        row = conn.execute("SELECT codigo FROM afiliados WHERE telegram_id=?", (telegram_id,)).fetchone()
        return str(row[0]) if row else f"SP{telegram_id:X}"
    finally:
        conn.close()


def resumo_rede(telegram_id: int, niveis: int = 5) -> dict[str, Any]:
    conn = conectar()
    try:
        rows = conn.execute(
            """
            WITH RECURSIVE rede(telegram_id, nivel) AS (
                SELECT telegram_id, 1 FROM afiliados WHERE convidado_por=?
                UNION ALL
                SELECT a.telegram_id, rede.nivel + 1
                FROM afiliados a JOIN rede ON a.convidado_por = rede.telegram_id
                WHERE rede.nivel < ?
            )
            SELECT nivel, COUNT(*) AS qtd FROM rede GROUP BY nivel ORDER BY nivel
            """,
            (telegram_id, niveis),
        ).fetchall()
        por_nivel = {int(r["nivel"]): int(r["qtd"]) for r in rows}
        total = sum(por_nivel.values())
        comissao = conn.execute(
            "SELECT COALESCE(SUM(valor_centavos),0) FROM comissoes WHERE beneficiario_id=? AND status='creditada'",
            (telegram_id,),
        ).fetchone()[0]
        return {"niveis": por_nivel, "total": total, "comissao_centavos": int(comissao or 0)}
    finally:
        conn.close()


def aplicar_comissao_afiliado(deposito_id: int, percentual_bp: int) -> tuple[sqlite3.Row | None, str]:
    """Crédito idempotente ao indicador direto. Com percentual 0 não faz nada."""
    if percentual_bp <= 0:
        return None, "desativada"
    with transacao_imediata() as conn:
        dep = conn.execute("SELECT * FROM depositos_api WHERE id=?", (deposito_id,)).fetchone()
        if dep is None or dep["status"] != "pago":
            return None, "deposito_invalido"
        rel = conn.execute("SELECT convidado_por FROM afiliados WHERE telegram_id=?", (dep["telegram_id"],)).fetchone()
        if not rel or rel[0] is None:
            return None, "sem_indicador"
        beneficiario = int(rel[0])
        valor = (int(dep["valor_liquido_centavos"]) * percentual_bp) // 10000
        if valor <= 0:
            return None, "valor_zero"
        referencia = f"comissao:deposito:{deposito_id}:nivel:1"
        try:
            cur = conn.execute(
                """
                INSERT INTO comissoes (
                    beneficiario_id, origem_telegram_id, deposito_id, nivel,
                    valor_centavos, status, referencia
                ) VALUES (?, ?, ?, 1, ?, 'creditada', ?)
                """,
                (beneficiario, dep["telegram_id"], deposito_id, valor, referencia),
            )
        except sqlite3.IntegrityError:
            return None, "ja_creditada"
        conn.execute(
            "INSERT OR IGNORE INTO saldos (telegram_id) VALUES (?)", (beneficiario,)
        )
        conn.execute(
            "UPDATE saldos SET disponivel_centavos=disponivel_centavos+?, atualizado_em=CURRENT_TIMESTAMP WHERE telegram_id=?",
            (valor, beneficiario),
        )
        conn.execute(
            "INSERT INTO movimentos_saldo (telegram_id, tipo, valor_centavos, referencia) VALUES (?, 'comissao', ?, ?)",
            (beneficiario, valor, referencia),
        )
        return conn.execute("SELECT * FROM comissoes WHERE id=?", (cur.lastrowid,)).fetchone(), "creditada"


def resumo_financeiro(telegram_id: int) -> dict[str, int]:
    conn = conectar()
    try:
        dep_pago = conn.execute(
            "SELECT COALESCE(SUM(valor_bruto_centavos),0) FROM depositos_api WHERE telegram_id=? AND status='pago'",
            (telegram_id,),
        ).fetchone()[0]
        sacado = conn.execute(
            "SELECT COALESCE(SUM(valor_solicitado_centavos),0) FROM saques_api WHERE telegram_id=? AND status='concluido'",
            (telegram_id,),
        ).fetchone()[0]
        pendentes = conn.execute(
            "SELECT COUNT(*) FROM depositos_api WHERE telegram_id=? AND status IN ('criando','pendente')",
            (telegram_id,),
        ).fetchone()[0] + conn.execute(
            "SELECT COUNT(*) FROM saques_api WHERE telegram_id=? AND status IN ('reservado','processando')",
            (telegram_id,),
        ).fetchone()[0]
        comissoes = conn.execute(
            "SELECT COALESCE(SUM(valor_centavos),0) FROM comissoes WHERE beneficiario_id=? AND status='creditada'",
            (telegram_id,),
        ).fetchone()[0]
        return {
            "depositado_centavos": int(dep_pago or 0),
            "sacado_centavos": int(sacado or 0),
            "pendentes": int(pendentes or 0),
            "comissoes_centavos": int(comissoes or 0),
        }
    finally:
        conn.close()


def resumo_mensal(telegram_id: int, limite: int = 6):
    conn = conectar()
    try:
        return conn.execute(
            """
            WITH meses AS (
                SELECT strftime('%Y-%m', criado_em) mes,
                       SUM(CASE WHEN status='pago' THEN valor_bruto_centavos ELSE 0 END) depositos,
                       0 saques
                FROM depositos_api WHERE telegram_id=? GROUP BY mes
                UNION ALL
                SELECT strftime('%Y-%m', criado_em) mes, 0 depositos,
                       SUM(CASE WHEN status='concluido' THEN valor_solicitado_centavos ELSE 0 END) saques
                FROM saques_api WHERE telegram_id=? GROUP BY mes
            )
            SELECT mes, SUM(depositos) depositos, SUM(saques) saques
            FROM meses GROUP BY mes ORDER BY mes DESC LIMIT ?
            """,
            (telegram_id, telegram_id, limite),
        ).fetchall()
    finally:
        conn.close()


def obter_config(chave: str, padrao: str = "") -> str:
    conn = conectar()
    try:
        row = conn.execute("SELECT valor FROM configuracoes_sistema WHERE chave=?", (chave,)).fetchone()
        return str(row[0]) if row else padrao
    finally:
        conn.close()


def definir_config(chave: str, valor: str) -> None:
    with transacao_imediata() as conn:
        conn.execute(
            """
            INSERT INTO configuracoes_sistema (chave, valor) VALUES (?, ?)
            ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor, atualizado_em=CURRENT_TIMESTAMP
            """,
            (chave, valor),
        )


def config_bool(chave: str, padrao: bool = False) -> bool:
    valor = obter_config(chave, "1" if padrao else "0").strip().lower()
    return valor in {"1", "true", "sim", "yes", "on"}


def estatisticas_admin() -> dict[str, int]:
    conn = conectar()
    try:
        usuarios = int(conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0])
        saldo = int(conn.execute("SELECT COALESCE(SUM(disponivel_centavos),0) FROM saldos").fetchone()[0] or 0)
        reservado = int(conn.execute("SELECT COALESCE(SUM(reservado_centavos),0) FROM saldos").fetchone()[0] or 0)
        volume_dep = int(conn.execute("SELECT COALESCE(SUM(valor_bruto_centavos),0) FROM depositos_api WHERE status='pago'").fetchone()[0] or 0)
        volume_saq = int(conn.execute("SELECT COALESCE(SUM(valor_solicitado_centavos),0) FROM saques_api WHERE status='concluido'").fetchone()[0] or 0)
        pend_dep = int(conn.execute("SELECT COUNT(*) FROM depositos_api WHERE status IN ('criando','pendente')").fetchone()[0])
        pend_saq = int(conn.execute("SELECT COUNT(*) FROM saques_api WHERE status IN ('reservado','processando')").fetchone()[0])
        return {
            "usuarios": usuarios,
            "saldo_centavos": saldo,
            "reservado_centavos": reservado,
            "volume_dep_centavos": volume_dep,
            "volume_saq_centavos": volume_saq,
            "depositos_pendentes": pend_dep,
            "saques_pendentes": pend_saq,
        }
    finally:
        conn.close()
