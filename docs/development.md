# Development

## Tests

```sh
tests/run                                # everything, several at once, about 6 minutes on a 14-core Mac
tests/run test_player test_tv            # some parts, several at once too: test_backend, test_player, test_ui, test_deploy, …
tests/run test_player.PlayerFastTest     # one class or one test, each test named as it runs
tests/run --serial test_player           # a part one test after another
KINO_REAL=1 tests/run test_real_server   # also against the real server, read-only
KINO_TEST_JOBS=4 tests/run               # how many at once (by default two thirds of the cores)
```

`tests/run` (`tests/parallel.py`) runs each test class in a process of its own, a big one in parts of 25, the longest
first: how long each part took is kept in `tests/.durations.json` (not in git) for the next run's order. The walks over
every page (`test_consistency`, `UiFocusTest`) are split into classes of their own (`…Part1Test`, …) so they run side
by side. More at once than two thirds of the cores and the tests that go by the clock fail now and then.

The tests need Python, mpv, ffmpeg and Chromium. They run against a fake Jellyfin (`tests/mockjf.py`) that keeps
watched state and positions, checks authentication like Jellyfin 12 and speaks WebSocket, and fakes of
Navidrome, Audiobookshelf and Podfetch, so they never talk to a real server. mpv runs without video and audio output, Chromium headless. On a Mac, Chrome for Testing's headless
shell keeps the many browsers the tests start out of the Dock; with it there, the tests take it by themselves:
`npx @puppeteer/browsers install chrome-headless-shell@stable --path ~/.cache/chrome-headless-shell`.

On GitHub, every push to `main` runs them on Linux as well (`.github/workflows/tests.yml`): Arch Linux in a container,
with its Chromium and mpv as on the TV computer, shellcheck over the shell scripts first. Every Monday, and whenever
`deploy/install.sh` changes, `.github/workflows/distros.yml` runs its package part (`KINO_PACKAGES_ONLY=1`) in fresh
Arch, Debian, Fedora, openSUSE Tumbleweed and Leap, LMDE, Manjaro and CachyOS containers: a package a distribution renamed or dropped shows there first. To run
the tests in a container on a Mac (Docker), the full Chromium needs `--no-sandbox` (Docker's seccomp profile leaves
it none to build) and gets the TV's 1920×1080 from the tests themselves, not from its headless window.

## Screenshots

```sh
tests/make-screenshots
```

Writes the README's pictures to `docs/screenshots/`: Matinee against the fake Jellyfin with made-up titles and pictures
drawn by ffmpeg, so nothing from a real library ends up in the repository.

## Layout

- `server.py` and `backend/`: the backend. Signs in to Jellyfin (with retries) and proxies API and image requests (the
  token stays in the backend); talks to Navidrome, Audiobookshelf and Podfetch; starts mpv,
  reports progress and loads the next episode. `server.py` starts it; the code is in `backend/`, one module a part
  (`core.py`: files, config and language; `http_api.py`: what the UI asks for; `video_player.py`,
  `tv.py` and so on), each with what it is for at its top. They load in a fixed order (`core` first, each loading the
  next at its end): what a module uses while it loads must be in an earlier one, as in a single file; what it uses
  only when called may be in any (a name another part changes, or one in a later part, is written `module.name`).
- `web/`: the UI (main menu, home, details, shows with seasons, libraries, search, music, books, podcasts, one's own channels)
  with spatial navigation and GPU-driven movement. `i18n.js` holds the English and German strings.
  The page's code is in `web/js/`, one file a part (`core.js` first: the API and the building blocks; `start.js`
  last: the screensaver and the start). They are classic scripts loaded in order by `index.html` and share one global
  scope, as a single file would: a function used while the files load must be in the same file or an earlier one.
- `mpv/`: an mpv configuration only for Matinee: hardware decoding, key bindings for remotes, and `scripts/kino.lua` with
  the player's bar, seeking and the info panel.
- `deploy/`: systemd service, installer, nightly update, boot theme, desktop entry and icon.
- `tests/`: backend, player, UI, setup and optionally the real server.

## Commits

A commit's subject is what Settings → Updates lists as its change, in English. A trailer in its message says it for a
Matinee in German, in words for whoever watches rather than whoever codes:

```
Grids: the space left over between the tiles, as far from the right edge as from the left

Note-de: Raster: rechts so viel Rand wie links
```

Without one, a German Matinee shows the subject.
