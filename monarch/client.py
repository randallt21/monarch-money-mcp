"""Shared async client wrapper for Monarch Money authentication."""

import os
from pathlib import Path

from monarchmoney import MonarchMoney
from monarchmoney.monarchmoney import MonarchMoneyEndpoints

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Session file uses the monarchmoney library's built-in pickle serialization.
# This is controlled entirely by the library and only stores auth tokens.
SESSION_FILE = PROJECT_ROOT / ".mm" / "mm_session.pickle"


def _load_dotenv() -> None:
    """Load .env file from project root into os.environ (no external deps)."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


async def get_client() -> MonarchMoney:
    """
    Authenticate and return an initialized MonarchMoney client.

    Tries to load a cached session first. If invalid, logs in fresh
    and saves the session for future use.

    Reads credentials from environment variables (or .env file in project root):
      MONARCH_EMAIL         — account email address
      MONARCH_PASSWORD      — account password
      MONARCH_MFA_SECRET    — TOTP secret for 2FA (optional but recommended)

    Raises:
        RuntimeError: if required environment variables are missing.
    """
    _load_dotenv()

    email = os.environ.get("MONARCH_EMAIL")
    password = os.environ.get("MONARCH_PASSWORD")
    mfa_secret = os.environ.get("MONARCH_MFA_SECRET")

    if not email:
        raise RuntimeError("MONARCH_EMAIL environment variable is required")
    if not password:
        raise RuntimeError("MONARCH_PASSWORD environment variable is required")

    # Patch the API base URL to the correct endpoint.
    MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"

    mm = MonarchMoney()
    mm._headers["Origin"] = "https://app.monarch.com"

    # Try cached session first to avoid TOTP exhaustion on rapid calls.
    if SESSION_FILE.exists():
        try:
            mm.load_session(str(SESSION_FILE))
            # Validate session with a lightweight call
            await mm.get_subscription_details()
            return mm
        except Exception:
            pass  # Session expired or invalid, fall through to fresh login

    # Fresh login
    try:
        await mm.login(
            email,
            password,
            mfa_secret_key=mfa_secret,
            save_session=False,
            use_saved_session=False,
        )
    except TypeError:
        await mm.login(email, password, mfa_secret_key=mfa_secret)

    # Save session for next time
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    mm.save_session(str(SESSION_FILE))

    return mm
