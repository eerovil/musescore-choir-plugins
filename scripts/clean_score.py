from lxml import etree
from copy import deepcopy
from collections import defaultdict

INPUT_FILE = "test.musicxml"
OUTPUT_FILE = "test_split.musicxml"

# Load MusicXML file
tree = etree.parse(INPUT_FILE)
root = tree.getroot()

# Add new score-part entries for T1, T2, B1, B2
new_parts = [
    ("T1", "P1_stem_up"),
    ("T2", "P1_stem_down"),
    ("B1", "P2_stem_up"),
    ("B2", "P2_stem_down"),
]

part_list = root.find(".//part-list")
for name, part_id in new_parts:
    score_part = etree.Element("score-part", id=part_id)
    part_name = etree.SubElement(score_part, "part-name")
    part_name.text = name
    part_list.append(score_part)
    part_elem = etree.Element("part", id=part_id)
    root.append(part_elem)

split_map = {
    "P1": {"up": "P1_stem_up", "down": "P1_stem_down"},
    "P2": {"up": "P2_stem_up", "down": "P2_stem_down"},
}

def get_stem_direction(note):
    stem = note.find("stem")
    return stem.text.strip() if stem is not None else "up"

def is_middle_of_slur(note):
    notations = note.find("notations")
    if notations is not None:
        for slur in notations.findall("slur"):
            if slur.attrib.get("type") == "stop":
                return True
    return False

def convert_to_tenor_clef(attributes):
    clef = attributes.find("clef")
    if clef is not None:
        sign = clef.find("sign")
        if sign is not None and sign.text == "G":
            clef_octave = clef.find("clef-octave-change")
            if clef_octave is None:
                clef_octave = etree.SubElement(clef, "clef-octave-change")
            clef_octave.text = "-1"

# FIRST PASS: store lyrics by (part, direction, time)
lyrics_by_time = defaultdict(lambda: defaultdict(dict))

for part in root.findall("part"):
    pid = part.attrib.get("id")
    if pid not in split_map:
        continue
    time_position = 0
    for measure in part.findall("measure"):
        for el in measure:
            if el.tag == "note":
                duration_el = el.find("duration")
                duration = int(duration_el.text) if duration_el is not None else 0
                lyric = el.find("lyric")
                direction = get_stem_direction(el)
                if lyric is not None:
                    lyrics_by_time[pid][time_position][direction] = deepcopy(lyric)
                time_position += duration
            elif el.tag == "backup":
                time_position -= int(el.find("duration").text)
            elif el.tag == "forward":
                time_position += int(el.find("duration").text)

# SECOND PASS: rewrite with lyric per time and direction fallback
new_measures = defaultdict(list)

for part in root.findall("part"):
    pid = part.attrib.get("id")
    if pid not in split_map:
        continue

    time_position = 0
    for measure in part.findall("measure"):
        m_num = measure.attrib.get("number")
        m_up = etree.Element("measure", number=m_num)
        m_down = etree.Element("measure", number=m_num)

        for el in measure:
            if el.tag == "attributes":
                attr_up = deepcopy(el)
                attr_down = deepcopy(el)
                convert_to_tenor_clef(attr_up)
                convert_to_tenor_clef(attr_down)
                m_up.append(attr_up)
                m_down.append(attr_down)
            elif el.tag in ("backup", "forward"):
                dur = int(el.find("duration").text)
                time_position += dur if el.tag == "forward" else -dur
                m_up.append(deepcopy(el))
                m_down.append(deepcopy(el))
            elif el.tag == "note":
                direction = get_stem_direction(el)
                new_note = deepcopy(el)
                for sub in list(new_note):
                    if sub.tag == "lyric":
                        new_note.remove(sub)

                if not is_middle_of_slur(el):
                    lyric = lyrics_by_time.get(pid, {}).get(time_position, {}).get(direction)
                    if not lyric:
                        lyric = lyrics_by_time.get("P1", {}).get(time_position, {}).get(direction)
                    if not lyric:
                        lyric = lyrics_by_time.get(pid, {}).get(time_position, {}).get("down" if direction == "up" else "up")
                    if not lyric:
                        lyric = lyrics_by_time.get("P1", {}).get(time_position, {}).get("down" if direction == "up" else "up")
                    if lyric:
                        new_note.append(deepcopy(lyric))

                if direction == "up":
                    m_up.append(new_note)
                    if el.find("rest") is not None:
                        m_down.append(deepcopy(new_note))
                else:
                    m_down.append(new_note)
                    if el.find("rest") is not None:
                        m_up.append(deepcopy(new_note))

                duration_el = el.find("duration")
                if duration_el is not None:
                    time_position += int(duration_el.text)

        new_measures[split_map[pid]["up"]].append(m_up)
        new_measures[split_map[pid]["down"]].append(m_down)

for part in root.findall("part"):
    pid = part.attrib.get("id")
    if pid in new_measures:
        part[:] = new_measures[pid]

with open(OUTPUT_FILE, "wb") as f:
    tree.write(f, pretty_print=True, xml_declaration=True, encoding="UTF-8")

print(f"Written transformed MusicXML to {OUTPUT_FILE}")
