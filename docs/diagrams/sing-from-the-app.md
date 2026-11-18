# Laulaminen choir appista

Miten Publish-paneelin "Laula ja julkaise" -nappi ja ääntämislistan "Kokeile"-nappi käynnistävät
saman `sing_song.py`:n kuin agentit, erillisenä systemd-ajona joka kestää sovelluksen uudelleenkäynnistyksen.
Näytönohjaimen lukko pitää huolen, että kerralla laulaa vain yksi ajo ja ajojen välissä on 60 s tauko.

```mermaid
flowchart TD
  subgraph Selain
    A[Laula ja julkaise -nappi<br/>Publish-paneeli]
    T[Kokeile-nappi<br/>ääntämislista]
    P[Edistyminen ja Pysäytä]
    AU[Soitin: kokeilun MP3]
  end
  subgraph Sovellus
    S1[POST /sing]
    S2[POST /sing/try]
    G{Laulun muu työ<br/>käynnissä?}
    R[systemd-run --user<br/>sing-slug -yksikkö]
    ST[GET /sing<br/>lukee .sing-run.json]
    F[GET /sing/try/tiedosto.mp3]
  end
  subgraph sing_song.py
    L{Näytönohjain<br/>vapaa?}
    W[Odottaa vuoroaan<br/>tila: waiting]
    SI[Laulaa osat]
    PU[Julkaisee kuoron listalle]
    TR[Kirjoittaa try/…mp3]
  end
  A --> S1 --> G
  T --> S2 --> G
  G -- Kyllä --> X[409: kerrotaan paneelissa]
  G -- Ei --> R --> L
  L -- Ei --> W --> L
  L -- Kyllä --> SI
  SI -- koko laulu --> PU
  SI -- kokeilu --> TR
  P --> ST
  TR --> F --> AU
  class A,T,P,AU blue
  class S1,S2,R,ST,F violet
  class G,L amber
  class X,W rose
  class SI,PU,TR green
```

## Colours

- **blue**: mitä ihminen näkee ja painaa
- **violet**: sovelluksen reitit ja ajon käynnistys
- **amber**: tarkistus ennen kuin mitään tehdään
- **rose**: odotus tai kieltäytyminen
- **green**: laulaminen ja sen tulos

## Boxes

- **systemd-run --user<br/>sing-slug -yksikkö**: `sing_run.py` käynnistää `sing_song.py`:n omana systemd-yksikkönään (`sing-<slug>`, kokeilu `sing-<slug>-try`, `RuntimeMaxSec=3h`) HEADin kopiosta hakemistossa `/var/tmp/sing-code/<sha>/`, jotta deploy-uudelleenkäynnistys tai merge kesken ajon ei muuta mitään. Pysäytä = `systemctl --user stop`, jonka SIGTERM `sing_song` jo käsittelee.
- **Näytönohjain<br/>vapaa?**: `src/gpu_card.py`, `flock` tiedostoon `~/.local/state/musescore-choir-plugins/card.lock`, sama agentin komentoriviajoille ja sovelluksen ajoille. Seuraava laulaja odottaa 60 s edellisen jälkeen; homr-luku ei odota vaan lukee prosessorilla.
- **GET /sing<br/>lukee .sing-run.json**: sama edistymistiedosto (`progress.py`), jota kirjastonäkymä jo näyttää; sovelluksen ajo tunnistetaan `run_id`:stä (`SING_RUN_ID`).
- **Kirjoittaa try/…mp3**: `--try PART:A-B`, tulos hakemistoon `~/.local/state/musescore-choir-plugins/sing/<slug>/try/<id>/` (kolme viimeisintä säilyy), käyttää tallennettuja ääntämisiä. Sovellus ei kirjoita `songs/`-kansioon mitään.
