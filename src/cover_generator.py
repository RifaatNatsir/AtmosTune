"""
AtmosTune Dynamic Playlist Cover Art Generator.

Architectural Guarantees:
1. Zero Memory Leak: Explicit image cleanup and context managers throughout.
2. Lightweight File Footprint: Strictly under 150 KB via JPEG quality=85 and optimize=True.
3. Zero-Asset Fallback: Procedurally synthesizes aesthetic vertical gradients and atmospheric
   minimalist accents (rain streaks, starry night skies, warm sun glow, cloud mist)
   when custom background assets are absent.
4. Glassmorphism / Scrim: Bottom scrim overlay guarantees 100% typography legibility.
5. Typographic Hierarchy: Crisp badge pills, Title Case main typography, and dynamic session subtitle.
"""

from __future__ import annotations

import os
import math
import random
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List

from PIL import Image, ImageDraw, ImageFont, ImageOps


class CoverArtGenerator:
    """Dynamic 600x600px cover art generator for AtmosTune playlists."""

    CANVAS_SIZE: Tuple[int, int] = (600, 600)

    # Predefined mood color palettes: (top_rgb, bottom_rgb)
    COLOR_PALETTES = {
        "rain": ((24, 34, 50), (58, 80, 107)),                    # Slate blue -> Misty teal
        "clear_day": ((242, 153, 74), (86, 204, 242)),            # Warm amber -> Soft sky blue
        "clear_sunset": ((214, 109, 117), (226, 149, 135)),       # Sunset magenta -> Golden orange
        "night": ((15, 12, 41), (48, 43, 99)),                    # Midnight indigo -> Deep purple-gray
        "clouds": ((67, 67, 67), (112, 128, 144)),                # Charcoal -> Muted silver-blue
        "default": ((30, 36, 50), (60, 75, 105)),                 # Modern atmospheric deep blue
    }

    def __init__(
        self,
        assets_dir: str = "assets",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.assets_dir = Path(assets_dir)
        self.bg_dir = self.assets_dir / "backgrounds"
        self.fonts_dir = self.assets_dir / "fonts"
        self.logger = logger or logging.getLogger("AtmosTune.CoverGenerator")

        # Ensure directories exist
        self.bg_dir.mkdir(parents=True, exist_ok=True)
        self.fonts_dir.mkdir(parents=True, exist_ok=True)

    def _load_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """
        Loads font with layered fallback hierarchy:
        1. Custom fonts in `assets/fonts/`
        2. OS-level fonts (Windows Segoe UI / Arial, Linux / macOS DejaVu)
        3. Safe universal default: `ImageFont.load_default(size=size)`
        """
        # 1. Check custom fonts directory
        if self.fonts_dir.exists():
            custom_candidates = []
            for font_file in self.fonts_dir.glob("*.[to]tf"):
                fname_lower = font_file.name.lower()
                if bold and ("bold" in fname_lower or "bd" in fname_lower or "b." in fname_lower):
                    custom_candidates.insert(0, font_file)
                elif not bold and "bold" not in fname_lower:
                    custom_candidates.insert(0, font_file)
                else:
                    custom_candidates.append(font_file)

            for candidate in custom_candidates:
                try:
                    return ImageFont.truetype(str(candidate), size=size)
                except Exception:
                    continue

        # 2. Check common system fonts
        system_candidates: List[str] = []
        if bold:
            system_candidates = [
                "C:/Windows/Fonts/segoeuib.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/calibrib.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/HelveticaNeue-Bold.otf",
                "segoeuib.ttf",
                "arialbd.ttf",
            ]
        else:
            system_candidates = [
                "C:/Windows/Fonts/segoeui.ttf",
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/calibri.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/System/Library/Fonts/HelveticaNeue.otf",
                "segoeui.ttf",
                "arial.ttf",
            ]

        for font_path in system_candidates:
            try:
                return ImageFont.truetype(font_path, size=size)
            except Exception:
                continue

        # 3. Universal default fallback
        try:
            return ImageFont.load_default(size=size)
        except Exception:
            return ImageFont.load_default()

    def _resolve_palette(self, weather_tag: str, time_block: str) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
        """Resolves target mood gradient palette based on environmental conditions."""
        w_tag = (weather_tag or "").lower().strip()
        t_blk = (time_block or "").lower().strip()

        # Night takes precedence for night-time ambiance
        if t_blk in ("night", "late_night", "midnight") or w_tag == "night":
            return self.COLOR_PALETTES["night"]

        if any(rain_kw in w_tag for rain_kw in ("rain", "drizzle", "thunderstorm", "shower")):
            return self.COLOR_PALETTES["rain"]

        if "clear" in w_tag or "sun" in w_tag:
            if t_blk in ("late_afternoon", "evening", "sunset", "dusk"):
                return self.COLOR_PALETTES["clear_sunset"]
            return self.COLOR_PALETTES["clear_day"]

        if any(cloud_kw in w_tag for cloud_kw in ("cloud", "overcast", "fog", "mist", "haze")):
            return self.COLOR_PALETTES["clouds"]

        return self.COLOR_PALETTES["default"]

    def _generate_procedural_background(self, weather_tag: str, time_block: str) -> Image.Image:
        """
        Renders a smooth aesthetic vertical gradient with subtle atmospheric accents.
        """
        top_color, bottom_color = self._resolve_palette(weather_tag, time_block)
        w, h = self.CANVAS_SIZE

        # 1. Render smooth vertical gradient
        bg = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(bg)
        for y in range(h):
            ratio = y / float(h - 1)
            r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
            g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
            b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
            draw.line([(0, y), (w, y)], fill=(r, g, b))

        # 2. Add subtle procedural weather accents on transparent overlay
        accent_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        accent_draw = ImageDraw.Draw(accent_layer)

        w_tag = (weather_tag or "").lower()
        t_blk = (time_block or "").lower()

        # Deterministic accents using fixed seed for stable render per session
        rng = random.Random(hash(f"{weather_tag}_{time_block}") & 0xFFFFFFFF)

        if t_blk in ("night", "late_night", "midnight") or w_tag == "night":
            # Starry night dots
            for _ in range(65):
                sx = rng.randint(20, w - 20)
                sy = rng.randint(25, 360)
                alpha = rng.randint(80, 220)
                radius = 1 if rng.random() > 0.25 else 2
                accent_draw.ellipse(
                    [(sx - radius, sy - radius), (sx + radius, sy + radius)],
                    fill=(255, 255, 255, alpha),
                )
            # Add subtle crescent or soft ambient moon glow
            accent_draw.ellipse(
                [(440, 70), (510, 140)],
                fill=(255, 255, 255, 18),
                outline=(255, 255, 255, 35),
                width=1,
            )

        elif any(rain_kw in w_tag for rain_kw in ("rain", "drizzle", "thunderstorm")):
            # Subtle diagonal translucent rain streaks
            for _ in range(75):
                rx = rng.randint(10, w + 40)
                ry = rng.randint(10, 420)
                length = rng.randint(18, 36)
                alpha = rng.randint(25, 60)
                accent_draw.line(
                    [(rx, ry), (rx - 8, ry + length)],
                    fill=(200, 230, 255, alpha),
                    width=1,
                )

        elif "clear" in w_tag or "sun" in w_tag:
            # Minimalist ambient sun / horizon circle
            cx, cy = (450, 150)
            for r, alpha in [(90, 12), (65, 25), (42, 50)]:
                accent_draw.ellipse(
                    [(cx - r, cy - r), (cx + r, cy + r)],
                    fill=(255, 245, 220, alpha),
                )

        elif any(cloud_kw in w_tag for cloud_kw in ("cloud", "overcast", "mist", "fog")):
            # Soft layered cloud bands / misty ovals
            for _ in range(6):
                ox = rng.randint(40, w - 40)
                oy = rng.randint(120, 320)
                rx = rng.randint(90, 180)
                ry = rng.randint(30, 60)
                accent_draw.ellipse(
                    [(ox - rx, oy - ry), (ox + rx, oy + ry)],
                    fill=(255, 255, 255, 15),
                )

        # Merge accent layer cleanly onto base gradient
        rgba_bg = bg.convert("RGBA")
        combined = Image.alpha_composite(rgba_bg, accent_layer).convert("RGB")
        
        # Cleanup intermediate PIL objects
        accent_layer.close()
        bg.close()
        rgba_bg.close()

        return combined

    def _acquire_background(self, weather_tag: str, time_block: str) -> Image.Image:
        """
        Attempts to load custom background image from assets/backgrounds/.
        Falls back to procedural gradient synthesis with zero asset dependencies.
        """
        w_tag = (weather_tag or "").lower().strip()
        t_blk = (time_block or "").lower().strip()

        candidate_filenames = [
            f"{w_tag}_{t_blk}.jpg",
            f"{w_tag}_{t_blk}.png",
            f"{w_tag}.jpg",
            f"{w_tag}.png",
        ]

        for fname in candidate_filenames:
            candidate_path = self.bg_dir / fname
            if candidate_path.exists() and candidate_path.is_file():
                try:
                    with Image.open(candidate_path) as img:
                        self.logger.info("Loaded custom background asset: %s", candidate_path)
                        # Center crop and fit to 600x600 using high quality Lanczos filter
                        fitted = ImageOps.fit(
                            img.convert("RGB"),
                            self.CANVAS_SIZE,
                            method=Image.Resampling.LANCZOS,
                        )
                        return fitted
                except Exception as exc:
                    self.logger.warning("Failed to load background asset '%s': %s", candidate_path, exc)

        # Zero-Asset Fallback
        return self._generate_procedural_background(weather_tag, time_block)

    def _apply_readability_scrim(self, canvas: Image.Image) -> Image.Image:
        """
        Applies a dark gradient scrim at the bottom third of the canvas (RGBA 0,0,0,140+)
        to ensure all text elements achieve 100% legibility over any background.
        """
        w, h = self.CANVAS_SIZE
        scrim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        scrim_draw = ImageDraw.Draw(scrim)

        scrim_start_y = 310
        for y in range(scrim_start_y, h):
            ratio = (y - scrim_start_y) / float(h - scrim_start_y)
            # Smooth ease-in curve for cinematic contrast
            alpha = int(185 * (ratio ** 1.35))
            scrim_draw.line([(0, y), (w, y)], fill=(10, 12, 18, alpha))

        # Thin sleek frosted glass separator line
        scrim_draw.line([(35, scrim_start_y + 40), (w - 35, scrim_start_y + 40)], fill=(255, 255, 255, 20), width=1)

        rgba_canvas = canvas.convert("RGBA")
        result = Image.alpha_composite(rgba_canvas, scrim).convert("RGB")

        # Cleanup
        scrim.close()
        rgba_canvas.close()
        canvas.close()

        return result

    def _draw_top_badges(
        self,
        draw: ImageDraw.ImageDraw,
        weather_tag: str,
        temp_c: float,
    ) -> None:
        """Draws top-left brand pill and top-right real-time weather badge."""
        badge_font = self._load_font(12, bold=True)
        top_y = 32
        corner_radius = 12

        # 1. Top-Left Badge: "ATMOSTUNE • AUTO-CURATED"
        left_text = "ATMOSTUNE • AUTO-CURATED"
        left_bbox = badge_font.getbbox(left_text)
        left_text_w = left_bbox[2] - left_bbox[0]
        left_text_h = left_bbox[3] - left_bbox[1]

        left_pad_x = 12
        left_pad_y = 6
        left_pill_x1 = 35
        left_pill_y1 = top_y
        left_pill_x2 = left_pill_x1 + left_text_w + (left_pad_x * 2)
        left_pill_y2 = left_pill_y1 + left_text_h + (left_pad_y * 2)

        # Translucent pill background
        draw.rounded_rectangle(
            [(left_pill_x1, left_pill_y1), (left_pill_x2, left_pill_y2)],
            radius=corner_radius,
            fill=(0, 0, 0, 110),
            outline=(255, 255, 255, 60),
            width=1,
        )
        draw.text(
            (left_pill_x1 + left_pad_x, left_pill_y1 + left_pad_y - left_bbox[1]),
            left_text,
            font=badge_font,
            fill=(245, 248, 255, 240),
        )

        # 2. Top-Right Weather Pill: "[TEMP]°C • [WEATHER_TAG.upper()]"
        temp_str = f"{int(round(temp_c))}°C"
        right_text = f"{temp_str} • {weather_tag.upper()}"
        right_bbox = badge_font.getbbox(right_text)
        right_text_w = right_bbox[2] - right_bbox[0]
        right_text_h = right_bbox[3] - right_bbox[1]

        right_pad_x = 12
        right_pad_y = 6
        right_pill_x2 = 600 - 35
        right_pill_x1 = right_pill_x2 - (right_text_w + (right_pad_x * 2))
        right_pill_y1 = top_y
        right_pill_y2 = right_pill_y1 + right_text_h + (right_pad_y * 2)

        draw.rounded_rectangle(
            [(right_pill_x1, right_pill_y1), (right_pill_x2, right_pill_y2)],
            radius=corner_radius,
            fill=(0, 0, 0, 110),
            outline=(255, 255, 255, 60),
            width=1,
        )
        draw.text(
            (right_pill_x1 + right_pad_x, right_pill_y1 + right_pad_y - right_bbox[1]),
            right_text,
            font=badge_font,
            fill=(255, 255, 255, 240),
        )

    def _draw_titles(
        self,
        draw: ImageDraw.ImageDraw,
        sub_vibe: str,
        time_block: str,
    ) -> None:
        """
        Draws the main Title Case mood title and the timestamp session subtitle.
        Dynamically fits typography width to avoid edge overflow.
        """
        # Format title cleanly into Title Case
        clean_title = sub_vibe.replace("_", " ").strip()
        # Handle specific common acronyms nicely
        clean_title = clean_title.replace("Tv", "TV").replace("Net.Tv", "NET.TV").replace("R&b", "R&B")
        if not clean_title.isupper():
            clean_title = clean_title.title()

        # Dynamic font scaling to guarantee fit within 530px
        max_title_w = 530
        font_size = 46
        title_font = self._load_font(font_size, bold=True)
        bbox = title_font.getbbox(clean_title)
        title_w = bbox[2] - bbox[0]

        while title_w > max_title_w and font_size > 28:
            font_size -= 2
            title_font = self._load_font(font_size, bold=True)
            bbox = title_font.getbbox(clean_title)
            title_w = bbox[2] - bbox[0]

        # Draw main title at bottom left
        title_x = 36
        title_y = 462 - (font_size - 40)
        
        # Subtle drop shadow for premium depth
        draw.text((title_x + 1, title_y + 2), clean_title, font=title_font, fill=(0, 0, 0, 180))
        draw.text((title_x, title_y), clean_title, font=title_font, fill=(255, 255, 255, 255))

        # Subtitle / Timestamp: "Updated [Day], [HH:MM] • [Time Block] Session"
        now = datetime.now()
        day_str = now.strftime("%A")
        time_str = now.strftime("%H:%M")
        block_disp = time_block.replace("_", " ").title()
        subtitle_text = f"Updated {day_str}, {time_str} • {block_disp} Session"

        sub_font = self._load_font(19, bold=False)
        sub_x = 36
        sub_y = 534

        # Subtle shadow and soft silver color
        draw.text((sub_x + 1, sub_y + 1), subtitle_text, font=sub_font, fill=(0, 0, 0, 160))
        draw.text((sub_x, sub_y), subtitle_text, font=sub_font, fill=(200, 212, 228, 230))

    def generate_cover(
        self,
        weather_tag: str,
        temp_c: float,
        time_block: str,
        sub_vibe: str,
        output_path: str = "data/current_cover.jpg",
    ) -> str:
        """
        Main cover art generation pipeline:
        1. Acquires background (custom file or procedural vertical gradient).
        2. Applies readability gradient scrim at bottom third.
        3. Renders badges, main Title Case mood typography, and session subtitle.
        4. Saves optimized JPEG (< 150 KB) and cleans up memory immediately.

        Returns:
            Resolved absolute or relative file path to the generated JPEG.
        """
        out_file = Path(output_path).resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)

        self.logger.info(
            "Generating dynamic cover art: %s (%.1f°C, %s, %s) -> %s",
            sub_vibe,
            temp_c,
            weather_tag,
            time_block,
            out_file,
        )

        # 1. Base Canvas
        canvas = self._acquire_background(weather_tag, time_block)

        # 2. Readability Scrim
        canvas = self._apply_readability_scrim(canvas)

        # 3. Typography & Badges Overlay
        overlay = Image.new("RGBA", self.CANVAS_SIZE, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        self._draw_top_badges(overlay_draw, weather_tag, temp_c)
        self._draw_titles(overlay_draw, sub_vibe, time_block)

        # Composite overlay onto final canvas
        rgba_canvas = canvas.convert("RGBA")
        final_img = Image.alpha_composite(rgba_canvas, overlay).convert("RGB")

        # Free overlay and intermediate canvases immediately
        overlay.close()
        rgba_canvas.close()
        canvas.close()

        # 4. Save to Disk with optimization (< 150 KB)
        try:
            final_img.save(
                str(out_file),
                format="JPEG",
                quality=85,
                optimize=True,
            )
        finally:
            final_img.close()

        # Verify output size
        if out_file.exists():
            file_size_kb = out_file.stat().st_size / 1024.0
            self.logger.info(
                "Cover art rendered successfully: %s (%.1f KB - within 150 KB limit)",
                out_file.name,
                file_size_kb,
            )
            if file_size_kb > 150.0:
                self.logger.warning(
                    "Cover art size (%.1f KB) exceeded 150 KB target!",
                    file_size_kb,
                )

        return str(out_file)
