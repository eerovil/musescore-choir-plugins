# How a song's language decides what is sung

The plan for eerovil/musescore-choir-plugins#484. The song keeps its language in its own file,
`language.json`, and every singing run reads it from there. Each syllable's sounds then come from a saved choice first,
in any language, else from the rules of the song's language and variant. English two-part vowels
are written as two vowels just before they are sung.

```mermaid
flowchart TD
    run[sing_song.py run] --> rec{Song has a language saved?}
    rec -- Yes --> flag{--lang given and different?}
    flag -- Yes --> refuse[Refuse: name both languages]
    flag -- No --> lang[Song's language and variant]
    rec -- No --> given{--lang given?}
    given -- Yes --> save[Save it on the song, say so] --> lang
    given -- No --> looks{Words look Finnish?}
    looks -- No --> refuse2[Refuse: set the song's language]
    looks -- Yes --> fi[Finnish] --> lang
    lang --> syl[Each sung word]
    syl --> choice{A saved reading for it?}
    choice -- Yes, ARPAbet --> en_only[English sounds, English songs only]
    choice -- Yes, voice sounds --> any[Voice sounds, any language]
    choice -- No --> rules[Language rules, with the variant's changes]
    en_only --> deal[Deal the sounds over the word's syllables]
    any --> deal
    rules --> diph[Two-part vowels split: AY is AA then IY]
    deal --> diph
    diph --> sing[sing_ufr.py sings it]
    class run,syl blue
    class rec,flag,given,looks,choice amber
    class refuse,refuse2 rose
    class save,lang,fi green
    class en_only,any,rules,deal,diph violet
    class sing teal
```

## Colours

- **blue**: what the run starts from
- **amber**: the questions that decide the language and the reading
- **rose**: the run stops before anything is sung
- **green**: the language the song is sung in
- **violet**: how a word's sounds are worked out (`g2p.py`)
- **teal**: singing

## Boxes

- **Song has a language saved?**: `songs/<slug>/language.json` (its own file, so an app job saving an older copy of `.song.json` cannot undo it), set on the New song form or the Start panel, or by the first `sing_song.py --lang` run.
- **--lang given and different?**: `sing_song.py --lang` now only confirms the saved language. `--change-lang` changes it.
- **Words look Finnish?**: a new check in `src/sing_song/main.py`, the reverse of `unmarked_finnish`: letters and pairs Finnish words seldom have (b, c, f, q, w, x, z, th, sh, ch, a lone g).
- **Language rules, with the variant's changes**: `src/sing_song/languages.py` lists each language and variant (`la` Italian church, `la-de` German style) and what a variant changes in its base's rules.
- **Voice sounds, any language**: a reading in `pronounce.json` written in the voice's own sound names (`de/h it/o it/d it/i it/e`), checked against the voice pack.
- **Two-part vowels split: AY is AA then IY**: English AY, AW and OY become two vowel sounds in `g2p.py`, the way the reporter wrote "child" by hand.
