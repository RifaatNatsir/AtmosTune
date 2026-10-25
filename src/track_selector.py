"""
AtmosTune Smart Track Selector Module.

Features:
1. Exponential Time-Decay anti-fatigue algorithm to prevent track repetition.
2. Gaussian (Bell-Curve) energy and valence distance scoring.
3. Multi-factor genre and contextual tag affinity weighting.
4. Intelligent playlist sequencing with Bell-Curve narrative arc (Intro -> Peak -> Outro).
5. State persistence updating last_played timestamps and play counts.
"""

from __future__ import annotations

import json
import math
import time
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Track:
    """Represents a curated audio track with sonic and contextual attributes."""
    id: str
    title: str
    artist: str
    album: str
    genre: str
    energy: float
    valence: float
    tempo_bpm: int
    tags: List[str]
    search_query: str
    duration_sec: int
    last_played_timestamp: Optional[float] = None
    play_count: int = 0
    calculated_score: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Track:
        return cls(
            id=data["id"],
            title=data["title"],
            artist=data["artist"],
            album=data.get("album", "Single"),
            genre=data.get("genre", "ambient"),
            energy=float(data.get("energy", 0.5)),
            valence=float(data.get("valence", 0.5)),
            tempo_bpm=int(data.get("tempo_bpm", 100)),
            tags=data.get("tags", []),
            search_query=data.get("search_query", f"{data['artist']} - {data['title']}"),
            duration_sec=int(data.get("duration_sec", 180)),
            last_played_timestamp=data.get("last_played_timestamp"),
            play_count=int(data.get("play_count", 0)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SmartTrackSelector:
    """
    Selects and sequences tracks based on atmospheric criteria, exponential fatigue decay,
    and bell-curve narrative energy shaping.
    """

    def __init__(
        self,
        tracks_db_path: str = "./data/tracks_db.json",
        config: Optional[Dict[str, Any]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.tracks_db_path = Path(tracks_db_path)
        self.config = config or {}
        self.logger = logger or logging.getLogger("AtmosTune.TrackSelector")
        self.playlist_cfg = self.config.get("playlist", {})
        self.tracks: List[Track] = self._load_tracks()

    def _load_tracks(self) -> List[Track]:
        """Loads and parses candidate tracks from persistent JSON database."""
        if not self.tracks_db_path.exists():
            self.logger.error("Tracks database file not found at %s", self.tracks_db_path)
            return []

        try:
            with open(self.tracks_db_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            return [Track.from_dict(item) for item in raw_data]
        except Exception as exc:
            self.logger.error("Failed to load tracks database: %s", exc)
            return []

    def save_tracks(self) -> None:
        """Persists updated track play counts and timestamps to disk."""
        try:
            with open(self.tracks_db_path, "w", encoding="utf-8") as f:
                json.dump([t.to_dict() for t in self.tracks], f, indent=2)
            self.logger.debug("Saved %d tracks to %s", len(self.tracks), self.tracks_db_path)
        except Exception as exc:
            self.logger.error("Failed to save tracks database: %s", exc)

    @staticmethod
    def calculate_exponential_decay(
        last_played: Optional[float],
        half_life_hours: float = 24.0,
        current_time: Optional[float] = None,
    ) -> float:
        """
        Anti-fatigue decay function:
        Score multiplier = 1.0 - e^(-(delta_t) / tau)
        Returns values in (0.0, 1.0]. Recently played tracks are heavily penalized.
        """
        if last_played is None:
            return 1.0  # Never played, zero fatigue

        now = current_time or time.time()
        elapsed_seconds = max(0.0, now - last_played)
        elapsed_hours = elapsed_seconds / 3600.0

        # Convert half-life to tau (tau = half_life / ln(2))
        tau = half_life_hours / math.log(2.0)
        fatigue_recovery = 1.0 - math.exp(-elapsed_hours / tau)

        # Floor at 0.05 to maintain small non-zero probability
        return max(0.05, fatigue_recovery)

    @staticmethod
    def gaussian_similarity(actual: float, target: float, sigma: float = 0.20) -> float:
        """Computes Bell-Curve similarity between [0.0, 1.0] attributes."""
        diff = actual - target
        return math.exp(- (diff ** 2) / (2.0 * (sigma ** 2)))

    def score_track(
        self,
        track: Track,
        target_energy: float,
        target_valence: float,
        preferred_genres: List[str],
        weather_condition: str,
        current_time: Optional[float] = None,
    ) -> float:
        """
        Computes composite multi-factor fitness score:
        Composite = [wE * Gaussian(Energy) + wV * Gaussian(Valence)] * GenreBonus * TagBonus * Decay
        """
        sigma = self.playlist_cfg.get("curve_sigma", 0.18)
        energy_sim = self.gaussian_similarity(track.energy, target_energy, sigma=sigma)
        valence_sim = self.gaussian_similarity(track.valence, target_valence, sigma=sigma + 0.05)

        base_affinity = (0.55 * energy_sim) + (0.45 * valence_sim)

        # Genre alignment bonus
        genre_bonus = 1.0
        norm_pref = [g.lower() for g in preferred_genres]
        if track.genre.lower() in norm_pref:
            genre_bonus = 1.30
        elif any(pref in track.genre.lower() for pref in norm_pref):
            genre_bonus = 1.15

        # Atmospheric tag alignment bonus
        tag_bonus = 1.0
        cond_lower = weather_condition.lower()
        if any(cond_lower in tag.lower() for tag in track.tags):
            tag_bonus = 1.20

        # Anti-fatigue time decay multiplier
        decay_factor = self.calculate_exponential_decay(
            track.last_played_timestamp,
            half_life_hours=24.0,
            current_time=current_time,
        )

        # Minor penalty for high total play count
        count_dampener = 1.0 / (1.0 + 0.05 * track.play_count)

        total_score = base_affinity * genre_bonus * tag_bonus * decay_factor * count_dampener
        track.calculated_score = round(total_score, 4)
        return total_score

    def select_tracks(
        self,
        target_mood: Dict[str, Any],
        weather_condition: str,
        count: Optional[int] = None,
        update_state: bool = True,
    ) -> List[Track]:
        """
        Selects top candidate tracks and sequences them according to the configured energy curve.
        """
        if not self.tracks:
            self.logger.warning("No tracks available in database.")
            return []

        target_count = count or self.playlist_cfg.get("max_tracks", 10)
        target_count = min(target_count, len(self.tracks))

        target_energy = float(target_mood.get("target_energy", 0.5))
        target_valence = float(target_mood.get("target_valence", 0.5))
        preferred_genres = target_mood.get("preferred_genres", [])

        now = time.time()
        for track in self.tracks:
            self.score_track(
                track=track,
                target_energy=target_energy,
                target_valence=target_valence,
                preferred_genres=preferred_genres,
                weather_condition=weather_condition,
                current_time=now,
            )

        # Sort candidate pool descending by composite fitness score
        pool = sorted(self.tracks, key=lambda t: t.calculated_score, reverse=True)
        top_candidates = pool[:target_count]

        # Sequence tracks along an intentional energy progression curve
        curve_type = self.playlist_cfg.get("curve_type", "bell_curve")
        sequenced = self._apply_narrative_curve(top_candidates, curve_type)

        if update_state:
            # Update play timestamps and counts
            for track in sequenced:
                track.last_played_timestamp = now
                track.play_count += 1
            self.save_tracks()

        self.logger.info(
            "Selected %d tracks for mood '%s' (curve: %s):",
            len(sequenced),
            target_mood.get("mood_name", "Adaptive"),
            curve_type,
        )
        for i, trk in enumerate(sequenced, 1):
            self.logger.info(
                "  [%02d] %s - %s (E: %.2f, V: %.2f, Fit: %.3f)",
                i,
                trk.artist,
                trk.title,
                trk.energy,
                trk.valence,
                trk.calculated_score,
            )

        return sequenced

    def _apply_narrative_curve(self, tracks: List[Track], curve_type: str) -> List[Track]:
        """
        Arranges tracks into an auditory narrative curve.
        'bell_curve': Sorts by energy so lower energy tracks bookend the playlist,
                      while peak energy sits at the 50-60% mark.
        'ascending': Gradually climbs from gentlest to highest energy.
        'steady': Maintains sorted fitness order.
        """
        if len(tracks) <= 2:
            return tracks

        if curve_type == "ascending":
            return sorted(tracks, key=lambda t: t.energy)

        if curve_type == "bell_curve":
            # Sort by energy ascending
            sorted_by_energy = sorted(tracks, key=lambda t: t.energy)
            left_half: List[Track] = []
            right_half: List[Track] = []

            # Distribute alternating items to create a symmetric Bell Curve:
            # lower energies on outside, peak energies in center
            for idx, trk in enumerate(sorted_by_energy):
                if idx % 2 == 0:
                    left_half.append(trk)
                else:
                    right_half.insert(0, trk)

            return left_half + right_half

        # Default: Return as sorted by fitness
        return tracks
