import json
import os
import shutil
import subprocess
import sys

from loguru import logger

from agyswitch.config import HOME, TOKEN_FILE, ensure_directories


def get_system_token_raw() -> str | None:
    """Retrieve the raw token string from the system credential store or token files."""
    # 1. Linux Secret Service (via secret-tool)
    if sys.platform.startswith("linux") and shutil.which("secret-tool"):
        try:
            res = subprocess.run(
                ["secret-tool", "lookup", "service", "gemini", "username", "antigravity"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception as e:
            logger.debug(f"secret-tool lookup error: {e}")

    # 2. macOS Keychain
    if sys.platform == "darwin" and shutil.which("security"):
        try:
            res = subprocess.run(
                ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception as e:
            logger.debug(f"macOS security lookup error: {e}")

    # 3. File fallback locations
    candidates = [
        TOKEN_FILE,
        HOME / "antigravity-oauth-token",
        HOME / ".gemini" / "antigravity-oauth-token",
    ]
    for p in candidates:
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8-sig").strip()
                if content:
                    return content
            except Exception as e:
                logger.debug(f"Failed reading token from {p}: {e}")

    return None


def get_system_token() -> dict | None:
    """Get parsed JSON token payload from the system store."""
    raw = get_system_token_raw()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.debug(f"Failed parsing system token JSON: {e}")
    return None


def set_system_token(payload: dict) -> bool:
    """Write token payload to system credential store and fallback files."""
    ensure_directories()
    raw_json = json.dumps(payload)
    success = False

    # 1. Linux Secret Service
    if sys.platform.startswith("linux") and shutil.which("secret-tool"):
        try:
            proc = subprocess.run(
                [
                    "secret-tool",
                    "store",
                    "--label=Password for 'antigravity' on 'gemini'",
                    "service",
                    "gemini",
                    "username",
                    "antigravity",
                ],
                input=raw_json,
                text=True,
                capture_output=True,
                timeout=5,
            )
            if proc.returncode == 0:
                success = True
                logger.debug("Successfully updated Secret Service credential via secret-tool")
        except Exception as e:
            logger.warning(f"Could not store credential via secret-tool: {e}")

    # 2. macOS Keychain
    if sys.platform == "darwin" and shutil.which("security"):
        try:
            proc = subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-s",
                    "gemini",
                    "-a",
                    "antigravity",
                    "-w",
                    raw_json,
                    "-U",
                ],
                capture_output=True,
                timeout=5,
            )
            if proc.returncode == 0:
                success = True
                logger.debug("Successfully updated macOS Keychain credential")
        except Exception as e:
            logger.warning(f"Could not store credential in macOS Keychain: {e}")

    # 3. Always write to file fallback ~/.gemini/antigravity-cli/antigravity-oauth-token
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically via temp file
        temp_file = TOKEN_FILE.with_suffix(".tmp")
        temp_file.write_text(raw_json, encoding="utf-8")
        os.chmod(temp_file, 0o600)
        temp_file.replace(TOKEN_FILE)
        success = True
        logger.debug(f"Successfully wrote fallback token to {TOKEN_FILE}")
    except Exception as e:
        logger.warning(f"Failed writing fallback token file: {e}")

    return success
