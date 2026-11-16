from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger

from agyswitch.config import (
    ACTIVE_PROFILE_FILE,
    PROFILES_DIR,
    ensure_directories,
)


@dataclass
class Profile:
    name: str
    email: str
    access_token: str
    refresh_token: str
    expiry: str = ""
    token_type: str = "Bearer"
    auth_method: str = "consumer"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_token_payload(self) -> dict:
        """Returns the format expected by Antigravity CLI and Google OAuth storage."""
        return {
            "token": {
                "access_token": self.access_token,
                "token_type": self.token_type,
                "refresh_token": self.refresh_token,
                "expiry": self.expiry,
            },
            "auth_method": self.auth_method,
            "id_token_email": self.email,
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        return cls(
            name=data["name"],
            email=data.get("email", ""),
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            expiry=data.get("expiry", ""),
            token_type=data.get("token_type", "Bearer"),
            auth_method=data.get("auth_method", "consumer"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )

    @classmethod
    def from_token_payload(cls, name: str, payload: dict) -> Profile:
        tok = payload.get("token", {}) if isinstance(payload.get("token"), dict) else payload
        email = (
            payload.get("id_token_email")
            or payload.get("email")
            or (tok.get("email") if isinstance(tok, dict) else "")
            or ""
        )
        return cls(
            name=name,
            email=email,
            access_token=tok.get("access_token", ""),
            refresh_token=tok.get("refresh_token", ""),
            expiry=str(tok.get("expiry", "")),
            token_type=tok.get("token_type", "Bearer"),
            auth_method=payload.get("auth_method", "consumer"),
        )

    def is_expired(self, buffer_seconds: int = 300) -> bool:
        """Check if token is expired or close to expiring (within buffer_seconds threshold)."""
        if not self.expiry:
            return False
        try:
            exp_dt = datetime.fromisoformat(self.expiry.replace("Z", "+00:00"))
            return exp_dt - timedelta(seconds=buffer_seconds) <= datetime.now(UTC)
        except Exception:
            return False


def _get_profile_path(name: str) -> Path:
    ensure_directories()
    return PROFILES_DIR / f"{name}.json"


def save_profile(profile: Profile) -> None:
    path = _get_profile_path(profile.name)
    profile.updated_at = time.time()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, indent=2)
        logger.debug(f"Saved profile '{profile.name}' to {path}")
    except Exception as e:
        logger.error(f"Failed to save profile '{profile.name}': {e}")
        raise


def load_profile(name: str) -> Profile | None:
    path = _get_profile_path(name)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return Profile.from_dict(data)
    except Exception as e:
        logger.error(f"Failed to load profile '{name}' from {path}: {e}")
        return None


def list_profiles() -> list[Profile]:
    ensure_directories()
    profiles: list[Profile] = []
    for f in sorted(PROFILES_DIR.glob("*.json")):
        p = load_profile(f.stem)
        if p:
            profiles.append(p)
    return profiles


def delete_profile(name: str) -> bool:
    path = _get_profile_path(name)
    if path.exists():
        path.unlink()
        if get_active_profile() == name:
            clear_active_profile()
        return True
    return False


def rename_profile(old_name: str, new_name: str) -> bool:
    profile = load_profile(old_name)
    if not profile:
        return False
    profile.name = new_name
    save_profile(profile)
    delete_profile(old_name)
    if get_active_profile() == old_name:
        set_active_profile(new_name)
    return True


def get_active_profile() -> str | None:
    ensure_directories()
    if ACTIVE_PROFILE_FILE.exists():
        try:
            name = ACTIVE_PROFILE_FILE.read_text(encoding="utf-8").strip()
            if name and _get_profile_path(name).exists():
                return name
        except Exception:
            pass
    return None


def set_active_profile(name: str) -> None:
    ensure_directories()
    ACTIVE_PROFILE_FILE.write_text(name, encoding="utf-8")


def clear_active_profile() -> None:
    if ACTIVE_PROFILE_FILE.exists():
        ACTIVE_PROFILE_FILE.unlink(missing_ok=True)
