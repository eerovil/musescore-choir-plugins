#!/usr/bin/env python3
"""
Rename parts in a MuseScore .mscx file using a part string (S, A, T, B, M, W).

Part string examples:
  SSAA     -> S1, S2, A1, A2  (short) / Soprano 1, Soprano 2, Alto 1, Alto 2 (full)
  SSSSAA   -> S1-1, S1-2, S2-1, S2-2, A1, A2  (short) / Soprano 1-1, ... (full)

After renaming, ensures there is an extra staff (one more than part_string) with distOffset 50,
filled with 8th rests. If the score has more Part elements than part_string length, the last one
is used as that extra staff; otherwise one is added.

Usage:
  python rename_parts.py score.mscx SSAA
  python rename_parts.py score.mscx SSSSAA -o score_renamed.mscx
"""

import argparse
import sys
from typing import List, Tuple

from lxml import etree


# Letter -> full name (for longName)
PART_FULL_NAMES = {
    "S": "Soprano",
    "A": "Alto",
    "T": "Tenor",
    "B": "Bass",
    "M": "Men",
    "W": "Women",
}

# Letter -> short prefix (for shortName, e.g. S1-1)
PART_SHORT_PREFIX = {
    "S": "S",
    "A": "A",
    "T": "T",
    "B": "B",
    "M": "M",
    "W": "W",
}


def parse_part_string(part_string: str) -> List[str]:
    """
    Parse a part string like 'SSAA' or 'SSSSAA' into a list of letters (one per part).
    Raises ValueError on invalid characters.
    """
    part_string = part_string.strip().upper()
    if not part_string:
        raise ValueError("part_string must not be empty")
    valid = set(PART_FULL_NAMES)
    for c in part_string:
        if c not in valid:
            raise ValueError(f"Invalid part letter: {c!r}. Allowed: S, A, T, B, M, W")
    return list(part_string)


def part_names_for_run(letter: str, run_length: int) -> List[Tuple[str, str]]:
    """
    For a run of the same letter, return [(short_name, full_name), ...].
    - 1 part: S1 / Soprano 1
    - 2 parts: S1, S2 / Soprano 1, Soprano 2
    - 3+ parts: pairs with -1, -2 (S1-1, S1-2, S2-1, S2-2, ...); last can be single (S2).
    """
    prefix = PART_SHORT_PREFIX[letter]
    full_base = PART_FULL_NAMES[letter]
    result: List[Tuple[str, str]] = []
    use_pairs = run_length >= 3
    for k in range(run_length):
        if use_pairs:
            group = k // 2 + 1
            if k % 2 == 1:
                short_name = f"{prefix}{group}-2"
                full_name = f"{full_base} {group}-2"
            elif k + 1 < run_length:
                short_name = f"{prefix}{group}-1"
                full_name = f"{full_base} {group}-1"
            else:
                short_name = f"{prefix}{group}"
                full_name = f"{full_base} {group}"
        else:
            num = k + 1
            short_name = f"{prefix}{num}"
            full_name = f"{full_base} {num}"
        result.append((short_name, full_name))
    return result


def build_part_names(part_string: str) -> List[Tuple[str, str]]:
    """
    Build list of (short_name, full_name) for each part from part_string.
    E.g. 'SSSSAA' -> [(S1-1, Soprano 1-1), (S1-2, ...), (S2-1, ...), (S2-2, ...), (A1, Alto 1), (A2, Alto 2)].
    """
    letters = parse_part_string(part_string)
    names: List[Tuple[str, str]] = []
    i = 0
    while i < len(letters):
        letter = letters[i]
        run_length = 0
        while i + run_length < len(letters) and letters[i + run_length] == letter:
            run_length += 1
        names.extend(part_names_for_run(letter, run_length))
        i += run_length
    return names


def _get_measure_count(score: etree._Element) -> int:
    """Return number of Measure elements in the first Staff that has measures (under Score)."""
    for staff in score.findall("Staff"):
        if staff.find("Measure") is not None:
            return len(staff.findall("Measure"))
    return 0


def _get_time_sigs_per_measure(score: etree._Element) -> List[Tuple[int, int]]:
    """Return (sigN, sigD) for each measure from the first Staff that has measures. Default 4/4."""
    for staff in score.findall("Staff"):
        measures = staff.findall("Measure")
        if not measures:
            continue
        result: List[Tuple[int, int]] = []
        current = (4, 4)
        for meas in measures:
            voice = meas.find("voice")
            if voice is not None:
                ts = voice.find("TimeSig")
                if ts is not None:
                    sn = ts.find("sigN")
                    sd = ts.find("sigD")
                    if sn is not None and sn.text and sd is not None and sd.text:
                        try:
                            current = (int(sn.text), int(sd.text))
                        except ValueError:
                            pass
            result.append(current)
        return result
    return []


def _eighth_count(sig_n: int, sig_d: int) -> int:
    """Number of eighth notes in a measure with given time sig (e.g. 4/4 -> 8, 3/4 -> 6)."""
    return sig_n * 8 // sig_d


def _make_eighth_rest() -> etree._Element:
    rest = etree.Element("Rest")
    dt = etree.SubElement(rest, "durationType")
    dt.text = "eighth"
    return rest


def _make_measure_voice_eighth_rests(sig_n: int, sig_d: int, include_time_sig: bool) -> etree._Element:
    """Voice element with TimeSig (if include_time_sig) and the right number of eighth rests for sig_n/sig_d."""
    voice = etree.Element("voice")
    if include_time_sig:
        ts = etree.SubElement(voice, "TimeSig")
        etree.SubElement(ts, "sigN").text = str(sig_n)
        etree.SubElement(ts, "sigD").text = str(sig_d)
    for _ in range(_eighth_count(sig_n, sig_d)):
        voice.append(_make_eighth_rest())
    return voice


def _ensure_staff_has_dist_offset(staff_el: etree._Element) -> None:
    """Ensure Staff has bracket, barLineSpan, distOffset 50."""
    if staff_el.find("bracket") is None:
        b = etree.SubElement(staff_el, "bracket", attrib={"type": "-1", "span": "1", "col": "0"})
    else:
        b = staff_el.find("bracket")
        b.set("type", "-1")
        b.set("span", "1")
        b.set("col", "0")
    if staff_el.find("barLineSpan") is None:
        etree.SubElement(staff_el, "barLineSpan").text = "1"
    do = staff_el.find("distOffset")
    if do is None:
        do = etree.SubElement(staff_el, "distOffset")
    do.text = "50"


def _part_with_extra_staff(staff_id: int, track_name: str = "Click") -> etree._Element:
    """Create a Part element with one Staff (distOffset 50, 8th-rest staff) and minimal piano Instrument."""
    part = etree.Element("Part")
    staff = etree.SubElement(part, "Staff", id=str(staff_id))
    st = etree.SubElement(staff, "StaffType", group="pitched")
    etree.SubElement(st, "name").text = "stdNormal"
    etree.SubElement(staff, "bracket", attrib={"type": "-1", "span": "1", "col": "0"})
    etree.SubElement(staff, "barLineSpan").text = "1"
    etree.SubElement(staff, "distOffset").text = "50"
    etree.SubElement(part, "trackName").text = track_name
    instr = etree.SubElement(part, "Instrument", id="piano")
    etree.SubElement(instr, "longName").text = track_name
    etree.SubElement(instr, "shortName").text = "Click"
    etree.SubElement(instr, "trackName").text = track_name
    etree.SubElement(instr, "instrumentId").text = "keyboard.piano"
    etree.SubElement(instr, "minPitchP").text = "21"
    etree.SubElement(instr, "maxPitchP").text = "108"
    etree.SubElement(instr, "minPitchA").text = "21"
    etree.SubElement(instr, "maxPitchA").text = "108"
    ch = etree.SubElement(instr, "Channel")
    etree.SubElement(ch, "program", value="0")
    etree.SubElement(ch, "synti").text = "Fluid"
    return part


def _staff_content_with_rest_measures(time_sigs: List[Tuple[int, int]]) -> etree._Element:
    """Create Staff element (for Score) with one measure per time_sig, each with correct TimeSig and eighth rests."""
    staff = etree.Element("Staff", id="0")  # caller will set id
    for i, (sig_n, sig_d) in enumerate(time_sigs):
        include_ts = i == 0 or time_sigs[i] != time_sigs[i - 1]
        meas = etree.SubElement(staff, "Measure")
        meas.append(_make_measure_voice_eighth_rests(sig_n, sig_d, include_ts))
    return staff


def ensure_extra_rest_staff(score: etree._Element, n_main_parts: int) -> None:
    """
    Ensure there is an extra staff after the main parts: Staff with distOffset 50, filled with 8th rests.
    Time signatures and rest counts follow the first staff (so 3/4 gets 6 eighths, 4/4 gets 8).
    If there are more Part elements than n_main_parts, the next one is used and ensured; otherwise one is added.
    """
    parts = score.findall("Part")
    time_sigs = _get_time_sigs_per_measure(score)
    n_measures = len(time_sigs)
    if n_measures == 0:
        return
    extra_staff_id = n_main_parts + 1
    if len(parts) <= n_main_parts:
        # Add new Part (before first Staff) and new Staff under Score
        part = _part_with_extra_staff(extra_staff_id)
        first_staff_idx = None
        for i, child in enumerate(score):
            if child.tag == "Staff":
                first_staff_idx = i
                break
        if first_staff_idx is not None:
            score.insert(first_staff_idx, part)
        else:
            score.append(part)
        content_staff = _staff_content_with_rest_measures(time_sigs)
        content_staff.set("id", str(extra_staff_id))
        score.append(content_staff)
        return
    # Use existing extra part
    extra_part = parts[n_main_parts]
    staff_el = extra_part.find("Staff")
    if staff_el is not None:
        _ensure_staff_has_dist_offset(staff_el)
    # Ensure Score > Staff with id=extra_staff_id has correct time sigs and 8th rests in every measure
    content_staff = None
    for s in score.findall("Staff"):
        if s.get("id") == str(extra_staff_id):
            content_staff = s
            break
    if content_staff is None:
        content_staff = _staff_content_with_rest_measures(time_sigs)
        content_staff.set("id", str(extra_staff_id))
        score.append(content_staff)
        return
    measures = content_staff.findall("Measure")
    if len(measures) != n_measures:
        # Rebuild measures to match
        for m in list(measures):
            content_staff.remove(m)
        for i, (sig_n, sig_d) in enumerate(time_sigs):
            include_ts = i == 0 or time_sigs[i] != time_sigs[i - 1]
            meas = etree.Element("Measure")
            meas.append(_make_measure_voice_eighth_rests(sig_n, sig_d, include_ts))
            content_staff.append(meas)
    else:
        for i, meas in enumerate(measures):
            sig_n, sig_d = time_sigs[i]
            include_ts = i == 0 or time_sigs[i] != time_sigs[i - 1]
            voice = meas.find("voice")
            if voice is None:
                meas.append(_make_measure_voice_eighth_rests(sig_n, sig_d, include_ts))
            else:
                for child in list(voice):
                    voice.remove(child)
                if include_ts:
                    ts = etree.SubElement(voice, "TimeSig")
                    etree.SubElement(ts, "sigN").text = str(sig_n)
                    etree.SubElement(ts, "sigD").text = str(sig_d)
                for _ in range(_eighth_count(sig_n, sig_d)):
                    voice.append(_make_eighth_rest())


def rename_parts_in_score(root: etree._Element, part_string: str) -> None:
    """Rename Part and Instrument names in the score (in-place). Then ensure extra rest staff exists."""
    score = root if root.tag == "Score" else root.find(".//Score")
    if score is None:
        raise ValueError("No <Score> found in document")
    parts = score.findall("Part")
    names = build_part_names(part_string)
    if len(parts) < len(names):
        raise ValueError(
            f"Part string has {len(names)} parts but score has only {len(parts)} Part elements. "
            f"Use a part string of length at most {len(parts)}."
        )
    for part_el, (short_name, full_name) in zip(parts[: len(names)], names):
        # Part-level trackName
        track_el = part_el.find("trackName")
        if track_el is not None:
            track_el.text = full_name
        # Instrument: longName, shortName, trackName
        instr = part_el.find("Instrument")
        if instr is not None:
            for tag in ("longName", "shortName", "trackName"):
                el = instr.find(tag)
                if el is not None:
                    el.text = full_name if tag == "longName" else short_name if tag == "shortName" else full_name
            tn = instr.find("trackName")
            if tn is not None:
                tn.text = full_name
    ensure_extra_rest_staff(score, len(names))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rename parts in a .mscx file using a part string (S, A, T, B, M, W)."
    )
    parser.add_argument("mscx", help="Input MuseScore .mscx file")
    parser.add_argument(
        "part_string",
        help="Part letters: S=Soprano, A=Alto, T=Tenor, B=Bass, M=Men, W=Women (e.g. SSAA or SSSSAA)",
    )
    parser.add_argument("-o", "--output", help="Output .mscx file (default: overwrite input)")
    args = parser.parse_args()

    with open(args.mscx, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))

    rename_parts_in_score(root, args.part_string)

    out_path = args.output or args.mscx
    with open(out_path, "wb") as f:
        f.write(etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
