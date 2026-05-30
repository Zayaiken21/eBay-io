import sqlite3
from pathlib import Path

import streamlit as st
from cryptography.fernet import Fernet

DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "ebay_accounts.db"


def get_cipher() -> Fernet:
    key = st.secrets.get("ENCRYPTION_KEY")

    if not key:
        raise RuntimeError("Missing ENCRYPTION_KEY in .streamlit/secrets.toml")

    return Fernet(key.encode())


def init_ebay_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ebay_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_name TEXT NOT NULL,
                role TEXT NOT NULL,
                environment TEXT NOT NULL,
                marketplace_id TEXT NOT NULL,
                encrypted_user_token TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def save_ebay_account(
    owner_name: str,
    role: str,
    environment: str,
    marketplace_id: str,
    user_access_token: str,
) -> None:
    init_ebay_db()

    cipher = get_cipher()
    encrypted_token = cipher.encrypt(user_access_token.encode()).decode()

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO ebay_accounts (
                owner_name,
                role,
                environment,
                marketplace_id,
                encrypted_user_token
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                owner_name,
                role,
                environment,
                marketplace_id,
                encrypted_token,
            ),
        )


def get_latest_ebay_account(owner_name: str) -> dict | None:
    init_ebay_db()

    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            """
            SELECT owner_name, role, environment, marketplace_id, encrypted_user_token
            FROM ebay_accounts
            WHERE owner_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (owner_name,),
        ).fetchone()

    if not row:
        return None

    cipher = get_cipher()

    return {
        "owner_name": row[0],
        "role": row[1],
        "environment": row[2],
        "marketplace_id": row[3],
        "user_access_token": cipher.decrypt(row[4].encode()).decode(),
    }