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
from typing import Any, Dict, List, Optional, Callable, Set

import telebot
from telebot import types

from src.context_fetcher import ContextFetcher, WeatherTimeContext
from src.track_selector import SmartTrackSelector, Track
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
        self._staged_sessions: Dict[int, Dict[str, Any]] = {}
        self._staged_lock = threading.Lock()
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

    def _edit_or_send(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[types.InlineKeyboardMarkup] = None,
        message_id: Optional[int] = None,
    ) -> Any:
        """Edits an existing inline message in-place to prevent chat spam, or sends fresh message if absent."""
        if message_id:
            try:
                return self.bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=reply_markup,
                    parse_mode="Markdown",
                )
            except Exception as exc:
                self.logger.debug("In-place message edit bypassed (%s), sending fresh message.", exc)

        return self.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )

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

    def _get_staged_keyboard(self, track_count: int) -> types.InlineKeyboardMarkup:
        """
        Creates interactive preview keyboard with numbered elimination buttons
        in 5 columns per row, plus Confirmation, Reroll, and Cancel buttons.
        """
        keyboard = types.InlineKeyboardMarkup()
        row: List[types.InlineKeyboardButton] = []
        for i in range(1, track_count + 1):
            row.append(types.InlineKeyboardButton(f"❌ {i}", callback_data=f"swap_track:{i}"))
            if len(row) == 5:
                keyboard.row(*row)
                row = []
        if row:
            keyboard.row(*row)

        keyboard.row(
            types.InlineKeyboardButton("🚀 Setuju & Update ke Spotify", callback_data="confirm_staged")
        )
        keyboard.row(
            types.InlineKeyboardButton("🔄 Acak Ulang", callback_data="reroll_staged"),
            types.InlineKeyboardButton("🔙 Batal", callback_data="cancel_staged"),
        )
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
            self._stage_and_preview(message.chat.id)

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
                self._stage_and_preview(message.chat.id, tag_filter=tag)
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
                self._stage_and_preview(message.chat.id, genre_filter=genre_name)
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
            self._stage_and_preview(message.chat.id)

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
                self._stage_and_preview(message.chat.id, mood_override=mood_name)
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
            msg_id = call.message.message_id
            if not self._is_authorized(chat_id):
                self._send_unauthorized_msg(chat_id)
                self.bot.answer_callback_query(call.id)
                return

            if call.data == "btn_rebuild":
                self.bot.answer_callback_query(call.id)
                self._stage_and_preview(chat_id, message_id=msg_id)
            elif call.data == "btn_weather":
                self.bot.answer_callback_query(call.id)
                self._send_weather_info(chat_id, message_id=msg_id)
            elif call.data == "btn_hits_menu":
                self.bot.answer_callback_query(call.id)
                self._edit_or_send(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="🔥 *Pilih Tangga Lagu Hits Terpopuler:*\n_Pilih kategori hits yang ingin disinkronkan ke Spotify:_",
                    reply_markup=self._get_hits_keyboard(),
                )
            elif call.data == "btn_genre_menu":
                self.bot.answer_callback_query(call.id)
                self._edit_or_send(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="🎸 *Pilih Genre Musik yang Ingin Didengarkan:*\n_Lagu akan disesuaikan dengan cuaca dan genre pilihan Anda:_",
                    reply_markup=self._get_genre_keyboard(),
                )
            elif call.data.startswith("set_hits:"):
                self.bot.answer_callback_query(call.id)
                hit_type = call.data.split(":", 1)[1]
                tag = "hits_indo" if hit_type in ("indo", "indopop") else ("hits_global" if hit_type == "global" else hit_type)
                self._stage_and_preview(chat_id, tag_filter=tag, message_id=msg_id)
            elif call.data.startswith("set_genre:"):
                self.bot.answer_callback_query(call.id)
                genre_name = call.data.split(":", 1)[1]
                self._stage_and_preview(chat_id, genre_filter=genre_name, message_id=msg_id)
            elif call.data == "btn_preview":
                self.bot.answer_callback_query(call.id)
                self._stage_and_preview(chat_id, message_id=msg_id)
            elif call.data == "btn_status":
                self.bot.answer_callback_query(call.id)
                self._send_status(chat_id, message_id=msg_id)
            elif call.data == "btn_mood_menu":
                self.bot.answer_callback_query(call.id)
                self._edit_or_send(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="🎨 *Select Target Atmosphere:*\n_Pilih suasana hati yang ingin Anda bangun:_",
                    reply_markup=self._get_mood_keyboard(),
                )
            elif call.data.startswith("set_mood:"):
                self.bot.answer_callback_query(call.id)
                mood_name = call.data.split(":", 1)[1]
                self._stage_and_preview(chat_id, mood_override=mood_name, message_id=msg_id)
            elif call.data.startswith("swap_track:"):
                idx_str = call.data.split(":", 1)[1]
                self._handle_swap_track(call, idx_str)
            elif call.data == "confirm_staged":
                self._handle_confirm_staged(call)
            elif call.data == "reroll_staged":
                self._handle_reroll_staged(call)
            elif call.data == "cancel_staged":
                self._handle_cancel_staged(call)
            elif call.data == "btn_clear_prompt":
                self.bot.answer_callback_query(call.id)
                self._send_clear_prompt(chat_id, message_id=msg_id)
            elif call.data == "btn_clear_confirm":
                self.bot.answer_callback_query(call.id)
                self._trigger_clear_async(chat_id, message_id=msg_id)
            elif call.data == "btn_clear_cancel":
                self.bot.answer_callback_query(call.id)
                self._edit_or_send(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text="❌ *Clear operation cancelled.*\n\nKembali ke menu utama:",
                    reply_markup=self._get_main_keyboard(),
                )
            elif call.data == "btn_back":
                self.bot.answer_callback_query(call.id)
                welcome_text = (
                    "👋 *AtmosTune Dashboard*\n\n"
                    "Control your Spotify atmospheric playlist dynamically based on real-time local weather & time of day.\n\n"
                    "Select an option below:"
                )
                self._edit_or_send(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=welcome_text,
                    reply_markup=self._get_main_keyboard(),
                )

    def _send_weather_info(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Fetches live meteorological context and sends formatted status in-place."""
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
            self._edit_or_send(chat_id=chat_id, text=msg, reply_markup=self._get_main_keyboard(), message_id=message_id)
        except Exception as exc:
            self.logger.error("Failed to fetch weather for Telegram: %s", exc)
            self._edit_or_send(chat_id=chat_id, text=f"⚠️ Error querying weather: `{exc}`", message_id=message_id)

    def _format_staged_message(self, session: Dict[str, Any]) -> str:
        """Formats the 20-song staging preview with atmospheric metadata and swap instructions."""
        mood_name = session.get("mood_name", "Atmospheric Sound")
        weather = session.get("weather", "Clear")
        temp = session.get("temperature", 25.0)
        time_block = session.get("time_block", "now")
        tracks = session.get("staged_tracks", [])
        matched_mood = session.get("matched_mood", {})
        target_e = matched_mood.get("target_energy", 0.5)
        target_v = matched_mood.get("target_valence", 0.5)

        filter_desc = ""
        if session.get("tag_filter") == "hits_indo":
            filter_desc = " • *Kategori*: `Top Hits Indonesia` 🇮🇩"
        elif session.get("tag_filter") == "hits_global":
            filter_desc = " • *Kategori*: `Global Top Hits` 🌍"
        elif session.get("genre_filter"):
            filter_desc = f" • *Genre*: `{session['genre_filter'].title()}` 🎸"
        elif session.get("mood_override"):
            filter_desc = f" • *Mood Override*: `{session['mood_override']}` 🎨"

        lines = [
            "🎧 *PREVIEW TRACKS SEBELUM UPDATE SPOTIFY*\n",
            f"🎨 *Tema / Mood*: *{mood_name}*{filter_desc}",
            f"🌦️ *Atmosphere*: `{weather}` ({temp:+.1f}°C) • `{time_block}`",
            f"🎯 *Target*: Energy `{target_e}` | Valence `{target_v}`\n",
            f"📋 *Daftar {len(tracks)} Lagu yang Akan Dimasukkan:*",
        ]

        for i, trk in enumerate(tracks, 1):
            lines.append(f"`{i:02d}.` *{trk.artist}* - {trk.title} `[E: {trk.energy:.2f}]`")

        lines.extend([
            "\n━━━━━━━━━━━━━━━━━━━━━",
            "💡 *Mau eliminasi/ganti lagu?*",
            f"_Klik tombol_ `❌ 1` _sampai_ `❌ {len(tracks)}` _di bawah untuk menggantinya dengan lagu rekomendasi lain secara instan._\n",
            "🚀 *Sudah setuju dengan pilihan di atas?*",
            "_Klik tombol_ *'🚀 Setuju & Update ke Spotify'* _untuk memasukkannya ke playlist Spotify Anda._",
        ])

        return "\n".join(lines)

    def _stage_and_preview(
        self,
        chat_id: int,
        mood_override: Optional[str] = None,
        genre_filter: Optional[str] = None,
        tag_filter: Optional[str] = None,
        message_id: Optional[int] = None,
    ) -> None:
        """
        Stages candidate tracks for preview & user elimination before committing to Spotify.
        """
        try:
            fetcher = ContextFetcher(config=self.config, logger=self.logger)
            ctx: WeatherTimeContext = fetcher.get_current_context()

            matched_mood = ctx.matched_mood
            if mood_override:
                found_mood = None
                for cond, blocks in self.config.get("mood_matrix", {}).items():
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

            selector = SmartTrackSelector(
                tracks_db_path="./data/tracks_db.json",
                config=self.config,
                logger=self.logger,
            )
            tracks = selector.select_tracks(
                target_mood=matched_mood,
                weather_condition=ctx.condition,
                update_state=False,
                genre_filter=genre_filter,
                tag_filter=tag_filter,
            )

            if not tracks:
                self._edit_or_send(
                    chat_id=chat_id,
                    text="⚠️ Tidak ada lagu yang cocok ditemukan di database untuk kategori ini.",
                    reply_markup=self._get_main_keyboard(),
                    message_id=message_id,
                )
                return

            with self._staged_lock:
                self._staged_sessions[chat_id] = {
                    "mood_name": mood_name,
                    "matched_mood": matched_mood,
                    "weather": ctx.condition,
                    "temperature": ctx.temperature,
                    "time_block": ctx.time_block,
                    "genre_filter": genre_filter,
                    "tag_filter": tag_filter,
                    "mood_override": mood_override,
                    "staged_tracks": tracks,
                    "rejected_track_ids": set(),
                }
                session = self._staged_sessions[chat_id]

            msg_text = self._format_staged_message(session)
            keyboard = self._get_staged_keyboard(len(tracks))
            self._edit_or_send(
                chat_id=chat_id,
                text=msg_text,
                reply_markup=keyboard,
                message_id=message_id,
            )
        except Exception as exc:
            self.logger.error("Failed to stage tracks for Telegram: %s", exc)
            self._edit_or_send(
                chat_id=chat_id,
                text=f"⚠️ Gagal menyiapkan preview lagu: `{exc}`",
                reply_markup=self._get_main_keyboard(),
                message_id=message_id,
            )

    def _handle_swap_track(self, call: types.CallbackQuery, idx_str: str) -> None:
        """Eliminates a specific track and replaces it in-place with another candidate."""
        chat_id = call.message.chat.id
        msg_id = call.message.message_id
        try:
            track_idx = int(idx_str) - 1
        except ValueError:
            self.bot.answer_callback_query(call.id, "Index lagu tidak valid!")
            return

        with self._staged_lock:
            session = self._staged_sessions.get(chat_id)
            if not session or not session.get("staged_tracks"):
                self.bot.answer_callback_query(call.id, "Sesi preview telah kedaluwarsa. Silakan pilih kembali dari menu.")
                return

            staged_tracks = session["staged_tracks"]
            if track_idx < 0 or track_idx >= len(staged_tracks):
                self.bot.answer_callback_query(call.id, "Nomor lagu di luar batas!")
                return

            old_track = staged_tracks[track_idx]
            session["rejected_track_ids"].add(old_track.id)

            selector = SmartTrackSelector(
                tracks_db_path="./data/tracks_db.json",
                config=self.config,
                logger=self.logger,
            )

            other_tracks = [t for i, t in enumerate(staged_tracks) if i != track_idx]

            new_track = selector.get_replacement_track(
                target_mood=session["matched_mood"],
                weather_condition=session["weather"],
                current_staged_tracks=other_tracks,
                excluded_track_ids=session["rejected_track_ids"],
                genre_filter=session["genre_filter"],
                tag_filter=session["tag_filter"],
            )

            if not new_track:
                self.bot.answer_callback_query(call.id, "⚠️ Tidak ada lagu pengganti lain yang tersedia di database!", show_alert=True)
                return

            staged_tracks[track_idx] = new_track
            session["staged_tracks"] = staged_tracks

        self.bot.answer_callback_query(
            call.id,
            f"✅ Lagu #{idx_str} diganti:\n{new_track.artist} - {new_track.title}",
        )

        msg_text = self._format_staged_message(session)
        keyboard = self._get_staged_keyboard(len(staged_tracks))
        self._edit_or_send(
            chat_id=chat_id,
            text=msg_text,
            reply_markup=keyboard,
            message_id=msg_id,
        )

    def _handle_confirm_staged(self, call: types.CallbackQuery) -> None:
        """Executes Spotify update once user confirms approval of the staged tracks."""
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        with self._staged_lock:
            session = self._staged_sessions.pop(chat_id, None)

        if not session or not session.get("staged_tracks"):
            self.bot.answer_callback_query(call.id, "Sesi preview tidak ditemukan. Silakan pilih kembali dari menu.", show_alert=True)
            return

        self.bot.answer_callback_query(call.id, "🚀 Menjalankan sinkronisasi Spotify...")

        staged_tracks = session["staged_tracks"]
        mood_override = session.get("mood_override")
        genre_filter = session.get("genre_filter")
        tag_filter = session.get("tag_filter")

        self._trigger_rebuild_async(
            chat_id=chat_id,
            mood_override=mood_override,
            genre_filter=genre_filter,
            tag_filter=tag_filter,
            tracks_override=staged_tracks,
            message_id=msg_id,
        )

    def _handle_reroll_staged(self, call: types.CallbackQuery) -> None:
        """Re-shuffles and re-selects candidate tracks."""
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        with self._staged_lock:
            session = self._staged_sessions.get(chat_id)

        if not session:
            self.bot.answer_callback_query(call.id, "Sesi preview tidak ditemukan.")
            return

        self.bot.answer_callback_query(call.id, "🔄 Mengacak ulang daftar lagu...")

        self._stage_and_preview(
            chat_id=chat_id,
            mood_override=session.get("mood_override"),
            genre_filter=session.get("genre_filter"),
            tag_filter=session.get("tag_filter"),
            message_id=msg_id,
        )

    def _handle_cancel_staged(self, call: types.CallbackQuery) -> None:
        """Cancels staging session and returns to dashboard."""
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        with self._staged_lock:
            self._staged_sessions.pop(chat_id, None)

        self.bot.answer_callback_query(call.id, "Operasi dibatalkan.")
        welcome_text = (
            "👋 *AtmosTune Dashboard*\n\n"
            "Control your Spotify atmospheric playlist dynamically based on real-time local weather & time of day.\n\n"
            "Select an option below:"
        )
        self._edit_or_send(
            chat_id=chat_id,
            message_id=msg_id,
            text=welcome_text,
            reply_markup=self._get_main_keyboard(),
        )

    def _send_preview(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Alias to open interactive staged preview."""
        self._stage_and_preview(chat_id=chat_id, message_id=message_id)

    def _send_status(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Audits current state file and cooldown status in-place."""
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
        self._edit_or_send(chat_id=chat_id, text=msg, reply_markup=self._get_main_keyboard(), message_id=message_id)

    def _send_clear_prompt(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Sends confirmation prompt before clearing playlist in-place."""
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        btn_yes = types.InlineKeyboardButton("🗑️ Yes, Delete All", callback_data="btn_clear_confirm")
        btn_no = types.InlineKeyboardButton("❌ Cancel", callback_data="btn_clear_cancel")
        keyboard.add(btn_yes, btn_no)

        msg = (
            "⚠️ *Konfirmasi Reset Playlist*\n\n"
            "Apakah Anda yakin ingin menghapus semua lagu yang ada di playlist Spotify sekarang?\n"
            "Playlist akan dikosongkan menjadi 0 lagu."
        )
        self._edit_or_send(chat_id=chat_id, text=msg, reply_markup=keyboard, message_id=message_id)

    def _trigger_clear_async(self, chat_id: int, message_id: Optional[int] = None) -> None:
        """Spawns background worker to clear Spotify playlist in-place."""
        with self._busy_lock:
            if self._is_busy:
                self.bot.send_message(chat_id, "⏳ AtmosTune is currently executing another task. Please wait.")
                return
            self._is_busy = True

        status_text = "⏳ *Membuka headless RPA browser untuk mengosongkan playlist Spotify...*"
        status_msg = self._edit_or_send(chat_id=chat_id, text=status_text, message_id=message_id)
        target_msg_id = status_msg.message_id if status_msg else message_id

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
                        f"✅ *Playlist Reset Berhasil!*\n\n"
                        f"Berhasil menghapus `{removed}` lagu lama dalam `{elapsed:.1f}s`.\n"
                        f"Playlist Spotify sekarang sudah kosong.",
                        chat_id=chat_id,
                        message_id=target_msg_id,
                        reply_markup=self._get_main_keyboard(),
                    )
                else:
                    err = res.get("error") or res.get("message") or "Unknown error"
                    self.bot.edit_message_text(
                        f"❌ *Failed to clear playlist*: `{err}`",
                        chat_id=chat_id,
                        message_id=target_msg_id,
                        reply_markup=self._get_main_keyboard(),
                    )
            except Exception as exc:
                self.logger.error("Clear error: %s", exc)
                self.bot.edit_message_text(
                    f"❌ *Clear error*: `{exc}`",
                    chat_id=chat_id,
                    message_id=target_msg_id,
                    reply_markup=self._get_main_keyboard(),
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
        tracks_override: Optional[List[Any]] = None,
        message_id: Optional[int] = None,
    ) -> None:
        """Spawns background thread to execute full headless RPA rebuild in-place."""
        with self._busy_lock:
            if self._is_busy:
                busy_msg = "⏳ Proses lain sedang berjalan di Spotify. Mohon tunggu beberapa saat..."
                if message_id:
                    self.bot.edit_message_text(busy_msg, chat_id=chat_id, message_id=message_id, reply_markup=self._get_main_keyboard())
                else:
                    self.bot.send_message(chat_id, busy_msg)
                return
            self._is_busy = True

        if tracks_override:
            status_text = f"⏳ *Menyinkronkan {len(tracks_override)} lagu yang telah disetujui ke Spotify...*\n_Membersihkan playlist & menambahkan tracks baru..._"
        elif tag_filter == "hits_indo":
            status_text = "⏳ *Menyiapkan playlist Top Hits Indonesia...*\n_Membersihkan lagu lama & memasukkan 20 lagu hits Indonesia..._"
        elif tag_filter == "hits_global":
            status_text = "⏳ *Menyiapkan playlist Global Top Hits...*\n_Membersihkan lagu lama & memasukkan 20 lagu hits dunia..._"
        elif tag_filter == "viral":
            status_text = "⏳ *Menyiapkan playlist Viral Indonesia...*\n_Membersihkan lagu lama & memasukkan lagu viral..._"
        elif genre_filter:
            status_text = f"⏳ *Menyiapkan playlist genre '{genre_filter.title()}'...*\n_Membersihkan lagu lama & menyinkronkan ke Spotify..._"
        elif mood_override:
            status_text = f"⏳ *Menyiapkan playlist mood '{mood_override}'...*\n_Membersihkan lagu lama & menyinkronkan ke Spotify..._"
        else:
            status_text = "⏳ *Menyiapkan playlist sesuai cuaca real-time...*\n_Membersihkan lagu lama & menyinkronkan ke Spotify..._"

        status_msg = self._edit_or_send(chat_id=chat_id, text=status_text, message_id=message_id)
        target_msg_id = status_msg.message_id if status_msg else message_id

        def _worker():
            try:
                from main import run_atmostune_cycle
                res = run_atmostune_cycle(
                    config=self.config,
                    force=True,
                    mood_override=mood_override,
                    genre_filter=genre_filter,
                    tag_filter=tag_filter,
                    tracks_override=tracks_override,
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
                        f"✅ *Spotify Playlist Berhasil Disinkronkan!*\n\n"
                        f"🎨 *Tema / Mood*: *{mood_name}*\n"
                        f"🌦️ *Atmosphere*: `{weather}` ({temp:+.1f}°C) • `{block}`\n"
                        f"🎵 *Tracks*: `{tracks_count}` lagu dimasukkan dalam `{elapsed:.1f}s`\n\n"
                        f"🔗 [Buka 'MyAtmosTune' di Spotify]({playlist_url})"
                    )
                    self.bot.edit_message_text(
                        reply_text,
                        chat_id=chat_id,
                        message_id=target_msg_id,
                        reply_markup=self._get_main_keyboard(),
                    )
                else:
                    err = res.get("message") or res.get("error") or "Unknown error"
                    self.bot.edit_message_text(
                        f"❌ *Rebuild Failed*: {err}",
                        chat_id=chat_id,
                        message_id=target_msg_id,
                        reply_markup=self._get_main_keyboard(),
                    )
            except AuthenticationRequiredError:
                self.bot.edit_message_text(
                    "❌ *Authentication Required!*\n\nPlease run `python main.py --auth` on your computer to log in once.",
                    chat_id=chat_id,
                    message_id=target_msg_id,
                )
            except Exception as exc:
                self.logger.error("Rebuild error: %s", exc)
                self.bot.edit_message_text(
                    f"❌ *Critical Execution Error*: `{exc}`",
                    chat_id=chat_id,
                    message_id=target_msg_id,
                    reply_markup=self._get_main_keyboard(),
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
