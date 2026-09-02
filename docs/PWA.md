# Mobile PWA

The song app is installable as a standalone mobile web app. Its approach follows
the live-dashboard boundary used by AgentDeck: installability and a resilient
static shell, **not** offline song data.

## App identity and ports

Choir uses its own manifest identity, `./choir-pwa`, instead of the generic root
identity `/`. Its `id`, `start_url`, `scope`, and icon paths are relative to the
manifest URL. Browsers therefore resolve them against the exact origin that served
the manifest — including a non-default port.

This matters when several PWAs live on the same machine or Tailscale hostname. For
example, AgentDeck may occupy `https://host` while Choir is served from
`https://host:PORT`; Choir must remain a separate installed app and launch the port
from which it was installed.

The first PWA release used `id: "/"`. Because changing a manifest ID intentionally
creates a different installed-app identity, remove an already-installed Choir icon
from that release and install it once again after this change. Future updates keep
the stable `choir-pwa` identity.

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
`src/song_app/pwa_assets.py`, and **nothing has to be regenerated**: the app
serves `/pwa-assets.js` from `pwa_assets.rendered_config()`, computed from the
files on disk when the service worker asks for it. Change a listed asset and
the generation follows it.

It was a checked-in file kept in step by a script, and that made every edit to
`app.js` or `style.css` a two-part change — forgetting the second half failed
`test_pwa.py` with a message about a hash, which says nothing about what was
actually changed. Adding a *new* asset is still a two-part change, because the
list itself is the thing being edited: put the URL in `SHELL_ASSETS`.

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

This pull request proposes making `viewport-fit=cover` conditional. Painting
under the notch and the home indicator is only wanted where the app owns the
whole screen; in an ordinary browser tab the same setting moves the page's
bottom edge under the browser's own toolbar, and the phone layout's pane-switcher
bar sits exactly there, so the app looks like it has lost its bottom nav (#54).
The shipped `<meta name="viewport">` therefore leaves it off, and a small inline
script in the head adds it back when `navigator.standalone` or
`(display-mode: standalone)` says the app is installed. The safe-area rules are
unchanged: without `cover`, `env(safe-area-inset-*)` is `0`, so every
`max(<pad>, inset)` falls back to the plain padding it had before.
