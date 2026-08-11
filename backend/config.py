import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=True)


def _strip_env_value(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1].strip()
    return v


def env(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return _strip_env_value(raw)


def require_env(name: str) -> str:
    v = env(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def httpx_verify() -> bool:
    raw = env("TABLEAU_SSL_VERIFY", "1").lower()
    return raw not in ("0", "false", "no")


def has_tableau_connected_app() -> bool:
    return bool(
        env("TABLEAU_SERVER")
        and env("TABLEAU_CONNECTED_APP_CLIENT_ID")
        and env("TABLEAU_CONNECTED_APP_SECRET_ID")
        and env("TABLEAU_CONNECTED_APP_SECRET")
        and env("TABLEAU_JWT_SUB_CLAIM")
    )


def has_tableau_pat() -> bool:
    return bool(env("TABLEAU_SERVER") and env("TABLEAU_PAT_NAME") and env("TABLEAU_PAT_VALUE"))


def has_tableau_creds() -> bool:
    """True when Connected App (preferred) or PAT is configured for Tableau REST access."""
    return has_tableau_connected_app() or has_tableau_pat()


def tableau_auth_mode() -> str:
    if has_tableau_connected_app():
        return "connected_app"
    if has_tableau_pat():
        return "pat"
    return "none"
