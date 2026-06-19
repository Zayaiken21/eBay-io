import json
import secrets
import string
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path("data")
TOKEN_FILE = DATA_DIR / "client_tokens.json"


def ensure_store() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text("[]", encoding="utf-8")


def load_tokens() -> list[dict]:
    ensure_store()

    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        TOKEN_FILE.write_text("[]", encoding="utf-8")
        return []


def save_tokens(tokens: list[dict]) -> None:
    ensure_store()
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def generate_short_token(length: int = 5) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_token(client_name: str) -> dict:
    tokens = load_tokens()
    existing_tokens = {item.get("token") for item in tokens}

    token = generate_short_token()

    while token in existing_tokens:
        token = generate_short_token()

    token_data = {
        "client_name": client_name.strip(),
        "token": token,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    tokens.append(token_data)
    save_tokens(tokens)

    return token_data


def cancel_token(token: str) -> None:
    tokens = load_tokens()

    for item in tokens:
        if item.get("token") == token:
            item["active"] = False

    save_tokens(tokens)


def cancel_all_tokens() -> None:
    tokens = load_tokens()

    for item in tokens:
        item["active"] = False

    save_tokens(tokens)


def validate_client_token(token: str) -> dict | None:
    tokens = load_tokens()

    for item in tokens:
        if item.get("token") == token and item.get("active") is True:
            return item

    return None