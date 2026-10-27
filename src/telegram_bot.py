"""
AtmosTune Telegram Bot Remote Controller.

Provides an interactive Telegram bot interface allowing the user to:
1. Rebuild playlist on-demand for current real-time weather & time of day.
2. View real-time atmospheric readings (temperature, weather, humidity, diurnal block).
3. Preview upcoming curated tracks without launching a browser.
4. Manually force a specific atmospheric mood from the matrix.
5. Check system status, execution counts, and cooldown timers.
6. Clear playlist tracks on demand.
"""

from __future__ import annotations

import os
import sys
import json
import time
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Callable

import telebot
from telebot import types

from src.context_fetcher import ContextFetcher, WeatherTimeContext
from src.track_selector import SmartTrackSelector
from src.rpa_executor import PlaywrightRPAExecutor, AuthenticationRequiredError


class AtmosTelegramBot:
    """Telegram controller bot for AtmosTune."""

    def __init__(
        self,
        config: Dict[str, Any],
        rebuild_callback: Optional[Callable[..., Dict[str, Any]]] = None,
        clear_callback: Optional[Callable[..., Dict[str, Any]]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("AtmosTune.TelegramBot")
        self.rebuild_callback = rebuild_callback
        self.clear_callback = clear_callback

        self.tg_cfg = config.get("telegram", {})
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", self.tg_cfg.get("bot_token", "")).strip()
        self.allowed_chat_ids = [int(cid) for cid in self.tg_cfg.get("allowed_chat_ids", []) if str(cid).strip().isdigit()]

        if not self.bot_token:
            raise ValueError(
                "No Telegram Bot token provided. Please set 'telegram.bot_token' in config/config.yaml "
                "or export TELEGRAM_BOT_TOKEN."
            )

        self.bot = telebot.TeleBot(self.bot_token, parse_mode="Markdown")
        self._is_busy = False
        self._busy_lock = threading.Lock()
        self._register_handlers()

    def _is_authorized(self, chat_id: int) -> bool:
        """Checks if chat ID is authorized. If whitelist is empty, unauthorized by default."""
        if not self.allowed_chat_ids:
            return False
        return chat_id in self.allowed_chat_ids

    def _send_unauthorized_msg(self, chat_id: int) -> None:
        """Sends security instruction displaying user's Chat ID for easy whitelisting."""
        msg = (
            "⛔ *Access Restricted*\n\n"
            f"Your Telegram Chat ID is: `{chat_id}`\n\n"
            "To authorize yourself to control AtmosTune, please add this ID to "
            "`config/config.yaml` under `telegram.allowed_chat_ids`:\n\n"
            "```yaml\n"
            "telegram:\n"
            f"  allowed_chat_ids: [{chat_id}]\n"
            "```\n"
            "Then restart the bot or AtmosTune."
        )
        self.bot.send_message(chat_id, msg)

    def _get_main_keyboard(self) -> types.InlineKeyboardMarkup:
        """Constructs interactive dashboard inline keyboard."""
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        btn_rebuild = types.InlineKeyboardButton("🔄 Rebuild Playlist", callback_data="btn_rebuild")
        btn_weather = types.InlineKeyboardButton("🌦️ Real Weather", callback_data="btn_weather")
        btn_hits = types.InlineKeyboardButton("🔥 Top Hits", callback_data="btn_hits_menu")
        btn_genre = types.InlineKeyboardButton("🎸 Choose Genre", callback_data="btn_genre_menu")
        btn_mood = types.InlineKeyboardButton("🎨 Choose Mood", callback_data="btn_mood_menu")
        btn_preview = types.InlineKeyboardButton("🎵 Preview Tracks", callback_data="btn_preview")
        btn_status = types.InlineKeyboardButton("📊 System Status", callback_data="btn_status")
        btn_clear = types.InlineKeyboardButton("🗑️ Clear Playlist", callback_data="btn_clear_prompt")

        keyboard.add(btn_rebuild, btn_weather)
        keyboard.add(btn_hits, btn_genre)
        keyboard.add(btn_mood, btn_preview)
        keyboard.add(btn_status, btn_clear)
        return keyboard

    def _get_mood_keyboard(self) -> types.InlineKeyboardMarkup:
        """Builds selection menu with diverse moods from the matrix."""
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        moods = [
            ("🌧️ Rainy Focus", "Rainy Day Focus"),
            ("🌆 Chill Twilight", "Chill Twilight"),
            ("🌅 Morning Rise", "Morning Acoustic Rise"),
            ("☀️ Midday Flow", "Midday Solar Flow"),
            ("☕ Cafe Lofi", "Cafe Rain Lofi"),
            ("🌃 Midnight Drift", "Midnight Reverie"),
            ("⚡ Electric Pulse", "Electric Storm Pulse"),
            ("🌫️ Amber Streetlights", "Amber Streetlights"),
        ]
        buttons = [
            types.InlineKeyboardButton(label, callback_data=f"set_mood:{name}")
            for label, name in moods
        ]
        keyboard.add(*buttons)
        keyboard.add(types.InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_back"))
        return keyboard

    def _get_hits_keyboard(self) -> types.InlineKeyboardMarkup:
        """Builds selection menu for top trending hits."""
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        hits = [
            ("🇮🇩 Top Hits Indonesia", "indo"),
            ("🌍 Global Top Hits", "global"),
            ("📈 Viral Indonesia", "viral"),
            ("✨ Indo Pop Hits", "indopop"),
        ]
        buttons = [
            types.InlineKeyboardButton(label, callback_data=f"set_hits:{name}")
            for label, name in hits
        ]
        keyboard.add(*buttons)
        keyboard.add(types.InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_back"))
        return keyboard

    def _get_genre_keyboard(self) -> types.InlineKeyboardMarkup:
        """Builds selection menu for specific musical genres."""
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        genres = [
            ("🇮🇩 Indo Pop", "indopop"),
            ("🎵 Global Pop", "pop"),
            ("🎸 Indie & Rock", "indie"),
            ("☕ Lo-Fi & Chill", "lofi"),
            ("⚡ Synthwave & EDM", "synthwave"),
            ("🌧️ Ambient & Sleep", "ambient"),
            ("🎻 Classical & Piano", "classical"),
            ("🎧 R&B / Chillwave", "r&b"),
        ]
        buttons = [
            types.InlineKeyboardButton(label, callback_data=f"set_genre:{name}")
            for label, name in genres
        ]
        keyboard.add(*buttons)
        keyboard.add(types.InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_back"))
        return keyboard

    def _register_handlers(self) -> None:
        """Registers all bot slash command handlers and inline callback queries."""

        @self.bot.message_handler(commands=["start", "help"])
        def handle_start(message: types.Message):
            chat_id = message.chat.id
            if not self._is_authorized(chat_id):
                self._send_unauthorized_msg(chat_id)
                return

            welcome_text = (
                "👋 *Welcome to AtmosTune Controller!*\n\n"
                "Control your Spotify atmospheric playlist dynamically based on real-time local weather & time of day.\n\n"
                "*Available Controls:*\n"
                "• 🔄 *Rebuild Playlist*: Headless RPA update in <30s\n"
                "• 🌦️ *Real Weather*: Live local meteorological readings\n"
                "• 🔥 *Top Hits*: Indonesian & Global trending chart toppers\n"
                "• 🎸 *Choose Genre*: Indo Pop, Pop, Indie, Lofi, Synthwave, dll.\n"
                "• 🎨 *Choose Mood*: Manually override the atmospheric mood\n"
                "• 🎵 *Preview Tracks*: Inspect next curated songs & energy\n"
                "• 📊 *System Status*: Audit cooldown & run history\n"
                "• 📥 */import <nama/url>*: Download lagu baru langsung dari Spotify\n"
                "• 🗑️ *Clear Playlist*: Empty the target Spotify playlist\n\n"
                "Use the interactive buttons below or slash commands like `/hits`, `/genre`, `/rebuild`, or `/weather`."
            )
            self.bot.send_message(chat_id, welcome_text, reply_markup=self._get_main_keyboard())

        @self.bot.message_handler(commands=["rebuild"])
        def handle_rebuild_cmd(message: types.Message):
            if not self._is_authorized(message.chat.id):
                self._send_unauthorized_msg(message.chat.id)
                return
            self._trigger_rebuild_async(message.chat.id)

        @self.bot.message_handler(commands=["weather"])
        def handle_weather_cmd(message: types.Message):
            if not self._is_authorized(message.chat.id):
                self._send_unauthorized_msg(message.chat.id)
                return
            self._send_weather_info(message.chat.id)

        @self.bot.message_handler(commands=["hits"])
        def handle_hits_cmd(message: types.Message):
            if not self._is_authorized(message.chat.id):
                self._send_unauthorized_msg(message.chat.id)
                return
            parts = message.text.split(maxsplit=1)
            if len(parts) > 1:
                hit_type = parts[1].strip().lower()
                tag = "hits_indo" if "indo" in hit_type else ("hits_global" if "global" in hit_type else hit_type)
                self._trigger_rebuild_async(message.chat.id, tag_filter=tag)
            else:
                self.bot.send_message(
                    message.chat.id,
                    "🔥 *Pilih Tangga Lagu Hits Terpopuler:*",
                    reply_markup=self._get_hits_keyboard(),
                )

        @self.bot.message_handler(commands=["genre"])
        def handle_genre_cmd(message: types.Message):
            if not self._is_authorized(message.chat.id):
                self._send_unauthorized_msg(message.chat.id)
                return
            parts = message.text.split(maxsplit=1)
            if len(parts) > 1:
                genre_name = parts[1].strip().lower()
                self._trigger_rebuild_async(message.chat.id, genre_filter=genre_name)
            else:
                self.bot.send_message(
                    message.chat.id,
                    "🎸 *Pilih Genre Musik yang Ingin Didengarkan:*",
                    reply_markup=self._get_genre_keyboard(),
                )

        @self.bot.message_handler(commands=["import"])
        def handle_import_cmd(message: types.Message):
            if not self._is_authorized(message.chat.id):
                self._send_unauthorized_msg(message.chat.id)
                return
            parts = message.text.split(maxsplit=1)
            if len(parts) > 1:
                target = parts[1].strip()
                self._trigger_import_async(message.chat.id, target)
            else:
                self.bot.send_message(
                    message.chat.id,
                    "📥 *Import Musik Baru ke Database:*\n\n"
                    "Ketik: `/import <URL Playlist/Album atau Nama Artis/Judul>`\n\n"
                    "*Contoh:*\n"
                    "• `/import Bernadya`\n"
                    "• `/import Tulus`\n"
                    "• `/import https://open.spotify.com/playlist/...`\n\n"
                    "Lagu-lagu baru akan otomatis dianalisis dan dimasukkan ke stok database AtmosTune!",
                )

        @self.bot.message_handler(commands=["preview"])
        def handle_preview_cmd(message: types.Message):
            if not self._is_authorized(message.chat.id):
                self._send_unauthorized_msg(message.chat.id)
                return
            self._send_preview(message.chat.id)

        @self.bot.message_handler(commands=["status"])
        def handle_status_cmd(message: types.Message):
            if not self._is_authorized(message.chat.id):
                self._send_unauthorized_msg(message.chat.id)
                return
            self._send_status(message.chat.id)

        @self.bot.message_handler(commands=["mood"])
        def handle_mood_cmd(message: types.Message):
            if not self._is_authorized(message.chat.id):
                self._send_unauthorized_msg(message.chat.id)
                return
            parts = message.text.split(maxsplit=1)
            if len(parts) > 1:
                mood_name = parts[1].strip()
                self._trigger_rebuild_async(message.chat.id, mood_override=mood_name)
            else:
                self.bot.send_message(
                    message.chat.id,
                    "Please specify a mood name, e.g. `/mood Chill Twilight`, or choose from the menu below:",
                    reply_markup=self._get_mood_keyboard(),
                )

        @self.bot.message_handler(commands=["clear"])
        def handle_clear_cmd(message: types.Message):
            if not self._is_authorized(message.chat.id):
                self._send_unauthorized_msg(message.chat.id)
                return
            self._send_clear_prompt(message.chat.id)

        # Inline Button Callbacks
        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_callbacks(call: types.CallbackQuery):
            chat_id = call.message.chat.id
            if not self._is_authorized(chat_id):
                self._send_unauthorized_msg(chat_id)
                self.bot.answer_callback_query(call.id)
                return

            self.bot.answer_callback_query(call.id)

            if call.data == "btn_rebuild":
                self._trigger_rebuild_async(chat_id)
            elif call.data == "btn_weather":
                self._send_weather_info(chat_id)
            elif call.data == "btn_hits_menu":
                self.bot.send_message(chat_id, "🔥 *Pilih Tangga Lagu Hits Terpopuler:*", reply_markup=self._get_hits_keyboard())
            elif call.data == "btn_genre_menu":
                self.bot.send_message(chat_id, "🎸 *Pilih Genre Musik yang Ingin Didengarkan:*", reply_markup=self._get_genre_keyboard())
            elif call.data.startswith("set_hits:"):
                hit_type = call.data.split(":", 1)[1]
                tag = "hits_indo" if hit_type in ("indo", "indopop") else ("hits_global" if hit_type == "global" else hit_type)
                self._trigger_rebuild_async(chat_id, tag_filter=tag)
            elif call.data.startswith("set_genre:"):
                genre_name = call.data.split(":", 1)[1]
                self._trigger_rebuild_async(chat_id, genre_filter=genre_name)
            elif call.data == "btn_preview":
                self._send_preview(chat_id)
            elif call.data == "btn_status":
                self._send_status(chat_id)
            elif call.data == "btn_mood_menu":
                self.bot.send_message(chat_id, "🎨 *Select Target Atmosphere:*", reply_markup=self._get_mood_keyboard())
            elif call.data.startswith("set_mood:"):
                mood_name = call.data.split(":", 1)[1]
                self._trigger_rebuild_async(chat_id, mood_override=mood_name)
            elif call.data == "btn_clear_prompt":
                self._send_clear_prompt(chat_id)
            elif call.data == "btn_clear_confirm":
                self._trigger_clear_async(chat_id)
            elif call.data == "btn_clear_cancel":
                self.bot.send_message(chat_id, "❌ Clear operation cancelled.", reply_markup=self._get_main_keyboard())
            elif call.data == "btn_back":
                self.bot.send_message(chat_id, "Main Menu:", reply_markup=self._get_main_keyboard())

    def _send_weather_info(self, chat_id: int) -> None:
        """Fetches live meteorological context and sends formatted status."""
        try:
            fetcher = ContextFetcher(config=self.config, logger=self.logger)
            ctx: WeatherTimeContext = fetcher.get_current_context()
            mood = ctx.matched_mood.get("mood_name", "Ambient Flow")
            desc = ctx.matched_mood.get("description", "")
            target_e = ctx.matched_mood.get("target_energy", 0.5)
            target_v = ctx.matched_mood.get("target_valence", 0.5)

            msg = (
                f"🌦️ *Current Atmospheric Context*\n\n"
                f"📍 *Location*: `{ctx.city_name}`\n"
                f"🌡️ *Temperature*: `{ctx.temperature:+.1f}°C` (Feels like `{ctx.feels_like:+.1f}°C`)\n"
                f"☁️ *Conditions*: `{ctx.condition}` ({ctx.condition_description})\n"
                f"💧 *Humidity*: `{ctx.humidity}%`\n"
                f"💨 *Wind*: `{ctx.wind_speed} m/s`\n"
                f"⏰ *Diurnal Block*: `{ctx.time_block}` (Hour: `{ctx.hour:02d}:00`)\n\n"
                f"🎨 *Mapped Mood*: *{mood}*\n"
                f"   _{desc}_\n"
                f"   Target Energy: `{target_e}` | Target Valence: `{target_v}`"
            )
            self.bot.send_message(chat_id, msg, reply_markup=self._get_main_keyboard())
        except Exception as exc:
            self.logger.error("Failed to fetch weather for Telegram: %s", exc)
            self.bot.send_message(chat_id, f"⚠️ Error querying weather: `{exc}`")

    def _send_preview(self, chat_id: int) -> None:
        """Computes track selection without modifying Spotify."""
        try:
            fetcher = ContextFetcher(config=self.config, logger=self.logger)
            ctx = fetcher.get_current_context()
            selector = SmartTrackSelector(
                tracks_db_path="./data/tracks_db.json",
                config=self.config,
                logger=self.logger,
            )
            tracks = selector.select_tracks(
                target_mood=ctx.matched_mood,
                weather_condition=ctx.condition,
                update_state=False,
            )

            mood = ctx.matched_mood.get("mood_name", "Atmospheric Drift")
            msg_lines = [
                f"🎵 *Preview: {mood.upper()}*",
                f"_{ctx.condition} at {ctx.time_block} ({ctx.temperature:+.1f}°C)_\n",
            ]
            for i, trk in enumerate(tracks[:15], 1):
                msg_lines.append(f"`{i:02d}.` *{trk.artist}* - {trk.title} `[E: {trk.energy:.2f}]`")

            if len(tracks) > 15:
                msg_lines.append(f"_...and {len(tracks) - 15} more tracks (Total: {len(tracks)})_")

            msg = "\n".join(msg_lines)
            self.bot.send_message(chat_id, msg, reply_markup=self._get_main_keyboard())
        except Exception as exc:
            self.logger.error("Failed to generate preview for Telegram: %s", exc)
            self.bot.send_message(chat_id, f"⚠️ Error generating preview: `{exc}`")

    def _send_status(self, chat_id: int) -> None:
        """Audits current state file and cooldown status."""
        state_path = Path("./data/state.json")
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

        last_run = state.get("last_run_timestamp", "Never")
        last_cond = state.get("last_weather_condition", "N/A")
        last_temp = state.get("last_temperature", "N/A")
        last_mood = state.get("last_mood", "N/A")
        exec_count = state.get("execution_count", 0)
        track_count = len(state.get("last_playlist_tracks", []))
        status = state.get("status", "ready")

        target_url = self.config.get("playlist", {}).get("target_playlist_url", "")
        sync_mode = self.config.get("playlist", {}).get("sync_mode", "replace")

        msg = (
            f"📊 *AtmosTune System Status*\n\n"
            f"• *Status*: `{status.upper()}`\n"
            f"• *Executions*: `{exec_count}`\n"
            f"• *Last Atmosphere*: `{last_cond}` ({last_temp}°C)\n"
            f"• *Last Mood*: *{last_mood}*\n"
            f"• *Playlist Tracks*: `{track_count}` songs (mode: `{sync_mode}`)\n"
            f"• *Last Rebuild*: `{last_run}`\n\n"
            f"🔗 [Target Spotify Playlist]({target_url})"
        )
        self.bot.send_message(chat_id, msg, reply_markup=self._get_main_keyboard())

    def _send_clear_prompt(self, chat_id: int) -> None:
        """Sends confirmation prompt before clearing playlist."""
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        btn_yes = types.InlineKeyboardButton("🗑️ Yes, Delete All", callback_data="btn_clear_confirm")
        btn_no = types.InlineKeyboardButton("❌ Cancel", callback_data="btn_clear_cancel")
        keyboard.add(btn_yes, btn_no)

        msg = (
            "⚠️ *Confirm Playlist Reset*\n\n"
            "Are you sure you want to delete all tracks currently on your Spotify playlist?\n"
            "This will clear the playlist to 0 tracks."
        )
        self.bot.send_message(chat_id, msg, reply_markup=keyboard)

    def _trigger_clear_async(self, chat_id: int) -> None:
        """Spawns background worker to clear Spotify playlist."""
        with self._busy_lock:
            if self._is_busy:
                self.bot.send_message(chat_id, "⏳ AtmosTune is currently executing another task. Please wait.")
                return
            self._is_busy = True

        status_msg = self.bot.send_message(chat_id, "⏳ Launching headless RPA browser to clear playlist...")

        def _worker():
            try:
                if self.clear_callback:
                    res = self.clear_callback(self.config, self.logger)
                else:
                    executor = PlaywrightRPAExecutor(
                        config=self.config,
                        selectors_path="./config/selectors.json",
                        logger=self.logger,
                    )
                    res = executor.clear_playlist()

                if res.get("success"):
                    removed = res.get("tracks_removed", 0)
                    elapsed = res.get("elapsed_seconds", 0)
                    self.bot.edit_message_text(
                        f"✅ *Playlist Reset Complete!*\n\n"
                        f"Removed `{removed}` tracks in `{elapsed:.1f}s`.\n"
                        f"Target playlist is now empty.",
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                        reply_markup=self._get_main_keyboard(),
                    )
                else:
                    err = res.get("error") or res.get("message") or "Unknown error"
                    self.bot.edit_message_text(
                        f"❌ *Failed to clear playlist*: `{err}`",
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                    )
            except Exception as exc:
                self.logger.error("Clear error: %s", exc)
                self.bot.edit_message_text(
                    f"❌ *Clear error*: `{exc}`",
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                )
            finally:
                with self._busy_lock:
                    self._is_busy = False

        threading.Thread(target=_worker, daemon=True).start()

    def _trigger_rebuild_async(
        self,
        chat_id: int,
        mood_override: Optional[str] = None,
        genre_filter: Optional[str] = None,
        tag_filter: Optional[str] = None,
    ) -> None:
        """Spawns background thread to execute full headless RPA rebuild."""
        with self._busy_lock:
            if self._is_busy:
                self.bot.send_message(chat_id, "⏳ Another rebuild is already in progress. Please wait a moment.")
                return
            self._is_busy = True

        if tag_filter == "hits_indo":
            status_text = "⏳ *Menyiapkan playlist Top Hits Indonesia...*"
        elif tag_filter == "hits_global":
            status_text = "⏳ *Menyiapkan playlist Global Top Hits...*"
        elif tag_filter == "viral":
            status_text = "⏳ *Menyiapkan playlist Viral Indonesia...*"
        elif genre_filter:
            status_text = f"⏳ *Menyiapkan playlist genre '{genre_filter.title()}'...*"
        elif mood_override:
            status_text = f"⏳ *Initiating Rebuild for mood '{mood_override}'...*"
        else:
            status_text = "⏳ *Initiating Rebuild for current real-time atmosphere...*"

        status_msg = self.bot.send_message(chat_id, status_text)

        def _worker():
            try:
                from main import run_atmostune_cycle
                res = run_atmostune_cycle(
                    config=self.config,
                    force=True,
                    mood_override=mood_override,
                    genre_filter=genre_filter,
                    tag_filter=tag_filter,
                    logger=self.logger,
                )

                if res.get("success"):
                    mood_name = res.get("mood_name", "Atmospheric Sound")
                    tracks_count = res.get("tracks_added", 20)
                    elapsed = res.get("elapsed_seconds", 0)
                    weather = res.get("weather", "Clear")
                    temp = res.get("temperature", 20.0)
                    block = res.get("time_block", "now")
                    playlist_url = self.config.get("playlist", {}).get("target_playlist_url", "")

                    reply_text = (
                        f"✅ *Spotify Playlist Rebuilt Successfully!*\n\n"
                        f"🎨 *Tema / Mood*: *{mood_name}*\n"
                        f"🌦️ *Atmosphere*: `{weather}` ({temp:+.1f}°C) • `{block}`\n"
                        f"🎵 *Tracks*: `{tracks_count}` tracks added in `{elapsed:.1f}s`\n\n"
                        f"🔗 [Buka 'MyAtmosTune' di Spotify]({playlist_url})"
                    )
                    self.bot.edit_message_text(
                        reply_text,
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                        reply_markup=self._get_main_keyboard(),
                    )
                else:
                    err = res.get("message") or res.get("error") or "Unknown error"
                    self.bot.edit_message_text(
                        f"❌ *Rebuild Failed*: {err}",
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                    )
            except AuthenticationRequiredError:
                self.bot.edit_message_text(
                    "❌ *Authentication Required!*\n\nPlease run `python main.py --auth` on your computer to log in once.",
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                )
            except Exception as exc:
                self.logger.error("Rebuild error: %s", exc)
                self.bot.edit_message_text(
                    f"❌ *Critical Execution Error*: `{exc}`",
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                )
            finally:
                with self._busy_lock:
                    self._is_busy = False

        threading.Thread(target=_worker, daemon=True).start()

    def _trigger_import_async(self, chat_id: int, target: str) -> None:
        """Spawns background thread to import tracks from Spotify into database."""
        status_msg = self.bot.send_message(chat_id, f"🔍 *Mencari dan mengimpor lagu untuk:* `{target}`...")

        def _worker():
            try:
                from src.spotify_importer import SpotifyImporter
                spotify_cfg = self.config.get("spotify_api", {})
                importer = SpotifyImporter(
                    client_id=spotify_cfg.get("client_id"),
                    client_secret=spotify_cfg.get("client_secret"),
                    db_path="./data/tracks_db.json",
                    logger=self.logger,
                )
                res = importer.import_from_url(target, limit=20)
                if res.get("success"):
                    added = res.get("added_count", 0)
                    total = res.get("total_in_db", 0)
                    self.bot.edit_message_text(
                        f"✅ *Import Berhasil!*\n\n"
                        f"• Berhasil menambahkan: `{added}` lagu baru\n"
                        f"• Total stok di database: `{total}` lagu\n\n"
                        f"Lagu-lagu ini sekarang siap dipilih ke playlist Spotify Anda!",
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                        reply_markup=self._get_main_keyboard(),
                    )
                else:
                    self.bot.edit_message_text(
                        f"❌ *Gagal mengimpor*: `{res.get('message', 'Target tidak ditemukan')}`",
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                    )
            except Exception as exc:
                self.logger.error("Import error: %s", exc)
                self.bot.edit_message_text(
                    f"❌ *Error saat import*: `{exc}`",
                    chat_id=chat_id,
                    message_id=status_msg.message_id,
                )

        threading.Thread(target=_worker, daemon=True).start()

    def run_polling(self) -> None:
        """Starts Telegram bot polling loop."""
        self.logger.info("Starting AtmosTune Telegram Bot polling daemon...")
        print(f"[*] Telegram Bot is online and listening. Whitelisted Chat IDs: {self.allowed_chat_ids}")
        self.bot.infinity_polling(timeout=20, long_polling_timeout=20)
