"""
AtmosTune: Real-Time Weather & Time-of-Day Dynamic Playlist Auto-Curator.

Main entry point and orchestrator implementing the Low-Memory 'Run-and-Kill' pattern:
- Checks environmental context first (<20MB RAM footprint).
- If update is unnecessary, exits immediately without launching browser.
- If update is warranted, triggers headless Playwright (<30s runtime), clears memory,
  saves persistent state, dispatches native OS notification, and exits cleanly.
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Any, Dict

import yaml

from src.context_fetcher import ContextFetcher, WeatherTimeContext
from src.track_selector import SmartTrackSelector
from src.rpa_executor import PlaywrightRPAExecutor, AuthenticationRequiredError
from src.notifier import AtmosNotifier, setup_logger


def load_yaml_config(config_path: Path) -> Dict[str, Any]:
    """Loads and validates configuration file."""
    if not config_path.exists():
        print(f"Error: Configuration file not found at {config_path}")
        sys.exit(1)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        print(f"Error: Failed to parse configuration file: {exc}")
        sys.exit(1)


def load_state(state_path: Path) -> Dict[str, Any]:
    """Loads execution state file, creating default if absent."""
    default_state = {
        "last_run_timestamp": None,
        "last_weather_condition": None,
        "last_temperature": None,
        "last_time_block": None,
        "last_mood": None,
        "last_playlist_tracks": [],
        "execution_count": 0,
        "status": "ready",
    }
    if not state_path.exists():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        save_state(state_path, default_state)
        return default_state

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_state


def save_state(state_path: Path, state_data: Dict[str, Any]) -> None:
    """Persists updated state to disk."""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)
    except Exception as exc:
        print(f"Warning: Failed to save state to {state_path}: {exc}")


def print_banner() -> None:
    """Renders formatted application banner."""
    banner = r"""
       __ _   _                        _____                  
      / /| | | |                      |_   _|                 
     / /_| |_| |_ __  ___  ___ _____    | |_   _ _ __   ___ 
    / /_  __  | '_ \| _ \/ __|_   _|    | | | | | '_ \ / _ \
   / /  | | | | | | |(_) \__ \ | |      | | |_| | | | |  __/
  /_/   |_| |_|_| |_|\___/|___/ |_|      \_/\__,_|_| |_|\___|
    Automated Atmospheric Soundtrack Generator • Run-and-Kill
    """
    print(banner)


def main() -> int:
    """Main CLI orchestrator for AtmosTune."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="AtmosTune",
        description="Dynamic playlist curator driven by real-time local weather & time of day.",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Launch interactive headed browser to log in and save persistent session cookies.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass cooldown timer and state comparisons to force an immediate playlist rebuild.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate weather, mood, and select tracks without opening browser or modifying playlist.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="./config/config.yaml",
        help="Path to custom configuration YAML (default: ./config/config.yaml).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable detailed debug logging.",
    )

    args = parser.parse_args()
    print_banner()

    config_path = Path(args.config)
    config = load_yaml_config(config_path)

    # Initialize Logger
    notify_cfg = config.get("notifications", {})
    logger = setup_logger(
        name="AtmosTune",
        log_file=notify_cfg.get("log_file", "./logs/atmostune.log"),
        log_level=notify_cfg.get("log_level", "INFO"),
        max_bytes=notify_cfg.get("max_log_bytes", 2097152),
        backup_count=notify_cfg.get("backup_count", 3),
        verbose=args.verbose,
    )
    notifier = AtmosNotifier(config=config, logger=logger)

    # 1. Interactive Authentication Mode
    if args.auth:
        logger.info("Executing interactive authentication routine...")
        executor = PlaywrightRPAExecutor(
            config=config,
            selectors_path="./config/selectors.json",
            logger=logger,
        )
        success = executor.setup_interactive_auth()
        if success:
            logger.info("Authentication setup complete. Headless executions will now reuse this profile.")
            return 0
        else:
            logger.error("Authentication setup was not completed.")
            return 1

    # 2. Lightweight Evaluation Phase (<20MB RAM)
    state_path = Path("./data/state.json")
    state = load_state(state_path)

    logger.info("Evaluating real-time atmospheric conditions...")
    context_fetcher = ContextFetcher(config=config, logger=logger)

    try:
        context: WeatherTimeContext = context_fetcher.get_current_context()
    except Exception as exc:
        logger.critical("Failed to acquire environmental context: %s", exc)
        return 1

    matched_mood = context.matched_mood
    mood_name = matched_mood.get("mood_name", "Atmospheric Drift")

    logger.info(
        "Current Context: %s (%.1f°C, %s) | Block: %s | Mood: '%s'",
        context.city_name,
        context.temperature,
        context.condition,
        context.time_block,
        mood_name,
    )

    # 3. Decision Engine: Check if browser invocation is necessary
    should_run, reason = context_fetcher.should_trigger_update(
        current_context=context,
        state=state,
        force=args.force,
    )

    if not should_run and not args.dry_run:
        logger.info("Decision Engine: %s", reason)
        logger.info("Execution complete. Script exiting cleanly to preserve system resources.")
        return 0

    logger.info("Decision Engine: %s [Triggering Playlist Synthesis]", reason)

    # 4. Track Selection with Exponential Decay & Bell-Curve Narrative Sorting
    track_db_path = "./data/tracks_db.json"
    selector = SmartTrackSelector(
        tracks_db_path=track_db_path,
        config=config,
        logger=logger,
    )

    curated_tracks = selector.select_tracks(
        target_mood=matched_mood,
        weather_condition=context.condition,
        update_state=not args.dry_run,
    )

    if not curated_tracks:
        logger.warning("No suitable tracks could be selected. Aborting.")
        return 1

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("\n" + "=" * 65)
    print(f"[*] CURATED ATMOSPHERIC SOUNDTRACK: {mood_name.upper()}")
    print(f"   Atmosphere: {context.condition} ({context.temperature:+.1f}°C) | Diurnal: {context.time_block}")
    print(f"   Target Attributes: Energy {matched_mood.get('target_energy')}, Valence {matched_mood.get('target_valence')}")
    print("=" * 65)
    for i, trk in enumerate(curated_tracks, 1):
        print(f"  {i:02d}. {trk.artist:<18} - {trk.title:<24} [E: {trk.energy:.2f} | Score: {trk.calculated_score:.3f}]")
    print("=" * 65 + "\n")

    # 5. Dry-Run Handling
    if args.dry_run:
        logger.info("Dry-run complete. Browser automation and state mutations bypassed.")
        notifier.notify_rebuild_success(
            mood_name=f"[Dry-Run] {mood_name}",
            condition=context.condition,
            temperature=context.temperature,
            time_block=context.time_block,
            track_count=len(curated_tracks),
            lead_track=f"{curated_tracks[0].artist} - {curated_tracks[0].title}",
        )
        return 0

    # 6. Playwright Headless Automation (Run-and-Kill <30s Execution)
    executor = PlaywrightRPAExecutor(
        config=config,
        selectors_path="./config/selectors.json",
        logger=logger,
    )

    try:
        rpa_result = executor.rebuild_playlist(
            tracks=curated_tracks,
            mood_name=mood_name,
            weather_condition=context.condition,
            time_block=context.time_block,
            temperature=context.temperature,
        )
    except AuthenticationRequiredError as auth_err:
        logger.error(
            "Authentication required! Please run 'python main.py --auth' to log in once.",
        )
        notifier.notify(
            title="Authentication Required",
            message="Please run 'python main.py --auth' to log into Spotify.",
        )
        return 1
    except Exception as exc:
        logger.error("RPA Execution encountered critical error: %s", exc)
        return 1

    # 7. Update Persistent State
    state["last_run_timestamp"] = context.timestamp
    state["last_weather_condition"] = context.condition
    state["last_temperature"] = context.temperature
    state["last_time_block"] = context.time_block
    state["last_mood"] = mood_name
    state["last_playlist_tracks"] = [t.id for t in curated_tracks]
    state["execution_count"] = state.get("execution_count", 0) + 1
    state["status"] = "success" if rpa_result.get("success") else "failed"

    save_state(state_path, state)
    logger.info("Updated state saved to %s (Execution #%d)", state_path, state["execution_count"])

    # 8. Dispatch Native Toast Notification
    lead_track = f"{curated_tracks[0].artist} - {curated_tracks[0].title}"
    notifier.notify_rebuild_success(
        mood_name=mood_name,
        condition=context.condition,
        temperature=context.temperature,
        time_block=context.time_block,
        track_count=len(curated_tracks),
        lead_track=lead_track,
    )

    logger.info(
        "AtmosTune cycle successfully completed in %s. Browser closed, RAM released. Exiting.",
        f"{rpa_result.get('elapsed_seconds', 0):.2f}s",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
