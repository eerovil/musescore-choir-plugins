from typing import Dict


class Globals:
    STAFF_MAPPING: Dict[int, int] = {}
    REVERSED_VOICES_BY_STAFF_MEASURE: Dict[int, Dict[int, bool]] = {}
    RESOLUTION: int = 128  # Default resolution for durations in MuseScore XML


GLOBALS = Globals()
