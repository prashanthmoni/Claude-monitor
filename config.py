"""
Configuration for Pangolin — Claude Usage Monitor.

All user-editable settings are defined here. Modify values below
to customize refresh intervals, alert thresholds, and file paths.
"""

import os

# ─── Refresh Interval ────────────────────────────────────────────────
# How often (in seconds) to poll for new usage data.
# Default: 900 seconds (15 minutes). Increase to reduce resource usage.
REFRESH_INTERVAL: int = 15 * 60  # seconds

# ─── Alert Thresholds ────────────────────────────────────────────────
# Fraction of usage that triggers a warning notification.
WARNING_THRESHOLD: float = 0.80   # 80% — first alert
# Fraction of usage that triggers a critical notification.
CRITICAL_THRESHOLD: float = 0.95  # 95% — critical alert

# ─── File Paths ──────────────────────────────────────────────────────
# Local JSON cache storing the most recent usage snapshot.
CACHE_FILE: str = os.path.expanduser("~/.claude_usage_cache.json")
# Rotating log file for errors and diagnostics (max 5 MB, 1 backup).
LOG_FILE: str = os.path.expanduser("~/.claude_usage_monitor.log")
LOG_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT: int = 1

# ─── Claude.ai Web Scraping ─────────────────────────────────────────
# Base URL for the Claude web dashboard.
CLAUDE_WEB_BASE_URL: str = "https://claude.ai"
# CSS selectors used to extract usage info from the dashboard.
# Update these if Anthropic changes their HTML structure.
CLAUDE_WEB_SELECTORS: dict = {
    "usage_section": "[data-testid='usage'], .usage-section, .plan-usage",
    "message_count": ".message-count, [data-testid='message-count']",
    "reset_time": ".reset-time, [data-testid='reset-time']",
}

# ─── Claude Code CLI Detection ───────────────────────────────────────
# Directories / files checked to determine recent CLI activity.
CLAUDE_CODE_PATHS: list = [
    os.path.expanduser("~/.claude"),
    os.path.expanduser("~/.claude/logs"),
    os.path.expanduser("~/.claude.json"),
]
# Activity within this window (seconds) counts as "active".
CLI_ACTIVE_WINDOW: int = 30 * 60  # 30 minutes

# ─── Keychain / Credentials ─────────────────────────────────────────
# Service name stored in macOS Keychain via the `keyring` library.
KEYCHAIN_SERVICE: str = "pangolin-claude-monitor"
KEYCHAIN_USERNAME: str = "session_cookie"

# ─── Network / Retry ────────────────────────────────────────────────
# Maximum number of retries for failed HTTP requests.
MAX_RETRIES: int = 3
# Base delay (seconds) for exponential back-off between retries.
RETRY_BASE_DELAY: float = 2.0

# ─── UI Strings ──────────────────────────────────────────────────────
APP_NAME: str = "Pangolin"
APP_ICON: str = "\U0001f98e"  # 🦎 — closest scaly-animal emoji (no pangolin in Unicode yet)
APP_ICON_GREEN: str = "🟢"
APP_ICON_YELLOW: str = "🟡"
APP_ICON_RED: str = "🔴"
