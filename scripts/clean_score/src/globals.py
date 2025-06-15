from typing import Any, Dict, List


STAFF_MAPPING: Dict[int, int] = {}
REVERSED_VOICES_BY_STAFF_MEASURE: Dict[int, Dict[int, bool]] = {}
LYRICS_BY_TIMEPOS: Dict[str, List[Dict[str, Any]]] = {}
RESOLUTION: int = 128  # Default resolution for durations in MuseScore XML
