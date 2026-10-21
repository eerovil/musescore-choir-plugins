"""Where a new part name goes in the site's voice tree (#430, stemmanauhat-cf#107).

The site keeps a tree of voices in D1 and refuses a part name in no place; the
person publishing says where each new one goes and the publish writes the rows.
D1 is the real SQLite database `FakeCloudflare` keeps, with a trimmed copy of the
site's migration 0009, so "the name is in the tree now" is a SELECT.
"""

import json

from src.song_app import publish
from src.song_app.tests.test_publish import (CONFIG, FakeCloudflare, _bundle,  # noqa: F401
                                             _job_done, _publish_client, app_song)

# stemmanauhat-cf migrations/0009_voices.sql, its tables and a few of its rows.
TREE = """
CREATE TABLE voices (id TEXT PRIMARY KEY, parent TEXT, name_fi TEXT NOT NULL,
                     name_en TEXT NOT NULL, names TEXT NOT NULL);
INSERT INTO voices VALUES
  ('men', NULL, 'Miehet', 'Men', '["Miehet", "Men"]'),
  ('T', 'men', 'Tenori', 'Tenor', '["Tenori", "Tenor", "T"]'),
  ('T1', 'T', 'Tenori 1', 'Tenor 1', '["Tenori 1", "Tenor 1", "T1"]'),
  ('T1u', 'T1', 'Tenori 1 ylä', 'Upper tenor 1', '["Tenori 1 ylä", "Upper T1"]'),
  ('B', 'men', 'Basso', 'Bass', '["Basso", "Bass", "B"]'),
  ('B1', 'B', 'Basso 1', 'Bass 1', '["Basso 1", "Bass 1", "B1"]'),
  ('B2', 'B', 'Basso 2', 'Bass 2', '["Basso 2", "Bass 2", "B2"]');
CREATE TABLE unplaced_parts (name TEXT PRIMARY KEY);
INSERT INTO unplaced_parts VALUES ('Solo');
"""


def _with_tree(cf):
    cf.db.executescript(TREE)
    return cf


def _tree(cf):
    return publish.voice_tree(publish.Cloudflare(CONFIG, cf))


def _names(cf, vid):
    return json.loads(cf.db.execute("SELECT names FROM voices WHERE id = ?", [vid]).fetchone()[0])


def test_a_name_is_known_ignoring_case_and_spaces_in_no_place_or_as_a_b_part():
    tree = _tree(_with_tree(FakeCloudflare()))
    assert publish.knows_name(tree, "tenori 1") and publish.knows_name(tree, "B 2")
    assert publish.knows_name(tree, "solo")
    # A b-part counts when the name without the "b" is above the fourth level ...
    assert publish.knows_name(tree, "B1b") and publish.knows_name(tree, "T1b")
    # ... and not below it, or when that name is unknown too.
    assert not publish.knows_name(tree, "Upper T1b")
    assert not publish.knows_name(tree, "Bassi") and not publish.knows_name(tree, "Kertojab")
    assert publish.unknown_names(tree, ["T1", "Bassi", "bassi", "Kertoja"]) == ["Bassi", "Kertoja"]


def test_the_places_are_offered_in_tree_order_and_the_fourth_level_holds_nothing_new():
    places = publish.tree_places(_tree(_with_tree(FakeCloudflare())))
    assert [(p["id"], p["depth"]) for p in places] == [
        ("men", 1), ("T", 2), ("T1", 3), ("T1u", 4), ("B", 2), ("B1", 3), ("B2", 3)]
    assert [p["id"] for p in places if not p["can_hold"]] == ["T1u"]
    assert places[0]["name"] == "Miehet"


def test_a_site_without_the_tree_is_published_to_as_before(tmp_path):
    cf = FakeCloudflare()
    assert _tree(cf) is None
    publish.publish(slug="laulu", title="Laulu", choir="jm", bundle=_bundle(tmp_path, ("Bassi",)),
                    client=publish.Cloudflare(CONFIG, cf))
    assert cf.listed("jm")


def test_an_unknown_name_with_no_place_stops_the_publish_before_anything_goes_up(tmp_path):
    cf = _with_tree(FakeCloudflare())
    try:
        publish.publish(slug="laulu", title="Laulu", choir="jm",
                        bundle=_bundle(tmp_path, ("T1", "Bassi", "Kertoja")),
                        client=publish.Cloudflare(CONFIG, cf),
                        places=[{"name": "Bassi", "id": "B"}])
    except publish.PublishError as exc:
        assert "Kertoja" in str(exc) and "Bassi" not in str(exc)
    else:
        raise AssertionError("published a part name the tree does not know")
    assert cf.objects == {} and cf.listed("jm") == []
    assert _names(cf, "B") == ["Basso", "Bass", "B"]


def test_each_kind_of_place_writes_its_row_and_the_next_publish_needs_none(tmp_path):
    cf = _with_tree(FakeCloudflare())
    log = []
    publish.publish(slug="laulu", title="Laulu", choir="jm",
                    bundle=_bundle(tmp_path, ("T1", "Bassi", "Basso 4", "Kertoja")),
                    client=publish.Cloudflare(CONFIG, cf), log=log.append,
                    places=[{"name": "Bassi", "id": "B"}, {"name": "Basso 4", "parent": "B"},
                            {"name": "Kertoja", "id": "-"}, {"name": "T1", "id": "T"}])
    assert _names(cf, "B") == ["Basso", "Bass", "B", "Bassi"]
    assert cf.db.execute("SELECT id, parent, name_fi, name_en, names FROM voices "
                         "WHERE id = 'Basso 4'").fetchone() == (
        "Basso 4", "B", "Basso 4", "Basso 4", '["Basso 4"]')
    assert ("Kertoja",) in cf.db.execute("SELECT name FROM unplaced_parts").fetchall()
    # A choice for a name the tree already knows writes nothing.
    assert _names(cf, "T") == ["Tenori", "Tenor", "T"]
    assert cf.listed("jm")
    assert any(line.startswith("Voice tree: ") and "Kertoja in no place" in line for line in log)

    # The tree knows them now.
    publish.publish(slug="laulu", title="Laulu", choir="jm",
                    bundle=_bundle(tmp_path, ("bassi", "Basso 4", "Kertoja")),
                    client=publish.Cloudflare(CONFIG, cf))


def test_a_place_the_tree_cannot_hold_is_refused_naming_it(tmp_path):
    tree = _tree(_with_tree(FakeCloudflare()))
    for place, said in (({"name": "X", "id": "B9"}, "no place B9"),
                        ({"name": "X", "parent": "T1u"}, "fourth level"),
                        ({"name": "X", "parent": "nowhere"}, "no place nowhere")):
        try:
            publish.place_statements(tree, ["X"], [place])
        except publish.PublishError as exc:
            assert said in str(exc)
        else:
            raise AssertionError(f"accepted {place}")


def test_a_new_place_named_like_an_existing_id_gets_an_id_of_its_own():
    tree = _tree(_with_tree(FakeCloudflare()))
    tree.voices[0]["names"] = ["Miehet"]  # "men" is now a name no place has
    [(sql, params)] = publish.place_statements(tree, ["Men"], [{"name": "Men", "parent": "T"}])
    assert params[0] == "Men"  # ids are case-sensitive; "men" is the group's
    tree.voices.append({"id": "Kuoro", "parent": None, "name": "Kuoro", "names": []})
    [(sql, params)] = publish.place_statements(tree, ["Kuoro"], [{"name": "Kuoro", "parent": "T"}])
    assert params[0] == "Kuoro 2"


# --- From the app -----------------------------------------------------------
def test_the_app_lists_the_unknown_names_and_publishes_once_each_has_a_place(
        app_song, monkeypatch, tmp_path):
    client, cf, _seen = _publish_client(monkeypatch, tmp_path)
    _with_tree(cf)
    reply = client.post("/api/voice-tree", json={"names": ["S1", "A1", "B1"]}).json()
    assert reply["available"] and reply["unknown"] == ["S1", "A1"] and reply["error"] is None
    assert reply["places"][0]["id"] == "men"

    # The fake bundle's parts are S1 and A1; the score is made to say the same.
    monkeypatch.setattr(publish, "score_parts", lambda _path: ["S1", "A1"])
    url = f"/api/songs/{app_song.slug}/publish"
    refused = client.post(url, json={"choir": "jm"})
    assert refused.status_code == 400 and "S1, A1" in refused.json()["detail"]
    assert not any("/r2/" in path for _m, path in cf.calls)

    places = [{"name": n, "id": "-"} for n in reply["unknown"]]
    assert client.post(url, json={"choir": "jm", "settings": {"places": places}}).status_code == 200
    data = _job_done(client, app_song.slug)
    assert data["jobs"]["publish"]["status"] == "succeeded", data["jobs"]["publish"]
    assert data["publish"]["places"] == places
    assert {r[0] for r in cf.db.execute("SELECT name FROM unplaced_parts")} >= {"S1", "A1"}
    assert client.post("/api/voice-tree", json={"names": ["S1", "A1"]}).json()["unknown"] == []


def test_a_bad_place_is_refused_and_no_tree_means_nothing_to_ask(app_song, monkeypatch, tmp_path):
    client, cf, _seen = _publish_client(monkeypatch, tmp_path)
    reply = client.post("/api/voice-tree", json={"names": ["S1"]}).json()
    assert reply == {"available": False, "unknown": [], "places": [], "error": None}
    bad = client.post(f"/api/songs/{app_song.slug}/publish-settings",
                      json={"places": [{"name": "S1", "id": "S", "parent": "women"}]})
    assert bad.status_code == 400
