"""
AtmosTune Context Fetcher Module.

Responsible for:
1. Querying real-time local weather from OpenWeatherMap API with low memory usage (<15MB RAM).
2. Graceful physics-based environmental fallback when API credentials are unset or offline.
3. Classifying local system time into semantic diurnal time blocks.
4. Matrix mapping (Weather, TimeBlock) -> Atmospheric Target Mood.
5. Evaluating state & cooldown thresholds to determine if an RPA browser run is justified.
"""

from __future__ import annotations

import os
import math
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import requests


@dataclass
class WeatherTimeContext:
    """Encapsulates evaluated real-time weather and diurnal context."""
    timestamp: str
    condition: str
    condition_description: str
    temperature: float
    feels_like: float
    humidity: int
    wind_speed: float
    time_block: str
    hour: int
    city_name: str
    matched_mood: Dict[str, Any]
    is_mock: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ContextFetcher:
    """Fetches real-time environmental data and determines if playlist reconfiguration is needed."""

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("AtmosTune.ContextFetcher")
        self.weather_cfg = config.get("weather_api", {})
        self.sched_cfg = config.get("scheduling", {})
        self.mood_matrix = config.get("mood_matrix", {})

    def get_time_block(self, current_time: Optional[datetime] = None) -> Tuple[str, int]:
        """
        Maps a datetime hour into semantic time blocks.
        Blocks: early_morning (05-08), morning (08-12), afternoon (12-17), evening (17-21), night (21-05).
        """
        now = current_time or datetime.now()
        hour = now.hour

        time_blocks = self.sched_cfg.get("time_blocks", {})
        for block_name, hours in time_blocks.items():
            start = hours.get("start", 0)
            end = hours.get("end", 24)

            # Standard range within same day (e.g. 8 to 12)
            if start < end:
                if start <= hour < end:
                    return block_name, hour
            # Overnight range wrapping around midnight (e.g. 21 to 5)
            else:
                if hour >= start or hour < end:
                    return block_name, hour

        return "night", hour

    def fetch_weather(self) -> Dict[str, Any]:
        """
        Retrieves live weather data from OpenWeatherMap API.
        Falls back to intelligent mock simulation if API key is not present or network fails.
        """
        api_key = os.getenv("OPENWEATHER_API_KEY", self.weather_cfg.get("api_key", "")).strip()
        lat = self.weather_cfg.get("lat", 40.7128)
        lon = self.weather_cfg.get("lon", -74.0060)
        units = self.weather_cfg.get("units", "metric")
        city_name = self.weather_cfg.get("city_name", "Local Station")
        timeout = self.weather_cfg.get("timeout_seconds", 5)

        if api_key and api_key != "YOUR_OPENWEATHER_API_KEY":
            try:
                url = "https://api.openweathermap.org/data/2.5/weather"
                params = {
                    "lat": lat,
                    "lon": lon,
                    "appid": api_key,
                    "units": units,
                }
                self.logger.info("Querying OpenWeatherMap API for lat=%.4f, lon=%.4f...", lat, lon)
                response = requests.get(url, params=params, timeout=timeout)
                response.raise_for_status()
                data = response.json()

                weather_main = data.get("weather", [{}])[0].get("main", "Clear")
                weather_desc = data.get("weather", [{}])[0].get("description", "clear sky")
                temp = float(data.get("main", {}).get("temp", 20.0))
                feels_like = float(data.get("main", {}).get("feels_like", temp))
                humidity = int(data.get("main", {}).get("humidity", 50))
                wind_speed = float(data.get("wind", {}).get("speed", 3.0))
                fetched_city = data.get("name", city_name)

                return {
                    "condition": weather_main,
                    "condition_description": weather_desc,
                    "temperature": round(temp, 1),
                    "feels_like": round(feels_like, 1),
                    "humidity": humidity,
                    "wind_speed": round(wind_speed, 1),
                    "city_name": fetched_city,
                    "is_mock": False,
                }
            except Exception as exc:
                self.logger.warning("OpenWeatherMap request failed (%s). Evaluating fallback...", exc)

        # Realistic diurnal + seasonal mock generator
        if self.weather_cfg.get("mock_fallback", True):
            return self._generate_environmental_fallback(lat, lon, city_name, units)

        raise RuntimeError("No valid OpenWeatherMap API key provided and mock_fallback is disabled.")

    def _generate_environmental_fallback(
        self, lat: float, lon: float, city_name: str, units: str
    ) -> Dict[str, Any]:
        """
        Generates realistic environmental readings using diurnal curves and seasonal cycles.
        Ensures AtmosTune is fully functional out-of-the-box before API key insertion.
        """
        now = datetime.now()
        day_of_year = now.timetuple().tm_yday
        hour = now.hour + (now.minute / 60.0)

        # Diurnal temperature cycle: peak around 15:00 (3 PM), trough around 05:00 (5 AM)
        diurnal_variation = math.sin((hour - 9.0) * math.pi / 12.0) * 5.0
        # Seasonal base temperature in temperate zones
        seasonal_base = 15.0 + 10.0 * math.sin((day_of_year - 80) * 2 * math.pi / 365.0)
        temp_celsius = seasonal_base + diurnal_variation

        # Determine plausible condition based on humidity/time
        conditions = ["Clear", "Clouds", "Rain", "Drizzle"]
        # Cycle conditions pseudo-deterministically using day of year + hour block
        condition_index = int((day_of_year * 3 + int(hour) // 4)) % len(conditions)
        condition = conditions[condition_index]

        if condition == "Rain":
            desc = "gentle rhythmic rainfall"
            humidity = 88
        elif condition == "Drizzle":
            desc = "light atmospheric drizzle"
            humidity = 82
        elif condition == "Clouds":
            desc = "scattered stratocumulus clouds"
            humidity = 65
        else:
            desc = "clear sunlit sky"
            humidity = 48

        temp = temp_celsius if units == "metric" else (temp_celsius * 9 / 5) + 32
        self.logger.info(
            "Using environmental simulator: %s (%.1f°%s, %s)",
            city_name,
            temp,
            "C" if units == "metric" else "F",
            condition,
        )

        return {
            "condition": condition,
            "condition_description": desc,
            "temperature": round(temp, 1),
            "feels_like": round(temp - 1.2, 1),
            "humidity": humidity,
            "wind_speed": 3.8,
            "city_name": f"{city_name} (Simulated)",
            "is_mock": True,
        }

    def match_mood(self, condition: str, time_block: str) -> Dict[str, Any]:
        """
        Correlates weather condition and diurnal time block against the configured mood matrix.
        """
        # Match condition with fallback to Default
        weather_rules = self.mood_matrix.get(condition)
        if not weather_rules:
            # Check for partial matches or fallback
            for known_condition in self.mood_matrix:
                if known_condition.lower() in condition.lower():
                    weather_rules = self.mood_matrix[known_condition]
                    break

        if not weather_rules:
            weather_rules = self.mood_matrix.get("Default", {})

        mood_data = weather_rules.get(time_block)
        if not mood_data:
            # Fallback to morning or first available block in section
            mood_data = list(weather_rules.values())[0] if weather_rules else {
                "mood_name": "Ambient Flow",
                "description": "Adaptive neutral acoustic and chill sounds",
                "target_energy": 0.50,
                "target_valence": 0.50,
                "preferred_genres": ["ambient", "lofi"],
            }

        return mood_data

    def get_current_context(self) -> WeatherTimeContext:
        """Assembles live weather and diurnal status into an evaluated context object."""
        time_block, hour = self.get_time_block()
        weather = self.fetch_weather()
        matched_mood = self.match_mood(weather["condition"], time_block)

        return WeatherTimeContext(
            timestamp=datetime.now(timezone.utc).isoformat(),
            condition=weather["condition"],
            condition_description=weather["condition_description"],
            temperature=weather["temperature"],
            feels_like=weather["feels_like"],
            humidity=weather["humidity"],
            wind_speed=weather["wind_speed"],
            time_block=time_block,
            hour=hour,
            city_name=weather["city_name"],
            matched_mood=matched_mood,
            is_mock=weather["is_mock"],
        )

    def should_trigger_update(
        self,
        current_context: WeatherTimeContext,
        state: Dict[str, Any],
        force: bool = False,
    ) -> Tuple[bool, str]:
        """
        Decision engine: checks if browser automation needs to be invoked.
        Enforces low-RAM run-and-kill pattern by skipping Playwright when conditions are static.
        """
        if force:
            return True, "Triggered via manual --force override."

        last_run = state.get("last_run_timestamp")
        if not last_run:
            return True, "Initial launch: no prior playlist state found."

        try:
            last_dt = datetime.fromisoformat(last_run)
            now_dt = datetime.now(timezone.utc)
            elapsed_minutes = (now_dt - last_dt).total_seconds() / 60.0
        except Exception:
            return True, "Unparseable prior timestamp; rebuilding state."

        cooldown = self.sched_cfg.get("cooldown_minutes", 60)
        last_condition = state.get("last_weather_condition")
        last_block = state.get("last_time_block")
        last_temp = state.get("last_temperature")

        # 1. State change: Weather condition transition
        if last_condition and current_context.condition != last_condition:
            return True, (
                f"Atmospheric transition detected: '{last_condition}' -> "
                f"'{current_context.condition}'."
            )

        # 2. State change: Diurnal time block transition
        if last_block and current_context.time_block != last_block:
            return True, (
                f"Diurnal shift detected: '{last_block}' -> '{current_context.time_block}'."
            )

        # 3. State change: Significant thermal variation
        min_temp_delta = self.sched_cfg.get("min_temperature_delta", 4.0)
        if last_temp is not None:
            delta = abs(current_context.temperature - float(last_temp))
            if delta >= min_temp_delta:
                return True, (
                    f"Thermal shift detected: {delta:.1f}° variation (>= {min_temp_delta}° threshold)."
                )

        # 4. Cooldown expiration
        if elapsed_minutes >= cooldown:
            return True, (
                f"Cooldown timer expired ({elapsed_minutes:.1f}m >= {cooldown}m limit). Periodic refresh."
            )

        # If none of the above are met, prevent browser execution to save RAM
        return False, (
            f"Conditions steady ({current_context.condition}, {current_context.time_block}). "
            f"Cooldown active ({elapsed_minutes:.1f}m / {cooldown}m). Browser execution bypassed."
        )
