# AtmosTune 🎧 🌦️

> **Production-grade, lightweight, "run-and-kill" background auto-curator that dynamically rebuilds your music playlist based on real-time local weather and time of day using Python and Playwright (Headless RPA).**

---

## 1. Architectural Philosophy: The "Run-and-Kill" Pattern

Conventional music automation scripts run persistent `while True: time.sleep(...)` loops in the background, continuously consuming **400MB to 800MB of RAM** just to keep Chromium and Node/Python runtimes resident in memory 24/7.

**AtmosTune** is architected for near-zero idle overhead:
1. **Lightweight Context Evaluation (< 20MB RAM):** Triggered periodically by your OS scheduler (Windows Task Scheduler or Cron). It queries local weather and current diurnal time block first.
2. **Intelligent Decision Engine:** Compares current conditions against local persistent state (`./data/state.json`). If weather conditions, time blocks, and temperature have not transitioned, and the cooldown has not elapsed, **it terminates immediately in < 0.1s without ever launching a browser**.
3. **Sub-30-Second Headless Burst:** If and only if a playlist update is required, AtmosTune launches a headless Playwright instance using a persistent browser profile (`./browser_profile`), injects the curated tracks, updates atmospheric metadata, **terminates all browser processes completely to release 100% of browser RAM**, saves state, dispatches an OS toast notification, and exits cleanly.

```mermaid
flowchart TD
    A[OS Task Scheduler / Cron Trigger] --> B[Load Config & Local State]
    B --> C[Fetch Weather & Time Block <15MB RAM]
    C --> D{Condition/Time Shift or Cooldown Expired?}
    D -- No --> E[Log Status & Exit Cleanly (0MB Leaked)]
    D -- Yes / --force --> F[SmartTrackSelector: Bell-Curve & Exponential Decay]
    F --> G[Launch Headless Playwright Context]
    G --> H[Rebuild Spotify Playlist <30s]
    H --> I[Close Browser & Release 100% RAM]
    I --> J[Save State & Dispatch Desktop Toast]
    J --> K[Process Terminated Cleanly]
```

---

## 2. Key Features

- **Persistent Browser Profile (Anti-Login Loop):** Uses Playwright's `launch_persistent_context` pointing to `./browser_profile`. Run `python main.py --auth` once in headed mode to log in; all subsequent background runs reuse your authenticated session seamlessly.
- **SmartTrackSelector with Exponential Fatigue Decay:**
  $$F_{\text{recency}}(\Delta t) = 1.0 - e^{-\frac{\Delta t}{\tau}}$$
  Prevents playlist burnout by exponentially penalizing recently played tracks based on configurable half-life ($\tau$).
- **Gaussian (Bell-Curve) Energy Progression:** Sequences tracks into an intentional narrative listening curve: gentle intro $\to$ central energetic peak $\to$ mellow outro.
- **Diurnal Time-Block Classification:** Automatically partitions the 24-hour cycle into `early_morning`, `morning`, `afternoon`, `evening`, and `night`.
- **Atmospheric Mood Matrix:** Rules matrix mapping combinations of weather conditions (`Rain`, `Clear`, `Clouds`, `Thunderstorm`, `Snow`, `Drizzle`) and diurnal blocks to energy, valence, and genres.
- **Decoupled DOM Selectors:** UI selectors are decoupled into `config/selectors.json` with multi-selector fallbacks, insulating the core logic from streaming player DOM updates.
- **Zero-Failure Fallbacks:** Includes physics-based diurnal temperature simulation if no OpenWeatherMap API key is provided, native OS desktop toast notifications, and rotating log files.

---

## 3. Project Directory Structure

```text
AtmosTune/
├── config/
│   ├── config.yaml          # Weather API settings, coordinates, cooldowns, and Mood Matrix
│   └── selectors.json       # Decoupled DOM selectors (aria-labels, data-testids) for UI maintenance
├── data/
│   ├── tracks_db.json       # Pre-populated database of 20+ diverse tracks with sonic metadata
│   └── state.json           # Tracks execution timestamps, weather conditions, and history
├── src/
│   ├── __init__.py
│   ├── context_fetcher.py   # Live OpenWeatherMap API & diurnal time-block classification
│   ├── track_selector.py    # SmartTrackSelector (Exponential Decay & Bell-Curve sequencing)
│   ├── rpa_executor.py      # Playwright persistent headless RPA & interactive auth
│   └── notifier.py          # Rotating file logger & OS desktop toast notification
├── logs/
│   └── atmostune.log        # Auto-rotating execution log (2MB ceiling, 3 backups)
├── main.py                  # Main CLI orchestrator (--auth, --force, --dry-run)
├── requirements.txt         # Minimal production dependencies
├── run_atmostune.bat        # Windows Batch launcher for Task Scheduler
├── run_silent.vbs           # Windows invisible background runner (no black cmd flash)
└── README.md                # System documentation
```

---

## 4. Installation & Setup

### Prerequisites
- Python 3.10+
- Internet connection

### Step 1: Clone and Install Dependencies
```bash
git clone https://github.com/your-username/AtmosTune.git
cd AtmosTune

# Install Python packages
pip install -r requirements.txt

# Install Playwright Chromium binaries
python -m playwright install chromium
```

### Step 2: One-Time Authentication (`--auth`)
To allow background headless runs to access your playlist without hitting login prompts, launch interactive authentication mode:

```bash
python main.py --auth
```
- A visible Chromium browser window will open.
- Log into your Spotify account normally.
- Once logged in, your session cookies are securely stored in `./browser_profile`.
- Return to your terminal and press `[Enter]` if prompted. You will never need to log in again.

---

## 5. Configuration (`config/config.yaml`)

Edit `config/config.yaml` to customize your environment:

### Weather Provider (100% Free Live Global Weather)
AtmosTune includes built-in auto-geolocation via IP and **Open-Meteo** API integration. It works out-of-the-box without registering or configuring any API keys!

```yaml
weather_api:
  # Provider: "open-meteo" (Free live global weather, NO API KEY REQUIRED)
  # or "openweathermap" (Requires free api_key from openweathermap.org)
  provider: "open-meteo"
  # Automatically detects your geographic coordinates and city via public IP
  auto_detect_location: true
  api_key: ""
  lat: -5.0060          # Fallback coordinates
  lon: 119.5730
  city_name: "Maros/Makassar"
  units: "metric"       # "metric" (°C) or "imperial" (°F)
  timeout_seconds: 8
  mock_fallback: true   # Diurnal physics simulation if totally offline
```

### Telegram Remote Controller Configuration
Control your playlist, trigger rebuilds, or check weather directly from Telegram:

```yaml
telegram:
  enabled: true
  # Obtain your token from @BotFather on Telegram (or export TELEGRAM_BOT_TOKEN)
  bot_token: "YOUR_TELEGRAM_BOT_TOKEN"
  # Whitelist of Telegram Chat IDs permitted to control AtmosTune (e.g. [123456789])
  # When empty, send /start to your bot to view your Chat ID!
  allowed_chat_ids: []
  notify_on_rebuild: true  # Broadcast alert whenever automated background rebuild succeeds
```

### Target Playlist
```yaml
playlist:
  target_playlist_url: "https://open.spotify.com/playlist/5qvujTFIG3TgSfgT5b8qqa"
  playlist_name_prefix: "AtmosTune • "
  max_tracks: 20
  sync_mode: "replace"      # "replace" (clears old tracks) or "append"
  curve_type: "bell_curve"  # "bell_curve", "ascending", or "steady"
```

### Scheduling & Cooldowns
```yaml
scheduling:
  cooldown_minutes: 60       # Minimum minutes between updates unless conditions shift
  min_temperature_delta: 4.0 # °C shift threshold to trigger an early refresh
```

---

## 6. Telegram Bot Remote Controller 📱

AtmosTune includes a companion Telegram Bot that lets you monitor and control your Spotify playlist from anywhere in the world on your phone or desktop.

### Setup Guide in 3 Steps:

1. **Create your Bot Token:**
   - Open Telegram and search for `@BotFather`.
   - Send `/newbot`, name your bot (e.g. `MyAtmosTuneBot`), and copy the HTTP API Token.
   - Paste the token into `config/config.yaml` under `telegram.bot_token:` (or set `export TELEGRAM_BOT_TOKEN="token"`).

2. **Start the Bot Listener:**
   - Double-click `run_telegram_bot.bat` or run:
     ```bash
     python main.py --telegram
     ```

3. **Authorize Your Account:**
   - Open Telegram, find your bot, and send `/start`.
   - The bot will greet you and display your unique **Chat ID** (e.g. `123456789`).
   - Add this number to `config/config.yaml` under `telegram.allowed_chat_ids: [123456789]` to lock down access so only you can control the playlist.

### Telegram Controls & Interactive Dashboard

The bot provides an interactive inline keyboard and slash commands:

| Command / Button | Action |
| :--- | :--- |
| `[🔄 Rebuild Playlist]` (`/rebuild`) | Triggers headless Playwright RPA to rebuild your Spotify playlist for current live weather in <30s. |
| `[🌦️ Real Weather]` (`/weather`) | Displays live meteorological readings (temperature, feels like, humidity, conditions, diurnal block). |
| `[🎵 Preview Tracks]` (`/preview`) | Preview next curated tracks and energy scores without touching Spotify. |
| `[📊 System Status]` (`/status`) | Displays last run timestamp, execution count, active mood, and track count. |
| `[🎨 Choose Mood]` (`/mood <name>`) | Force a specific mood override (e.g. *Chill Twilight*, *Rainy Day Focus*, *Morning Acoustic Rise*). |
| `[🗑️ Clear Playlist]` (`/clear`) | Confirms and empties all tracks from the target playlist on demand. |

---

## 7. CLI Usage & Testing

### Test with Dry-Run
Evaluates real local weather, diurnal block, selects tracks, and prints the Bell-Curve energy scores without touching Playwright or mutating any playlist:
```bash
python main.py --dry-run
```

### Force an Immediate Rebuild
Bypasses the cooldown timer and state comparisons to execute an instant rebuild:
```bash
python main.py --force
```

### Force a Specific Mood
Rebuilds the playlist with an explicit target mood:
```bash
python main.py --mood "Chill Twilight" --force
```

### Clear Playlist Tracks On Demand
Clears all tracks from the Spotify playlist:
```bash
python main.py --clear
```

### Launch Telegram Bot Listener
Runs the interactive Telegram remote controller daemon:
```bash
python main.py --telegram
```

### Normal Automated Execution (Run-and-Kill)
```bash
python main.py
```
*If conditions are unchanged and the cooldown is active, AtmosTune exits in <0.1s.*

---

## 7. OS Background Automation (Scheduling)

### A. Windows Task Scheduler Setup

To run AtmosTune automatically every 30 or 60 minutes:

#### Method 1: Instant PowerShell Registration (Recommended)
Open PowerShell as Administrator and run:
```powershell
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"d:\Github Repository\AtmosTune\run_silent.vbs`"" -WorkingDirectory "d:\Github Repository\AtmosTune"
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration ([TimeSpan]::MaxValue)
Register-ScheduledTask -TaskName "AtmosTune_AutoCurator" -Action $Action -Trigger $Trigger -Description "AtmosTune Background Weather/Time Playlist Auto-Curator"
```

#### Method 2: Windows Task Scheduler GUI
1. Press `Win + R`, type `taskschd.msc`, and press Enter.
2. Click **Create Task...** in the right sidebar.
3. **General Tab:**
   - Name: `AtmosTune Auto-Curator`
   - Select: *Run only when user is logged on* (or *Run whether user is logged on or not*).
4. **Triggers Tab:**
   - Click **New...** $\to$ Begin the task: *On a schedule*.
   - Settings: *Daily*, repeat every **30 minutes** or **1 hour** for a duration of *Indefinitely*.
5. **Actions Tab:**
   - Click **New...** $\to$ Action: *Start a program*.
   - Program/script: `wscript.exe`
   - Add arguments: `"d:\Github Repository\AtmosTune\run_silent.vbs"`
   - Start in (optional): `d:\Github Repository\AtmosTune`
   *(Using `run_silent.vbs` ensures NO black console window ever flashes on your screen!)*
6. Click **OK** to save.

---

### B. Linux / macOS Cron Setup

Edit your crontab:
```bash
crontab -e
```

Add the entry to run AtmosTune every 30 minutes:
```bash
*/30 * * * * cd /path/to/AtmosTune && /usr/bin/python3 main.py >> /path/to/AtmosTune/logs/cron.log 2>&1
```

---

## 8. Track Database & Customization

Tracks reside in `data/tracks_db.json`. You can easily add your favorite tracks with sonic metadata:

```json
{
  "id": "trk_021",
  "title": "Song Title",
  "artist": "Artist Name",
  "album": "Album Name",
  "genre": "lofi",
  "energy": 0.40,
  "valence": 0.50,
  "tempo_bpm": 85,
  "tags": ["rain", "cozy", "study"],
  "search_query": "Artist Name - Song Title",
  "duration_sec": 210,
  "last_played_timestamp": null,
  "play_count": 0
}
```

- **`energy`** (`0.0` - `1.0`): Perceptual intensity and activity.
- **`valence`** (`0.0` - `1.0`): Musical positivity / happiness.
- **`tags`**: Matching tags receive scoring bonuses during weather matching.

---

## 9. Troubleshooting & FAQ

| Issue | Resolution |
| :--- | :--- |
| **`Authentication required!` error** | Run `python main.py --auth` to log into Spotify interactively and establish your browser cookies. |
| **Console Unicode error on older Windows** | AtmosTune automatically reconfigures `sys.stdout` to UTF-8 and uses ASCII-safe fallback characters. |
| **Spotify UI DOM updated** | Open `config/selectors.json` and update the relevant selector without touching any Python code. |
| **Notification icon didn't show** | AtmosTune catches Windows notification service exceptions gracefully; verify `notifications.enabled` in `config.yaml`. |

---

## 10. License

MIT License. Designed with high-performance systems engineering principles for zero-overhead background personal automation.
