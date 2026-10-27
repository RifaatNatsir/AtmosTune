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
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.context_fetcher import ContextFetcher, WeatherTimeContext
from src.track_selector import SmartTrackSelector
from src.rpa_executor import PlaywrightRPAExecutor, AuthenticationRequiredError
from src.notifier import AtmosNotifier, setup_logger


def load_dotenv_if_present(env_path: Path = Path(".env")) -> None:
    """Lightweight .env file loader without external dependencies."""
    if not env_path.exists():
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


def load_yaml_config(config_path: Path) -> Dict[str, Any]:
    """Loads and validates configuration file."""
    load_dotenv_if_present()
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
    genre_filter: Optional[str] = None,
    tag_filter: Optional[str] = None,
    tracks_override: Optional[List[Any]] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    """
    Executes a single end-to-end AtmosTune run-and-kill cycle:
    1. Fetches environmental context.
    2. Matches target mood (or applies mood / genre / hits override).
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
    elif tag_filter == "hits_indo":
        mood_name = "Top Hits Indonesia"
        matched_mood = {
            "mood_name": mood_name,
            "description": "Trending chart toppers & popular Indonesian hits",
            "target_energy": 0.75,
            "target_valence": 0.70,
            "preferred_genres": ["indopop", "pop", "indie"],
        }
    elif tag_filter == "hits_global":
        mood_name = "Global Top Hits"
        matched_mood = {
            "mood_name": mood_name,
            "description": "Worldwide chart toppers & viral global bangers",
            "target_energy": 0.78,
            "target_valence": 0.72,
            "preferred_genres": ["pop", "dance", "electronic"],
        }
    elif tag_filter in ("breakout", "tv_hits") or genre_filter == "breakout":
        tag_filter = "breakout"
        genre_filter = None
        mood_name = "Breakout NET.TV Hits"
        matched_mood = {
            "mood_name": mood_name,
            "description": "Nostalgic chart toppers and viral music video vibes inspired by Breakout NET.TV",
            "target_energy": 0.78,
            "target_valence": 0.74,
            "preferred_genres": ["pop", "electronic", "indopop", "indie", "r&b"],
        }
    elif genre_filter and genre_filter.lower() in ("r&b", "soul"):
        mood_name = "R&B & Soul Vibes"
        matched_mood = {
            "mood_name": mood_name,
            "description": "Smooth, slow-jam, and soulful R&B grooves (Usher, SZA, Kehlani, etc.)",
            "target_energy": 0.55,
            "target_valence": 0.58,
            "preferred_genres": ["r&b"],
        }
    elif genre_filter:
        mood_name = f"{genre_filter.title()} Sound"
        matched_mood = {
            "mood_name": mood_name,
            "description": f"Curated selection strictly for genre: {genre_filter.title()}",
            "target_energy": matched_mood.get("target_energy", 0.50),
            "target_valence": matched_mood.get("target_valence", 0.50),
            "preferred_genres": [genre_filter],
        }
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
        force=force or bool(mood_override) or bool(genre_filter) or bool(tag_filter) or bool(tracks_override),
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

    if tracks_override:
        curated_tracks = tracks_override
        if not dry_run:
            now = time.time()
            for track in curated_tracks:
                track.last_played_timestamp = now
                track.play_count += 1
            selector.save_tracks()
    else:
        curated_tracks = selector.select_tracks(
            target_mood=matched_mood,
            weather_condition=context.condition,
            update_state=not dry_run,
            genre_filter=genre_filter,
            tag_filter=tag_filter,
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
        "--genre",
        type=str,
        default=None,
        help="Target a specific musical genre (e.g. 'indopop', 'pop', 'indie', 'lofi', 'synthwave').",
    )
    parser.add_argument(
        "--hits",
        type=str,
        default=None,
        choices=["indo", "global", "viral"],
        help="Generate playlist directly from top trending hits ('indo', 'global', or 'viral').",
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
        "--import-spotify",
        type=str,
        default=None,
        metavar="URL_OR_ID",
        help="Import tracks from Spotify playlist/album URL using Spotify Developer API audio features.",
    )
    parser.add_argument(
        "--import-genre",
        type=str,
        default=None,
        help="Optional genre override for imported tracks (e.g. 'lofi', 'ambient', 'synthwave').",
    )
    parser.add_argument(
        "--import-tags",
        type=str,
        default=None,
        help="Optional comma-separated atmospheric tags (e.g. 'rain,night,chill').",
    )
    parser.add_argument(
        "--import-limit",
        type=int,
        default=50,
        help="Maximum tracks to import from playlist (default: 50).",
    )
    parser.add_argument(
        "--import-energy",
        type=float,
        default=None,
        help="Optional target energy override (0.0 to 1.0) for imported tracks.",
    )
    parser.add_argument(
        "--import-valence",
        type=float,
        default=None,
        help="Optional target valence override (0.0 to 1.0) for imported tracks.",
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

    # 4. Spotify Web API Track Importer Mode
    if args.import_spotify:
        from src.spotify_importer import SpotifyImporter, SpotifyAPIError
        spotify_cfg = config.get("spotify_api", {})
        importer = SpotifyImporter(
            client_id=spotify_cfg.get("client_id"),
            client_secret=spotify_cfg.get("client_secret"),
            db_path="./data/tracks_db.json",
            logger=logger,
        )
        custom_tags = [t.strip() for t in args.import_tags.split(",")] if args.import_tags else None
        try:
            res = importer.import_from_url(
                url_or_id=args.import_spotify,
                limit=args.import_limit,
                genre_override=args.import_genre,
                custom_tags=custom_tags,
                energy_override=args.import_energy,
                valence_override=args.import_valence,
            )
            if res.get("success"):
                print(f"\n[+] Import complete! Added {res['added_count']} new tracks (skipped {res['skipped_count']} duplicates). Total in DB: {res['total_in_db']}")
                return 0
            else:
                print(f"\n[-] Import failed: {res.get('message')}")
                return 1
        except SpotifyAPIError as api_err:
            print(f"\n[!] Spotify API Error: {api_err}")
            return 1
        except Exception as exc:
            print(f"\n[!] Import Error: {exc}")
            return 1

    # 5. Standard Run-and-Kill Execution Cycle
    try:
        tag_filter = None
        if args.hits == "indo":
            tag_filter = "hits_indo"
        elif args.hits == "global":
            tag_filter = "hits_global"
        elif args.hits == "viral":
            tag_filter = "viral"

        res = run_atmostune_cycle(
            config=config,
            force=args.force or bool(args.hits) or bool(args.genre),
            dry_run=args.dry_run,
            mood_override=args.mood,
            genre_filter=args.genre,
            tag_filter=tag_filter,
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
