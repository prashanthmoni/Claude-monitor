# Pangolin

A lightweight macOS menu bar application that monitors your Claude Pro usage across **claude.ai** and **Claude Code CLI**. Displays real-time usage percentages with color-coded indicators and sends system notifications when you approach your limits.

Named after the pangolin — small, armored, and always keeping watch. Designed for minimal resource usage (~30-50 MB RAM) — works well on older Intel MacBooks.

## Features

- **Menu bar icon** with color-coded usage percentage
  - 🟢 Green: < 60% usage
  - 🟡 Yellow: 60–80% usage
  - 🔴 Red: > 80% usage
- **Dropdown menu** with detailed breakdown (messages used/remaining, reset time, CLI status)
- **Background polling** every 15 minutes (configurable)
- **System notifications** at 80% and 95% usage
- **Local caching** to minimize network and CPU usage
- **macOS Keychain** for secure credential storage
- **Auto-launch on startup** (optional)

## Requirements

- macOS 10.14+
- Python 3.8+
- A Claude Pro subscription on [claude.ai](https://claude.ai)

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/your-user/claude-usage-monitor.git
cd claude-usage-monitor

# 2. Run the setup script
chmod +x setup.sh
./setup.sh

# 3. Or install manually and run
pip3 install -r requirements.txt
python3 main.py
```

## Authentication Methods

Pangolin needs your Claude session cookie to fetch usage data. Choose the method that works best for you:

### Method 1: Browser Setup Wizard (Recommended for Safari/Chrome)

On first launch, a **setup wizard** opens in your browser:

**Option A: One-Click Bookmarklet**
1. Drag the **"Get Claude Cookie"** button to your Bookmarks Bar
2. Go to [claude.ai](https://claude.ai) and log in
3. Click the bookmarklet → Done! Cookie is automatically extracted and validated

**Option B: Manual Copy-Paste**
1. Open [claude.ai](https://claude.ai) and log in
2. Press `Cmd + Option + I` → **Console** tab
3. Type: `copy(document.cookie)` and press Enter
4. Paste into the setup page → **Validate & Save**

**To launch the wizard:**
- First run: Automatic
- Re-authenticate: Menu bar → **Preferences → Re-authenticate**
- Terminal: `python3 main.py --setup`

### Method 2: Safari Cookie Bridge (For Arc Users)

Arc browser's encryption prevents automatic cookie extraction. Use this workaround:

1. **Open Safari** and log into [claude.ai](https://claude.ai)
2. Run: `python3 main.py --setup`
3. The app will extract cookies from Safari automatically
4. **Close Safari** and return to using Arc
5. Cookies are saved to Keychain and work regardless of which browser you use

**Why this works:** Cookies are stored in Keychain, not tied to a specific browser. Once extracted from Safari, they work even if you only use Arc.

### Method 3: Terminal-Only Setup (No Browser)

For headless systems or automation:

```bash
python3 main.py --setup --cli
```

Follow the prompts to paste your cookie directly in the terminal.

### Method 4: Direct Keychain Entry (Advanced)

If you already have your cookie string:

```bash
python3 -c "import keyring; keyring.set_password('pangolin-claude-monitor', 'session_cookie', 'YOUR_COOKIE_STRING')"
```

---

**Security Notes:**
- Cookies are validated against claude.ai before being saved
- Stored securely in macOS Keychain (never written to disk)
- Cookies typically remain valid for weeks/months
- Re-authenticate when you see "Authentication failed" errors

### Re-authentication

**When to re-authenticate:**
- "Authentication failed" or "No data available" errors
- After changing your Claude password
- If you log out of claude.ai

**How to re-authenticate:**
- **From menu bar:** Preferences → Re-authenticate
- **From terminal:** `python3 main.py --setup`
- **CLI only:** `python3 main.py --setup --cli`

## Configuration

Edit `config.py` to customize behavior:

| Variable | Default | Description |
|---|---|---|
| `REFRESH_INTERVAL` | `900` (15 min) | Seconds between usage checks |
| `WARNING_THRESHOLD` | `0.80` | Usage fraction that triggers a warning notification |
| `CRITICAL_THRESHOLD` | `0.95` | Usage fraction that triggers a critical notification |
| `CACHE_FILE` | `~/.claude_usage_cache.json` | Path to the local usage cache |
| `LOG_FILE` | `~/.claude_usage_monitor.log` | Path to the rotating log file |
| `CLI_ACTIVE_WINDOW` | `1800` (30 min) | Seconds — CLI activity within this window = "active" |

### Example: Change refresh interval to 5 minutes

```python
# config.py
REFRESH_INTERVAL = 5 * 60  # 300 seconds
```

## Menu Bar Layout

```
🦎 Pangolin
━━━━━━━━━━━━━━━━━━━━
📊 Overall: 75% ⚠️

claude.ai
  Messages: 45/50 (90%)
  Resets in: 3h 30m

Claude Code CLI
  Status: Active ✓
  Last used: 2h ago
━━━━━━━━━━━━━━━━━━━━
🔄 Refresh Now
⚙️ Preferences...
  └─ Set Refresh Interval
  └─ Configure Alerts
  └─ Re-authenticate
❌ Quit
```

## Project Structure

```
claude-usage-monitor/
├── main.py              # Menu bar app, UI logic, entry point
├── setup_wizard.py      # Browser-based setup wizard with bookmarklet
├── usage_tracker.py     # Usage data fetching, parsing, caching
├── config.py            # All user-editable configuration
├── requirements.txt     # Python dependencies
├── setup.sh             # Installation & auto-launch setup
├── .gitignore           # Excludes cache, logs, credentials
└── README.md            # This file
```

## Browser Compatibility

| Browser | Automatic Extraction | Recommended Setup Method |
|---------|---------------------|--------------------------|
| **Safari** ✅ | Yes | Method 1 (Browser Wizard) or Method 2 |
| **Arc** ⚠️ | No (encrypted cookies) | Method 2 (Safari Bridge) - See below |
| **Chrome** ⚠️ | Partial (if installed) | Method 1 (Browser Wizard) |
| **Firefox** ⚠️ | Partial | Method 1 (Browser Wizard) |
| **Brave/Edge** ⚠️ | Partial | Method 1 (Browser Wizard) |

### Arc Users: Safari Bridge Setup

Arc's cookie encryption prevents direct extraction. Use this one-time setup:

```bash
# 1. Open Safari, log into claude.ai
# 2. Run setup (extracts from Safari automatically)
python3 main.py --setup

# 3. Close Safari, return to Arc
# ✓ App now works with saved cookies
```

**Why this works:** Cookies are stored in Keychain after extraction, independent of which browser you currently use. Safari is only needed once for the initial setup.

### Troubleshooting Authentication

**"No cookies found" during setup:**
- Make sure you're logged into claude.ai in the browser
- Try Method 1 (manual copy-paste) instead
- For Arc: Use Method 2 (Safari Bridge)

**"Authentication failed" after setup:**
- Cookie may have expired - re-authenticate
- Try: `python3 main.py --setup`

## Troubleshooting

### "Authentication failed" or "No data available"
- Your session cookie may have expired. Re-authenticate:
  ```bash
  python3 main.py --setup
  ```

### No data showing after setup
- Check your internet connection.
- Look at the log file for errors:
  ```bash
  cat ~/.claude_usage_monitor.log
  ```

### High CPU usage
- Increase the refresh interval in `config.py`:
  ```python
  REFRESH_INTERVAL = 30 * 60  # 30 minutes
  ```

### Removing auto-launch
```bash
launchctl unload ~/Library/LaunchAgents/com.pangolin.claude-monitor.plist
rm ~/Library/LaunchAgents/com.pangolin.claude-monitor.plist
```

## Uninstall

```bash
# 1. Remove auto-launch (if configured)
launchctl unload ~/Library/LaunchAgents/com.pangolin.claude-monitor.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.pangolin.claude-monitor.plist

# 2. Remove cached data and logs
rm -f ~/.claude_usage_cache.json
rm -f ~/.claude_usage_monitor.log
rm -f ~/.claude_usage_monitor.log.1

# 3. Remove Keychain entry
python3 -c "import keyring; keyring.delete_password('pangolin-claude-monitor', 'session_cookie')"

# 4. Remove the project directory
rm -rf /path/to/claude-usage-monitor

# 5. Uninstall Python packages (optional)
pip3 uninstall rumps requests keyring beautifulsoup4 python-dateutil
```

## License

MIT
