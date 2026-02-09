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

## First-Time Authentication

On first launch, a **setup wizard** opens in your browser with two options:

### Option A: One-Click Bookmarklet (easiest)
1. Drag the **"Get Claude Cookie"** button to your Bookmarks Bar.
2. Go to [claude.ai](https://claude.ai) and make sure you're logged in.
3. Click the bookmarklet. Done — the cookie is sent and validated automatically.

### Option B: Paste Manually
1. Go to [claude.ai](https://claude.ai) and log in.
2. Open DevTools (`Cmd + Option + I`) → **Console** tab.
3. Type `copy(document.cookie)` and press Enter — this copies cookies to your clipboard.
4. Paste into the setup page's text box and click **Validate & Save**.

The cookie is validated against claude.ai immediately, then stored securely in your macOS Keychain. It is **never** written to disk or logged.

### Re-authenticate later
- From the menu bar: **Preferences → Re-authenticate** (opens the browser wizard)
- From the terminal: `python3 main.py --setup`
- Terminal-only mode (no browser): `python3 main.py --setup --cli`

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

**✅ Tested and Working:**
- **Safari** - Recommended for best compatibility

**⚠️ Known Issues:**
- **Arc Browser** - Automatic cookie extraction doesn't work due to encryption. Use Safari to log in once for setup, then you can continue using Arc for normal browsing.
- **Chrome** - Should work if installed, but not extensively tested

**Recommended Setup Flow for Arc Users:**
1. Open Safari and log into [claude.ai](https://claude.ai)
2. Run `python3 main.py --setup` to extract cookies from Safari
3. Close Safari and return to using Arc for normal browsing
4. The app will continue working with the saved Safari cookies

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
