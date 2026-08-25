# Mobile PWA

The song app is installable as a standalone mobile web app. Its approach follows
the live-dashboard boundary used by AgentDeck: installability and a resilient
static shell, **not** offline song data.

## Cache boundary

`src/song_app/static/sw.js` handles only:

- the exact shell URLs generated in `pwa-assets.js`;
- document navigations, network-first, with `offline.html` only when the server
  cannot be reached or a reverse proxy returns 502/503/504.

It deliberately leaves `/api/`, `/ws/`, `/healthz`, song files, score/PDF
responses, and rendered media untouched. There is no stale-data fallback for
any of them.

## Updating shell assets

The cache name is derived from the Git blob digest of every URL in
`src/song_app/pwa_assets.py`. After changing a listed asset, regenerate the
checked-in configuration from the repository root:

```bash
.venv/bin/python scripts/update-pwa-assets.py
```

`test_pwa.py` fails if the generated file, asset list, or content-derived cache
generation is out of date.

The service worker imports that generated file with `updateViaCache: "none"`.
A new generation activates immediately, but `pwa.js` postpones the page reload
while a form field is focused or differs from its settled value.

## Mobile installation

- iPhone/iPad: open the app in Safari, Share, then **Add to Home Screen**.
- Android/desktop Chromium: use **Install app** from the browser menu.

The manifest supplies regular 192/512 icons and a maskable 512 icon. The HTML
and additive `pwa.css` include standalone metadata plus all four safe-area
insets, so the header, one-pane workspace, score tabs, and bottom switcher stay
outside notches and the home indicator.
