"""
Usage data fetching and aggregation for Pangolin.

This module handles:
  - Scraping claude.ai web dashboard for message counts and reset times
  - Detecting Claude Code CLI activity from local filesystem
  - Computing overall usage percentage
  - Caching results to a local JSON file
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

try:
    from curl_cffi import requests
    USING_CURL_CFFI = True
except ImportError:
    import requests
    USING_CURL_CFFI = False

from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

import config

logger = logging.getLogger(__name__)

# Lock that guards reads/writes to the JSON cache file so background
# thread and UI thread never corrupt it.
_cache_lock = threading.Lock()


# ─── Credential Helpers ──────────────────────────────────────────────


def get_session_cookie() -> Optional[str]:
    """Retrieve the claude.ai session cookie from macOS Keychain.

    Returns the stored cookie string, or None if not found.
    """
    try:
        import keyring
        cookie = keyring.get_password(
            config.KEYCHAIN_SERVICE, config.KEYCHAIN_USERNAME
        )
        return cookie
    except Exception:
        logger.exception("Failed to read session cookie from Keychain")
        return None


def store_session_cookie(cookie: str) -> bool:
    """Persist *cookie* in macOS Keychain.

    Returns True on success, False on failure.
    """
    try:
        import keyring
        keyring.set_password(
            config.KEYCHAIN_SERVICE, config.KEYCHAIN_USERNAME, cookie
        )
        return True
    except Exception:
        logger.exception("Failed to store session cookie in Keychain")
        return False


def delete_session_cookie() -> bool:
    """Remove the stored session cookie from macOS Keychain.

    Returns True on success, False on failure.
    """
    try:
        import keyring
        keyring.delete_password(
            config.KEYCHAIN_SERVICE, config.KEYCHAIN_USERNAME
        )
        return True
    except Exception:
        logger.exception("Failed to delete session cookie from Keychain")
        return False


# ─── Claude Web Usage ────────────────────────────────────────────────


def _build_session(cookie: str) -> requests.Session:
    """Create a requests.Session pre-loaded with authentication headers.

    The session cookie from claude.ai is sent as a standard Cookie header.
    A persistent session object is reused across calls to avoid TCP/TLS
    overhead on every poll.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
        "Accept": "text/html,application/xhtml+xml,application/json",
    })
    return session


def _request_with_retry(
    session: requests.Session, url: str
) -> Optional[requests.Response]:
    """GET *url* with exponential back-off retries.

    Retries up to ``config.MAX_RETRIES`` times. Delay doubles each
    attempt starting from ``config.RETRY_BASE_DELAY`` seconds.

    Uses curl_cffi for browser impersonation to bypass Cloudflare if available.

    Returns the Response on success, or None after all retries fail.
    """
    delay = config.RETRY_BASE_DELAY
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            if USING_CURL_CFFI:
                # Use curl_cffi with browser impersonation to bypass Cloudflare
                resp = requests.get(
                    url,
                    headers=dict(session.headers),
                    impersonate="chrome110",
                    timeout=30
                )
            else:
                resp = session.get(url, timeout=30)

            resp.raise_for_status()
            return resp
        except Exception as exc:
            logger.warning(
                "Request attempt %d/%d failed: %s",
                attempt, config.MAX_RETRIES, exc,
            )
            if attempt < config.MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
    return None


def _parse_usage_from_html(html: str) -> Dict[str, Any]:
    """Extract message count, limit, and reset time from dashboard HTML.

    Strategy:
      1. Try data-testid attributes first (most stable).
      2. Fall back to class-based selectors.
      3. Search raw text for patterns like "45 / 50 messages".

    Returns a dict with keys: messages_used, messages_limit, reset_time,
    percentage.  Missing fields default to None.
    """
    soup = BeautifulSoup(html, "html.parser")
    result: Dict[str, Any] = {
        "messages_used": None,
        "messages_limit": None,
        "reset_time": None,
        "percentage": None,
    }

    # --- Approach 1: CSS selectors from config ---
    for selector in config.CLAUDE_WEB_SELECTORS["usage_section"].split(", "):
        section = soup.select_one(selector)
        if section:
            # Look for message count inside the section
            text = section.get_text(" ", strip=True)
            _extract_from_text(text, result)
            if result["messages_used"] is not None:
                break

    # --- Approach 2: broad text search as fallback ---
    if result["messages_used"] is None:
        body_text = soup.get_text(" ", strip=True)
        _extract_from_text(body_text, result)

    # --- Approach 3: look for JSON data embedded in script tags ---
    if result["messages_used"] is None:
        for script in soup.find_all("script"):
            if script.string and "usage" in script.string.lower():
                _extract_from_json_blob(script.string, result)
                if result["messages_used"] is not None:
                    break

    # Compute percentage if we have both numerator and denominator.
    if result["messages_used"] is not None and result["messages_limit"]:
        result["percentage"] = round(
            result["messages_used"] / result["messages_limit"], 4
        )

    return result


def _extract_from_text(text: str, result: Dict[str, Any]) -> None:
    """Scan *text* for patterns like '45/50' or '45 of 50 messages'.

    Mutates *result* in place when matches are found.
    """
    import re

    # Pattern: "45/50", "45 / 50", "45 of 50"
    match = re.search(
        r"(\d+)\s*(?:/|of)\s*(\d+)\s*(?:messages?)?", text, re.IGNORECASE
    )
    if match:
        result["messages_used"] = int(match.group(1))
        result["messages_limit"] = int(match.group(2))

    # Pattern: "resets in 3h 30m", "resets at 6:00 PM"
    reset_match = re.search(
        r"resets?\s+(?:in|at)\s+(.+?)(?:\.|$)", text, re.IGNORECASE
    )
    if reset_match:
        result["reset_time"] = reset_match.group(1).strip()


def _extract_from_json_blob(script_text: str, result: Dict[str, Any]) -> None:
    """Try to parse embedded JSON from a <script> tag for usage data.

    Some SPAs embed state as ``window.__NEXT_DATA__`` or similar.
    """
    import re

    # Find anything that looks like a JSON object containing "usage"
    for match in re.finditer(r"\{[^{}]{10,}\}", script_text):
        try:
            data = json.loads(match.group())
            for key in ("messageCount", "messagesUsed", "messages_used"):
                if key in data:
                    result["messages_used"] = int(data[key])
            for key in ("messageLimit", "messagesLimit", "messages_limit"):
                if key in data:
                    result["messages_limit"] = int(data[key])
            if result["messages_used"] is not None:
                return
        except (json.JSONDecodeError, ValueError, KeyError):
            continue


def fetch_claude_web_usage(
    session_cookie: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch usage data from the claude.ai API.

    Args:
        session_cookie: Optional override. When None, reads from Keychain.

    Returns:
        Dict with keys: messages_used, messages_limit, reset_time,
        percentage.  All values may be None on failure.
    """
    cookie = session_cookie or get_session_cookie()
    empty: Dict[str, Any] = {
        "messages_used": None,
        "messages_limit": None,
        "reset_time": None,
        "percentage": None,
    }

    if not cookie:
        logger.error("No session cookie available — cannot fetch web usage")
        return empty

    session = _build_session(cookie)

    # Step 1: Get organization UUID from account endpoint
    account_url = f"{config.CLAUDE_WEB_BASE_URL}/api/account"
    account_resp = _request_with_retry(session, account_url)

    if account_resp is None:
        logger.warning("Could not fetch account data")
        return empty

    try:
        account_data = account_resp.json()
        memberships = account_data.get("memberships", [])

        if not memberships:
            logger.warning("No organization memberships found")
            return empty

        org_uuid = memberships[0]["organization"]["uuid"]
        logger.debug(f"Found organization UUID: {org_uuid}")

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error(f"Failed to parse account data: {e}")
        return empty

    # Step 2: Get usage data from organization usage endpoint
    usage_url = f"{config.CLAUDE_WEB_BASE_URL}/api/organizations/{org_uuid}/usage"
    usage_resp = _request_with_retry(session, usage_url)

    if usage_resp is None:
        logger.warning("Could not fetch usage data")
        return empty

    try:
        usage_data = usage_resp.json()

        # Use 5-hour window as primary metric (more granular)
        five_hour = usage_data.get("five_hour", {})
        utilization = five_hour.get("utilization", 0.0)
        resets_at = five_hour.get("resets_at")

        # Convert utilization percentage to used/limit
        # Assuming 100 = limit for calculation purposes
        percentage = utilization / 100.0
        messages_used = int(utilization)
        messages_limit = 100

        result = {
            "messages_used": messages_used,
            "messages_limit": messages_limit,
            "reset_time": resets_at,
            "percentage": percentage,
        }

        logger.info(f"Usage: {utilization}% (resets at {resets_at})")
        return result

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse usage data: {e}")
        return empty


def _parse_api_json(data: Any) -> Dict[str, Any]:
    """Extract usage info from a JSON API response.

    Handles both list-of-orgs and single-object shapes.
    """
    result: Dict[str, Any] = {
        "messages_used": None,
        "messages_limit": None,
        "reset_time": None,
        "percentage": None,
    }

    # Normalize to list
    items = data if isinstance(data, list) else [data]

    for item in items:
        if not isinstance(item, dict):
            continue
        # Walk nested keys looking for usage-related data
        for sub in (item, item.get("usage", {}), item.get("rate_limit", {})):
            if not isinstance(sub, dict):
                continue
            for key in ("messages_used", "messageCount", "messagesUsed"):
                if key in sub:
                    result["messages_used"] = int(sub[key])
            for key in ("messages_limit", "messageLimit", "messagesLimit"):
                if key in sub:
                    result["messages_limit"] = int(sub[key])
            for key in ("reset_time", "resetTime", "resetsAt"):
                if key in sub:
                    result["reset_time"] = str(sub[key])

        if result["messages_used"] is not None:
            if result["messages_limit"]:
                result["percentage"] = round(
                    result["messages_used"] / result["messages_limit"], 4
                )
            return result

    return result


# ─── Claude Code CLI Detection ───────────────────────────────────────


def estimate_code_cli_usage() -> Dict[str, Any]:
    """Best-effort detection of recent Claude Code CLI activity.

    Checks modification times of known CLI paths. If any file under
    ``~/.claude/`` was modified within ``config.CLI_ACTIVE_WINDOW``
    seconds, the status is "active"; otherwise "idle". If the directory
    doesn't exist, returns "unknown".

    Returns:
        Dict with keys: status, last_activity.
    """
    result: Dict[str, Any] = {
        "status": "unknown",
        "last_activity": None,
    }

    latest_mtime: float = 0.0

    for path in config.CLAUDE_CODE_PATHS:
        try:
            if os.path.isdir(path):
                # Walk directory for most recently modified file
                for root, _dirs, files in os.walk(path):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        try:
                            mtime = os.path.getmtime(fpath)
                            if mtime > latest_mtime:
                                latest_mtime = mtime
                        except OSError:
                            continue
            elif os.path.isfile(path):
                mtime = os.path.getmtime(path)
                if mtime > latest_mtime:
                    latest_mtime = mtime
        except OSError:
            continue

    if latest_mtime == 0.0:
        return result

    last_dt = datetime.fromtimestamp(latest_mtime, tz=timezone.utc)
    result["last_activity"] = last_dt.isoformat()

    age_seconds = time.time() - latest_mtime
    result["status"] = "active" if age_seconds < config.CLI_ACTIVE_WINDOW else "idle"

    return result


# ─── Aggregation ─────────────────────────────────────────────────────


def calculate_overall_percentage(
    web_data: Dict[str, Any],
    _cli_data: Dict[str, Any],
) -> float:
    """Compute an overall usage percentage from web and CLI data.

    Currently the percentage is driven entirely by the web message count
    because Claude Code CLI shares the same underlying quota. If the web
    percentage is unavailable, returns 0.0.
    """
    web_pct = web_data.get("percentage")
    if web_pct is not None:
        return float(web_pct)
    return 0.0


# ─── Reset Time Helpers ──────────────────────────────────────────────


def parse_reset_time(raw: Optional[str]) -> Optional[datetime]:
    """Parse a reset-time string into a timezone-aware datetime.

    Handles ISO 8601 strings and relative descriptions like "3h 30m".
    Returns None when parsing fails.
    """
    if not raw:
        return None

    # Try ISO 8601 first
    try:
        return dateutil_parser.parse(raw)
    except (ValueError, OverflowError):
        pass

    # Try relative format: "3h 30m"
    import re
    hours = minutes = 0
    h_match = re.search(r"(\d+)\s*h", raw, re.IGNORECASE)
    m_match = re.search(r"(\d+)\s*m", raw, re.IGNORECASE)
    if h_match:
        hours = int(h_match.group(1))
    if m_match:
        minutes = int(m_match.group(1))
    if hours or minutes:
        from datetime import timedelta
        return datetime.now(timezone.utc) + timedelta(hours=hours, minutes=minutes)

    return None


def time_until_reset(reset_dt: Optional[datetime]) -> str:
    """Return a human-friendly string like '3h 30m' until *reset_dt*.

    Returns 'Unknown' when *reset_dt* is None.
    """
    if reset_dt is None:
        return "Unknown"

    now = datetime.now(timezone.utc)
    # Make reset_dt offset-aware if naive
    if reset_dt.tzinfo is None:
        reset_dt = reset_dt.replace(tzinfo=timezone.utc)

    delta = reset_dt - now
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "Now"

    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ─── Cache I/O ───────────────────────────────────────────────────────


def load_cache() -> Dict[str, Any]:
    """Load the most recent usage snapshot from disk.

    Returns an empty dict if the file doesn't exist or is corrupted.
    Thread-safe via ``_cache_lock``.
    """
    with _cache_lock:
        try:
            with open(config.CACHE_FILE, "r") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


def save_cache(data: Dict[str, Any]) -> None:
    """Atomically write *data* to the JSON cache file.

    Uses write-then-rename to avoid partial writes on crash.
    Thread-safe via ``_cache_lock``.
    """
    with _cache_lock:
        tmp_path = config.CACHE_FILE + ".tmp"
        try:
            with open(tmp_path, "w") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_path, config.CACHE_FILE)
        except OSError:
            logger.exception("Failed to write cache file")


# ─── High-Level Refresh ─────────────────────────────────────────────


def refresh_usage_data(
    session_cookie: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch fresh usage data, update cache, and return the snapshot.

    This is the single entry-point called by the background polling
    thread.  It aggregates web + CLI data, timestamps the result,
    writes it to disk, and returns the complete snapshot dict.
    """
    web_data = fetch_claude_web_usage(session_cookie)
    cli_data = estimate_code_cli_usage()
    overall_pct = calculate_overall_percentage(web_data, cli_data)

    snapshot: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "claude_web": web_data,
        "claude_code": cli_data,
        "overall_percentage": overall_pct,
        "last_alert_sent": None,  # managed by main.py
    }

    # Preserve last_alert_sent from previous cache
    old = load_cache()
    if old.get("last_alert_sent"):
        snapshot["last_alert_sent"] = old["last_alert_sent"]

    save_cache(snapshot)
    return snapshot
