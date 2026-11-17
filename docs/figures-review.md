# Reviewing the figures

How every figure screen is checked for keyboard use, screen readers, forced
colours, zoom and reflow, in the browser engines and web views agdash runs in.
Most of it is automated; a screen-reader pass and the desktop web views need a
person.

## The synthetic store

Every check runs the built app over a store of made-up sessions that fills
every figure: two months of history in four harnesses, a live board, one long
session across a night, and a WorkChord connection with two projects. It never
reads a harness folder, and it serves on loopback only.

```bash
(cd frontend && npm run build)
(cd backend && ../.venv/bin/python tests/screens.py --port 8790 --ui ../frontend/dist)
```

Open `http://127.0.0.1:8790/#agdash-token=screens-token` in a browser to use it
by hand: the link hands the tab the store's token, as `agdash open` does.

## The automated run

```bash
(cd frontend && node scripts/screens.mjs --engine webkit)
(cd frontend && node scripts/screens.mjs --engine chromium --forced-colors)
```

Each screen with a figure (Live, History, a session, Projects over 7 and 30
days, and Analytics' Usage, Tools and Activity) is opened at 1440px, at 200%
zoom (720px at twice the density) and at 320px. On each it checks:

- **Reflow:** the page never scrolls sideways, and no figure's text runs past
  the figure's edge or is cut off.
- **Motion:** nothing in a figure animates or transitions, with motion allowed
  at 1440px and with reduced motion asked for at the other sizes.
- **Keyboard (1440px):** Tab alone reaches every plot and every selection
  handle; in each plot the arrow keys, Home and End make a labelled mark active
  and show its tooltip; a handle's arrow keys move it and it says its value in
  words; nothing inside a drawing takes focus of its own. The live board's
  strips are checked as DESIGN.md has them: hidden from assistive technology,
  with the same said in words in their row.
- **Forced colours (Chromium):** every drawing keeps marks in a colour unlike
  its surface.

Screenshots land in `frontend/specimen-shots/screens/<engine>/`, with
`accessibility.json` holding each figure's accessibility tree and the label
each key lands on: what a screen reader should announce. CI runs it on macOS
and Ubuntu as "Figure screens" and keeps the screenshots.

## By hand: VoiceOver

Turn VoiceOver on with Command-F5. In Safari, open the store's link above; in
the desktop app, see the next section. On each figure screen:

1. Move to the figure with VO-Right Arrow: VoiceOver reads its title and
   caption, then its legend.
2. Press Tab until the drawing is focused: VoiceOver says the figure's name and
   "chart", then how to move ("Use the arrow keys to move between values").
3. Press Right Arrow, End and Home: each announces the active mark's label, the
   same words `accessibility.json` lists for that key. Escape hides the value.
4. On History, Tab to the two selection handles: each announces its name and
   its date, and the arrow keys change the date read out.
5. Open "Show as a table" and read a row with VO-Right Arrow.

Note anything announced twice, not at all, or differently from
`accessibility.json`.

## By hand: the desktop web views

The desktop app uses the system's web view: WKWebView on macOS, WebKitGTK on
Ubuntu. Point it at the synthetic store instead of the real service: it uses a
healthy service on `AGDASH_PORT` as it is, and takes its token from the command
`AGDASH_BIN` names, so a stand-in that prints the store's token is enough.

```bash
printf '#!/bin/sh\n[ "$1" = token ] && echo screens-token\n' > /tmp/agdash-screens && chmod +x /tmp/agdash-screens
(cd frontend && npm run dev)   # a debug build loads the UI from here
(cd desktop && AGDASH_PORT=8790 AGDASH_BIN=/tmp/agdash-screens cargo run)
```

An installed app takes the same two variables: on macOS
`/Applications/agdash.app/Contents/MacOS/agdash-app`, on Ubuntu `agdash-app`.
In the window, open each figure screen, use the keyboard as above, and repeat
the VoiceOver pass in the macOS app. On Ubuntu, Orca reads the same tree.
