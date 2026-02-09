"""
Setup Wizard for Claude Usage Monitor.

Provides a user-friendly, browser-based setup experience:

  1. Spins up a tiny local HTTP server on localhost.
  2. Opens the user's browser to a guided setup page.
  3. Offers a **bookmarklet** the user can drag to their bookmark bar —
     when clicked on claude.ai it auto-extracts the session cookie and
     sends it back to the local server.
  4. Also provides a manual paste option for users who prefer it.
  5. Validates the cookie immediately by making a test request.
  6. Stores the validated cookie in macOS Keychain.

The server shuts itself down once setup completes or the user cancels.
"""

import http.server
import json
import logging
import os
import secrets
import socketserver
import threading
import time
import webbrowser
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import requests

import config
import usage_tracker

logger = logging.getLogger(__name__)

# One-time token to prevent other local processes from posting to our server.
_SETUP_TOKEN = secrets.token_urlsafe(16)

# Port for the local setup server.
SETUP_PORT = 17834

# Signals from the handler back to the main thread.
_setup_result: Dict[str, Any] = {"done": False, "success": False, "cookie": ""}


def _validate_cookie(cookie: str) -> Dict[str, Any]:
    """Test a session cookie by making a lightweight request to claude.ai.

    Returns a dict with:
      - valid (bool): whether the cookie authenticated successfully
      - detail (str): human-readable status message
      - account (str|None): account email or name if detectable
    """
    result = {"valid": False, "detail": "", "account": None}

    if not cookie or not cookie.strip():
        result["detail"] = "Cookie is empty."
        return result

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie.strip(),
        "Accept": "application/json, text/html",
    })

    try:
        resp = session.get(
            f"{config.CLAUDE_WEB_BASE_URL}/api/organizations",
            timeout=15,
        )
        if resp.status_code == 200:
            result["valid"] = True
            result["detail"] = "Cookie is valid!"
            # Try to extract account info from the response.
            try:
                data = resp.json()
                if isinstance(data, list) and data:
                    name = data[0].get("name", "")
                    email = data[0].get("email_address", "")
                    result["account"] = email or name or None
            except (ValueError, KeyError, IndexError):
                pass
        elif resp.status_code in (401, 403):
            result["detail"] = (
                "Cookie was rejected (401/403). It may be expired — "
                "please log in to claude.ai again and copy a fresh cookie."
            )
        else:
            result["detail"] = (
                f"Unexpected response (HTTP {resp.status_code}). "
                "The cookie might still work — try saving it."
            )
    except requests.ConnectionError:
        result["detail"] = (
            "Could not connect to claude.ai. "
            "Check your internet connection and try again."
        )
    except requests.Timeout:
        result["detail"] = (
            "Request timed out. claude.ai may be slow — try again."
        )
    except requests.RequestException as exc:
        result["detail"] = f"Network error: {exc}"

    return result


# ─── HTML Template ───────────────────────────────────────────────────

def _build_html() -> str:
    """Return the full HTML page for the setup wizard.

    The page is entirely self-contained (inline CSS/JS, no external
    dependencies) so it works offline and loads instantly.
    """
    # The bookmarklet JS is a self-invoking function that:
    #  1. Reads document.cookie from the current page (must be claude.ai)
    #  2. POSTs it to our local setup server
    #  3. Shows a brief confirmation banner
    bookmarklet_js = (
        "javascript:void((function(){"
        "if(!location.hostname.includes('claude.ai')){"
        "alert('Please navigate to claude.ai first, then click this bookmarklet.');"
        "return;}"
        "var c=document.cookie;"
        "if(!c){alert('No cookies found. Make sure you are logged in.');return;}"
        f"fetch('http://localhost:{SETUP_PORT}/api/submit',"
        "{method:'POST',headers:{'Content-Type':'application/json'},"
        f"body:JSON.stringify({{token:'{_SETUP_TOKEN}',cookie:c}})"
        "}).then(function(r){return r.json()}).then(function(d){"
        "if(d.valid){alert('Cookie saved and validated! You can close this tab.')}"
        "else{alert('Cookie sent but validation failed: '+d.detail)}"
        "}).catch(function(e){alert('Could not reach the setup server. "
        "Is the monitor still running?')});"
        "})())"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Monitor Setup</title>
<style>
  :root {{
    --bg: #faf9f7;
    --card: #ffffff;
    --border: #e5e2dc;
    --text: #1a1a1a;
    --muted: #6b6560;
    --accent: #d97706;
    --accent-bg: #fef3c7;
    --success: #16a34a;
    --success-bg: #dcfce7;
    --error: #dc2626;
    --error-bg: #fee2e2;
    --blue: #2563eb;
    --blue-bg: #dbeafe;
    --radius: 12px;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 40px 20px;
  }}
  .container {{
    max-width: 680px;
    margin: 0 auto;
  }}
  .logo {{
    text-align: center;
    font-size: 48px;
    margin-bottom: 8px;
  }}
  h1 {{
    text-align: center;
    font-size: 24px;
    font-weight: 600;
    margin-bottom: 4px;
  }}
  .subtitle {{
    text-align: center;
    color: var(--muted);
    margin-bottom: 32px;
    font-size: 15px;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 24px;
    margin-bottom: 20px;
  }}
  .card h2 {{
    font-size: 17px;
    font-weight: 600;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .card h2 .badge {{
    background: var(--accent-bg);
    color: var(--accent);
    font-size: 12px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 999px;
  }}
  .steps {{
    counter-reset: step;
    list-style: none;
    padding: 0;
  }}
  .steps li {{
    counter-increment: step;
    padding: 10px 0 10px 44px;
    position: relative;
    border-bottom: 1px solid var(--border);
  }}
  .steps li:last-child {{ border-bottom: none; }}
  .steps li::before {{
    content: counter(step);
    position: absolute;
    left: 0;
    top: 10px;
    width: 28px;
    height: 28px;
    background: var(--blue-bg);
    color: var(--blue);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 700;
  }}
  .bookmarklet-zone {{
    background: var(--blue-bg);
    border: 2px dashed var(--blue);
    border-radius: var(--radius);
    padding: 20px;
    text-align: center;
    margin: 16px 0 8px;
  }}
  .bookmarklet-link {{
    display: inline-block;
    background: var(--blue);
    color: white !important;
    text-decoration: none;
    padding: 10px 24px;
    border-radius: 8px;
    font-weight: 600;
    font-size: 15px;
    cursor: grab;
    box-shadow: 0 2px 8px rgba(37, 99, 235, 0.3);
    transition: transform 0.15s;
  }}
  .bookmarklet-link:hover {{ transform: scale(1.05); }}
  .bookmarklet-hint {{
    font-size: 13px;
    color: var(--muted);
    margin-top: 10px;
  }}
  .divider {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 12px 0;
    color: var(--muted);
    font-size: 13px;
  }}
  .divider::before, .divider::after {{
    content: "";
    flex: 1;
    height: 1px;
    background: var(--border);
  }}
  textarea {{
    width: 100%;
    height: 90px;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    font-family: "SF Mono", Menlo, monospace;
    font-size: 13px;
    resize: vertical;
    outline: none;
    transition: border-color 0.2s;
  }}
  textarea:focus {{ border-color: var(--blue); }}
  .btn {{
    display: inline-block;
    background: var(--text);
    color: white;
    border: none;
    padding: 10px 24px;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    margin-top: 12px;
    transition: opacity 0.2s;
  }}
  .btn:hover {{ opacity: 0.85; }}
  .btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  #status {{
    margin-top: 16px;
    padding: 12px 16px;
    border-radius: 8px;
    font-size: 14px;
    display: none;
  }}
  #status.success {{
    display: block;
    background: var(--success-bg);
    color: var(--success);
  }}
  #status.error {{
    display: block;
    background: var(--error-bg);
    color: var(--error);
  }}
  #status.info {{
    display: block;
    background: var(--blue-bg);
    color: var(--blue);
  }}
  .help-text {{
    font-size: 13px;
    color: var(--muted);
    margin-top: 8px;
  }}
  .done-card {{
    display: none;
    text-align: center;
    padding: 40px 24px;
  }}
  .done-card .checkmark {{
    font-size: 64px;
    margin-bottom: 16px;
  }}
  .done-card h2 {{
    justify-content: center;
    font-size: 22px;
    margin-bottom: 8px;
  }}
  .done-card p {{
    color: var(--muted);
  }}
  kbd {{
    background: #f3f4f6;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 6px;
    font-family: "SF Mono", Menlo, monospace;
    font-size: 12px;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="logo">&#x1F4CA;</div>
  <h1>Claude Usage Monitor</h1>
  <p class="subtitle">Let's connect to your Claude account. This takes about 30 seconds.</p>

  <!-- ── Setup Cards ── -->
  <div id="setup-flow">

    <!-- Option A: Bookmarklet (recommended) -->
    <div class="card">
      <h2>
        Option A: One-Click Bookmarklet
        <span class="badge">EASIEST</span>
      </h2>
      <ol class="steps">
        <li>Drag the button below to your <strong>Bookmarks Bar</strong>.</li>
        <li>Go to <a href="https://claude.ai" target="_blank" rel="noopener">claude.ai</a> and make sure you're <strong>logged in</strong>.</li>
        <li>Click the <strong>"Get Claude Cookie"</strong> bookmark. Done!</li>
      </ol>

      <div class="bookmarklet-zone">
        <a class="bookmarklet-link"
           href="{bookmarklet_js}"
           title="Drag this to your Bookmarks Bar"
           onclick="event.preventDefault(); alert('Don\\'t click here — drag this button to your Bookmarks Bar, then click it while on claude.ai.');">
          &#x1F36A; Get Claude Cookie
        </a>
        <p class="bookmarklet-hint">
          Drag the button above to your Bookmarks Bar.<br>
          Don't see the bar? Press <kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>B</kbd> in Chrome or <kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>B</kbd> in Firefox.
        </p>
      </div>
    </div>

    <div class="divider">OR</div>

    <!-- Option B: Manual paste -->
    <div class="card">
      <h2>Option B: Paste Cookie Manually</h2>
      <ol class="steps">
        <li>Open <a href="https://claude.ai" target="_blank" rel="noopener">claude.ai</a> and log in.</li>
        <li>
          Open DevTools: press <kbd>Cmd</kbd>+<kbd>Option</kbd>+<kbd>I</kbd>
        </li>
        <li>
          Click the <strong>Console</strong> tab, then paste this and press Enter:<br>
          <code style="display:block; background:#f3f4f6; padding:8px 12px; margin-top:6px; border-radius:6px; font-size:13px; word-break:break-all; user-select:all;">copy(document.cookie)</code>
          <span class="help-text">This copies all cookies to your clipboard.</span>
        </li>
        <li>Paste the result into the box below.</li>
      </ol>

      <textarea id="cookie-input"
                placeholder="Paste your cookie string here..."></textarea>
      <br>
      <button class="btn" id="submit-btn" onclick="submitManual()">
        Validate &amp; Save
      </button>

      <div id="status"></div>
    </div>
  </div>

  <!-- ── Success Card (shown after validation) ── -->
  <div class="card done-card" id="done-card">
    <div class="checkmark">&#x2705;</div>
    <h2>You're all set!</h2>
    <p id="done-account"></p>
    <p style="margin-top:16px;">
      The Claude Monitor is now running in your menu bar.<br>
      You can close this page.
    </p>
  </div>
</div>

<script>
const API = "http://localhost:{SETUP_PORT}";
const TOKEN = "{_SETUP_TOKEN}";

function setStatus(msg, type) {{
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = type;  // "success", "error", or "info"
}}

async function submitManual() {{
  const cookie = document.getElementById("cookie-input").value.trim();
  if (!cookie) {{
    setStatus("Please paste a cookie first.", "error");
    return;
  }}
  const btn = document.getElementById("submit-btn");
  btn.disabled = true;
  btn.textContent = "Validating...";
  setStatus("Testing your cookie against claude.ai...", "info");

  try {{
    const resp = await fetch(API + "/api/submit", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ token: TOKEN, cookie: cookie }})
    }});
    const data = await resp.json();
    if (data.valid) {{
      showSuccess(data.account);
    }} else {{
      setStatus(data.detail || "Validation failed. Please check your cookie.", "error");
      btn.disabled = false;
      btn.textContent = "Validate & Save";
    }}
  }} catch (err) {{
    setStatus("Could not reach the setup server. Is it still running?", "error");
    btn.disabled = false;
    btn.textContent = "Validate & Save";
  }}
}}

function showSuccess(account) {{
  document.getElementById("setup-flow").style.display = "none";
  const doneCard = document.getElementById("done-card");
  doneCard.style.display = "block";
  if (account) {{
    document.getElementById("done-account").textContent =
      "Connected as " + account;
  }}
}}

// Poll for bookmarklet-based submissions so the page updates
// automatically after the user clicks the bookmarklet on claude.ai.
let pollHandle = setInterval(async () => {{
  try {{
    const resp = await fetch(API + "/api/status");
    const data = await resp.json();
    if (data.done && data.success) {{
      clearInterval(pollHandle);
      showSuccess(data.account);
    }}
  }} catch (_) {{}}
}}, 2000);
</script>
</body>
</html>"""


# ─── HTTP Server ─────────────────────────────────────────────────────


class _SetupHandler(http.server.BaseHTTPRequestHandler):
    """Handles requests for the local setup wizard."""

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging from BaseHTTPRequestHandler."""
        logger.debug(format, *args)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight for bookmarklet cross-origin POST."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            html = _build_html().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        elif path == "/api/status":
            # Polling endpoint so the page knows when the bookmarklet succeeded.
            self._send_json({
                "done": _setup_result["done"],
                "success": _setup_result["success"],
                "account": _setup_result.get("account"),
            })

        else:
            self.send_error(404)

    def do_POST(self) -> None:
        global _setup_result
        path = urlparse(self.path).path

        if path != "/api/submit":
            self.send_error(404)
            return

        # Read and parse JSON body.
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0 or content_length > 100_000:
            self._send_json({"valid": False, "detail": "Invalid request."}, 400)
            return

        try:
            body = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"valid": False, "detail": "Invalid JSON."}, 400)
            return

        # Verify the token to prevent abuse from other local processes.
        if body.get("token") != _SETUP_TOKEN:
            self._send_json({"valid": False, "detail": "Invalid token."}, 403)
            return

        cookie = body.get("cookie", "").strip()
        if not cookie:
            self._send_json({"valid": False, "detail": "No cookie provided."}, 400)
            return

        # Validate the cookie against claude.ai.
        validation = _validate_cookie(cookie)

        if validation["valid"]:
            # Store in Keychain.
            stored = usage_tracker.store_session_cookie(cookie)
            if stored:
                _setup_result = {
                    "done": True,
                    "success": True,
                    "cookie": cookie,
                    "account": validation.get("account"),
                }
                self._send_json({
                    "valid": True,
                    "detail": "Cookie saved!",
                    "account": validation.get("account"),
                })
            else:
                self._send_json({
                    "valid": False,
                    "detail": (
                        "Cookie is valid, but could not save to Keychain. "
                        "Check macOS Keychain permissions."
                    ),
                })
        else:
            # Even if validation failed, let the user force-save.
            # Include a "save_anyway" flag the frontend can use.
            self._send_json({
                "valid": False,
                "detail": validation["detail"],
                "allow_force": True,
            })


class _QuietTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


# ─── Public API ──────────────────────────────────────────────────────


def run_setup_wizard() -> bool:
    """Launch the browser-based setup wizard.

    Blocks until the user completes setup or closes the terminal
    (Ctrl+C). Returns True if a valid cookie was saved, False otherwise.
    """
    global _setup_result
    _setup_result = {"done": False, "success": False, "cookie": ""}

    server = _QuietTCPServer(("127.0.0.1", SETUP_PORT), _SetupHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://localhost:{SETUP_PORT}"

    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║         Claude Monitor — Setup Wizard                   ║")
    print("  ╠══════════════════════════════════════════════════════════╣")
    print(f"  ║  Opening your browser to: {url:<28s} ║")
    print("  ║                                                          ║")
    print("  ║  If it doesn't open, copy the URL above into            ║")
    print("  ║  your browser manually.                                  ║")
    print("  ║                                                          ║")
    print("  ║  Press Ctrl+C here when you're done.                    ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print()

    webbrowser.open(url)

    try:
        while not _setup_result["done"]:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()

    if _setup_result["success"]:
        account = _setup_result.get("account", "")
        msg = "  Setup complete!"
        if account:
            msg += f" Connected as {account}."
        print(f"\n  {msg}\n")
        return True
    else:
        print("\n  Setup was not completed.\n")
        return False


def run_cli_setup() -> bool:
    """Fallback CLI-only setup for environments without a browser.

    Provides a streamlined terminal experience with numbered steps,
    immediate validation, and clear feedback.
    """
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║         Claude Monitor — Setup (Terminal Mode)          ║")
    print("  ╠══════════════════════════════════════════════════════════╣")
    print("  ║                                                          ║")
    print("  ║  Step 1: Open https://claude.ai and log in              ║")
    print("  ║                                                          ║")
    print("  ║  Step 2: Open DevTools (Cmd+Option+I)                   ║")
    print("  ║          Click the Console tab                          ║")
    print("  ║                                                          ║")
    print("  ║  Step 3: Paste this command and press Enter:            ║")
    print("  ║          copy(document.cookie)                          ║")
    print("  ║                                                          ║")
    print("  ║  Step 4: Come back here and paste (Cmd+V)               ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print()

    try:
        cookie = input("  Paste cookie here: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Setup cancelled.\n")
        return False

    if not cookie:
        print("  No cookie provided. Setup cancelled.\n")
        return False

    print("  Validating cookie...", end=" ", flush=True)
    validation = _validate_cookie(cookie)

    if validation["valid"]:
        print("Valid!")
        stored = usage_tracker.store_session_cookie(cookie)
        if stored:
            account = validation.get("account", "")
            msg = "  Cookie saved to Keychain."
            if account:
                msg += f" Account: {account}"
            print(msg)
            return True
        else:
            print("  ERROR: Could not save to Keychain.")
            return False
    else:
        print("Failed.")
        print(f"  {validation['detail']}")
        try:
            answer = input("  Save anyway? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer == "y":
            stored = usage_tracker.store_session_cookie(cookie)
            if stored:
                print("  Cookie saved (unvalidated).")
                return True
            print("  ERROR: Could not save to Keychain.")
        return False
