from datetime import datetime, timezone

from app.utils import oauth, rotation
from app.utils.postgres import AccountDb


def test_plan_display_name_overrides_legacy_rate_limit_tier():
    org = {"rate_limit_tier": "default_claude_max_20x", "plan_display_name": "Ultra"}
    assert oauth.extract_tier({"organization": org}) == "default_claude_ultra"
    assert oauth.extract_tier({"organization": {"rate_limit_tier": "default_claude_max_5x"}}) == "default_claude_max_5x"
    assert oauth.extract_tier({"organization": {"rate_limit_tier": "x", "plan_display_name": "Max 5x"}}) == "default_claude_max_5x"


def test_null_five_hour_window_clears_stale_session_block():
    reset = datetime(2026, 11, 3, 15, tzinfo=timezone.utc)
    account = AccountDb(
        session_used_pct=1.0,
        session_reset_at=reset,
        weekly_used_pct=0.2,
        five_hour_rotation_threshold=1.0,
        weekly_rotation_threshold=1.0,
    )
    rotation.apply_usage_probe(account, {"five_hour": None, "seven_day": {"utilization": 0.3, "reset_at": reset}})
    assert account.session_used_pct is None
    assert account.session_reset_at is None
    assert account.weekly_used_pct == 0.3
    assert rotation.blocking_quota_window(account) is None
