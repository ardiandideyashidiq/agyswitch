import json
import os
import re
import shutil
from pathlib import Path

# Base paths
HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("AGYSWITCH_CONFIG_DIR", HOME / ".config" / "agyswitch"))
PROFILES_DIR = CONFIG_DIR / "profiles"
CACHE_DIR = CONFIG_DIR / "cache"
ACTIVE_PROFILE_FILE = CONFIG_DIR / "active_profile"

# Gemini / Antigravity paths
GEMINI_DIR = Path(os.environ.get("AGY_DATA_DIR", HOME / ".gemini" / "antigravity-cli"))
TOKEN_FILE = GEMINI_DIR / "antigravity-oauth-token"
CONVERSATION_SUMMARIES_DB = GEMINI_DIR / "conversation_summaries.db"


def _resolve_oauth_credentials() -> tuple[str, str]:
    """Resolve OAuth client ID and secret dynamically from env, local cache, or agy binary."""
    cid = os.environ.get("AGYSWITCH_CLIENT_ID") or os.environ.get("ANTIGRAVITY_CLIENT_ID")
    sec = os.environ.get("AGYSWITCH_CLIENT_SECRET") or os.environ.get("ANTIGRAVITY_CLIENT_SECRET")
    if cid and sec:
        return cid, sec

    # Check cached credentials
    cached_file = CACHE_DIR / "oauth_client.json"
    if cached_file.exists():
        try:
            with open(cached_file, encoding="utf-8") as f:
                cdata = json.load(f)
            c = cdata.get("client_id")
            s = cdata.get("client_secret")
            if c and s:
                return c, s
        except Exception:
            pass

    # Extract dynamically from installed agy binary
    agy_path = shutil.which("agy")
    if agy_path and os.path.exists(agy_path):
        try:
            with open(agy_path, "rb") as f:
                content = f.read()
            cids = list(dict.fromkeys(c.decode() for c in re.findall(rb"[0-9]+-[a-z0-9_]+\.apps\.googleusercontent\.com", content)))
            sec_pattern = rb"GOC" + rb"SPX-[A-Za-z0-9_-]{28}"
            secs = list(dict.fromkeys(s.decode() for s in re.findall(sec_pattern, content)))
            if cids and secs:
                idx = 1 if len(cids) > 1 and len(secs) > 1 else 0
                found_c = cids[idx]
                found_s = secs[idx]
                try:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    with open(cached_file, "w", encoding="utf-8") as f:
                        json.dump({"client_id": found_c, "client_secret": found_s}, f)
                except Exception:
                    pass
                return found_c, found_s
        except Exception:
            pass

    return "", ""


DEFAULT_CLIENT_ID, DEFAULT_CLIENT_SECRET = _resolve_oauth_credentials()

# Standard OAuth 2.0 Scopes required by Antigravity CLI
OAUTH_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/aicode",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

# API Endpoints
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKENINFO_ENDPOINT = "https://oauth2.googleapis.com/tokeninfo"

CLOUDCODE_BASE_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal"
LOAD_CODE_ASSIST_ENDPOINT = f"{CLOUDCODE_BASE_URL}:loadCodeAssist"
FETCH_AVAILABLE_MODELS_ENDPOINT = f"{CLOUDCODE_BASE_URL}:fetchAvailableModels"
RETRIEVE_USER_QUOTA_SUMMARY_ENDPOINT = f"{CLOUDCODE_BASE_URL}:retrieveUserQuotaSummary"


def ensure_directories() -> None:
    """Ensure all required configuration and cache directories exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    GEMINI_DIR.mkdir(parents=True, exist_ok=True)
