import asyncio
import io
import os
import threading
import uuid
import logging
import json
import hashlib
import hmac
import base64
import sqlite3
from typing import Optional, Dict, List
from datetime import datetime, timedelta

import requests
import qrcode
from flask import Flask, jsonify, request as flask_request
from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─── Configuração de Logs ─────────────────────────────────────────────────────

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Configuração ─────────────────────────────────────────────────────────────

TOKEN = os.getenv("BOT_TOKEN", "8836174688:AAEZchrHohYkL7n2PZuCuQt2WGZmJB_66Eo")
API_KEY = os.getenv("API_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0Z19pZCI6ODkyMjg4MTA4OSwiZGJfbm0iOiJzdWJfZGF0YTEwMCJ9.xVGegt96SZ35LGijZ9CUznNTWgEr7bRdGGeh7cOSgKI")
API_BASE_URL = os.getenv("API_BASE_URL", "https://public-api.undresstool.fun")
CALLBACK_URL = os.getenv("CALLBACK_URL", "https://deepbotfff.squareweb.app/webhook")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "80"))

# ─── Configuração LofyPay ──────────────────────────────────────────────────

LOFYPAY_PUBLIC = os.getenv("LOFYPAY_PUBLIC", "pk_live_cf115ac5e35f2fa1cbe281ba")
LOFYPAY_SECRET = os.getenv("LOFYPAY_SECRET", "sk_live_c81ae9a709a600b13ce59e2a25acd215ef15f24816174e95")
LOFYPAY_URL = "https://app.lofypay.com/api/v1/gateway"
LOFYPAY_WEBHOOK_URL = os.getenv("LOFYPAY_WEBHOOK_URL", "https://deepbotfff.squareweb.app/lofypay_webhook")

# ─── Configuração do Admin ──────────────────────────────────────────────────

ADMIN_ID = 8922881089
BOT_USERNAME = "bot"  # sobrescrito em main() com o username real

# ─── Canais de Log ────────────────────────────────────────────────────────────

CANAL_PAGAMENTO = -1003931074376
CANAL_ERROS     = -1004460228565
CANAL_IMAGEM    = -1004375816993
CANAL_RESULTADO = -1003948709993

# ═══════════════════════════════════════════════════════════════════════════
# 🗄️  BANCO DE DADOS SQLITE
# ═══════════════════════════════════════════════════════════════════════════

DB_PATH = "bot_data.db"

def get_db():
    """Retorna uma conexão com o banco de dados."""
    return sqlite3.connect(DB_PATH)

def init_db():
    """Inicializa o banco de dados com todas as tabelas."""
    conn = get_db()
    cursor = conn.cursor()
    
    # Tabela de usuários
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            credits INTEGER DEFAULT 0,
            referral_code TEXT UNIQUE,
            referred_by INTEGER,
            total_earned INTEGER DEFAULT 0,
            joined_at TEXT,
            last_active TEXT
        )
    ''')
    
    # Tabela de indicações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            date TEXT,
            FOREIGN KEY (referrer_id) REFERENCES users(user_id),
            FOREIGN KEY (referred_id) REFERENCES users(user_id)
        )
    ''')
    
    # Tabela de transações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            type TEXT,
            description TEXT,
            date TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Tabela de processamentos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            style TEXT,
            params TEXT,
            credits_used INTEGER,
            date TEXT,
            status TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Tabela de compras
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            package_id TEXT,
            credits INTEGER,
            amount REAL,
            transaction_id TEXT UNIQUE,
            status TEXT,
            date TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Tabela de configurações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # Tabela de estatísticas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            daily_processed INTEGER DEFAULT 0,
            monthly_processed INTEGER DEFAULT 0,
            total_processed INTEGER DEFAULT 0,
            total_users INTEGER DEFAULT 0,
            total_revenue REAL DEFAULT 0,
            total_affiliate_payouts INTEGER DEFAULT 0
        )
    ''')
    
    # Tabela de pacotes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS credit_packages (
            id TEXT PRIMARY KEY,
            credits INTEGER,
            price REAL,
            label TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("✅ Banco de dados inicializado")

# ─── Funções do Banco - Usuários ────────────────────────────────────────────

def create_user(user_id: int, referral_code: str = None, referred_by: int = None):
    """Cria um novo usuário."""
    conn = get_db()
    cursor = conn.cursor()
    
    if not referral_code:
        referral_code = hashlib.md5(f"{user_id}{uuid.uuid4()}".encode()).hexdigest()[:8].upper()
    
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, credits, referral_code, referred_by, joined_at, last_active)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, 0, referral_code, referred_by, datetime.now().isoformat(), datetime.now().isoformat()))
    
    conn.commit()
    conn.close()

def get_user(user_id: int) -> Optional[dict]:
    """Retorna dados de um usuário."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, credits, referral_code, referred_by, total_earned, joined_at, last_active
        FROM users WHERE user_id = ?
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "user_id": row[0],
            "credits": row[1],
            "referral_code": row[2],
            "referred_by": row[3],
            "total_earned": row[4],
            "joined_at": row[5],
            "last_active": row[6]
        }
    return None

def get_user_credits_db(user_id: int) -> int:
    """Retorna os créditos do usuário."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT credits FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

def add_user_credits_db(user_id: int, amount: int):
    """Adiciona créditos a um usuário."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users SET credits = credits + ? WHERE user_id = ?
    ''', (amount, user_id))
    conn.commit()
    conn.close()
    logger.info(f"💰 +{amount} créditos para usuário {user_id}")

def deduct_user_credits_db(user_id: int, amount: int) -> bool:
    """Deduz créditos. Retorna True se conseguiu."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT credits FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    
    if row and row[0] >= amount:
        cursor.execute('''
            UPDATE users SET credits = credits - ? WHERE user_id = ?
        ''', (amount, user_id))
        conn.commit()
        conn.close()
        return True
    
    conn.close()
    return False

def get_all_users_db() -> List[dict]:
    """Retorna todos os usuários."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, credits, referral_code, referred_by, total_earned FROM users')
    rows = cursor.fetchall()
    conn.close()
    
    return [{
        "user_id": row[0],
        "credits": row[1],
        "referral_code": row[2],
        "referred_by": row[3],
        "total_earned": row[4]
    } for row in rows]

def get_total_users_db() -> int:
    """Retorna o número total de usuários."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    count = cursor.fetchone()[0]
    conn.close()
    return count

# ─── Funções do Banco - Indicações ──────────────────────────────────────────

def add_referral_db(referrer_id: int, referred_id: int):
    """Registra uma indicação."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO referrals (referrer_id, referred_id, date)
        VALUES (?, ?, ?)
    ''', (referrer_id, referred_id, datetime.now().isoformat()))
    
    cursor.execute('''
        UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?
    ''', (system_config_get("referral_reward", 5), referrer_id))
    
    cursor.execute('''
        UPDATE users SET referred_by = ? WHERE user_id = ?
    ''', (referrer_id, referred_id))
    
    conn.commit()
    conn.close()

def get_user_referrals_db(user_id: int) -> List[int]:
    """Retorna lista de usuários indicados."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT referred_id FROM referrals WHERE referrer_id = ?', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_referral_count_db(user_id: int) -> int:
    """Retorna número de indicações."""
    return len(get_user_referrals_db(user_id))

# ─── Funções do Banco - Transações ──────────────────────────────────────────

def add_transaction_db(user_id: int, amount: int, type: str, description: str = ""):
    """Registra uma transação."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO transactions (user_id, amount, type, description, date)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, amount, type, description, datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ─── Funções do Banco - Processamentos ──────────────────────────────────────

def add_processing_db(user_id: int, type: str, style: str, params: str, credits_used: int, status: str = "pending"):
    """Registra um processamento."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO processing (user_id, type, style, params, credits_used, date, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, type, style, params, credits_used, datetime.now().isoformat(), status))
    conn.commit()
    conn.close()

def get_user_processing_count(user_id: int) -> int:
    """Retorna quantos processamentos o usuário já realizou."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM processing WHERE user_id = ?', (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

# ─── Funções do Banco - Compras ─────────────────────────────────────────────

def add_purchase_db(user_id: int, package_id: str, credits: int, amount: float, transaction_id: str, status: str = "pending"):
    """Registra uma compra."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO purchases (user_id, package_id, credits, amount, transaction_id, status, date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, package_id, credits, amount, transaction_id, status, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_purchase_status_db(transaction_id: str, status: str):
    """Atualiza status de uma compra."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE purchases SET status = ? WHERE transaction_id = ?
    ''', (status, transaction_id))
    conn.commit()
    conn.close()

def get_purchase_db(transaction_id: str) -> Optional[dict]:
    """Retorna dados de uma compra."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, package_id, credits, amount, status FROM purchases
        WHERE transaction_id = ?
    ''', (transaction_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "user_id": row[0],
            "package_id": row[1],
            "credits": row[2],
            "amount": row[3],
            "status": row[4]
        }
    return None

# ─── Funções do Banco - Configurações ──────────────────────────────────────

def system_config_set(key: str, value):
    """Salva uma configuração."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO system_config (key, value)
        VALUES (?, ?)
    ''', (key, json.dumps(value) if not isinstance(value, str) else str(value)))
    conn.commit()
    conn.close()

def system_config_get(key: str, default=None):
    """Retorna uma configuração."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM system_config WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        try:
            return json.loads(row[0])
        except:
            return row[0]
    return default

def system_config_get_all() -> dict:
    """Retorna todas as configurações."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT key, value FROM system_config')
    rows = cursor.fetchall()
    conn.close()
    
    config = {}
    for key, value in rows:
        try:
            config[key] = json.loads(value)
        except:
            config[key] = value
    return config

# ─── Funções do Banco - Pacotes ─────────────────────────────────────────────

def package_save_db(package_id: str, credits: int, price: float, label: str):
    """Salva um pacote."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO credit_packages (id, credits, price, label)
        VALUES (?, ?, ?, ?)
    ''', (package_id, credits, price, label))
    conn.commit()
    conn.close()

def package_delete_db(package_id: str):
    """Remove um pacote."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM credit_packages WHERE id = ?', (package_id,))
    conn.commit()
    conn.close()

def package_get_all_db() -> Dict[str, dict]:
    """Retorna todos os pacotes."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, credits, price, label FROM credit_packages')
    rows = cursor.fetchall()
    conn.close()
    
    packages = {}
    for row in rows:
        packages[row[0]] = {
            "credits": row[1],
            "price": row[2],
            "label": row[3]
        }
    return packages

# ─── Funções do Banco - Estatísticas ────────────────────────────────────────

def stats_update_db():
    """Atualiza estatísticas."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT id FROM stats WHERE date = ?', (today,))
    row = cursor.fetchone()
    
    if row:
        cursor.execute('''
            UPDATE stats SET
                daily_processed = daily_processed + 1,
                total_processed = total_processed + 1,
                total_users = (SELECT COUNT(*) FROM users)
            WHERE date = ?
        ''', (today,))
    else:
        cursor.execute('''
            INSERT INTO stats (date, daily_processed, total_processed, total_users)
            VALUES (?, 1, (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM users))
        ''', (today,))
    
    conn.commit()
    conn.close()

def stats_get_db() -> dict:
    """Retorna estatísticas."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            SUM(daily_processed),
            SUM(total_processed),
            MAX(total_users),
            SUM(total_revenue),
            SUM(total_affiliate_payouts)
        FROM stats
    ''')
    row = cursor.fetchone()
    conn.close()
    
    return {
        "total_processed": row[1] or 0,
        "total_users": row[2] or 0,
        "total_revenue": row[3] or 0.0,
        "total_affiliate_payouts": row[4] or 0
    }

# ─── Inicialização do Sistema ──────────────────────────────────────────────

def init_system():
    """Inicializa o sistema com configurações padrão."""
    # Pacotes padrão
    if not package_get_all_db():
        default_packages = {
            "10": {"credits": 10, "price": 14.00, "label": "10 créditos - R$14,00"},
            "20": {"credits": 20, "price": 22.00, "label": "20 créditos - R$22,00"},
            "100": {"credits": 100, "price": 85.00, "label": "100 créditos - R$85,00"},
        }
        for key, pkg in default_packages.items():
            package_save_db(key, pkg["credits"], pkg["price"], pkg["label"])
    
    # Configurações padrão
    default_config = {
        "photo_cost": 2,
        "photo_custom_cost": 3,
        "video_cost": 5,
        "referral_reward": 5,
        "referral_bonus": 2,
        "new_user_bonus": 0,
        "canal_pagamento": CANAL_PAGAMENTO,
        "canal_erros":     CANAL_ERROS,
        "canal_imagem":    CANAL_IMAGEM,
        "canal_resultado": CANAL_RESULTADO,
        "start_text": (
            "👋 *Bem-vindo ao Bot de Processamento de Mídia!*\n\n"
            "💰 *Seus créditos:* `{credits}`\n\n"
            "📌 Envie uma **foto** para começar a processar!\n\n"
            "Escolha uma opção abaixo:"
        ),
        "start_photo_id": "",
        "start_buttons": "[]",
    }
    for key, value in default_config.items():
        if system_config_get(key) is None:
            system_config_set(key, value)
    
    logger.info("✅ Sistema inicializado")

# ═══════════════════════════════════════════════════════════════════════════
# FIM DO BANCO DE DADOS
# ═══════════════════════════════════════════════════════════════════════════

# ─── Funções de Créditos (Wrappers) ────────────────────────────────────────

def get_user_credits(user_id: int) -> int:
    return get_user_credits_db(user_id)

def add_user_credits(user_id: int, amount: int):
    add_user_credits_db(user_id, amount)
    add_transaction_db(user_id, amount, "add", "Créditos adicionados")

def deduct_user_credits(user_id: int, amount: int) -> bool:
    return deduct_user_credits_db(user_id, amount)

# ─── Funções de Afiliados (Wrappers) ──────────────────────────────────────

def generate_referral_code(user_id: int) -> str:
    return hashlib.md5(f"{user_id}{uuid.uuid4()}".encode()).hexdigest()[:8].upper()

def get_user_data(user_id: int) -> dict:
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    
    return {
        "referral_code": user["referral_code"],
        "referred_by": user["referred_by"],
        "referrals": get_user_referrals_db(user_id),
        "total_earned": user["total_earned"]
    }

def get_referral_link(user_id: int) -> str:
    user = get_user(user_id)
    if not user:
        create_user(user_id)
        user = get_user(user_id)
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user['referral_code']}"

def process_referral(new_user_id: int, referral_code: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE referral_code = ?', (referral_code,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return False
    
    referrer_id = row[0]
    
    if referrer_id == new_user_id:
        return False
    
    user = get_user(new_user_id)
    if user and user.get("referred_by"):
        return False
    
    create_user(new_user_id, referred_by=referrer_id)
    add_referral_db(referrer_id, new_user_id)
    
    reward = system_config_get("referral_reward", 5)
    bonus = system_config_get("referral_bonus", 2)
    
    add_user_credits(referrer_id, reward)
    add_user_credits(new_user_id, bonus)
    
    logger.info(f"✅ Indicação: {new_user_id} indicado por {referrer_id}")
    return True

# ─── Funções de Estatísticas (Wrappers) ───────────────────────────────────

def update_stats():
    stats = stats_get_db()
    stats["total_users"] = get_total_users_db()
    return stats

def add_processed_count():
    stats_update_db()

# ─── VALORES VÁLIDOS ──────────────────────────────────────────────────────────

VALID_AGES = ["18", "20", "30", "40", "50"]
VALID_BREAST_SIZES = ["small", "normal", "big"]
VALID_BODY_TYPES = ["skinny", "normal", "curvy", "muscular"]
VALID_BUTT_SIZES = ["small", "normal", "big"]
VALID_CLOTHES = [
    "Naked", "Bikini", "Lingerie", "Sport wear", "BDSM", "Latex",
    "Teacher", "Schoolgirl", "Bikini leopard", "Naked cum", "Naked tatoo",
    "Witch", "Sexy Witch", "Maid", "Christmas underwear", "Pregnant",
    "Cheerleader", "Police", "Secretary", "Blooming Bouquet",
    "Leather dress", "Corset", "Mini bikini"
]
VALID_POST_GEN = ["upscale", "anime"]

# ─── TRADUÇÕES ──────────────────────────────────────────────────────────────

TRADUCOES = {
    "18": "18 anos", "20": "20 anos", "30": "30 anos", "40": "40 anos", "50": "50 anos",
    "small": "Pequeno", "normal": "Normal", "big": "Grande",
    "skinny": "Magro", "normal": "Normal", "curvy": "Curvilíneo", "muscular": "Musculoso",
    "upscale": "Alta Resolução", "anime": "Estilo Anime",
    "Naked": "Pelado", "Bikini": "Biquíni", "Lingerie": "Lingerie",
    "Sport wear": "Roupa Esportiva", "BDSM": "BDSM", "Latex": "Látex",
    "Teacher": "Professora", "Schoolgirl": "Colegial", "Bikini leopard": "Biquíni Leopardo",
    "Naked cum": "Pelado com sêmen", "Naked tatoo": "Pelado com tatuagem",
    "Witch": "Bruxa", "Sexy Witch": "Bruxa Sexy", "Maid": "Empregada",
    "Christmas underwear": "Roupa íntima de Natal", "Pregnant": "Grávida",
    "Cheerleader": "Líder de torcida", "Police": "Policial", "Secretary": "Secretária",
    "Blooming Bouquet": "Buquê Florescente", "Leather dress": "Vestido de Couro",
    "Corset": "Espartilho", "Mini bikini": "Mini Biquíni",
    "Summer solstice": "Solstício de Verão", "Solstice": "Solstício",
    "Missionary POV": "Missionário POV", "Ahegao cum": "Ahegao com sêmen",
    "Anal Fuck": "Anal", "Cumshot POV": "Cumshot POV",
    "Doggy Style": "Doggy Style", "Shibari": "Shibari",
    "Spreading legs": "Pernas Abertas", "Tit Fuck": "Esfregação",
    "Cowgirl POV": "Cowgirl POV", "Cumshot": "Cumshot",
    "Blowjob": "Oral", "Ahegao": "Ahegao",
}

# ─── TRADUÇÕES PARA ESTILOS DE VÍDEO ──────────────────────────────────────

VIDEO_TRADUCOES = {
    "Bouncing Missionary": "Missionário com movimento",
    "BBC Teasing": "Provocação com BBC",
    "Pullout on Belly": "Gozo na barriga",
    "Bouncy Tits 2.0": "Peitos balançando 2.0",
    "Pissing": "Urinação",
    "Double Girls Blowjob": "Boquete a duas",
    "Double Penetration": "Dupla penetração",
    "Sided Cumshot": "Gozo lateral",
    "Rubbing Pussy": "Esfregando a buceta",
    "Walking Undress": "Despindo-se enquanto caminha",
    "Cowgirl With Blowjob": "Cavalgada com boquete",
    "Double Pussy Kissing": "Beijo entre bucetas",
    "Gangbang": "Gangbang",
    "Dildo Masturbation": "Masturbação com dildo",
    "Cumming Blowjob": "Gozo durante o boquete",
    "Lifted Sex": "Sexo com a parceira suspensa",
    "Sex with Choking": "Sexo com estrangulamento",
    "Milkies": "Peitões",
    "LiftedBBC": "BBC com a parceira suspensa",
    "Spread Legs Missionary": "Missionário com pernas abertas",
    "Bouncy tits": "Peitos balançando",
    "Footjob 2.0": "Footjob 2.0",
    "Masturbation 2.0": "Masturbação 2.0",
    "Tits massage": "Massagem nos peitos",
    "Titjob 2.0": "Titjob 2.0",
    "Reverse Cowgirl 2.0": "Cavalgada reversa 2.0",
    "Missionary 2.0": "Missionário 2.0",
    "Hair Grab Doggy": "Quatro com puxão de cabelo",
    "Hand heart": "Coração com as mãos",
    "Naked Cupid": "Cupido nu",
    "Show feet": "Mostrando os pés",
    "Ahegao": "Ahegao",
    "Bound Boobs": "Peitos amarrados",
    "Double Handjob": "Handjob a duas",
    "French Kiss": "Beijo de língua",
    "Full Nelson": "Golpe Full Nelson",
    "Lesbian Kiss": "Beijo lésbico",
    "Poof!": "Poof!",
    "Tgirl orgasm 2.0": "Orgasmo de T-girl 2.0",
    "Pullout Doggy Style": "Quatro com retirada",
    "POV Doggy": "POV de quatro",
    "Doggy style 2.0": "Quatro 2.0",
    "Double Blowjob": "Boquete duplo",
    "Low Blowjob": "Boquete baixo",
    "Double Cumshot": "Gozo duplo",
    "Scanner": "Scanner",
    "Cowgirl POV": "POV de cavalgada",
    "Handjob": "Handjob",
    "Teasing tits": "Provocação com os peitos",
    "Tits & Heart": "Peitos e coração",
    "Cowgirl": "Cavalgada",
    "Succubus": "Súcubo",
    "Twerk 2.0": "Twerk 2.0",
    "Squirt 2.0": "Squirt 2.0",
    "Fingering 2.0": "Dedada 2.0",
    "Blowjob 2.0": "Boquete 2.0",
    "Cumshot 2.0": "Gozo 2.0",
    "Standing Doggy Style": "Quatro em pé",
    "Doggy Style From Back": "Quatro de costas",
    "Cumming in Mouth": "Gozo na boca",
    "Sideway Penetration": "Penetração lateral",
    "Shows tits": "Mostrando os peitos",
    "Front Doggy": "Quatro de frente",
    "Peace Pose": "Pose de paz",
    "Gloryhole": "Gloryhole",
    "Undressing Peeping": "Espiando enquanto se despe",
    "Dick Slaps": "Tapas com o pau",
    "Hard Sex": "Sexo intenso",
    "Cumshot POV": "POV de gozo",
    "Dildo Ride": "Cavalgada no dildo",
    "Combo Blowjob": "Combo de boquete",
    "Low Blowjob 2.0": "Boquete baixo 2.0",
    "Bukkake": "Bukkake",
    "Shows Tits 2.0": "Mostrando os peitos 2.0",
    "Ahegao 2.0": "Ahegao 2.0",
    "Two Santas": "Duas Mamães Noéis",
    "Shows Pussy": "Mostrando a buceta",
    "Hard Doggy": "Quatro intenso",
    "Sassy Girl": "Garota atrevida",
    "Creampie": "Creampie",
    "Bouncing Cowgirl": "Cavalgada com movimento",
    "Tits Sucking": "Chupada de peitos",
    "Slaps With Dick": "Tapas com o pau",
    "Face-to-Face": "Cara a cara",
    "Workout": "Treino",
    "BBC Missionary Close-Up": "Close-up de missionário com BBC",
    "Twisted Captivity": "Cativeiro retorcido",
    "Pussy Stroking": "Acariciando a buceta",
    "Lace Surprise": "Surpresa de renda",
    "Blowjob POV": "POV de boquete",
    "Hard Blowjob": "Boquete intenso",
    "Lying Blowjob": "Boquete deitada",
}

# ─── Função de Tradução Atualizada ──────────────────────────────────────────

def traduzir(valor: str) -> str:
    # Primeiro verifica se é um estilo de vídeo
    if valor in VIDEO_TRADUCOES:
        return VIDEO_TRADUCOES[valor]
    # Depois verifica as traduções normais
    if valor in TRADUCOES:
        return TRADUCOES[valor]
    # Fallback
    return valor.replace("_", " ").replace("-", " ").title()

# ─── Cliente da API ──────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Accept": "application/json", "X-API-Key": API_KEY}

# ─── Log para Canais ─────────────────────────────────────────────────────────

def canal_log(canal_id: int, texto: str, foto: bytes = None, video: bytes = None):
    try:
        tg = f"https://api.telegram.org/bot{TOKEN}"
        if foto:
            requests.post(f"{tg}/sendPhoto",
                files={"photo": ("log.jpg", foto, "image/jpeg")},
                data={"chat_id": canal_id, "caption": texto[:1024], "parse_mode": "Markdown"},
                timeout=20)
        elif video:
            requests.post(f"{tg}/sendVideo",
                files={"video": ("log.mp4", video, "video/mp4")},
                data={"chat_id": canal_id, "caption": texto[:1024], "parse_mode": "Markdown"},
                timeout=40)
        else:
            requests.post(f"{tg}/sendMessage",
                json={"chat_id": canal_id, "text": texto[:4096], "parse_mode": "Markdown"},
                timeout=10)
    except Exception as e:
        logger.error(f"❌ canal_log({canal_id}): {e}")

async def canal_log_async(canal_id: int, texto: str, foto: bytes = None, video: bytes = None):
    await asyncio.to_thread(canal_log, canal_id, texto, foto, video)

def get_canal(tipo: str) -> int:
    """Lê o ID do canal de log do banco. Fallback para o valor padrão."""
    defaults = {
        "pagamento": CANAL_PAGAMENTO,
        "erros":     CANAL_ERROS,
        "imagem":    CANAL_IMAGEM,
        "resultado": CANAL_RESULTADO,
    }
    val = system_config_get(f"canal_{tipo}")
    return int(val) if val else defaults.get(tipo, 0)

def api_get_image_poses() -> List[str]:
    logger.info("📡 Buscando poses de imagem...")
    r = requests.get(f"{API_BASE_URL}/api/v1/photos/poses", headers=_headers(), timeout=10)
    r.raise_for_status()
    poses = r.json().get("poses", [])
    logger.info(f"✅ {len(poses)} poses de imagem")
    return poses

def api_get_video_poses() -> List[Dict]:
    logger.info("📡 Buscando poses de vídeo...")
    r = requests.get(f"{API_BASE_URL}/api/v1/video/poses", headers=_headers(), timeout=10)
    r.raise_for_status()
    poses = r.json().get("poses", [])
    logger.info(f"✅ {len(poses)} poses de vídeo")
    return poses

def api_get_user() -> Dict:
    r = requests.get(f"{API_BASE_URL}/api/v1/me", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def api_process_image(image_bytes: bytes, filename: str, id_gen: str, pose_id: Optional[str] = None, **kwargs) -> Dict:
    logger.info(f"📤 Processando imagem | ID: {id_gen}")
    files = {"photo": (filename, image_bytes, "image/jpeg")}
    data = {"id_gen": id_gen, "webhook": CALLBACK_URL}
    
    for key, value in kwargs.items():
        if value:
            data[key] = value
    
    if pose_id:
        endpoint = f"{API_BASE_URL}/api/v1/photos/poses/undress"
        data["pose"] = pose_id
    else:
        endpoint = f"{API_BASE_URL}/api/v1/photos/undress"
    
    r = requests.post(endpoint, headers=_headers(), files=files, data=data, timeout=120)
    r.raise_for_status()
    resp = r.json()
    logger.info(f"📥 Resposta API imagem | ID: {id_gen} | Keys: {list(resp.keys()) if isinstance(resp, dict) else resp}")
    return resp

def api_process_video(image_bytes: bytes, filename: str, id_gen: str, pose_id: Optional[str] = None) -> Dict:
    logger.info(f"📤 Processando VÍDEO | ID: {id_gen}")
    files = {"photo": (filename, image_bytes, "image/jpeg")}
    data = {"id_gen": id_gen, "webhook": CALLBACK_URL}
    
    if pose_id:
        endpoint = f"{API_BASE_URL}/api/v1/video/poses/undress"
        data["pose_id"] = pose_id
    else:
        endpoint = f"{API_BASE_URL}/api/v1/video/undress"
    
    r = requests.post(endpoint, headers=_headers(), files=files, data=data, timeout=180)
    r.raise_for_status()
    resp = r.json()
    logger.info(f"📥 Resposta API vídeo | ID: {id_gen} | Keys: {list(resp.keys()) if isinstance(resp, dict) else resp}")
    return resp

# ─── Funções LofyPay ──────────────────────────────────────────────────────

def create_pix_payment(user_id: int, package_key: str) -> Optional[Dict]:
    packages = package_get_all_db()
    package = packages.get(package_key)
    if not package:
        return None
    
    amount = package["price"]
    credits = package["credits"]
    external_ref = f"CREDIT_{user_id}_{package_key}_{uuid.uuid4().hex[:8]}"
    
    payload = {
        "amount": amount,
        "method": "pix",
        "external_reference": external_ref,
        "notification_url": LOFYPAY_WEBHOOK_URL,
        "client": {
            "name": f"Usuario_{user_id}",
            "document": "00000000000",
            "email": f"user{user_id}@bot.com"
        }
    }
    
    headers = {
        "Authorization": f"Bearer {LOFYPAY_SECRET}",
        "Content-Type": "application/json"
    }
    
    try:
        logger.info(f"💳 Criando PIX para usuário {user_id} - Pacote: {package_key}")
        r = requests.post(LOFYPAY_URL, json=payload, headers=headers, timeout=30)
        
        if r.status_code == 200:
            data = r.json()
            logger.info(f"✅ PIX criado: {data}")
            
            id_transaction = data.get("idTransaction")
            if id_transaction:
                add_purchase_db(user_id, package_key, credits, amount, id_transaction, "pending")
            
            return data
        else:
            logger.error(f"❌ Erro ao criar PIX: {r.text}")
            return None
    except Exception as e:
        logger.error(f"❌ Erro ao criar PIX: {e}")
        return None

def check_pix_payment(id_transaction: str) -> Optional[Dict]:
    """Consulta o status de um pagamento PIX na LofyPay via POST /api/v1/status."""
    try:
        r = requests.post(
            "https://app.lofypay.com/api/v1/status",
            headers={"Authorization": f"Bearer {LOFYPAY_SECRET}", "Content-Type": "application/json"},
            json={"idtransaction": id_transaction},
            timeout=15,
        )
        logger.info(f"🔍 check_pix_payment {id_transaction}: {r.status_code} {r.text[:200]}")
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        logger.error(f"❌ check_pix_payment erro: {e}")
        return None

# ─── Servidor Webhook ────────────────────────────────────────────────────────

flask_app = Flask(__name__)

pending_requests: Dict[str, dict] = {}  # id_gen → {chat_id, processing_msg}

@flask_app.before_request
def log_request():
    logger.info(f"📡 {flask_request.method} {flask_request.path} | IP: {flask_request.remote_addr} | CT: {flask_request.content_type}")

@flask_app.route("/ping", methods=["GET", "POST"])
def ping():
    return jsonify({"ok": True, "msg": "bot online"})

@flask_app.route("/webhook", methods=["POST"])
def handle_callback():
    try:
        # Log completo do payload recebido para diagnóstico
        logger.info(f"📨 Webhook recebido | Content-Type: {flask_request.content_type}")
        logger.info(f"📨 Headers: {dict(flask_request.headers)}")

        media_data = None
        media_filename = "result.jpg"
        media_url = None
        is_video = False

        if flask_request.is_json:
            payload = flask_request.json or {}
        else:
            payload = flask_request.form.to_dict()
            # Varre todos os arquivos recebidos (imagem ou vídeo)
            for field_name, file_obj in flask_request.files.items():
                media_data = file_obj.read()
                media_filename = file_obj.filename or field_name
                logger.info(f"📎 Arquivo recebido | campo={field_name} | nome={media_filename} | {len(media_data)} bytes")
                break  # pega o primeiro arquivo

        # Log dos campos (sem mostrar conteúdo binário/base64)
        payload_keys = list(payload.keys()) if isinstance(payload, dict) else []
        logger.info(f"📨 Payload keys: {payload_keys}")

        # Tenta encontrar o ID nos campos possíveis
        # idGeneration vem como "uuid|token" — usa só a parte antes do "|"
        raw_id = (
            payload.get("id_gen")
            or payload.get("idGeneration")
            or payload.get("request_id")
            or payload.get("id")
            or payload.get("requestId")
        )
        id_gen = raw_id.split("|")[0] if raw_id else None

        # filename vindo do payload indica o nome do vídeo resultante
        result_filename = payload.get("filename")
        if result_filename:
            media_filename = result_filename
            is_video = any(media_filename.lower().endswith(ext) for ext in (".mp4", ".mov", ".avi", ".webm"))

        # Imagem em base64 no campo "photo" (formato API de imagem)
        photo_b64 = payload.get("photo")
        if photo_b64 and not media_data:
            try:
                media_data = base64.b64decode(photo_b64)
                logger.info(f"📸 Imagem base64 decodificada: {len(media_data)} bytes")
            except Exception as e:
                logger.error(f"❌ Erro ao decodificar base64: {e}")

        # URL do resultado (fallback)
        media_url = (
            payload.get("result_url")
            or payload.get("video_url")
            or payload.get("image_url")
            or payload.get("url")
            or payload.get("result")
            or payload.get("output_url")
            or payload.get("output")
        )

        if not id_gen:
            logger.warning("⚠️ Webhook sem id_gen/request_id — ignorando")
            return jsonify({"status": "ok"})

        pending = pending_requests.pop(id_gen, None)
        if not pending:
            logger.warning(f"⚠️ ID não encontrado em pending_requests: {id_gen}")
            logger.warning(f"⚠️ Pendentes: {list(pending_requests.keys())}")
            return jsonify({"status": "ok"})

        chat_id = pending.get("chat_id")
        processing_msg = pending.get("processing_msg")

        # Apaga a mensagem "⏳ Processando..."
        if processing_msg and chat_id:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TOKEN}/deleteMessage",
                    json={"chat_id": chat_id, "message_id": processing_msg.message_id},
                    timeout=10,
                )
                logger.info(f"🗑️ Mensagem de processamento apagada | ID: {id_gen}")
            except Exception as e:
                logger.error(f"❌ Erro ao apagar mensagem: {e}")

        # Monta legenda
        caption_lines = ["✅ *Processado com sucesso!*"]
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT type, style, params, credits_used FROM processing WHERE user_id = ? ORDER BY id DESC LIMIT 1',
            (chat_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            proc_type, style, params, credits = row
            if style and style != "Nenhum":
                caption_lines.append(f"🎨 Estilo: `{style}`")
            if params and params != "{}":
                try:
                    params_dict = json.loads(params)
                    params_str = ", ".join([f"{traduzir(v)}" for v in params_dict.values()])
                    caption_lines.append(f"⚙️ {params_str}")
                except Exception:
                    pass
            caption_lines.append(f"💰 Créditos usados: {credits}")

        caption_lines.append(f"💳 Saldo: {get_user_credits(chat_id)}")
        caption = "\n".join(caption_lines)

        # Baixa da URL se não veio como arquivo
        if not media_data and media_url:
            try:
                r = requests.get(media_url, timeout=60)
                r.raise_for_status()
                media_data = r.content
                is_video = "video" in r.headers.get("Content-Type", "")
                logger.info(f"📥 Mídia baixada da URL ({len(media_data)} bytes)")
            except Exception as e:
                logger.error(f"❌ Erro ao baixar mídia da URL: {e}")

        if chat_id and media_data:
            if is_video or media_filename.lower().endswith((".mp4", ".mov", ".avi", ".webm")):
                tg_url = f"https://api.telegram.org/bot{TOKEN}/sendVideo"
                files = {'video': (media_filename, media_data, 'video/mp4')}
                data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown', 'has_spoiler': True, 'supports_streaming': True}
                media_type = "vídeo"
            else:
                tg_url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
                files = {'photo': (media_filename, media_data, 'image/jpeg')}
                data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown', 'has_spoiler': True}
                media_type = "foto"

            resp = requests.post(tg_url, files=files, data=data, timeout=60)
            logger.info(f"✅ {media_type} enviado com spoiler | ID: {id_gen} | Status: {resp.status_code}")
            if resp.ok:
                canal_log(
                    get_canal("resultado"),
                    f"✅ *Resultado entregue*\n\n"
                    f"🆔 ID: `{id_gen}`\n"
                    f"👤 Chat: `{chat_id}`\n"
                    f"📦 Tipo: {media_type}\n"
                    f"📏 Tamanho: {len(media_data) // 1024} KB",
                    foto=media_data if not is_video else None,
                    video=media_data if is_video else None,
                )
            else:
                logger.error(f"❌ Telegram respondeu: {resp.text[:300]}")
                canal_log(get_canal("erros"),
                    f"❌ *Erro ao enviar resultado ao usuário*\n\n"
                    f"🆔 ID: `{id_gen}`\n"
                    f"👤 Chat: `{chat_id}`\n"
                    f"📋 Resposta Telegram: `{resp.text[:300]}`")
        elif chat_id:
            logger.warning(f"⚠️ Resultado sem arquivo para ID: {id_gen} | Payload: {payload}")
            canal_log(get_canal("erros"),
                f"⚠️ *Resultado sem arquivo*\n\n🆔 ID: `{id_gen}`\n👤 Chat: `{chat_id}`")
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": "⚠️ Processamento concluído, mas nenhum arquivo foi retornado.", "parse_mode": "Markdown"},
                timeout=10,
            )

        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"❌ Webhook erro: {e}", exc_info=True)
        canal_log(get_canal("erros"), f"❌ *Erro no webhook de resultado*\n\n`{str(e)[:500]}`")
        return jsonify({"status": "error"}), 500

@flask_app.route("/lofypay_webhook", methods=["POST"])
def handle_lofypay_webhook():
    try:
        payload = flask_request.json
        logger.info(f"📨 Webhook LofyPay recebido: {payload}")
        
        id_transaction = payload.get("idTransaction")
        status = payload.get("status")
        
        if status == "PAID" and id_transaction:
            purchase = get_purchase_db(id_transaction)
            if purchase and purchase["status"] == "pending":
                user_id = purchase["user_id"]
                credits = purchase["credits"]
                package_key = purchase["package_id"]
                
                add_user_credits(user_id, credits)
                update_purchase_status_db(id_transaction, "completed")

                canal_log(
                    get_canal("pagamento"),
                    f"💰 *Pagamento confirmado!*\n\n"
                    f"👤 Usuário: `{user_id}`\n"
                    f"📦 Pacote: `{package_key}`\n"
                    f"💵 Valor: R$ {purchase['amount']:.2f}\n"
                    f"🎁 Créditos: `+{credits}`\n"
                    f"💳 Saldo após: `{get_user_credits(user_id)}` créditos\n"
                    f"🔑 Transação: `{id_transaction}`"
                )

                saldo_atual = get_user_credits(user_id)
                pkg = package_get_all_db().get(package_key, {})
                pkg_label = pkg.get("label", f"{credits} créditos")
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                        json={
                            "chat_id": user_id,
                            "text": (
                                f"✅ *Pagamento confirmado!*\n\n"
                                f"📦 *Pacote:* {pkg_label}\n"
                                f"🎁 *Créditos adicionados:* `+{credits}`\n"
                                f"💳 *Saldo atual:* `{saldo_atual}` créditos\n\n"
                                f"Obrigado pela compra! Envie uma foto para começar. 🚀"
                            ),
                            "parse_mode": "Markdown",
                        },
                        timeout=10,
                    )
                except Exception as e:
                    logger.error(f"❌ Erro ao notificar usuário: {e}")

                logger.info(f"✅ Pagamento processado: {id_transaction} | Usuário: {user_id}")
        
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"❌ Erro no webhook LofyPay: {e}")
        canal_log(get_canal("erros"), f"❌ *Erro no webhook LofyPay*\n\n`{str(e)[:500]}`")
        return jsonify({"status": "error"}), 500

def _run_flask():
    flask_app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False, use_reloader=False)

# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _submit_image(id_gen: str, image_bytes: bytes, chat_id: int, user_id: int, 
                        processing_msg: Optional[object] = None, 
                        pose_id: Optional[str] = None, **kwargs):
    cost = system_config_get("photo_custom_cost", 3) if kwargs else system_config_get("photo_cost", 2)
    
    proc_type = "Foto com Pose" if pose_id else "Foto Personalizada" if kwargs else "Foto Básica"
    style = pose_id or "Nenhum"
    params_json = json.dumps(kwargs) if kwargs else "{}"
    
    if not deduct_user_credits(user_id, cost):
        raise Exception(f"Saldo insuficiente! Você precisa de {cost} créditos.")
    
    add_processing_db(user_id, proc_type, style, params_json, cost, "processing")
    
    pending_requests[id_gen] = {
        "chat_id": chat_id,
        "processing_msg": processing_msg
    }
    
    try:
        result = await asyncio.to_thread(api_process_image, image_bytes, "image.jpg", id_gen, pose_id, **kwargs)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE processing SET status = ? WHERE id = (
                SELECT MAX(id) FROM processing WHERE user_id = ?
            )
        ''', ("completed", user_id))
        conn.commit()
        conn.close()

        add_processed_count()

        # Se a API retornou a imagem direto na resposta, entrega sem esperar webhook
        photo_b64 = result.get("photo") if isinstance(result, dict) else None
        if photo_b64:
            logger.info(f"📸 API retornou imagem diretamente | ID: {id_gen}")
            pending_requests.pop(id_gen, None)
            try:
                photo_bytes = base64.b64decode(photo_b64)
            except Exception as decode_err:
                logger.error(f"❌ Erro ao decodificar foto direta: {decode_err}")
                return
            if processing_msg:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TOKEN}/deleteMessage",
                        json={"chat_id": chat_id, "message_id": processing_msg.message_id},
                        timeout=10,
                    )
                except Exception:
                    pass
            caption = f"✅ *Processado com sucesso!*\n💳 Saldo: {get_user_credits(user_id)}"
            resp = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                files={"photo": ("result.jpg", photo_bytes, "image/jpeg")},
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown", "has_spoiler": True},
                timeout=60,
            )
            logger.info(f"✅ Foto entregue direto da resposta | ID: {id_gen} | Telegram: {resp.status_code}")
        else:
            logger.info(f"⏳ Sem foto na resposta, aguardando webhook | ID: {id_gen}")

    except Exception as e:
        add_user_credits(user_id, cost)
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE processing SET status = ? WHERE id = (
                SELECT MAX(id) FROM processing WHERE user_id = ?
            )
        ''', ("failed", user_id))
        conn.commit()
        conn.close()
        pending_requests.pop(id_gen, None)
        asyncio.create_task(canal_log_async(
            get_canal("erros"),
            f"❌ *Erro ao processar imagem*\n\n"
            f"👤 Usuário: `{user_id}`\n"
            f"🆔 ID: `{id_gen}`\n"
            f"`{str(e)[:400]}`"
        ))
        raise

async def _submit_video(id_gen: str, image_bytes: bytes, chat_id: int, user_id: int, 
                        processing_msg: Optional[object] = None, 
                        pose_id: Optional[str] = None):
    cost = system_config_get("video_cost", 5)
    proc_type = "Vídeo com Pose" if pose_id else "Vídeo Básico"
    style = pose_id or "Nenhum"
    
    if not deduct_user_credits(user_id, cost):
        raise Exception(f"Saldo insuficiente! Você precisa de {cost} créditos.")
    
    add_processing_db(user_id, proc_type, style, "{}", cost, "processing")
    
    pending_requests[id_gen] = {
        "chat_id": chat_id,
        "processing_msg": processing_msg
    }
    
    try:
        result = await asyncio.to_thread(api_process_video, image_bytes, "image.jpg", id_gen, pose_id)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE processing SET status = ? WHERE id = (
                SELECT MAX(id) FROM processing WHERE user_id = ?
            )
        ''', ("completed", user_id))
        conn.commit()
        conn.close()

        add_processed_count()

        # Se a API retornou o vídeo direto (campo video ou photo na resposta)
        video_b64 = (result.get("video") or result.get("photo")) if isinstance(result, dict) else None
        if video_b64:
            logger.info(f"🎬 API retornou vídeo diretamente | ID: {id_gen}")
            pending_requests.pop(id_gen, None)
            try:
                video_bytes = base64.b64decode(video_b64)
            except Exception as decode_err:
                logger.error(f"❌ Erro ao decodificar vídeo direto: {decode_err}")
                return
            if processing_msg:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TOKEN}/deleteMessage",
                        json={"chat_id": chat_id, "message_id": processing_msg.message_id},
                        timeout=10,
                    )
                except Exception:
                    pass
            caption = f"✅ *Processado com sucesso!*\n💳 Saldo: {get_user_credits(user_id)}"
            resp = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendVideo",
                files={"video": ("result.mp4", video_bytes, "video/mp4")},
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown", "has_spoiler": True, "supports_streaming": True},
                timeout=120,
            )
            logger.info(f"✅ Vídeo entregue direto da resposta | ID: {id_gen} | Telegram: {resp.status_code}")
        else:
            logger.info(f"⏳ Sem vídeo na resposta, aguardando webhook | ID: {id_gen}")

    except Exception as e:
        add_user_credits(user_id, cost)
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE processing SET status = ? WHERE id = (
                SELECT MAX(id) FROM processing WHERE user_id = ?
            )
        ''', ("failed", user_id))
        conn.commit()
        conn.close()
        pending_requests.pop(id_gen, None)
        asyncio.create_task(canal_log_async(
            get_canal("erros"),
            f"❌ *Erro ao processar vídeo*\n\n"
            f"👤 Usuário: `{user_id}`\n"
            f"🆔 ID: `{id_gen}`\n"
            f"`{str(e)[:400]}`"
        ))
        raise

# ─── Handlers de Administrador ─────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Painel de administrador."""
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        if update.message:
            await update.message.reply_text("❌ Acesso negado. Apenas administradores.")
        return

    stats = stats_get_db()
    stats["total_users"] = get_total_users_db()

    admin_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Gerenciar Usuários", callback_data="admin:users")],
        [InlineKeyboardButton("💰 Adicionar Créditos", callback_data="admin:add_credits")],
        [InlineKeyboardButton("📦 Gerenciar Pacotes", callback_data="admin:packages")],
        [InlineKeyboardButton("👥 Configurar Afiliados", callback_data="admin:affiliate_config")],
        [InlineKeyboardButton("📊 Relatórios", callback_data="admin:reports")],
        [InlineKeyboardButton("⚙️ Configurações Gerais", callback_data="admin:config")],
        [InlineKeyboardButton("📢 Canais de Log", callback_data="admin:canais")],
        [InlineKeyboardButton("✏️ Mensagem de Start", callback_data="admin:start_msg")],
    ])

    text = (
        f"🔐 *Painel de Administração*\n\n"
        f"📊 *Estatísticas:*\n"
        f"• Usuários totais: {stats['total_users']}\n"
        f"• Total processamentos: {stats['total_processed']}\n"
        f"• Receita total: R$ {stats['total_revenue']:.2f}\n"
        f"• Pagamentos afiliados: {stats['total_affiliate_payouts']} créditos\n\n"
        f"⚙️ *Configurações atuais:*\n"
        f"• Custo foto: {system_config_get('photo_cost', 2)} créditos\n"
        f"• Custo foto personalizada: {system_config_get('photo_custom_cost', 3)} créditos\n"
        f"• Custo vídeo: {system_config_get('video_cost', 5)} créditos\n"
        f"• Recompensa indicação: {system_config_get('referral_reward', 5)} créditos\n"
        f"• Bônus novo usuário: {system_config_get('referral_bonus', 2)} créditos"
    )

    # Chamado via callback query (botão Voltar) — edita a mensagem existente
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_keyboard)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gerencia os callbacks do painel admin."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ Acesso negado.")
        return
    
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else None
    
    if action == "users":
        text = "👤 *Lista de Usuários*\n\n"
        users = get_all_users_db()
        for user in users[:20]:
            referrals = get_referral_count_db(user["user_id"])
            text += f"• ID: `{user['user_id']}` | Créditos: {user['credits']} | Indicações: {referrals}\n"
        
        if len(users) > 20:
            text += f"\n... e mais {len(users) - 20} usuários"
        
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:back")]
            ])
        )
    
    elif action == "add_credits":
        await query.edit_message_text(
            "💰 *Adicionar Créditos*\n\n"
            "Envie o ID do usuário e a quantidade no formato:\n"
            "`/addcredits ID QUANTIDADE`\n\n"
            "Exemplo: `/addcredits 123456789 50`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:back")]
            ])
        )
    
    elif action == "packages":
        text = "📦 *Pacotes de Créditos*\n\n"
        packages = package_get_all_db()
        for key, pkg in packages.items():
            text += f"• {pkg['label']}\n"
            text += f"  ID: `{key}` | Créditos: {pkg['credits']} | Preço: R$ {pkg['price']:.2f}\n\n"
        
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Adicionar Pacote", callback_data="admin:add_package")],
                [InlineKeyboardButton("✏️ Editar Pacote", callback_data="admin:edit_package")],
                [InlineKeyboardButton("❌ Remover Pacote", callback_data="admin:remove_package")],
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:back")]
            ])
        )
    
    elif action == "add_package":
        await query.edit_message_text(
            "➕ *Adicionar Pacote*\n\n"
            "Envie no formato:\n"
            "`/addpackage ID CREDITOS PRECO`\n\n"
            "Exemplo: `/addpackage 50 50 65.00`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:packages")]
            ])
        )
    
    elif action == "edit_package":
        await query.edit_message_text(
            "✏️ *Editar Pacote*\n\n"
            "Envie no formato:\n"
            "`/editpackage ID CREDITOS PRECO`\n\n"
            "Exemplo: `/editpackage 10 15 20.00`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:packages")]
            ])
        )
    
    elif action == "remove_package":
        await query.edit_message_text(
            "❌ *Remover Pacote*\n\n"
            "Envie o ID do pacote:\n"
            "`/removepackage ID`\n\n"
            "Exemplo: `/removepackage 10`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:packages")]
            ])
        )
    
    elif action == "affiliate_config":
        reward    = system_config_get("referral_reward", 5)
        bonus     = system_config_get("referral_bonus",  2)
        new_bonus = system_config_get("new_user_bonus",  0)
        await query.edit_message_text(
            f"👥 *Configuração de Afiliados*\n\n"
            f"🤝 Créditos para quem *indica:* `{reward}`\n"
            f"🎁 Bônus para quem *é indicado:* `{bonus}`\n"
            f"🆕 Bônus para *novos* (sem indicação): `{new_bonus}` "
            f"{'✅' if int(new_bonus or 0) > 0 else '❌ desativado'}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"✏️ *Comandos para editar:*\n\n"
            f"• Afiliado (quem indica):\n  `/setafiliado 10`\n\n"
            f"• Indicado (novo via link):\n  `/setindicado 5`\n\n"
            f"• Novo usuário (sem link):\n  `/setnewuser 3`\n"
            f"  Use `0` para desativar.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:back")]
            ])
        )
    
    elif action == "reports":
        stats = stats_get_db()
        stats["total_users"] = get_total_users_db()
        
        packages = package_get_all_db()
        total_referrals = 0
        users = get_all_users_db()
        for user in users:
            total_referrals += get_referral_count_db(user["user_id"])
        
        text = (
            f"📊 *Relatórios*\n\n"
            f"📈 *Resumo Geral:*\n"
            f"• Usuários totais: {stats['total_users']}\n"
            f"• Processamentos totais: {stats['total_processed']}\n"
            f"• Receita total: R$ {stats['total_revenue']:.2f}\n\n"
            f"👥 *Afiliados:*\n"
            f"• Total de indicações: {total_referrals}\n"
            f"• Créditos pagos: {stats['total_affiliate_payouts']}\n\n"
            f"📦 *Pacotes:*\n"
            f"• Total de pacotes: {len(packages)}"
        )
        
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:back")]
            ])
        )
    
    elif action == "config":
        text = (
            f"⚙️ *Configurações Gerais*\n\n"
            f"💰 *Custos:*\n"
            f"• Foto básica: `{system_config_get('photo_cost', 2)}` créditos\n"
            f"• Foto personalizada: `{system_config_get('photo_custom_cost', 3)}` créditos\n"
            f"• Vídeo: `{system_config_get('video_cost', 5)}` créditos\n\n"
            f"Para alterar, use:\n"
            f"`/setcosts FOTO FOTO_CUSTOM VIDEO`\n\n"
            f"Exemplo: `/setcosts 2 3 5`"
        )
        
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:back")]
            ])
        )
    
    elif action == "canais":
        await query.edit_message_text(
            f"📢 *Canais de Log*\n\n"
            f"💰 Pagamento:  `{get_canal('pagamento')}`\n"
            f"❌ Erros:       `{get_canal('erros')}`\n"
            f"📸 Imagem:      `{get_canal('imagem')}`\n"
            f"✅ Resultado:   `{get_canal('resultado')}`\n\n"
            f"Para alterar use:\n"
            f"`/setcanal TIPO ID`\n\n"
            f"Tipos: `pagamento` `erros` `imagem` `resultado`\n\n"
            f"Exemplo:\n"
            f"`/setcanal erros -1001234567890`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:back")]
            ])
        )

    elif action == "start_msg":
        url_buttons = _get_start_buttons()

        photo_id = system_config_get("start_photo_id", "")
        botoes_txt = "\n".join(
            f"  {i+1}. {b['text']} → {b['url']}" for i, b in enumerate(url_buttons)
        ) or "  (nenhum)"

        await query.edit_message_text(
            f"✏️ *Mensagem de Start*\n\n"
            f"📷 Foto: {'Configurada ✅' if photo_id else 'Usando start.png ou nenhuma'}\n\n"
            f"📝 *Texto atual:*\n"
            f"```\n{system_config_get('start_text', '')}\n```\n\n"
            f"🔗 *Botões URL:*\n{botoes_txt}\n\n"
            f"*Comandos:*\n"
            f"`/setstarttexto TEXTO` — altera o texto\n"
            f"  Use `{{credits}}` para mostrar créditos do usuário\n"
            f"`/setstartfoto` — envie este cmd e depois uma foto\n"
            f"`/addstartbotao LABEL | URL` — adiciona botão URL\n"
            f"`/removestartbotao N` — remove botão pelo número\n"
            f"`/removestartfoto` — remove a foto do start",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="admin:back")]
            ])
        )

    elif action == "back":
        await cmd_admin(update, context)

# ─── Comandos de Admin ──────────────────────────────────────────────────────

async def cmd_add_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Use: `/addcredits ID QUANTIDADE`",
            parse_mode="Markdown"
        )
        return
    
    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
        
        add_user_credits(target_id, amount)
        await update.message.reply_text(
            f"✅ Adicionado `{amount}` créditos para usuário `{target_id}`\n"
            f"💳 Saldo atual: {get_user_credits(target_id)}",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ ID ou quantidade inválidos.")

async def cmd_add_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ Use: `/addpackage ID CREDITOS PRECO`\n"
            "Exemplo: `/addpackage 50 50 65.00`",
            parse_mode="Markdown"
        )
        return
    
    try:
        pkg_id = context.args[0]
        credits = int(context.args[1])
        price = float(context.args[2])
        
        package_save_db(pkg_id, credits, price, f"{credits} créditos - R$ {price:.2f}")
        
        await update.message.reply_text(
            f"✅ Pacote `{pkg_id}` adicionado!\n"
            f"📦 Créditos: {credits}\n"
            f"💰 Preço: R$ {price:.2f}",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Valores inválidos.")

async def cmd_edit_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ Use: `/editpackage ID CREDITOS PRECO`\n"
            "Exemplo: `/editpackage 10 15 20.00`",
            parse_mode="Markdown"
        )
        return
    
    try:
        pkg_id = context.args[0]
        credits = int(context.args[1])
        price = float(context.args[2])
        
        packages = package_get_all_db()
        if pkg_id not in packages:
            await update.message.reply_text(f"❌ Pacote `{pkg_id}` não encontrado.")
            return
        
        package_save_db(pkg_id, credits, price, f"{credits} créditos - R$ {price:.2f}")
        
        await update.message.reply_text(
            f"✅ Pacote `{pkg_id}` atualizado!\n"
            f"📦 Créditos: {credits}\n"
            f"💰 Preço: R$ {price:.2f}",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Valores inválidos.")

async def cmd_remove_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text(
            "❌ Use: `/removepackage ID`\n"
            "Exemplo: `/removepackage 10`",
            parse_mode="Markdown"
        )
        return
    
    pkg_id = context.args[0]
    
    packages = package_get_all_db()
    if pkg_id not in packages:
        await update.message.reply_text(f"❌ Pacote `{pkg_id}` não encontrado.")
        return
    
    package_delete_db(pkg_id)
    await update.message.reply_text(f"✅ Pacote `{pkg_id}` removido!")

async def cmd_set_afiliado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Define créditos ganhos por quem indica."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    if not context.args:
        await update.message.reply_text("❌ Use: `/setafiliado QUANTIDADE`\nExemplo: `/setafiliado 10`", parse_mode="Markdown")
        return
    try:
        valor = int(context.args[0])
        system_config_set("referral_reward", valor)
        await update.message.reply_text(
            f"✅ Créditos do *afiliado* (quem indica) atualizados!\n\n🤝 Novo valor: `{valor}` créditos",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Use um número inteiro.")

async def cmd_set_indicado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Define créditos de bônus para quem é indicado."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    if not context.args:
        await update.message.reply_text("❌ Use: `/setindicado QUANTIDADE`\nExemplo: `/setindicado 5`", parse_mode="Markdown")
        return
    try:
        valor = int(context.args[0])
        system_config_set("referral_bonus", valor)
        await update.message.reply_text(
            f"✅ Bônus do *indicado* (novo usuário) atualizado!\n\n🎁 Novo valor: `{valor}` créditos",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Use um número inteiro.")

async def cmd_set_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    if not context.args:
        atual = system_config_get("new_user_bonus", 0)
        await update.message.reply_text(
            f"ℹ️ Bônus atual para novos usuários: `{atual}` créditos\n\n"
            f"Use: `/setnewuser QUANTIDADE`\n"
            f"Use `0` para desativar. Exemplo: `/setnewuser 3`",
            parse_mode="Markdown"
        )
        return
    try:
        valor = int(context.args[0])
        if valor < 0:
            raise ValueError
        system_config_set("new_user_bonus", valor)
        msg = "✅ Bônus de boas-vindas *desativado*." if valor == 0 else \
              f"✅ Novos usuários (sem indicação) receberão `{valor}` créditos ao entrar."
        await update.message.reply_text(msg, parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Use um número inteiro positivo ou 0 para desativar.")

async def cmd_set_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Use: `/setreferral REWARD BONUS`\n"
            "Exemplo: `/setreferral 10 5`",
            parse_mode="Markdown"
        )
        return

    try:
        reward = int(context.args[0])
        bonus = int(context.args[1])

        system_config_set("referral_reward", reward)
        system_config_set("referral_bonus", bonus)

        await update.message.reply_text(
            f"✅ Configurações de afiliados atualizadas!\n"
            f"👥 Recompensa: {reward} créditos\n"
            f"🎁 Bônus novo usuário: {bonus} créditos",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Valores inválidos.")

async def cmd_set_canal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return

    tipos_validos = ["pagamento", "erros", "imagem", "resultado"]

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Use: `/setcanal TIPO ID`\n\n"
            f"Tipos: {', '.join(f'`{t}`' for t in tipos_validos)}\n\n"
            "Exemplo: `/setcanal erros -1001234567890`",
            parse_mode="Markdown"
        )
        return

    tipo = context.args[0].lower()
    if tipo not in tipos_validos:
        await update.message.reply_text(
            f"❌ Tipo inválido. Use: {', '.join(f'`{t}`' for t in tipos_validos)}",
            parse_mode="Markdown"
        )
        return

    try:
        canal_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ ID inválido. Use um número inteiro (ex: `-1001234567890`).", parse_mode="Markdown")
        return

    system_config_set(f"canal_{tipo}", canal_id)
    await update.message.reply_text(
        f"✅ Canal de *{tipo}* atualizado!\n\nNovo ID: `{canal_id}`",
        parse_mode="Markdown"
    )

async def cmd_set_costs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ Use: `/setcosts FOTO FOTO_CUSTOM VIDEO`\n"
            "Exemplo: `/setcosts 2 3 5`",
            parse_mode="Markdown"
        )
        return
    
    try:
        photo = int(context.args[0])
        custom = int(context.args[1])
        video = int(context.args[2])
        
        system_config_set("photo_cost", photo)
        system_config_set("photo_custom_cost", custom)
        system_config_set("video_cost", video)
        
        await update.message.reply_text(
            f"✅ Custos atualizados!\n"
            f"📸 Foto: {photo} créditos\n"
            f"⚙️ Foto personalizada: {custom} créditos\n"
            f"🎬 Vídeo: {video} créditos",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Valores inválidos.")

# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM HANDLERS (Menu, Callbacks, etc)
# ═══════════════════════════════════════════════════════════════════════════

# ─── Teclados ──────────────────────────────────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🎬 Vídeo Poses", callback_data="menu:video_poses")],
        [InlineKeyboardButton("🖼 Foto Poses", callback_data="menu:photo_poses")],
        [InlineKeyboardButton("👗 Trocar Roupa", callback_data="menu:cloth")],
        [InlineKeyboardButton("⚡ Despir Foto", callback_data="menu:undress")],
    ]
    return InlineKeyboardMarkup(keyboard)

def _get_start_buttons() -> list:
    """Retorna a lista de botões URL do start, sempre como lista Python."""
    val = system_config_get("start_buttons", [])
    if isinstance(val, list):
        return val
    try:
        return json.loads(val) if val else []
    except Exception:
        return []

def _set_start_buttons(buttons: list):
    """Salva a lista de botões do start como JSON string."""
    system_config_set("start_buttons", json.dumps(buttons, ensure_ascii=False))

def build_start_keyboard() -> InlineKeyboardMarkup:
    """Monta o teclado do start com botões URL customizáveis + botões fixos."""
    url_buttons = _get_start_buttons()

    keyboard = [
        [InlineKeyboardButton("💰 Comprar Créditos ", callback_data="start:buy")],
        #[InlineKeyboardButton("📖 Como Funciona", callback_data="start:howto")],
        [InlineKeyboardButton("Sistema de Afiliados", callback_data="start:affiliate")],
    ]
    for btn in url_buttons:
        if btn.get("text") and btn.get("url"):
            keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
    return InlineKeyboardMarkup(keyboard)

def _video_pose_keyboard(poses: List[Dict], id_gen: str, page: int = 0) -> InlineKeyboardMarkup:
    items_per_page = 8
    total_pages = (len(poses) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = min(start + items_per_page, len(poses))
    
    keyboard = []
    for i in range(start, end):
        pose = poses[i]
        name = pose.get("name", "Desconhecido")
        label = traduzir(name)
        if len(label) > 28:
            label = label[:26] + "..."
        keyboard.append([InlineKeyboardButton(label, callback_data=f"vid:{id_gen}:pose:{i}")])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"vid:{id_gen}:page:{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"vid:{id_gen}:page:{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([
        InlineKeyboardButton(f"📊 {page+1}/{total_pages}", callback_data="ignore"),
        InlineKeyboardButton("🔙 Voltar", callback_data="menu:back")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def _photo_pose_keyboard(poses: List[str], id_gen: str, page: int = 0) -> InlineKeyboardMarkup:
    items_per_page = 8
    total_pages = (len(poses) + items_per_page - 1) // items_per_page
    start = page * items_per_page
    end = min(start + items_per_page, len(poses))
    
    keyboard = []
    for i in range(start, end):
        label = traduzir(poses[i])
        keyboard.append([InlineKeyboardButton(label, callback_data=f"img:{id_gen}:pose:{poses[i]}")])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"img:{id_gen}:page:{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"img:{id_gen}:page:{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([
        InlineKeyboardButton(f"📊 {page+1}/{total_pages}", callback_data="ignore"),
        InlineKeyboardButton("🔙 Voltar", callback_data="menu:back")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def cloth_keyboard(id_gen: str) -> InlineKeyboardMarkup:
    keyboard = []
    for cloth in VALID_CLOTHES:
        label = traduzir(cloth)
        if len(label) > 25:
            label = label[:23] + "..."
        keyboard.append([InlineKeyboardButton(label, callback_data=f"cloth:{id_gen}:{cloth}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Voltar", callback_data="menu:back")])
    return InlineKeyboardMarkup(keyboard)

def undress_keyboard(id_gen: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("⚡ Despir Direto", callback_data=f"undress:{id_gen}:direct")],
        [InlineKeyboardButton("⚙️ Personalizar", callback_data=f"undress:{id_gen}:customize")],
        [InlineKeyboardButton("🔙 Voltar", callback_data="menu:back")],
    ]
    return InlineKeyboardMarkup(keyboard)

def customize_keyboard(id_gen: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📏 Tamanho dos Seios", callback_data=f"custom:{id_gen}:breast_size")],
        [InlineKeyboardButton("🍑 Tamanho do Bumbum", callback_data=f"custom:{id_gen}:butt_size")],
        [InlineKeyboardButton("🏋️ Tipo de Corpo", callback_data=f"custom:{id_gen}:body_type")],
        [InlineKeyboardButton("📅 Idade", callback_data=f"custom:{id_gen}:age")],
        [InlineKeyboardButton("🎨 Estilo (Pós-Gen)", callback_data=f"custom:{id_gen}:post_gen")],
        [InlineKeyboardButton("👗 Roupa", callback_data=f"custom:{id_gen}:cloth")],
        [InlineKeyboardButton("✅ Processar com Parâmetros", callback_data=f"custom:{id_gen}:process")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=f"custom:{id_gen}:cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)

def value_keyboard(prefix: str, id_gen: str, param: str, values: List[str]) -> InlineKeyboardMarkup:
    keyboard = []
    for v in values:
        keyboard.append([InlineKeyboardButton(traduzir(v), callback_data=f"set:{id_gen}:{param}:{v}")])
    keyboard.append([InlineKeyboardButton("🔙 Voltar", callback_data=f"{prefix}:{id_gen}:back")])
    return InlineKeyboardMarkup(keyboard)

def buy_credits_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    packages = package_get_all_db()
    for key, package in packages.items():
        keyboard.append([InlineKeyboardButton(
            f"📦 {package['label']}",
            callback_data=f"buy:{key}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Voltar", callback_data="start:back")])
    return InlineKeyboardMarkup(keyboard)

# ─── Handlers do Telegram ────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    is_new = get_user(user_id) is None
    referral_aplicado = False

    # Processa indicação
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            referral_code = arg[4:]
            if process_referral(user_id, referral_code):
                referral_aplicado = True
                await update.message.reply_text(
                    f"🎉 *Bem-vindo!*\n\n"
                    f"Você ganhou `{system_config_get('referral_bonus', 2)}` créditos de bônus por usar o link de indicação!",
                    parse_mode="Markdown"
                )

    create_user(user_id)

    # Bônus para novos usuários sem indicação (silencioso)
    if is_new and not referral_aplicado:
        bonus = system_config_get("new_user_bonus", 0)
        if bonus and int(bonus) > 0:
            add_user_credits(user_id, int(bonus))
    
    start_text = (system_config_get("start_text") or "").replace("{credits}", str(get_user_credits(user_id)))
    start_keyboard = build_start_keyboard()
    photo_id = system_config_get("start_photo_id", "")

    if photo_id:
        await update.message.reply_photo(
            photo=photo_id,
            caption=start_text,
            parse_mode="Markdown",
            reply_markup=start_keyboard,
        )
    elif os.path.exists("start.png"):
        with open("start.png", 'rb') as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=start_text,
                parse_mode="Markdown",
                reply_markup=start_keyboard,
            )
    else:
        await update.message.reply_text(
            start_text,
            parse_mode="Markdown",
            reply_markup=start_keyboard,
        )

    # Mensagem extra apenas para novos usuários
    if is_new:
        await update.message.reply_text(
            "📸 *Me envie uma foto para eu processar!*\n\n"
            "Basta enviar a imagem aqui no chat e eu cuido do resto 😉",
            parse_mode="Markdown",
        )

async def cmd_creditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    credits = get_user_credits(user_id)
    
    await update.message.reply_text(
        f"💰 *Seus créditos:* `{credits}`\n\n"
        f"📊 *Custos:*\n"
        f"  • Foto básica: {system_config_get('photo_cost', 2)} créditos\n"
        f"  • Foto personalizada: {system_config_get('photo_custom_cost', 3)} créditos\n"
        f"  • Vídeo: {system_config_get('video_cost', 5)} créditos",
        parse_mode="Markdown"
    )

# ─── Handler de Foto ─────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Admin definindo foto do start
    if user_id == ADMIN_ID and context.user_data.get("esperando_foto_start"):
        context.user_data.pop("esperando_foto_start")
        photo = update.message.photo[-1]
        system_config_set("start_photo_id", photo.file_id)
        await update.message.reply_text("✅ Foto do start atualizada!")
        return

    if get_user_credits(user_id) < system_config_get('photo_cost', 2):
        await update.message.reply_text(
            f"❌ *Saldo insuficiente!*\n\n"
            f"💰 Seu saldo: `{get_user_credits(user_id)}` créditos\n"
            f"📸 Para processar uma foto você precisa de **{system_config_get('photo_cost', 2)} créditos**.\n\n"
            f"Clique em *Comprar Créditos* no menu.",
            parse_mode="Markdown"
        )
        return
    
    processing_msg = None
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        image_bytes = buf.getvalue()
        
        id_gen = str(uuid.uuid4())
        chat_id = update.effective_chat.id
        
        context.user_data["image_data"] = {
            "id_gen": id_gen,
            "image_bytes": image_bytes,
            "chat_id": chat_id,
            "user_id": user_id,
            "params": {},
        }

        user = update.effective_user
        username = f"@{user.username}" if user.username else "sem username"
        asyncio.create_task(canal_log_async(
            get_canal("imagem"),
            f"📸 *Imagem recebida*\n\n"
            f"👤 [{user.first_name}](tg://user?id={user_id}) {username}\n"
            f"🆔 `{user_id}`\n"
            f"💳 Créditos: `{get_user_credits(user_id)}`",
            foto=image_bytes,
        ))

        # Primeira foto do usuário → processa direto sem menu
        if get_user_processing_count(user_id) == 0:
            processing_msg = await update.message.reply_text(
                "⏳ *Processando imagem...*\n\n🔄 Isso pode levar alguns segundos.",
                parse_mode="Markdown",
            )
            await _submit_image(id_gen, image_bytes, chat_id, user_id, processing_msg)
        else:
            await update.message.reply_text(
                f"📸 *Imagem carregada!*\n\n"
                f"💰 Saldo: {get_user_credits(user_id)} créditos\n\n"
                f"Escolha uma opção:",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown",
            )

    except Exception as e:
        await update.message.reply_text(
            f"❌ *Erro ao carregar imagem:*\n{str(e)[:200]}",
            parse_mode="Markdown"
        )

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ *Apenas imagens são aceitas!*\n\n"
        "📸 Por favor, envie uma **foto** para processamento.\n"
        "🎬 Vídeos não são suportados neste bot.",
        parse_mode="Markdown"
    )

# ─── Callbacks do Start ─────────────────────────────────────────────────────

async def handle_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    action = query.data.split(":")[1]
    
    async def send_new(text, reply_markup=None, parse_mode="Markdown", send_photo=False):
        try:
            try:
                await query.delete_message()
            except:
                pass
            
            if send_photo:
                photo_id = system_config_get("start_photo_id", "")
                if photo_id:
                    await query.message.reply_photo(
                        photo=photo_id,
                        caption=text,
                        parse_mode=parse_mode,
                        reply_markup=reply_markup,
                    )
                    return
                elif os.path.exists("start.png"):
                    with open("start.png", 'rb') as photo:
                        await query.message.reply_photo(
                            photo=photo,
                            caption=text,
                            parse_mode=parse_mode,
                            reply_markup=reply_markup,
                        )
                    return
            
            await query.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")
    
    if action == "buy":
        await send_new(
            "💰 *Comprar Créditos*\n\nEscolha um pacote:",
            buy_credits_keyboard(),
        )
    
    elif action == "howto":
        await send_new(
            f"📖 *Como Funciona*\n\n"
            f"1️⃣ Envie uma **foto** para o bot\n"
            f"2️⃣ Escolha uma opção no menu\n"
            f"3️⃣ O bot processa a imagem\n"
            f"4️⃣ Você recebe o resultado em **spoiler**\n\n"
            f"💰 *Créditos:*\n"
            f"• Foto básica: {system_config_get('photo_cost', 2)} créditos\n"
            f"• Foto personalizada: {system_config_get('photo_custom_cost', 3)} créditos\n"
            f"• Vídeo: {system_config_get('video_cost', 5)} créditos\n\n"
            f"🔄 *Indicações:*\n"
            f"• Convidar amigos = ganhar créditos!\n"
            f"• Você ganha {system_config_get('referral_reward', 5)} créditos por indicação\n"
            f"• Seu amigo ganha {system_config_get('referral_bonus', 2)} créditos de bônus",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="start:back")]
            ])
        )
    
    elif action == "affiliate":
        link = get_referral_link(user_id)
        referrals = get_user_referrals_db(user_id)
        user = get_user(user_id)
        total_earned = user["total_earned"] if user else 0
        
        await send_new(
            f"👥 *Sistema de Afiliados*\n\n"
            f"🔗 *Seu link de convite:*\n"
            f"`{link}`\n\n"
            f"📊 *Suas estatísticas:*\n"
            f"• Indicações: {len(referrals)} usuários\n"
            f"• Total ganho: {total_earned} créditos\n\n"
            f"💡 *Como funciona:*\n"
            f"• Cada amigo que entrar com seu link\n"
            f"• Você ganha **{system_config_get('referral_reward', 5)} créditos**\n"
            f"• Seu amigo ganha **{system_config_get('referral_bonus', 2)} créditos** de bônus\n\n"
            f"📤 Compartilhe o link com seus amigos!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Compartilhar Link", callback_data="start:share")],
                [InlineKeyboardButton("🔙 Voltar", callback_data="start:back")]
            ])
        )
    
    elif action == "share":
        link = get_referral_link(user_id)
        
        await send_new(
            f"📤 *Compartilhe seu link!*\n\n"
            f"Copie e cole o link abaixo para seus amigos:\n\n"
            f"`{link}`\n\n"
            f"🔹 *Benefícios:*\n"
            f"• Você ganha {system_config_get('referral_reward', 5)} créditos por indicação\n"
            f"• Seu amigo ganha {system_config_get('referral_bonus', 2)} créditos de bônus",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="start:affiliate")]
            ])
        )
    
    elif action == "back":
        back_text = (system_config_get("start_text") or "").replace("{credits}", str(get_user_credits(user_id)))
        await send_new(
            back_text,
            build_start_keyboard(),
            send_photo=True,
        )

# ─── Callback de Compra de Créditos ──────────────────────────────────────

async def handle_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    if len(parts) < 2:
        return
    
    package_key = parts[1]
    user_id = update.effective_user.id
    
    packages = package_get_all_db()
    package = packages.get(package_key)
    if not package:
        await query.edit_message_text("❌ Pacote inválido.")
        return
    
    await query.edit_message_text("⏳ Gerando PIX...")
    
    pix_data = await asyncio.to_thread(create_pix_payment, user_id, package_key)
    
    if pix_data and pix_data.get("paymentCode"):
        payment_code = pix_data.get("paymentCode")
        id_transaction = pix_data.get("idTransaction", "")

        caption = (
            f"💳 *Pagamento PIX*\n\n"
            f"📦 *Pacote:* {package['label']}\n"
            f"💰 *Créditos:* {package['credits']}\n"
            f"💵 *Valor:* R$ {package['price']:.2f}\n\n"
            f"📋 *Código PIX — toque para copiar:*\n"
            f"```\n{payment_code}\n```\n\n"
            f"⏳ Após o pagamento seus créditos são adicionados automaticamente."
        )

        pix_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Copiar código PIX", copy_text=CopyTextButton(text=payment_code))],
            [InlineKeyboardButton("🔄 Verificar Pagamento", callback_data=f"pix_check:{id_transaction}")],
        ])

        # Gera QR code: usa base64 da API se vier, senão gera localmente
        qr_b64 = pix_data.get("paymentCodeBase64")
        try:
            if qr_b64:
                qr_bytes = base64.b64decode(qr_b64)
            else:
                buf = io.BytesIO()
                qrcode.make(payment_code).save(buf, format="PNG")
                qr_bytes = buf.getvalue()

            tg_url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
            resp = await asyncio.to_thread(
                requests.post,
                tg_url,
                files={"photo": ("qr.png", qr_bytes, "image/png")},
                data={
                    "chat_id": user_id,
                    "caption": caption,
                    "parse_mode": "Markdown",
                    "reply_markup": pix_keyboard.to_json(),
                },
                timeout=30,
            )
            logger.info(f"📤 QR sendPhoto: {resp.status_code} {resp.text[:200]}")
            if resp.ok:
                await query.delete_message()
                return
        except Exception as e:
            logger.error(f"❌ Erro ao enviar QR: {e}")

        # Fallback sem imagem
        await query.edit_message_text(caption, parse_mode="Markdown", reply_markup=pix_keyboard)
    else:
        await query.edit_message_text(
            "❌ *Erro ao gerar PIX*\n\nTente novamente mais tarde.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Voltar", callback_data="start:back")]
            ])
        )

# ─── Callback Verificar Pagamento PIX ───────────────────────────────────────

async def handle_pix_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔄 Verificando pagamento...", show_alert=False)

    parts = query.data.split(":", 1)
    if len(parts) < 2 or not parts[1]:
        await query.answer("❌ ID de transação inválido.", show_alert=True)
        return

    id_transaction = parts[1]
    user_id = update.effective_user.id

    data = await asyncio.to_thread(check_pix_payment, id_transaction)
    if not data:
        await query.answer("❌ Não foi possível consultar o pagamento. Tente novamente.", show_alert=True)
        return

    # A API retorna ex: {"status": "PAID_OUT"} ou {"status": "WAITING_FOR_APPROVAL"}
    status = (data.get("status") or "").upper()

    PAID_STATUSES = {"PAID_OUT", "PAID"}
    STATUS_MAP = {
        "PAID_OUT":              "✅ Pago",
        "PAID":                  "✅ Pago",
        "WAITING_FOR_APPROVAL":  "⏳ Aguardando pagamento",
        "EXPIRED":               "❌ Expirado",
        "REFUNDED":              "↩️ Reembolsado",
        "FAILED":                "❌ Falhou",
    }
    status_label = STATUS_MAP.get(status, f"🔵 {status}" if status else "❓ Desconhecido")

    purchase = get_purchase_db(id_transaction)
    credits = purchase["credits"] if purchase else "?"
    amount = f"R$ {purchase['amount']:.2f}" if purchase else "?"

    if status in PAID_STATUSES:
        if purchase and purchase.get("status") == "pending":
            add_user_credits(user_id, purchase["credits"])
            update_purchase_status_db(id_transaction, "paid")
            extra = f"\n\n🎉 {purchase['credits']} créditos adicionados ao seu saldo!"
        else:
            extra = "\n\nCréditos já foram adicionados anteriormente."
    else:
        extra = ""

    await query.answer(
        f"{status_label}\n💰 {credits} créditos | {amount}{extra.strip()}",
        show_alert=True,
    )

# ─── Callbacks do Menu Principal ────────────────────────────────────────────

async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else None
    
    img_data = context.user_data.get("image_data")
    user_id = update.effective_user.id
    
    if not img_data:
        await query.edit_message_text(
            "❌ *Nenhuma imagem carregada!*\n\nEnvie uma foto primeiro.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return
    
    id_gen = img_data["id_gen"]
    
    if action == "video_poses":
        if get_user_credits(user_id) < system_config_get('video_cost', 5):
            await query.edit_message_text(
                f"❌ *Saldo insuficiente!*\n\n"
                f"💰 Seu saldo: {get_user_credits(user_id)} créditos\n"
                f"🎬 Vídeo custa: {system_config_get('video_cost', 5)} créditos",
                parse_mode="Markdown",
            )
            return
        
        try:
            poses = await asyncio.to_thread(api_get_video_poses)
            if poses:
                await query.edit_message_text(
                    f"🎬 *Vídeo Poses*\n\nEscolha um estilo ({len(poses)} disponíveis):\n💰 Custo: {system_config_get('video_cost', 5)} créditos",
                    reply_markup=_video_pose_keyboard(poses, id_gen, 0),
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text("❌ Nenhum estilo de vídeo disponível.")
        except Exception as e:
            await query.edit_message_text(f"❌ Erro: {e}")
    
    elif action == "photo_poses":
        if get_user_credits(user_id) < system_config_get('photo_cost', 2):
            await query.edit_message_text(
                f"❌ *Saldo insuficiente!*\n\n"
                f"💰 Seu saldo: {get_user_credits(user_id)} créditos\n"
                f"🖼 Foto custa: {system_config_get('photo_cost', 2)} créditos",
                parse_mode="Markdown",
            )
            return
        
        try:
            poses = await asyncio.to_thread(api_get_image_poses)
            if poses:
                await query.edit_message_text(
                    f"🖼 *Foto Poses*\n\nEscolha um estilo ({len(poses)} disponíveis):\n💰 Custo: {system_config_get('photo_cost', 2)} créditos",
                    reply_markup=_photo_pose_keyboard(poses, id_gen, 0),
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text("❌ Nenhum estilo de foto disponível.")
        except Exception as e:
            await query.edit_message_text(f"❌ Erro: {e}")
    
    elif action == "cloth":
        if get_user_credits(user_id) < system_config_get('photo_custom_cost', 3):
            await query.edit_message_text(
                f"❌ *Saldo insuficiente!*\n\n"
                f"💰 Seu saldo: {get_user_credits(user_id)} créditos\n"
                f"👗 Trocar roupa custa: {system_config_get('photo_custom_cost', 3)} créditos",
                parse_mode="Markdown",
            )
            return
        
        await query.edit_message_text(
            "👗 *Trocar Roupa*\n\nEscolha a roupa:\n💰 Custo: {system_config_get('photo_custom_cost', 3)} créditos",
            reply_markup=cloth_keyboard(id_gen),
            parse_mode="Markdown",
        )
    
    elif action == "undress":
        await query.edit_message_text(
            "⚡ *Despir Foto*\n\nEscolha uma opção:",
            reply_markup=undress_keyboard(id_gen),
            parse_mode="Markdown",
        )
    
    elif action == "back":
        await query.edit_message_text(
            f"📸 *Menu Principal*\n\n💰 Saldo: {get_user_credits(user_id)} créditos",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )

# ─── Callbacks de Vídeo ─────────────────────────────────────────────────────

async def handle_vid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    
    _, id_gen, action = parts[0], parts[1], parts[2]
    
    img_data = context.user_data.get("image_data")
    if not img_data or img_data.get("id_gen") != id_gen:
        await query.edit_message_text("❌ Sessão expirada.")
        return
    
    user_id = update.effective_user.id
    
    if action == "page":
        page = int(parts[3])
        try:
            poses = await asyncio.to_thread(api_get_video_poses)
            if poses:
                await query.edit_message_text(
                    f"🎬 *Vídeo Poses ({len(poses)} disponíveis)*\n💰 Custo: {system_config_get('video_cost', 5)} créditos",
                    reply_markup=_video_pose_keyboard(poses, id_gen, page),
                    parse_mode="Markdown",
                )
        except Exception as e:
            await query.edit_message_text(f"❌ Erro: {e}")
    
    elif action == "pose":
        try:
            index = int(parts[3])
            poses = await asyncio.to_thread(api_get_video_poses)
            
            if index < len(poses):
                pose = poses[index]
                pose_id = pose.get("id")
                pose_name = pose.get("name", "Desconhecido")
                
                processing_msg = await query.edit_message_text(
                    "⏳ *Processando vídeo...*\n\n"
                    f"🎬 Estilo: `{traduzir(pose_name)}`\n"
                    "🔄 Isso pode levar até 2 minutos.",
                    parse_mode="Markdown"
                )
                
                try:
                    await _submit_video(
                        id_gen, 
                        img_data["image_bytes"], 
                        img_data["chat_id"], 
                        user_id,
                        processing_msg,  # ← PASSA A MENSAGEM
                        pose_id          # ← PASSA A POSE
                    )
                    
                    # NÃO APAGA A MENSAGEM AQUI - O WEBHOOK VAI APAGAR
                    
                except Exception as e:
                    await processing_msg.delete()
                    await query.message.reply_text(
                        f"❌ *Erro ao processar:*\n{str(e)[:200]}",
                        parse_mode="Markdown"
                    )
            else:
                await query.edit_message_text("❌ Estilo inválido.")
        except Exception as e:
            await query.edit_message_text(f"❌ Erro: {str(e)[:200]}")

# ─── Callbacks de Foto ──────────────────────────────────────────────────────

async def handle_img_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    
    _, id_gen, action = parts[0], parts[1], parts[2]
    
    img_data = context.user_data.get("image_data")
    if not img_data or img_data.get("id_gen") != id_gen:
        await query.edit_message_text("❌ Sessão expirada.")
        return
    
    user_id = update.effective_user.id
    
    if action == "page":
        page = int(parts[3])
        try:
            poses = await asyncio.to_thread(api_get_image_poses)
            if poses:
                await query.edit_message_text(
                    f"🖼 *Foto Poses ({len(poses)} disponíveis)*\n💰 Custo: {system_config_get('photo_cost', 2)} créditos",
                    reply_markup=_photo_pose_keyboard(poses, id_gen, page),
                    parse_mode="Markdown",
                )
        except Exception as e:
            await query.edit_message_text(f"❌ Erro: {e}")
    
    elif action == "pose":
        pose_id = parts[3]
        
        processing_msg = await query.edit_message_text(
            "⏳ *Processando imagem...*\n\n"
            "🔄 Isso pode levar alguns segundos.",
            parse_mode="Markdown"
        )
        
        try:
            params = img_data.get("params", {})
            await _submit_image(
                id_gen, 
                img_data["image_bytes"], 
                img_data["chat_id"], 
                user_id,
                processing_msg,  # ← PASSA A MENSAGEM
                pose_id,         # ← PASSA A POSE
                **params
            )
            
            # NÃO APAGA A MENSAGEM AQUI - O WEBHOOK VAI APAGAR
            
        except Exception as e:
            await processing_msg.delete()
            await query.message.reply_text(
                f"❌ *Erro ao processar:*\n{str(e)[:200]}",
                parse_mode="Markdown"
            )

# ─── Callbacks de Roupa ─────────────────────────────────────────────────────

async def handle_cloth_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    
    _, id_gen, cloth_name = parts[0], parts[1], parts[2]
    
    img_data = context.user_data.get("image_data")
    if not img_data or img_data.get("id_gen") != id_gen:
        await query.edit_message_text("❌ Sessão expirada.")
        return
    
    user_id = update.effective_user.id
    
    if cloth_name in VALID_CLOTHES:
        img_data["params"]["cloth"] = cloth_name
        context.user_data["image_data"] = img_data
        
        processing_msg = await query.edit_message_text(
            f"⏳ *Processando imagem...*\n\n"
            f"👗 Roupa selecionada: {traduzir(cloth_name)}\n"
            f"🔄 Isso pode levar alguns segundos.",
            parse_mode="Markdown"
        )
        
        try:
            params = img_data.get("params", {})
            await _submit_image(
                id_gen, 
                img_data["image_bytes"], 
                img_data["chat_id"], 
                user_id,
                processing_msg,  # ← PASSA A MENSAGEM
                None,            # ← SEM POSE
                **params
            )
            
            # NÃO APAGA A MENSAGEM AQUI - O WEBHOOK VAI APAGAR
            
        except Exception as e:
            await processing_msg.delete()
            await query.message.reply_text(
                f"❌ *Erro ao processar:*\n{str(e)[:200]}",
                parse_mode="Markdown"
            )
    else:
        await query.edit_message_text("❌ Roupa inválida.", parse_mode=None)

# ─── Callbacks de Despir ─────────────────────────────────────────────────────

async def handle_undress_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    
    _, id_gen, action = parts[0], parts[1], parts[2]
    
    img_data = context.user_data.get("image_data")
    if not img_data or img_data.get("id_gen") != id_gen:
        await query.edit_message_text("❌ Sessão expirada.")
        return
    
    user_id = update.effective_user.id
        
    if action == "direct":
        if get_user_credits(user_id) < system_config_get('photo_cost', 2):
            await query.edit_message_text(
                f"❌ *Saldo insuficiente!*\n\n"
                f"💰 Seu saldo: {get_user_credits(user_id)} créditos\n"
                f"⚡ Despir custa: {system_config_get('photo_cost', 2)} créditos",
                parse_mode="Markdown",
            )
            return
        
        processing_msg = await query.edit_message_text(
            "⏳ *Processando imagem...*\n\n"
            "🔄 Isso pode levar alguns segundos.",
            parse_mode="Markdown"
        )
        
        try:
            await _submit_image(
                id_gen, 
                img_data["image_bytes"], 
                img_data["chat_id"], 
                user_id,
                processing_msg,  # ← PASSA A MENSAGEM
                None             # ← SEM POSE
            )
            
            # NÃO APAGA A MENSAGEM AQUI - O WEBHOOK VAI APAGAR
            
        except Exception as e:
            await processing_msg.delete()
            await query.message.reply_text(
                f"❌ *Erro ao processar:*\n{str(e)[:200]}",
                parse_mode="Markdown"
            )

# ─── Callbacks de Personalização ───────────────────────────────────────────

async def handle_custom_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    
    _, id_gen, action = parts[0], parts[1], parts[2]
    
    img_data = context.user_data.get("image_data")
    if not img_data or img_data.get("id_gen") != id_gen:
        await query.edit_message_text("❌ Sessão expirada.")
        return
    
    user_id = update.effective_user.id
    
    if action == "back":
        await query.edit_message_text(
            "⚡ *Despir Foto*",
            reply_markup=undress_keyboard(id_gen),
            parse_mode="Markdown",
        )
    
    elif action == "cancel":
        img_data["params"] = {}
        context.user_data["image_data"] = img_data
        await query.edit_message_text(
            "❌ Personalização cancelada.",
            reply_markup=undress_keyboard(id_gen),
            parse_mode=None,
        )
    
    elif action == "process":
        params = img_data.get("params", {})
        if not params:
            await query.edit_message_text(
                "⚠️ Nenhum parâmetro selecionado.\nUse os botões para personalizar.",
                reply_markup=customize_keyboard(id_gen),
                parse_mode=None,
            )
            return
        
        if get_user_credits(user_id) < system_config_get('photo_custom_cost', 3):
            await query.edit_message_text(
                f"❌ *Saldo insuficiente!*\n\n"
                f"💰 Seu saldo: {get_user_credits(user_id)} créditos\n"
                f"⚙️ Despir personalizado custa: {system_config_get('photo_custom_cost', 3)} créditos",
                parse_mode="Markdown",
            )
            return
        
        processing_msg = await query.edit_message_text(
            "⏳ *Processando imagem com parâmetros personalizados...*\n\n"
            "🔄 Isso pode levar alguns segundos.",
            parse_mode="Markdown"
        )
        
        try:
            await _submit_image(
                id_gen, 
                img_data["image_bytes"], 
                img_data["chat_id"], 
                user_id,
                processing_msg,  # ← PASSA A MENSAGEM
                None,            # ← SEM POSE
                **params
            )
            
            # NÃO APAGA A MENSAGEM AQUI - O WEBHOOK VAI APAGAR
            
        except Exception as e:
            await processing_msg.delete()
            await query.message.reply_text(
                f"❌ *Erro ao processar:*\n{str(e)[:200]}",
                parse_mode="Markdown"
            )
    
    else:
        param = action
        values_map = {
            "breast_size": VALID_BREAST_SIZES,
            "butt_size": VALID_BUTT_SIZES,
            "body_type": VALID_BODY_TYPES,
            "age": VALID_AGES,
            "post_gen": VALID_POST_GEN,
            "cloth": VALID_CLOTHES,
        }
        if param in values_map:
            await query.edit_message_text(
                f"📌 *Selecione {traduzir(param)}:*",
                reply_markup=value_keyboard("custom", id_gen, param, values_map[param]),
                parse_mode="Markdown",
            )

# ─── Callbacks de Set ─────────────────────────────────────────────────────

async def handle_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    if len(parts) < 4:
        return
    
    _, id_gen, param, value = parts[0], parts[1], parts[2], parts[3]
    
    img_data = context.user_data.get("image_data")
    if not img_data or img_data.get("id_gen") != id_gen:
        await query.edit_message_text("❌ Sessão expirada.")
        return
    
    img_data["params"][param] = value
    context.user_data["image_data"] = img_data
    
    params_text = "\n".join([f"  • {traduzir(k)}: {traduzir(v)}" for k, v in img_data["params"].items()])
    
    await query.edit_message_text(
        f"✅ *{traduzir(param)}:* `{traduzir(value)}`\n\n"
        "⚙️ *Parâmetros atuais:*\n" + (params_text if params_text else "  • Nenhum"),
        reply_markup=customize_keyboard(id_gen),
        parse_mode="Markdown",
    )

# ─── Comandos de Start (Admin) ───────────────────────────────────────────────

async def cmd_set_start_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    # Tudo após o comando (preserva quebras de linha e espaços)
    full_text = update.message.text
    parts = full_text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "❌ Use: `/setstarttexto TEXTO`\n\nUse `{credits}` para mostrar os créditos do usuário.",
            parse_mode="Markdown"
        )
        return
    new_text = parts[1]
    system_config_set("start_text", new_text)
    await update.message.reply_text(
        f"✅ Texto do start atualizado!\n\n*Preview:*\n{new_text.replace('{credits}', '99')}",
        parse_mode="Markdown"
    )

async def cmd_set_start_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    context.user_data["esperando_foto_start"] = True
    await update.message.reply_text("📷 Agora envie a foto que deseja usar no start.")

async def cmd_remove_start_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    system_config_set("start_photo_id", "")
    await update.message.reply_text("✅ Foto do start removida. Será usado start.png se existir.")

async def cmd_add_start_botao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    full_text = update.message.text
    parts = full_text.split(None, 1)
    if len(parts) < 2 or "|" not in parts[1]:
        await update.message.reply_text(
            "❌ Use: `/addstartbotao LABEL | URL`\n\nExemplo:\n`/addstartbotao Nosso Canal | https://t.me/canal`",
            parse_mode="Markdown"
        )
        return
    label_url = parts[1].split("|", 1)
    label = label_url[0].strip()
    url = label_url[1].strip()
    if not label or not url:
        await update.message.reply_text("❌ Label ou URL inválidos.")
        return
    buttons = _get_start_buttons()
    buttons.append({"text": label, "url": url})
    _set_start_buttons(buttons)
    await update.message.reply_text(
        f"✅ Botão adicionado!\n• *{label}* → `{url}`\n\nTotal: {len(buttons)} botão(s).",
        parse_mode="Markdown"
    )

async def cmd_remove_start_botao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Acesso negado.")
        return
    if not context.args:
        await update.message.reply_text(
            "❌ Use: `/removestartbotao N` (N = número do botão)\n\nUse `/painel` → ✏️ Mensagem de Start para ver a lista.",
            parse_mode="Markdown"
        )
        return
    try:
        idx = int(context.args[0]) - 1
        buttons = _get_start_buttons()
        if idx < 0 or idx >= len(buttons):
            await update.message.reply_text(f"❌ Botão {idx+1} não existe. Total: {len(buttons)}.")
            return
        removed = buttons.pop(idx)
        _set_start_buttons(buttons)
        await update.message.reply_text(
            f"✅ Botão removido: *{removed['text']}*\nRestam {len(buttons)} botão(s).",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("❌ Número inválido.")

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("🚀 INICIANDO BOT")
    logger.info(f"📡 API: {API_BASE_URL}")
    logger.info(f"🔗 Webhook: {CALLBACK_URL}")
    logger.info("=" * 60)
    
    # Inicializa banco de dados
    init_db()
    init_system()
    
    threading.Thread(target=_run_flask, daemon=True).start()
    logger.info(f"✅ Webhook server: http://0.0.0.0:{WEBHOOK_PORT}/webhook")

    app = Application.builder().token(TOKEN).build()

    # Captura o username real do bot via requests síncrono (sem tocar no loop async)
    global BOT_USERNAME
    try:
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=10)
        BOT_USERNAME = r.json()["result"]["username"]
        logger.info(f"🤖 Bot username: @{BOT_USERNAME}")
    except Exception as e:
        logger.warning(f"⚠️ Não foi possível obter username do bot: {e}")
    
    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("creditos", cmd_creditos))
    
    # Handlers de mídia
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    
    # Comandos de Admin
    app.add_handler(CommandHandler("painel", cmd_admin))
    app.add_handler(CommandHandler("addcredits", cmd_add_credits))
    app.add_handler(CommandHandler("addpackage", cmd_add_package))
    app.add_handler(CommandHandler("editpackage", cmd_edit_package))
    app.add_handler(CommandHandler("removepackage", cmd_remove_package))
    app.add_handler(CommandHandler("setnewuser",  cmd_set_new_user))
    app.add_handler(CommandHandler("setafiliado", cmd_set_afiliado))
    app.add_handler(CommandHandler("setindicado", cmd_set_indicado))
    app.add_handler(CommandHandler("setreferral", cmd_set_referral))
    app.add_handler(CommandHandler("setcosts", cmd_set_costs))
    app.add_handler(CommandHandler("setcanal", cmd_set_canal))
    app.add_handler(CommandHandler("setstarttexto", cmd_set_start_texto))
    app.add_handler(CommandHandler("setstartfoto", cmd_set_start_foto))
    app.add_handler(CommandHandler("removestartfoto", cmd_remove_start_foto))
    app.add_handler(CommandHandler("addstartbotao", cmd_add_start_botao))
    app.add_handler(CommandHandler("removestartbotao", cmd_remove_start_botao))
    
    # Callbacks de Admin
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern=r"^admin:"))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_start_callback, pattern=r"^start:"))
    app.add_handler(CallbackQueryHandler(handle_buy_callback, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(handle_pix_check_callback, pattern=r"^pix_check:"))
    app.add_handler(CallbackQueryHandler(handle_menu_callback, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(handle_vid_callback, pattern=r"^vid:"))
    app.add_handler(CallbackQueryHandler(handle_img_callback, pattern=r"^img:"))
    app.add_handler(CallbackQueryHandler(handle_cloth_callback, pattern=r"^cloth:"))
    app.add_handler(CallbackQueryHandler(handle_undress_callback, pattern=r"^undress:"))
    app.add_handler(CallbackQueryHandler(handle_custom_callback, pattern=r"^custom:"))
    app.add_handler(CallbackQueryHandler(handle_set_callback, pattern=r"^set:"))
    app.add_handler(CallbackQueryHandler(lambda u, c: u.answer(), pattern=r"^ignore"))

    logger.info("🤖 Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()