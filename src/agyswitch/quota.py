import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from curl_cffi import requests
from loguru import logger

from agyswitch.config import (
    CACHE_DIR,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    FETCH_AVAILABLE_MODELS_ENDPOINT,
    LOAD_CODE_ASSIST_ENDPOINT,
    RETRIEVE_USER_QUOTA_SUMMARY_ENDPOINT,
    TOKEN_ENDPOINT,
    ensure_directories,
)
from agyswitch.keyring import set_system_token
from agyswitch.vault import Profile, get_active_profile, save_profile

MODEL_DISPLAY_NAMES = {
    "gemini-3.8-flash-tiered": "Gemini 3.8 Flash (High)",
    "gemini-3.7-flash-tiered": "Gemini 3.7 Flash (High)",
    "gemini-3.6-flash-tiered": "Gemini 3.6 Flash (High)",
    "gemini-4-argon-high": "Gemini 4 Argon (High)",
    "gemini-4-argon-medium": "Gemini 4 Argon (Medium)",
    "gemini-4-argon-low": "Gemini 4 Argon (Low)",
    "gemini-4-argon": "Gemini 4 Argon",
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)",
    "claude-opus-4-6-thinking": "Claude Opus 4.6 (Thinking)",
    "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)",
}


def format_duration(seconds: float) -> str:
    """Format seconds into readable human duration (e.g., '6d 17h', '2h 15m', '45m')."""
    if seconds <= 0:
        return "Now"
    total_secs = int(seconds)
    days, remainder = divmod(total_secs, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m"
    return "1m"


def refresh_profile_token(
    profile: Profile,
    client_id: str = DEFAULT_CLIENT_ID,
    client_secret: str = DEFAULT_CLIENT_SECRET,
    force: bool = False,
) -> str:
    """Refresh access token if expired or force requested."""
    if not force and not profile.is_expired() and profile.access_token:
        return profile.access_token

    if not profile.refresh_token:
        raise ValueError(f"Profile '{profile.name}' has no refresh token.")

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": profile.refresh_token,
    }

    resp = requests.post(
        TOKEN_ENDPOINT,
        data=data,
        impersonate="chrome",
        timeout=10,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to refresh token for '{profile.name}' (HTTP {resp.status_code}): {resp.text}"
        )

    res_data = resp.json()
    new_token = res_data.get("access_token")
    if not new_token:
        raise ValueError("No access_token returned in refresh response.")

    profile.access_token = new_token
    expires_in = res_data.get("expires_in", 3599)
    from datetime import timedelta
    expiry_dt = datetime.now(UTC) + timedelta(seconds=expires_in)
    profile.expiry = expiry_dt.isoformat()

    save_profile(profile)

    # If this is the active profile, also update system token store
    if get_active_profile() == profile.name:
        set_system_token(profile.to_token_payload())

    return new_token


def _get_tier_label(paid_tier: dict | None, curr_tier: dict | None) -> str:
    val = (paid_tier or {}).get("name") or (paid_tier or {}).get("id") or (curr_tier or {}).get("name") or (curr_tier or {}).get("id")
    if not val:
        return "STANDARD"
    t = str(val).upper()
    if "ULTRA" in t:
        return "ULTRA"
    if "PRO" in t:
        return "PRO"
    if "STANDARD" in t or "DEFAULT" in t or "FREE" in t:
        return "STANDARD"
    return t.replace("TIER_", "")


def fetch_profile_quota(profile: Profile, force_refresh_token: bool = False) -> dict:
    """Fetch quota usage and limits for a single profile."""
    ensure_directories()
    result = {
        "profile": profile.name,
        "email": profile.email,
        "tier": "STANDARD",
        "models": [],
        "min_gemini_quota": 100.0,
        "avg_gemini_quota": 100.0,
        "is_exhausted": False,
        "error": None,
    }

    try:
        access_token = refresh_profile_token(profile, force=force_refresh_token)
    except Exception as e:
        logger.error(f"Error getting access token for {profile.name}: {e}")
        result["error"] = f"Token error: {e}"
        result["is_exhausted"] = True
        result["min_gemini_quota"] = 0.0
        return result

    # CRITICAL: daily-cloudcode-pa.googleapis.com requires User-Agent: antigravity
    auth_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity",
    }

    tier_cache_file = CACHE_DIR / f"tier_{profile.name}.json"
    cached_tier = None
    cached_proj = "aicode-consumers"

    if tier_cache_file.exists():
        try:
            with open(tier_cache_file, encoding="utf-8") as f:
                cdata = json.load(f)
                if time.time() - cdata.get("time", 0) < 43200:
                    cached_tier = cdata.get("tier")
                    cached_proj = cdata.get("proj_id", "aicode-consumers")
        except Exception:
            pass

    def call_lca():
        try:
            r = requests.post(
                LOAD_CODE_ASSIST_ENDPOINT,
                headers=auth_headers,
                json={"metadata": {"ideType": "ANTIGRAVITY", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}},
                timeout=10,
            )
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def call_fam(proj: str):
        try:
            r = requests.post(
                FETCH_AVAILABLE_MODELS_ENDPOINT,
                headers=auth_headers,
                json={"project": proj},
                timeout=10,
            )
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def call_uqs(proj: str):
        try:
            r = requests.post(
                RETRIEVE_USER_QUOTA_SUMMARY_ENDPOINT,
                headers=auth_headers,
                json={"project": proj},
                timeout=10,
            )
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    proj_id = cached_proj
    res_fam = None
    res_uqs = None

    if not cached_tier:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_lca = pool.submit(call_lca)
            fut_fam = pool.submit(call_fam, proj_id)
            fut_uqs = pool.submit(call_uqs, proj_id)
            res_lca = fut_lca.result()
            res_fam = fut_fam.result()
            res_uqs = fut_uqs.result()

        if res_lca:
            result["tier"] = _get_tier_label(res_lca.get("paidTier"), res_lca.get("currentTier"))
            proj_data = res_lca.get("cloudaicompanionProject")
            if isinstance(proj_data, dict):
                proj_id = proj_data.get("id", "aicode-consumers")
            elif isinstance(proj_data, str) and proj_data:
                proj_id = proj_data
            else:
                proj_id = "aicode-consumers"

            try:
                with open(tier_cache_file, "w", encoding="utf-8") as f:
                    json.dump({"tier": result["tier"], "proj_id": proj_id, "time": time.time()}, f)
            except Exception:
                pass
    else:
        result["tier"] = cached_tier
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_fam = pool.submit(call_fam, proj_id)
            fut_uqs = pool.submit(call_uqs, proj_id)
            res_fam = fut_fam.result()
            res_uqs = fut_uqs.result()

    now_utc = datetime.now(UTC)

    # Process group limits (e.g. Claude & GPT weekly and 5h limits)
    group_limits: dict[str, float] = {}
    group_weekly: dict[str, dict] = {}

    if res_uqs and isinstance(res_uqs.get("groups"), list):
        for grp in res_uqs["groups"]:
            grp_name = (grp.get("displayName") or "").lower()
            for bkt in grp.get("buckets", []):
                window = (bkt.get("window") or "").lower()
                bucket_id = (bkt.get("bucketId") or "").lower()
                reset_str = bkt.get("resetTime")

                reset_diff = 0.0
                if reset_str:
                    try:
                        dt = datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
                        reset_diff = max(0.0, (dt - now_utc).total_seconds())
                    except Exception:
                        pass

                # Protobuf 0.0 omission: if remainingFraction is absent and reset is future -> 0.0
                if "remainingFraction" in bkt and bkt["remainingFraction"] is not None:
                    rem_frac = float(bkt["remainingFraction"])
                else:
                    rem_frac = 1.0 if reset_diff <= 0 else 0.0

                pct = rem_frac * 100.0
                group_limits[grp_name] = min(group_limits.get(grp_name, 100.0), pct)

                if window == "weekly" or "weekly" in bucket_id:
                    group_weekly[grp_name] = {
                        "group_name": grp.get("displayName", "Group"),
                        "percentage": pct,
                        "reset_diff": reset_diff,
                        "reset_str": format_duration(reset_diff),
                        "description": grp.get("description", ""),
                    }

    result["weekly_limits"] = list(group_weekly.values())

    raw_models = (res_fam.get("models", {}) if res_fam else {})
    items = raw_models.items() if isinstance(raw_models, dict) else [(m.get("model", ""), m) for m in raw_models]

    parsed_models = []
    gemini_percentages = []
    seen_names = set()

    for mid, mdata in items:
        # Ignore internal preview or tab completion endpoints with no display name or quota info
        quota_info = mdata.get("quotaInfo") or {}
        reset_str = quota_info.get("resetTime")
        if not reset_str:
            continue

        name = mdata.get("displayName") or MODEL_DISPLAY_NAMES.get(mid)
        if not name:
            if mid.startswith("gemini-"):
                clean = mid.replace("-tiered", " (High)").replace("-", " ")
                name = " ".join(w.capitalize() if w.lower() not in ("flash", "pro", "lite", "high") else w.title() for w in clean.split())
            else:
                continue

        # Prevent duplicate identical entries
        if name in seen_names:
            continue
        seen_names.add(name)

        reset_diff = 0.0
        try:
            dt = datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
            reset_diff = max(0.0, (dt - now_utc).total_seconds())
        except Exception:
            pass

        # Protobuf 0.0 omission logic
        if "remainingFraction" in quota_info and quota_info["remainingFraction"] is not None:
            rem_frac = float(quota_info["remainingFraction"])
        else:
            rem_frac = 1.0 if reset_diff <= 0 else 0.0

        pct = rem_frac * 100.0

        # Apply group quota limits if applicable
        if any(term in name.lower() for term in ["claude", "gpt", "oss"]):
            for g_name, g_pct in group_limits.items():
                if "claude" in g_name or "gpt" in g_name or "3p" in g_name:
                    pct = min(pct, g_pct)

        dur_str = format_duration(reset_diff)
        is_gemini = "gemini" in mid.lower() or "gemini" in name.lower()
        if is_gemini:
            gemini_percentages.append(pct)

        # Check if model belongs to any group with weekly reset
        model_weekly_str = "-"
        model_weekly_pct = None

        is_3p = any(term in mid.lower() or term in name.lower() for term in ["claude", "gpt", "oss", "3p"])
        for g_name, g_info in group_weekly.items():
            if is_3p and any(term in g_name for term in ["claude", "gpt", "3p"]):
                model_weekly_str = g_info["reset_str"]
                model_weekly_pct = g_info["percentage"]
                break
            elif is_gemini and ("gemini" in g_name or "google" in g_name):
                model_weekly_str = g_info["reset_str"]
                model_weekly_pct = g_info["percentage"]
                break
            elif mid.lower() in g_info.get("description", "").lower() or name.lower() in g_info.get("description", "").lower():
                model_weekly_str = g_info["reset_str"]
                model_weekly_pct = g_info["percentage"]
                break

        # If model has no specific group weekly limit but the profile has an active weekly tier cycle,
        # propagate the subscription tier's weekly reset to Gemini models as well.
        if model_weekly_str == "-" and group_weekly:
            primary_weekly = next(iter(group_weekly.values()))
            model_weekly_str = primary_weekly["reset_str"]
            model_weekly_pct = primary_weekly["percentage"]

        parsed_models.append({
            "id": mid,
            "name": name,
            "percentage": pct,
            "reset_str": dur_str,
            "weekly_reset_str": model_weekly_str,
            "weekly_percentage": model_weekly_pct,
            "is_gemini": is_gemini,
        })

    # Sort models: Gemini first, then Claude, then GPT
    def _sort_key(m: dict) -> tuple[int, str]:
        n = m["name"].lower()
        if "gemini" in n:
            return (0, n)
        if "claude" in n:
            return (1, n)
        return (2, n)

    parsed_models.sort(key=_sort_key)
    result["models"] = parsed_models

    if gemini_percentages:
        result["min_gemini_quota"] = min(gemini_percentages)
        result["avg_gemini_quota"] = sum(gemini_percentages) / len(gemini_percentages)
        result["is_exhausted"] = result["min_gemini_quota"] <= 0.0
    else:
        result["min_gemini_quota"] = 100.0
        result["avg_gemini_quota"] = 100.0

    return result


def fetch_all_quotas(profiles: list[Profile]) -> list[dict]:
    """Fetch quotas across multiple profiles in parallel."""
    if not profiles:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(profiles))) as executor:
        return list(executor.map(fetch_profile_quota, profiles))
