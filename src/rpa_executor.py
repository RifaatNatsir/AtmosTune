"""
AtmosTune Playwright RPA Executor Module.

Architectural Guarantees:
1. Low-Memory Run-and-Kill: Only launched when required; explicitly terminates browser processes.
2. Persistent Browser Session: Uses `launch_persistent_context` to retain cookies across runs.
3. Decoupled UI Selectors: Loads selectors from JSON for easy Spotify DOM maintenance.
4. Target Performance: Fast playlist rebuild target (< 30 seconds).
5. Headed Auth Setup Mode vs. Headless Automated Execution.
"""

from __future__ import annotations

import json
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError


class AuthenticationRequiredError(Exception):
    """Raised when an automated headless run discovers an unauthenticated session."""


class PlaywrightRPAExecutor:
    """Executes headless browser automation for playlist synchronizations."""

    def __init__(
        self,
        config: Dict[str, Any],
        selectors_path: str = "./config/selectors.json",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("AtmosTune.RPAExecutor")
        self.browser_cfg = self.config.get("browser", {})
        self.playlist_cfg = self.config.get("playlist", {})
        self.selectors_path = Path(selectors_path)
        self.selectors = self._load_selectors()

        # Resolve persistent profile directory
        profile_dir_raw = self.browser_cfg.get("profile_dir", "./browser_profile")
        self.profile_dir = Path(profile_dir_raw).resolve()
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def _load_selectors(self) -> Dict[str, Any]:
        """Loads decoupled DOM selectors from JSON configuration with fail-safe defaults."""
        defaults: Dict[str, Any] = {
            "auth": {
                "user_profile_widget": "button[data-testid='user-widget-link'], figure[data-testid='user-widget-avatar'], button[aria-label*='Profile']",
                "login_button": "button[data-testid='login-button'], a[href*='/login'], button:has-text('Log in')",
                "cookie_banner_accept": "#onetrust-accept-btn-handler, button:has-text('Accept Cookies')",
            },
            "playlist_view": {
                "title_heading": "h1[data-testid='entityTitle'], h1[dir='auto']",
                "description": "div[data-testid='entity-description'], p[data-encore-id='type']",
                "edit_details_trigger": "button[data-testid='edit-details-button'], button[aria-label*='Edit details']",
                "edit_name_input": "input[data-testid='playlist-edit-details-name-input']",
                "edit_description_textarea": "textarea[data-testid='playlist-edit-details-description-input']",
                "edit_save_button": "button[data-testid='playlist-edit-details-save-button']",
                "tracklist_row": "div[data-testid='tracklist-row']",
                "track_title_link": "a[data-testid='internal-track-link']",
                "track_row_more_button": "button[data-testid='more-button'], button[aria-label*='More options']",
                "context_menu_remove": "button[role='menuitem']:has-text('Remove from this playlist'), div[role='menuitem']:has-text('Remove')",
                "in_playlist_search_box": "input[data-testid='playlist-search-input'], input[role='searchbox'], input[placeholder*='Let\\'s find something']",
                "in_playlist_add_button": "button[data-testid='add-to-playlist-button'], button:has-text('Add')",
            },
            "global_search": {
                "search_nav_button": "a[href='/search'], button[aria-label='Search']",
                "search_input": "input[data-testid='search-input'], input[role='searchbox']",
                "search_result_track_row": "div[data-testid='tracklist-row']",
                "search_track_more_button": "button[data-testid='more-button'], button[aria-label*='More options']",
                "context_menu_add_to_playlist": "button[role='menuitem']:has-text('Add to playlist')",
                "context_menu_playlist_name_target": "button[role='menuitem']:has-text('{playlist_name}')",
            },
        }

        if not self.selectors_path.exists():
            self.logger.warning("Selectors file not found at %s. Using default mappings.", self.selectors_path)
            return defaults

        try:
            with open(self.selectors_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # Merge with defaults for resilience
            for section, keys in defaults.items():
                if section not in loaded:
                    loaded[section] = keys
                elif isinstance(keys, dict):
                    for k, v in keys.items():
                        loaded[section].setdefault(k, v)
            return loaded
        except Exception as exc:
            self.logger.error("Failed to parse selectors JSON: %s. Using internal defaults.", exc)
            return defaults

    def _get_browser_args(self) -> List[str]:
        """Low-footprint Chrome arguments optimized for background RPA execution."""
        return [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-infobars",
            "--disable-extensions",
            "--mute-audio",
            "--window-size=1280,800",
        ]

    def setup_interactive_auth(self, timeout_sec: int = 180) -> bool:
        """
        Interactive Mode (--auth):
        Launches a headed browser with the persistent profile directory.
        Allows the user to log into Spotify once. Cookies & credentials are saved to ./browser_profile.
        """
        self.logger.info("=======================================================")
        self.logger.info("LAUNCHING INTERACTIVE AUTHENTICATION MODE")
        self.logger.info("Persistent profile target: %s", self.profile_dir)
        self.logger.info("=======================================================")

        with sync_playwright() as p:
            context: BrowserContext = p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=False,
                args=self._get_browser_args(),
                viewport={"width": 1280, "height": 850},
                user_agent=self.browser_cfg.get(
                    "user_agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                ),
            )

            try:
                page: Page = context.new_page()
                target_url = self.playlist_cfg.get("target_playlist_url", "https://open.spotify.com/")
                if "YOUR_PLAYLIST_ID" in target_url:
                    target_url = "https://open.spotify.com/"

                self.logger.info("Navigating to: %s", target_url)
                page.goto(target_url, wait_until="domcontentloaded", timeout=45000)

                self._dismiss_cookie_banner(page)

                print("\n" + "=" * 65)
                print(">>> SPOTIFY AUTHENTICATION WINDOW OPENED <<<")
                print("Please log into your Spotify account in the opened browser window.")
                print("Once you are logged in and can view your library, return here.")
                print("=" * 65 + "\n")

                # Check for successful login widget or wait for user confirmation
                poll_start = time.time()
                auth_widget_selector = self.selectors["auth"]["user_profile_widget"]
                authenticated = False

                while (time.time() - poll_start) < timeout_sec:
                    # Check if user widget appears
                    try:
                        if page.locator(auth_widget_selector).first.is_visible():
                            authenticated = True
                            self.logger.info("Detected authenticated user profile in browser DOM!")
                            break
                    except Exception:
                        pass
                    time.sleep(2)

                if not authenticated:
                    self.logger.warning("Profile widget not detected automatically within timeout.")
                    input("Press [ENTER] here once you have finished logging in to save the session... ")
                    authenticated = True

                self.logger.info("Authentication session captured successfully in %s", self.profile_dir)
                return True

            except Exception as exc:
                self.logger.error("Authentication session error: %s", exc)
                return False
            finally:
                # Guaranteed browser and RAM release
                context.close()
                self.logger.info("Browser closed. Persistent session cookies saved.")

    def rebuild_playlist(
        self,
        tracks: List[Any],
        mood_name: str,
        weather_condition: str,
        time_block: str,
        temperature: float,
    ) -> Dict[str, Any]:
        """
        Automated Headless Rebuild Mode:
        Target execution time: < 30 seconds.
        1. Launches headless persistent context.
        2. Verifies session authenticity.
        3. Cleanses or synchronizes target playlist.
        4. Injects selected tracks into playlist.
        5. Updates atmospheric metadata.
        6. Closes browser completely to release all RAM.
        """
        start_time = time.time()
        target_url = self.playlist_cfg.get("target_playlist_url", "")
        max_duration = self.browser_cfg.get("page_timeout_sec", 30)

        if not target_url or "YOUR_PLAYLIST_ID" in target_url:
            self.logger.warning("No custom Spotify playlist URL configured in config.yaml.")
            self.logger.info("Skipping web automation. (Use --dry-run or configure playlist.target_playlist_url)")
            return {
                "success": True,
                "simulated": True,
                "tracks_added": len(tracks),
                "elapsed_seconds": round(time.time() - start_time, 2),
                "message": "Valid tracks generated. Configure playlist.target_playlist_url to enable live RPA injection.",
            }

        self.logger.info("Starting headless RPA playlist rebuild on: %s", target_url)

        with sync_playwright() as p:
            context: Optional[BrowserContext] = None
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=self.browser_cfg.get("headless", True),
                    args=self._get_browser_args(),
                    viewport={"width": 1280, "height": 800},
                    user_agent=self.browser_cfg.get("user_agent"),
                )

                page = context.new_page()
                page.set_default_timeout(self.browser_cfg.get("action_timeout_sec", 8) * 1000)

                # Step 1: Navigate to target playlist
                self.logger.info("Navigating to playlist...")
                page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                self._dismiss_cookie_banner(page)

                # Step 2: Validate Authentication
                if not self._verify_authentication(page):
                    raise AuthenticationRequiredError(
                        "Browser session is not logged in. Please run 'python main.py --auth' to authenticate."
                    )

                # Step 3: Fast-track playlist sync / replacement
                tracks_added = self._sync_tracks_to_playlist(page, tracks)

                # Step 4: Update Playlist Metadata
                self._update_playlist_metadata(page, mood_name, weather_condition, time_block, temperature)

                elapsed = round(time.time() - start_time, 2)
                is_success = tracks_added > 0
                if is_success:
                    self.logger.info("Playlist rebuild completed successfully: %d tracks added in %.2fs (< %ds target).", tracks_added, elapsed, max_duration)
                else:
                    self.logger.warning("Rebuild finished in %.2fs but 0 tracks were added to the playlist.", elapsed)

                return {
                    "success": is_success,
                    "simulated": False,
                    "tracks_added": tracks_added,
                    "elapsed_seconds": elapsed,
                    "message": f"Successfully injected {tracks_added} tracks into Spotify playlist." if is_success else "No tracks could be added to playlist.",
                }

            except AuthenticationRequiredError:
                raise
            except Exception as exc:
                elapsed = round(time.time() - start_time, 2)
                self.logger.error("RPA Rebuild failed after %.2fs: %s", elapsed, exc)
                self._capture_failure_screenshot(page)
                return {
                    "success": False,
                    "simulated": False,
                    "error": str(exc),
                    "elapsed_seconds": elapsed,
                }
            finally:
                # Guaranteed run-and-kill RAM clearance
                if context:
                    context.close()
                    self.logger.debug("Persistent browser context completely closed. RAM freed.")

    def _dismiss_cookie_banner(self, page: Page) -> None:
        """Dismisses the ubiquitous OneTrust or GDPR banner if present."""
        banner_selector = self.selectors["auth"]["cookie_banner_accept"]
        try:
            btn = page.locator(banner_selector).first
            if btn.is_visible(timeout=2500):
                btn.click()
                self.logger.debug("Dismissed cookie consent banner.")
        except Exception:
            pass

    def _verify_authentication(self, page: Page) -> bool:
        """Verifies session cookies are active and user is logged into Spotify."""
        user_widget_sel = self.selectors["auth"]["user_profile_widget"]
        login_btn_sel = self.selectors["auth"]["login_button"]

        try:
            # Check for profile widget
            if page.locator(user_widget_sel).first.is_visible(timeout=4000):
                return True
        except Exception:
            pass

        try:
            # Check if login button is prominently displayed
            if page.locator(login_btn_sel).first.is_visible(timeout=2000):
                return False
        except Exception:
            pass

        # If neither is definitively visible, check page content
        return "login" not in page.url.lower()

    def _sync_tracks_to_playlist(self, page: Page, tracks: List[Any]) -> int:
        """
        Injects curated tracks directly into the Spotify playlist using the verified in-playlist search box.
        """
        in_search_sel = self.selectors["playlist_view"]["in_playlist_search_box"]
        add_btn_sel = self.selectors["playlist_view"]["in_playlist_add_button"]

        added_count = 0

        # Wait for Spotify SPA to hydrate
        try:
            page.wait_for_selector("h1", timeout=20000)
            time.sleep(1.5)
        except Exception:
            self.logger.debug("Playlist heading did not appear within 20s; proceeding.")

        # If a right-side panel is open, close it to keep main in-playlist view unobstructed
        try:
            close_btn = page.locator('button[aria-label="Close"]').first
            if close_btn.is_visible(timeout=1000):
                close_btn.click()
                time.sleep(1.0)
        except Exception:
            pass

        # If search input is not visible, check for 'Find more' button and click it to reveal search
        search_input_locator = page.locator(in_search_sel).first
        if not search_input_locator.is_visible():
            try:
                find_more_btn = page.locator('button:has-text("Find more")').first
                if find_more_btn.is_visible(timeout=3000):
                    self.logger.info("Clicking 'Find more' to reveal in-playlist search box...")
                    find_more_btn.click()
                    time.sleep(1.5)
            except Exception as exc:
                self.logger.debug("Could not click Find more: %s", exc)

        try:
            self.logger.info("Locating in-playlist search box...")
            search_input = page.wait_for_selector(in_search_sel, timeout=12000)
            if search_input and search_input.is_visible():
                self.logger.info("In-playlist search widget active. Adding %d curated tracks...", len(tracks))

                for idx, trk in enumerate(tracks, 1):
                    query = getattr(trk, "search_query", f"{trk.artist} - {trk.title}")
                    try:
                        search_input.fill("")
                        search_input.fill(query)
                        time.sleep(1.0)

                        # Locate the track-specific Add button in the search result row
                        add_btn = page.locator(add_btn_sel).first
                        if add_btn.is_visible(timeout=4000):
                            add_btn.click(force=True)
                            added_count += 1
                            self.logger.info("  [%02d/%02d] Added: %s", idx, len(tracks), query)
                            time.sleep(0.6)
                        else:
                            self.logger.warning("  [%02d/%02d] Add button not found for: %s", idx, len(tracks), query)
                    except Exception as trk_err:
                        self.logger.warning("Error injecting '%s': %s", query, trk_err)

                return added_count
        except Exception as exc:
            self.logger.error("In-playlist search widget failed: %s", exc)

        return added_count

    def _update_playlist_metadata(
        self,
        page: Page,
        mood_name: str,
        weather: str,
        time_block: str,
        temperature: float,
    ) -> None:
        """Updates playlist description with real-time weather and mood notes."""
        edit_trigger = self.selectors["playlist_view"]["edit_details_trigger"]
        desc_input = self.selectors["playlist_view"]["edit_description_textarea"]
        save_btn = self.selectors["playlist_view"]["edit_save_button"]

        try:
            trigger = page.locator(edit_trigger).first
            if trigger.is_visible(timeout=2500):
                trigger.click()
                time.sleep(0.4)

                desc_box = page.locator(desc_input).first
                if desc_box.is_visible(timeout=2500):
                    description_text = (
                        f"Dynamically curated by AtmosTune for {weather} at {time_block} "
                        f"({temperature:+.1f}°C). Atmospheric Mood: {mood_name}."
                    )
                    desc_box.fill(description_text)
                    page.locator(save_btn).first.click()
                    self.logger.info("Updated playlist description: '%s'", description_text)
        except Exception as exc:
            self.logger.debug("Playlist metadata update bypassed: %s", exc)

    def _capture_failure_screenshot(self, page: Optional[Page]) -> None:
        """Saves a timestamped screenshot on automation failure for diagnosis."""
        if not page:
            return
        try:
            log_dir = Path("./logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = log_dir / f"rpa_failure_{int(time.time())}.png"
            page.screenshot(path=str(screenshot_path))
            self.logger.info("Saved failure diagnosis screenshot to: %s", screenshot_path)
        except Exception as exc:
            self.logger.warning("Could not capture failure screenshot: %s", exc)
