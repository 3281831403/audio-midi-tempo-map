"""MIDI 变速映射包。"""

from .map import (
    TempoError,
    bpm_to_us,
    map_batch,
    normalize_events,
    scale_map,
    seconds_to_tick,
    tempo_at,
    tick_to_seconds,
    validate_map,
)

__all__ = [
    "TempoError",
    "bpm_to_us",
    "map_batch",
    "normalize_events",
    "scale_map",
    "seconds_to_tick",
    "tempo_at",
    "tick_to_seconds",
    "validate_map",
]
