"""Configured Claude model choices exposed by the proxy dashboard.

The base lineup is deterministic: ``ALLOWED_MODELS`` in ``.env`` overrides the
defaults without rebuilding the image. When ``MODEL_SYNC_ENABLED`` is on,
``sync_models`` also merges in the Claude models Anthropic currently lists, so
new releases work without a code change or restart.
"""

import re
from collections.abc import Iterable

from app.config import get_settings
from app.logger import get_logger

logger = get_logger()

MODEL_SYNC_INTERVAL_SECONDS = 3600

DEFAULT_MODEL_IDS: tuple[str, ...] = (
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
    "claude-haiku-5-5",
    "claude-opus-4-6",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "claude-sonnet-5-5",
)

# Backwards-compatible import for integrations that used the old constant.
MODEL_IDS = DEFAULT_MODEL_IDS

# ponytail: per-process cache, refilled at startup and hourly; a DB table if synced models must survive outages.
_synced_model_ids: tuple[str, ...] = ()


def configured_model_ids(raw: str | None = None) -> tuple[str, ...]:
    """Return the normalized allowlist from ``ALLOWED_MODELS`` or defaults, plus synced models."""
    settings = get_settings()
    value = settings.ALLOWED_MODELS if raw is None else raw
    models = tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip())) or DEFAULT_MODEL_IDS
    if raw is None and settings.MODEL_SYNC_ENABLED:
        models = tuple(dict.fromkeys((*models, *_synced_model_ids)))
    return models


def model_catalog(model_ids: Iterable[str] | None = None) -> list[str]:
    """Return a fresh list for callers that expose model selector options."""
    ids = configured_model_ids() if model_ids is None else tuple(model_ids)
    return list(ids)


def normalize_synced_ids(ids: Iterable[str]) -> tuple[str, ...]:
    """Keep Claude ids only, and add the undated alias of dated ids (``claude-x-20251001`` -> ``claude-x``)."""
    found: set[str] = set()
    for raw in ids:
        model = raw.strip().lower()
        if model.startswith("claude-"):
            found |= {model, re.sub(r"-\d{8}$", "", model)}
    return tuple(sorted(found))


def sync_models() -> None:
    """Refresh the synced model list; any failure keeps the last good list."""
    global _synced_model_ids
    # Imported lazily: these modules import the API models, which import this module.
    from app.utils import anthropic_fallbacks, egress, oauth, rotation
    from app.utils.models.api import AccountStatus, ProviderHealth
    from app.utils.postgres import AccountDb, AnthropicFallbackDb, get_db_cm

    found: set[str] = set()
    try:
        with get_db_cm() as db:
            for provider in db.query(AnthropicFallbackDb).all():
                found |= anthropic_fallbacks.model_ids(provider) or set()
            accounts = (
                db.query(AccountDb)
                .filter(AccountDb.status == AccountStatus.ACTIVE, AccountDb.provider_health != ProviderHealth.REAUTH_REQUIRED)
                .all()
            )
            # One account's answer is enough; try the next only when it fails.
            for account in accounts:
                try:
                    target = egress.get_pool().resolve(account.egress_target_id)
                    token = rotation.ensure_fresh_token(db, account, egress_target=target)
                    db.commit()
                    found.update(oauth.fetch_model_ids(token, egress_target=target))
                    break
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    logger.warning("Model sync via account '%s' failed (error_type=%s)", account.label, type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Model sync failed (error_type=%s): %s", type(exc).__name__, exc)
    synced = normalize_synced_ids(found)
    if synced:
        added = sorted(set(synced) - set(_synced_model_ids))
        _synced_model_ids = synced
        if added:
            logger.info("Model sync added: %s", ", ".join(added))
