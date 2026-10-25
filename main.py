"""
AtmosTune: Real-Time Weather & Time-of-Day Dynamic Playlist Auto-Curator.

Main entry point and orchestrator implementing the Low-Memory 'Run-and-Kill' pattern:
- Checks environmental context first (<20MB RAM footprint).
- If update is unnecessary, exits immediately without launching browser.
- If update is warranted, triggers headless Playwright (<30s runtime), clears memory,
  saves persistent state, dispatches native OS and Telegram notifications, and exits cleanly.
- Also provides Telegram remote control daemon via `--telegram`.
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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


def clear_playlist_tracks(
    config: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """Clears all tracks from target Spotify playlist headlessly."""
    log = logger or logging.getLogger("AtmosTune")
    executor = PlaywrightRPAExecutor(
        config=config,
        selectors_path="./config/selectors.json",
        logger=log,
    )
    result = executor.clear_playlist()

    if result.get("success"):
        state_path = Path("./data/state.json")
        state = load_state(state_path)
        state["last_playlist_tracks"] = []
        save_state(state_path, state)

    return result


def run_atmostune_cycle(
    config: Dict[str, Any],
    force: bool = False,
    dry_run: bool = False,
    mood_override: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """
    Executes a single end-to-end AtmosTune run-and-kill cycle:
    1. Fetches environmental context.
    2. Matches target mood (or applies mood override).
    3. Evaluates decision engine (skips browser if conditions are static).
    4. Selects tracks with anti-fatigue exponential decay and narrative curve.
    5. Injects tracks into Spotify via headless Playwright.
    6. Updates state and fires notifications.
    """
    log = logger or logging.getLogger("AtmosTune")
    notifier = AtmosNotifier(config=config, logger=log)
    state_path = Path("./data/state.json")
    state = load_state(state_path)

    log.info("Evaluating real-time atmospheric conditions...")
    context_fetcher = ContextFetcher(config=config, logger=log)

    try:
        context: WeatherTimeContext = context_fetcher.get_current_context()
    except Exception as exc:
        log.critical("Failed to acquire environmental context: %s", exc)
        return {"success": False, "error": f"Failed to acquire context: {exc}"}

    matched_mood = context.matched_mood
    if mood_override:
        # Search for requested mood name in mood_matrix
        found_mood = None
        for cond, blocks in config.get("mood_matrix", {}).items():
            if isinstance(blocks, dict):
                for blk, mdata in blocks.items():
                    if isinstance(mdata, dict) and mdata.get("mood_name", "").lower() == mood_override.lower():
                        found_mood = mdata
                        break
            if found_mood:
                break

        if found_mood:
            matched_mood = found_mood
            mood_name = found_mood.get("mood_name")
        else:
            mood_name = mood_override
            matched_mood = {
                "mood_name": mood_name,
                "description": f"Custom mood override: {mood_name}",
                "target_energy": 0.50,
                "target_valence": 0.50,
                "preferred_genres": ["ambient", "indie", "electronic"],
            }
        log.info("Applied manual mood override: '%s'", mood_name)
    else:
        mood_name = matched_mood.get("mood_name", "Atmospheric Drift")

    log.info(
        "Current Context: %s (%.1f°C, %s) | Block: %s | Mood: '%s'",
        context.city_name,
        context.temperature,
        context.condition,
        context.time_block,
        mood_name,
    )

    # Decision Engine: Check if browser invocation is necessary
    should_run, reason = context_fetcher.should_trigger_update(
        current_context=context,
        state=state,
        force=force or bool(mood_override),
    )

    if not should_run and not dry_run:
        log.info("Decision Engine: %s", reason)
        log.info("Execution complete. Script exiting cleanly to preserve system resources.")
        return {
            "success": True,
            "skipped": True,
            "reason": reason,
            "mood_name": mood_name,
            "weather": context.condition,
            "temperature": context.temperature,
            "time_block": context.time_block,
        }

    log.info("Decision Engine: %s [Triggering Playlist Synthesis]", reason)

    # Track Selection
    track_db_path = "./data/tracks_db.json"
    selector = SmartTrackSelector(
        tracks_db_path=track_db_path,
        config=config,
        logger=log,
    )

    curated_tracks = selector.select_tracks(
        target_mood=matched_mood,
        weather_condition=context.condition,
        update_state=not dry_run,
    )

    if not curated_tracks:
        log.warning("No suitable tracks could be selected. Aborting.")
        return {"success": False, "error": "No tracks could be selected."}

    print("\n" + "=" * 65)
    print(f"[*] CURATED ATMOSPHERIC SOUNDTRACK: {mood_name.upper()}")
    print(f"   Atmosphere: {context.condition} ({context.temperature:+.1f}°C) | Diurnal: {context.time_block}")
    print(f"   Target Attributes: Energy {matched_mood.get('target_energy')}, Valence {matched_mood.get('target_valence')}")
    print("=" * 65)
    for i, trk in enumerate(curated_tracks, 1):
        print(f"  {i:02d}. {trk.artist:<18} - {trk.title:<24} [E: {trk.energy:.2f} | Score: {trk.calculated_score:.3f}]")
    print("=" * 65 + "\n")

    lead_track = f"{curated_tracks[0].artist} - {curated_tracks[0].title}"
    playlist_url = config.get("playlist", {}).get("target_playlist_url", "")

    # Dry-Run handling
    if dry_run:
        log.info("Dry-run complete. Browser automation and state mutations bypassed.")
        notifier.notify_rebuild_success(
            mood_name=f"[Dry-Run] {mood_name}",
            condition=context.condition,
            temperature=context.temperature,
            time_block=context.time_block,
            track_count=len(curated_tracks),
            lead_track=lead_track,
            playlist_url=playlist_url,
        )
        return {
            "success": True,
            "dry_run": True,
            "mood_name": mood_name,
            "weather": context.condition,
            "temperature": context.temperature,
            "time_block": context.time_block,
            "tracks_added": len(curated_tracks),
            "lead_track": lead_track,
        }

    # Headless RPA Playwright Execution
    executor = PlaywrightRPAExecutor(
        config=config,
        selectors_path="./config/selectors.json",
        logger=log,
    )

    rpa_result = executor.rebuild_playlist(
        tracks=curated_tracks,
        mood_name=mood_name,
        weather_condition=context.condition,
        time_block=context.time_block,
        temperature=context.temperature,
    )

    if not rpa_result.get("success"):
        return rpa_result

    # Update Persistent State
    state["last_run_timestamp"] = context.timestamp
    state["last_weather_condition"] = context.condition
    state["last_temperature"] = context.temperature
    state["last_time_block"] = context.time_block
    state["last_mood"] = mood_name
    state["last_playlist_tracks"] = [t.id for t in curated_tracks]
    state["execution_count"] = state.get("execution_count", 0) + 1
    state["status"] = "success"

    save_state(state_path, state)
    log.info("Updated state saved to %s (Execution #%d)", state_path, state["execution_count"])

    # Dispatch Toast & Telegram Notifications
    notifier.notify_rebuild_success(
        mood_name=mood_name,
        condition=context.condition,
        temperature=context.temperature,
        time_block=context.time_block,
        track_count=len(curated_tracks),
        lead_track=lead_track,
        playlist_url=playlist_url,
    )

    elapsed = rpa_result.get("elapsed_seconds", 0)
    log.info("AtmosTune cycle successfully completed in %.2fs. Browser closed, RAM released.", elapsed)

    return {
        "success": True,
        "mood_name": mood_name,
        "weather": context.condition,
        "temperature": context.temperature,
        "time_block": context.time_block,
        "tracks_added": len(curated_tracks),
        "elapsed_seconds": elapsed,
        "lead_track": lead_track,
    }


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
        "--mood",
        type=str,
        default=None,
        help="Manually force a specific mood from the matrix (e.g. 'Chill Twilight').",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Headlessly clear all tracks from the target Spotify playlist.",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Launch interactive Telegram Bot remote controller listener.",
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

    # 2. Clear Playlist Mode
    if args.clear:
        logger.info("Executing on-demand playlist clear routine...")
        res = clear_playlist_tracks(config=config, logger=logger)
        if res.get("success"):
            logger.info("Successfully cleared playlist (%d tracks removed in %.2fs).", res.get("tracks_removed", 0), res.get("elapsed_seconds", 0))
            return 0
        else:
            logger.error("Failed to clear playlist: %s", res.get("error"))
            return 1

    # 3. Telegram Controller Mode
    if args.telegram:
        from src.telegram_bot import AtmosTelegramBot
        try:
            bot = AtmosTelegramBot(
                config=config,
                rebuild_callback=run_atmostune_cycle,
                clear_callback=clear_playlist_tracks,
                logger=logger,
            )
            bot.run_polling()
            return 0
        except ValueError as val_err:
            logger.error("Telegram configuration error: %s", val_err)
            print(f"\n[!] Telegram Error: {val_err}")
            return 1
        except Exception as exc:
            logger.critical("Telegram bot crashed: %s", exc)
            return 1

    # 4. Standard Run-and-Kill Execution Cycle
    try:
        res = run_atmostune_cycle(
            config=config,
            force=args.force,
            dry_run=args.dry_run,
            mood_override=args.mood,
            logger=logger,
        )
        return 0 if res.get("success") else 1
    except AuthenticationRequiredError:
        logger.error("Authentication required! Please run 'python main.py --auth' to log in once.")
        return 1
    except Exception as exc:
        logger.critical("Critical execution error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
