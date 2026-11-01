# Path: app/utils/oauth.py
# Description: Claude Code subscription OAuth (PKCE) -- mint and refresh pooled-account tokens, probe quota/profile.

import base64
import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from app import config
from app.utils import egress

# The upstream usage endpoint rate-limits aggressively and its 429s are transient -- they do NOT mean the account is
# out of quota (inference keeps working). Retry a few times with short backoff so a single stale 429 doesn't surface
# as a hard failure on the dashboard's "Refresh quota" button. Backoff is capped so a foreground click never hangs.
USAGE_PROBE_ATTEMPTS = 4
_USAGE_PROBE_BACKOFF = (0.5, 1.0, 2.0)  # seconds before the next attempt, indexed by prior-attempt count
_USAGE_PROBE_MAX_SLEEP = 3.0
_RETRYABLE_PROBE_STATUSES = frozenset({429, 500, 502, 503, 504})
FIVE_HOUR_WINDOW_SECONDS = 5 * 60 * 60
_COLD_WINDOW_TOLERANCE_SECONDS = 5.0


def _b64url(raw: bytes) -> str:
    """Base64url-encode without padding (per the reference clients)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_pkce() -> Tuple[str, str]:
    """Return (verifier, challenge) for a fresh PKCE exchange (S256)."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def build_authorize_url(verifier: str, challenge: str) -> str:
    """Build the authorization URL the admin visits to approve a new account.

    Two quirks are confirmed across the reference clients: the non-standard `code=true` param makes the console
    display a copy-pasteable code, and `state` is set equal to the PKCE verifier.
    """
    params = {
        "code": "true",
        "client_id": config.OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "scope": config.OAUTH_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    return f"{config.OAUTH_AUTHORIZE_URL}?{httpx.QueryParams(params)}"


def _expiry_from(expires_in: Optional[int]) -> datetime:
    seconds = expires_in if isinstance(expires_in, int) and expires_in > 0 else 3600
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _outbound_headers() -> Dict[str, str]:
    return {"Content-Type": "application/json", "User-Agent": config.CLAUDE_CODE_USER_AGENT}


def exchange_code(
    pasted_code: str,
    verifier: str,
    *,
    egress_target: egress.EgressTarget | None = None,
) -> Dict:
    """Exchange the pasted `code#state` for tokens. Returns {"access_token", "refresh_token", "expires_at"}."""
    splits = pasted_code.strip().split("#")
    code = splits[0]
    state = splits[1] if len(splits) > 1 else verifier

    body = {
        "code": code,
        "state": state,
        "grant_type": "authorization_code",
        "client_id": config.OAUTH_CLIENT_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "code_verifier": verifier,
    }

    with egress.sync_client(egress_target, timeout=30.0) if egress_target else httpx.Client(timeout=30.0) as client:
        resp = client.post(config.OAUTH_TOKEN_URL, json=body, headers=_outbound_headers())
        resp.raise_for_status()
        data = resp.json()

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": _expiry_from(data.get("expires_in")),
    }


def refresh_access_token(
    refresh_token: str,
    *,
    egress_target: egress.EgressTarget | None = None,
) -> Dict:
    """Refresh an access token. Keeps the old refresh token if the response omits a new one."""
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.OAUTH_CLIENT_ID,
    }

    with egress.sync_client(egress_target, timeout=30.0) if egress_target else httpx.Client(timeout=30.0) as client:
        resp = client.post(config.OAUTH_TOKEN_URL, json=body, headers=_outbound_headers())
        resp.raise_for_status()
        data = resp.json()

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or refresh_token,
        "expires_at": _expiry_from(data.get("expires_in")),
    }


def _bearer_headers(access_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "anthropic-beta": config.ANTHROPIC_BETA,
        "anthropic-version": config.ANTHROPIC_VERSION,
        "User-Agent": config.CLAUDE_CODE_USER_AGENT,
    }


def fetch_profile(access_token: str, *, egress_target: egress.EgressTarget | None = None) -> Dict:
    """Fetch the account profile (email / org / subscription tier) -- best effort, display only."""
    with egress.sync_client(egress_target, timeout=15.0) if egress_target else httpx.Client(timeout=15.0) as client:
        resp = client.get(config.OAUTH_PROFILE_URL, headers=_bearer_headers(access_token))
        resp.raise_for_status()
        return resp.json()


def extract_email(profile: Dict) -> Optional[str]:
    """Pull the account email out of a profile blob, tolerating a couple of likely shapes."""
    if not isinstance(profile, dict):
        return None
    account = profile.get("account")
    if isinstance(account, dict):
        email = account.get("email") or account.get("email_address")
        if email:
            return email
    return profile.get("email") or profile.get("email_address")


def extract_tier(profile: Dict) -> Optional[str]:
    """Pull the raw subscription tier (e.g. "default_claude_max_5x") out of a profile blob.

    Renamed plans keep their old ``rate_limit_tier`` (Ultra still reports
    ``default_claude_max_20x``), so a ``plan_display_name`` wins when present:
    "Ultra" -> "default_claude_ultra".
    """
    if not isinstance(profile, dict):
        return None
    org = profile.get("organization")
    if isinstance(org, dict):
        plan = org.get("plan_display_name")
        if isinstance(plan, str) and plan.strip():
            return "default_claude_" + re.sub(r"[^a-z0-9]+", "_", plan.strip().lower()).strip("_")
        return org.get("rate_limit_tier") or org.get("organization_type")
    return None


def _normalize_bucket(
    bucket: Optional[Dict],
    *,
    window_seconds: Optional[float] = None,
    observed_at: Optional[datetime] = None,
) -> Optional[Dict]:
    """Normalize a usage bucket into {"utilization": 0..1, "reset_at": datetime|None}, tolerating upstream variants."""
    if not isinstance(bucket, dict):
        return None

    raw_util = bucket.get("utilization")
    if raw_util is None:
        raw_util = bucket.get("used_percentage")

    # The upstream reports utilization on a 0..100 percent scale (e.g. 24.0 == 24%, 1.0 == 1%); normalize to a 0..1
    # fraction and clamp. (A `> 1.0` heuristic used to live here and wrongly read a true 1% as 100%.)
    util: Optional[float] = None
    if raw_util is not None:
        util = max(0.0, min(1.0, float(raw_util) / 100.0))

    reset_at: Optional[datetime] = None
    raw_reset = bucket.get("resets_at") or bucket.get("reset_at") or bucket.get("reset")
    if isinstance(raw_reset, (int, float)):
        ts = float(raw_reset)
        if ts > 1e12:  # milliseconds -> seconds
            ts = ts / 1000.0
        reset_at = datetime.fromtimestamp(ts, tz=timezone.utc)
    elif isinstance(raw_reset, str):
        try:
            parsed = datetime.fromisoformat(raw_reset.replace("Z", "+00:00"))
            reset_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            reset_at = None

    raw_reset_after = bucket.get("reset_after_seconds")
    reset_after_seconds = float(raw_reset_after) if isinstance(raw_reset_after, (int, float)) else None
    if reset_after_seconds is None and reset_at is not None and window_seconds is not None:
        observed_at = observed_at or datetime.now(timezone.utc)
        reset_after_seconds = (reset_at - observed_at).total_seconds()

    return {
        "utilization": util,
        "reset_at": reset_at,
        "window_seconds": window_seconds,
        "reset_after_seconds": reset_after_seconds,
        # An unused provider window can be represented as a rolling reset one
        # complete window from every probe. It has not started yet.
        "is_cold": (
            util == 0.0
            and window_seconds is not None
            and reset_after_seconds is not None
            and reset_after_seconds >= window_seconds - _COLD_WINDOW_TOLERANCE_SECONDS
        ),
    }


def _probe_retry_delay(resp: httpx.Response, prior_attempts: int) -> float:
    """Backoff before the next probe attempt: honor a short `Retry-After`, else a fixed schedule, capped."""
    backoff = _USAGE_PROBE_BACKOFF[min(prior_attempts, len(_USAGE_PROBE_BACKOFF) - 1)]
    raw_retry_after = resp.headers.get("retry-after")
    if raw_retry_after:
        try:
            backoff = max(backoff, float(raw_retry_after))
        except ValueError:
            pass
    return min(backoff, _USAGE_PROBE_MAX_SLEEP)


def fetch_usage(access_token: str, *, egress_target: egress.EgressTarget | None = None) -> Dict:
    """Zero-spend quota probe. Returns normalized {"five_hour", "seven_day"} utilization buckets.

    The upstream usage endpoint rate-limits aggressively and its 429s are transient (they do not reflect real quota
    exhaustion), so transient 429/5xx responses are retried with short backoff before giving up.
    """
    with egress.sync_client(egress_target, timeout=15.0) if egress_target else httpx.Client(timeout=15.0) as client:
        for attempt in range(USAGE_PROBE_ATTEMPTS):
            resp = client.get(config.OAUTH_USAGE_URL, headers=_bearer_headers(access_token))
            if resp.status_code in _RETRYABLE_PROBE_STATUSES and attempt + 1 < USAGE_PROBE_ATTEMPTS:
                time.sleep(_probe_retry_delay(resp, attempt))
                continue
            resp.raise_for_status()
            data = resp.json()
            observed_at = datetime.now(timezone.utc)
            return {
                "five_hour": _normalize_bucket(
                    data.get("five_hour"),
                    window_seconds=FIVE_HOUR_WINDOW_SECONDS,
                    observed_at=observed_at,
                ),
                "seven_day": _normalize_bucket(data.get("seven_day")),
            }
    raise RuntimeError("unreachable")  # pragma: no cover -- the loop always returns or raises


def fetch_model_ids(access_token: str, *, egress_target: egress.EgressTarget | None = None) -> List[str]:
    """List the model ids Anthropic currently serves to this account."""
    with egress.sync_client(egress_target, timeout=15.0) if egress_target else httpx.Client(timeout=15.0) as client:
        resp = client.get(
            f"{config.UPSTREAM_BASE_URL}/v1/models",
            params={"limit": 1000},
            headers=_bearer_headers(access_token),
        )
        resp.raise_for_status()
        data = resp.json().get("data")
        return [str(item["id"]) for item in data or [] if isinstance(item, dict) and item.get("id")]
