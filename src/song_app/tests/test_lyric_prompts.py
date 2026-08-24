"""The AI handoff must explain that lyric hyphens control score word joins."""

from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_ai_prompts_warn_that_missing_hyphens_create_new_words():
    for name in ("lyric_json_prompt.txt", "lyrics_txt_prompt.txt"):
        prompt = (ROOT / name).read_text(encoding="utf-8")
        prompt = " ".join(prompt.split())

        assert "hyphen between every pair of sung syllables" in prompt
        assert "A missing hyphen makes the score show a new word" in prompt
