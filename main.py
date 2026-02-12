#!/usr/bin/env python3
"""
Pangolin — macOS Menu Bar Claude Usage Monitor.

A lightweight menu bar widget that shows real-time Claude Pro usage
(claude.ai messages + Claude Code CLI activity) with color-coded
indicators and system notifications at configurable thresholds.

Built with `rumps` for a native macOS experience and minimal resource
footprint (~30-50 MB RSS).

Usage:
    python3 main.py             # Normal launch
    python3 main.py --setup     # Browser-based setup wizard
    python3 main.py --setup --cli  # Terminal-only setup
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

import rumps

import config
import setup_wizard
import usage_tracker

# ─── Logging Setup ───────────────────────────────────────────────────
# Rotating log file: max 5 MB, keeps 1 backup (.log.1).

_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

_file_handler = RotatingFileHandler(
    config.LOG_FILE,
    maxBytes=config.LOG_MAX_BYTES,
    backupCount=config.LOG_BACKUP_COUNT,
)
_file_handler.setFormatter(_log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_file_handler])
logger = logging.getLogger(__name__)


# ─── Credential Setup ────────────────────────────────────────────────


def run_setup(cli_only: bool = False) -> bool:
    """Run the setup wizard (browser-based or CLI fallback).

    Returns True if credentials were saved successfully.
    """
    if cli_only:
        return setup_wizard.run_cli_setup()
    return setup_wizard.run_setup_wizard()


# ─── Menu Bar Application ───────────────────────────────────────────


class PangolinApp(rumps.App):
    """Main menu bar application.

    Lifecycle:
      1. ``__init__`` builds the menu structure.
      2. ``on_tick`` fires every ``REFRESH_INTERVAL`` seconds via a
         ``rumps.Timer`` to poll fresh data on a background thread.
      3. ``_update_ui`` applies the latest snapshot to menu items
         (called on the main thread via ``rumps.Timer``).
    """

    def __init__(self) -> None:
        # Start with a generic title; updated after first data fetch.
        super().__init__(
            name=config.APP_NAME,
            title=f"{config.APP_ICON} ...",
            quit_button=None,  # We add a custom Quit below.
        )

        # ── Build Menu Items ──
        # Use lambda: None to make items non-greyed (clickable but do nothing)
        self.header_item = rumps.MenuItem(f"{config.APP_ICON} Pangolin", callback=lambda _: None)
        self.separator1 = rumps.separator

        self.overall_item = rumps.MenuItem("📊 Overall: —", callback=self.on_show_details)
        self.separator2 = rumps.separator

        self.web_header = rumps.MenuItem("🌐 claude.ai", callback=self.on_open_claude_ai)
        self.web_messages = rumps.MenuItem("  📋 Messages: —", callback=self.on_copy_stats)
        self.web_reset = rumps.MenuItem("  Resets in: —", callback=lambda _: None)
        self.separator3 = rumps.separator

        self.cli_header = rumps.MenuItem("Claude Code CLI", callback=lambda _: None)
        self.cli_status = rumps.MenuItem("  Status: —", callback=lambda _: None)
        self.cli_last = rumps.MenuItem("  Last used: —", callback=lambda _: None)
        self.separator4 = rumps.separator

        self.refresh_item = rumps.MenuItem(
            "🔄 Refresh Now", callback=self.on_refresh_clicked
        )

        # Preferences sub-menu
        self.prefs_menu = rumps.MenuItem("⚙️ Preferences...", callback=lambda _: None)
        self.prefs_interval = rumps.MenuItem(
            f"  Refresh Interval: {config.REFRESH_INTERVAL // 60}m",
            callback=lambda _: None
        )
        self.prefs_alerts = rumps.MenuItem(
            f"  Warning: {int(config.WARNING_THRESHOLD * 100)}% / "
            f"Critical: {int(config.CRITICAL_THRESHOLD * 100)}%",
            callback=lambda _: None
        )
        self.prefs_reauth = rumps.MenuItem(
            "  Re-authenticate", callback=self.on_reauth_clicked
        )
        self.prefs_copy_cookie = rumps.MenuItem(
            "  Copy Cookie (Debug)", callback=self.on_copy_cookie
        )
        self.prefs_menu.update([
            self.prefs_interval,
            self.prefs_alerts,
            self.prefs_reauth,
            self.prefs_copy_cookie,
        ])

        self.quit_item = rumps.MenuItem("❌ Quit", callback=self.on_quit)

        self.menu = [
            self.header_item,
            self.separator1,
            self.overall_item,
            self.separator2,
            self.web_header,
            self.web_messages,
            self.web_reset,
            self.separator3,
            self.cli_header,
            self.cli_status,
            self.cli_last,
            self.separator4,
            self.refresh_item,
            self.prefs_menu,
            self.quit_item,
        ]

        # ── State ──
        self._latest_snapshot: Dict[str, Any] = {}
        self._fetch_in_progress = False
        # Track which alert tiers have already fired so we don't spam.
        self._alerted_warning = False
        self._alerted_critical = False

        # ── Background Timer ──
        # rumps.Timer calls back on the main run-loop, so we launch the
        # actual network call in a daemon thread from the callback.
        self._timer = rumps.Timer(self._on_timer_tick, config.REFRESH_INTERVAL)
        self._timer.start()

        # Kick off an immediate first fetch.
        self._start_background_fetch()

    # ── Timer / Fetch ────────────────────────────────────────────────

    def _on_timer_tick(self, _sender: Any) -> None:
        """Called by rumps.Timer on the main thread every REFRESH_INTERVAL."""
        self._start_background_fetch()

    def _start_background_fetch(self) -> None:
        """Spawn a daemon thread to fetch usage data without blocking UI."""
        if self._fetch_in_progress:
            return
        self._fetch_in_progress = True
        t = threading.Thread(target=self._do_fetch, daemon=True)
        t.start()

    def _do_fetch(self) -> None:
        """Network-bound work that runs on a background thread.

        After fetching, updates the UI directly. rumps handles thread safety
        for simple property updates.
        """
        try:
            snapshot = usage_tracker.refresh_usage_data()
            self._latest_snapshot = snapshot
            logger.info(
                "Usage data refreshed — overall %.1f%%",
                snapshot.get("overall_percentage", 0) * 100,
            )

            # Update UI directly from background thread
            # rumps should handle thread safety for property updates
            self._update_ui_from_snapshot(snapshot)

        except Exception:
            logger.exception("Background fetch failed")
        finally:
            self._fetch_in_progress = False

    def _update_ui_from_snapshot(self, snap: Dict[str, Any]) -> None:
        """Update all menu items from a snapshot.

        Can be called from any thread - rumps handles thread safety.
        """
        if not snap:
            return

        overall_pct = snap.get("overall_percentage", 0)
        web = snap.get("claude_web", {})
        cli = snap.get("claude_code", {})

        # ── Title & Color ──
        pct_display = int(overall_pct * 100)
        icon = self._color_icon(overall_pct)
        self.title = f"{icon} {config.APP_ICON} {pct_display}%"

        # ── Overall ──
        alert_symbol = ""
        if overall_pct >= config.CRITICAL_THRESHOLD:
            alert_symbol = " 🔴"
        elif overall_pct >= config.WARNING_THRESHOLD:
            alert_symbol = " ⚠️"
        self.overall_item.title = f"📊 Overall: {pct_display}%{alert_symbol}"

        # ── Web Messages ──
        used = web.get("messages_used")
        limit = web.get("messages_limit")
        if used is not None and limit is not None:
            web_pct = int((used / limit) * 100) if limit else 0
            self.web_messages.title = (
                f"  Messages: {used}/{limit} ({web_pct}%)"
            )
        else:
            self.web_messages.title = "  Messages: unavailable"

        # ── Reset Time ──
        raw_reset = web.get("reset_time")
        reset_dt = usage_tracker.parse_reset_time(raw_reset)
        self.web_reset.title = (
            f"  Resets in: {usage_tracker.time_until_reset(reset_dt)}"
        )

        # ── CLI ──
        cli_status = cli.get("status", "unknown")
        status_icon = {"active": "✓", "idle": "—", "unknown": "?"}.get(
            cli_status, "?"
        )
        self.cli_status.title = (
            f"  Status: {cli_status.capitalize()} {status_icon}"
        )

        last_activity = cli.get("last_activity")
        if last_activity:
            self.cli_last.title = (
                f"  Last used: {self._relative_time(last_activity)}"
            )
        else:
            self.cli_last.title = "  Last used: —"

        # ── Notifications ──
        self._maybe_notify(overall_pct, used, limit, reset_dt)

    # ── Notifications ────────────────────────────────────────────────

    def _maybe_notify(
        self,
        pct: float,
        used: Optional[int],
        limit: Optional[int],
        reset_dt: Optional[datetime],
    ) -> None:
        """Send macOS notifications when thresholds are crossed.

        Each threshold only fires once per reset cycle. When the
        percentage drops (after a reset), flags are cleared.
        """
        reset_str = usage_tracker.time_until_reset(reset_dt)
        remaining = (limit - used) if (limit and used is not None) else "?"

        if pct >= config.CRITICAL_THRESHOLD and not self._alerted_critical:
            rumps.notification(
                title=f"🔴 {config.APP_ICON} Usage Critical!",
                subtitle=f"Only {remaining} messages left.",
                message=f"Resets in {reset_str}.",
            )
            self._alerted_critical = True
            self._save_alert_timestamp()
            logger.info("Critical notification sent (%.0f%%)", pct * 100)

        elif pct >= config.WARNING_THRESHOLD and not self._alerted_warning:
            rumps.notification(
                title=f"⚠️ {config.APP_ICON} Usage at 80%",
                subtitle=f"You have {remaining} messages remaining.",
                message=f"Resets in {reset_str}.",
            )
            self._alerted_warning = True
            self._save_alert_timestamp()
            logger.info("Warning notification sent (%.0f%%)", pct * 100)

        # Reset flags when usage drops below warning (after quota reset).
        if pct < config.WARNING_THRESHOLD:
            self._alerted_warning = False
            self._alerted_critical = False

    def _save_alert_timestamp(self) -> None:
        """Persist the time of the last alert into the cache file."""
        try:
            cache = usage_tracker.load_cache()
            cache["last_alert_sent"] = datetime.now(timezone.utc).isoformat()
            usage_tracker.save_cache(cache)
        except Exception:
            logger.exception("Failed to save alert timestamp")

    # ── Menu Callbacks ───────────────────────────────────────────────

    def on_open_claude_ai(self, _sender: Any = None) -> None:
        """Open claude.ai in the default browser."""
        import subprocess
        try:
            subprocess.run(["open", config.CLAUDE_WEB_BASE_URL], check=True)
            logger.info("Opened claude.ai in browser")
        except subprocess.CalledProcessError:
            logger.exception("Failed to open browser")

    def on_show_details(self, _sender: Any = None) -> None:
        """Show detailed usage breakdown in a dialog window."""
        snap = self._latest_snapshot
        if not snap:
            rumps.alert(
                title="No Data Available",
                message="Usage data hasn't been fetched yet. Try refreshing."
            )
            return

        overall_pct = snap.get("overall_percentage", 0)
        web = snap.get("claude_web", {})
        cli = snap.get("claude_code", {})

        # Format the detailed message
        used = web.get("messages_used", "?")
        limit = web.get("messages_limit", "?")
        reset_time = web.get("reset_time", "Unknown")
        cli_status = cli.get("status", "unknown")
        cli_last = cli.get("last_activity", "Never")

        reset_dt = usage_tracker.parse_reset_time(reset_time)
        reset_str = usage_tracker.time_until_reset(reset_dt) if reset_dt else "Unknown"

        message = (
            f"Overall Usage: {int(overall_pct * 100)}%\n\n"
            f"Web Messages:\n"
            f"  Used: {used}/{limit}\n"
            f"  Resets: {reset_str}\n\n"
            f"Claude Code CLI:\n"
            f"  Status: {cli_status.capitalize()}\n"
            f"  Last Activity: {self._relative_time(cli_last) if cli_last != 'Never' else 'Never'}\n\n"
            f"Last Updated: {snap.get('timestamp', 'Unknown')[:19]}"
        )

        rumps.alert(
            title=f"{config.APP_ICON} Pangolin Usage Details",
            message=message,
            ok="Close"
        )
        logger.info("Displayed usage details dialog")

    def on_copy_stats(self, _sender: Any = None) -> None:
        """Copy usage statistics to clipboard."""
        import subprocess

        snap = self._latest_snapshot
        if not snap:
            rumps.notification(
                title="No Data",
                message="No usage data available to copy.",
            )
            return

        overall_pct = snap.get("overall_percentage", 0)
        web = snap.get("claude_web", {})
        cli = snap.get("claude_code", {})

        used = web.get("messages_used", "?")
        limit = web.get("messages_limit", "?")
        reset_dt = usage_tracker.parse_reset_time(web.get("reset_time"))
        reset_str = usage_tracker.time_until_reset(reset_dt) if reset_dt else "Unknown"
        cli_status = cli.get("status", "unknown")

        # Format clipboard text
        stats_text = (
            f"Claude Usage Report\n"
            f"{'=' * 40}\n"
            f"Overall: {int(overall_pct * 100)}%\n"
            f"Messages: {used}/{limit}\n"
            f"Resets in: {reset_str}\n"
            f"CLI Status: {cli_status.capitalize()}\n"
            f"Timestamp: {snap.get('timestamp', 'Unknown')[:19]}\n"
        )

        try:
            # Use pbcopy to copy to clipboard
            process = subprocess.Popen(
                ["pbcopy"],
                stdin=subprocess.PIPE,
                text=True
            )
            process.communicate(input=stats_text)

            rumps.notification(
                title=f"{config.APP_ICON} Stats Copied",
                subtitle="",
                message="Usage statistics copied to clipboard.",
            )
            logger.info("Copied usage stats to clipboard")
        except Exception:
            logger.exception("Failed to copy to clipboard")
            rumps.notification(
                title="Copy Failed",
                subtitle="",
                message="Could not copy stats to clipboard.",
            )

    def on_copy_cookie(self, _sender: Any = None) -> None:
        """Copy session cookie to clipboard (for debugging)."""
        import subprocess

        # Confirm action first
        response = rumps.alert(
            title="Copy Cookie?",
            message="This will copy your session cookie to the clipboard. "
                    "Only share this with trusted support channels.",
            ok="Copy",
            cancel="Cancel"
        )

        if response != 1:  # User cancelled
            return

        cookie = usage_tracker.get_session_cookie()
        if not cookie:
            rumps.notification(
                title="No Cookie",
                message="No session cookie found in Keychain.",
            )
            return

        try:
            # Copy full cookie to clipboard
            process = subprocess.Popen(
                ["pbcopy"],
                stdin=subprocess.PIPE,
                text=True
            )
            process.communicate(input=cookie)

            rumps.notification(
                title="Cookie Copied",
                message="Session cookie copied to clipboard.",
            )
            logger.info("Copied session cookie to clipboard")
        except Exception:
            logger.exception("Failed to copy cookie to clipboard")
            rumps.notification(
                title="Copy Failed",
                message="Could not copy cookie to clipboard.",
            )

    @rumps.clicked("🔄 Refresh Now")
    def on_refresh_clicked(self, _sender: Any = None) -> None:
        """User clicked 'Refresh Now'."""
        logger.info("Manual refresh requested")
        self._start_background_fetch()

    def on_reauth_clicked(self, _sender: Any = None) -> None:
        """Launch the browser-based setup wizard in a background thread.

        Opens the user's default browser to a friendly local setup page
        where they can re-authenticate using the bookmarklet or paste flow.
        """
        threading.Thread(
            target=setup_wizard.run_setup_wizard, daemon=True
        ).start()

    def on_quit(self, _sender: Any = None) -> None:
        """Clean up and exit."""
        logger.info("Application quitting")
        self._timer.stop()
        rumps.quit_application()

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _color_icon(pct: float) -> str:
        """Return a colored indicator based on usage percentage."""
        if pct >= config.CRITICAL_THRESHOLD:
            return config.APP_ICON_RED
        if pct >= config.WARNING_THRESHOLD:
            return config.APP_ICON_YELLOW
        if pct >= 0.60:
            return config.APP_ICON_YELLOW
        return config.APP_ICON_GREEN

    @staticmethod
    def _relative_time(iso_str: str) -> str:
        """Convert an ISO timestamp to a human-readable relative string."""
        try:
            from dateutil import parser as dp
            dt = dp.parse(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - dt
            secs = int(delta.total_seconds())
            if secs < 60:
                return "just now"
            if secs < 3600:
                return f"{secs // 60}m ago"
            if secs < 86400:
                return f"{secs // 3600}h ago"
            return f"{secs // 86400}d ago"
        except Exception:
            return iso_str


# ─── Entry Point ─────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=config.APP_NAME)
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the setup wizard (opens browser-based guide)",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Use terminal-only setup instead of browser wizard",
    )
    args = parser.parse_args()

    if args.setup:
        run_setup(cli_only=args.cli)
        return

    # On first run, check whether credentials exist.
    cookie = usage_tracker.get_session_cookie()
    if not cookie:
        print("No credentials found. Starting setup wizard...")
        if not run_setup(cli_only=args.cli):
            print("Setup incomplete — exiting.")
            sys.exit(1)

    logger.info("Starting %s", config.APP_NAME)
    app = PangolinApp()
    app.run()


if __name__ == "__main__":
    main()
