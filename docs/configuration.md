# Configuration

Matinee reads `~/.config/kino/config.json`. Only `server` and `username` are needed (and `set-password` once):

```json
{
  "server": "http://192.168.1.10:8096",
  "username": "me",
  "language": "en"
}
```

Everything else has a default and can be changed on the TV, in one settings page with parts:
General, Video and Audio, the apps (Movies & Shows, Music, Audiobooks, Podcasts) and System (restart, shut down, updates,
Matinee's version). It opens from the Settings tile in the main menu, from the gear at the top of each part (on that
part's settings), and from the options of anything (OK held).

The TV writes its choices into `config.json`:

| Key | Values (default first) | What it does |
| --- | --- | --- |
| `language` | system locale, `en`, `de` | UI, player overlays, preferred audio and subtitle languages |
| `device_name` | the TV computer: `TV` (`Fernseher` in German); a Mac: its name; else the host's | what other Matinees offer this one as ("Continue on"), and its name in Jellyfin's dashboard; set by hand; kept to ASCII (umlauts as ae, oe, ue, other accents dropped: `Café` becomes `Cafe`) |
| `quality` | `auto`, `original`, `20`, `8`, `4`, `2` | streaming quality (see below) |
| `audio` | `auto`, `de`, `en`, `original` | audio language of movies and shows (`auto`: Matinee's language) |
| `subtitles` | `off`, `auto`, `always` | `off`: every start without, not even forced ones; turned on in the player, they stay for that playback only. `auto`: only when the audio is in another language (forced ones always) |
| `sublang` | `auto`, `de`, `en` | subtitle language (`auto`: Matinee's language) |
| `substyle` | `outline`, `box` | subtitles with a dark edge, or on a dark box (styled ASS subtitles keep their own look) |
| `moviesound` | `level`, `original` | `level` lifts the quiet parts of a film's sound |
| `ask` | `3`, `2`, `5`, `off` | "Still watching?" after that many episodes without a key |
| `spoilers` | `show`, `hide` | episodes not watched yet: their still and text, or a blurred still and no text |
| `badges` | `all`, `missing`, `off` | whether shows and seasons have all their aired episodes: `✓ 13` or `11/13` on their posters and seasons, missing episodes dimmed in a season; `missing` shows only the incomplete ones. Needs Jellyfin to list missing episodes (a metadata provider's placeholders, and *Display missing episodes* in the user's profile) |
| `effects` | `auto`, `full`, `light` | `light` (Reduced) leaves out the frosted glass, the pictures at the top and the covers' walls, nothing drifts and moves are quicker, for weak graphics chips; the blurred backdrop behind everything and a film's or show's own picture on its page and on Jellyfin's start stay. `auto` chooses it on a computer with at most four cores or 4 GB of memory. `plain` and `black` from older versions count as `light` |
| `size` | `auto`, `tv`, `desk` | how large everything is drawn: `tv` for the sofa, `desk` three quarters as large with more on the screen, for a Mac or laptop seen from close by; `auto` is `tv` on the TV computer, `desk` elsewhere |
| `textsize` | `normal`, `large` | every text 15 % larger, the room around it as it is |
| `motion` | `full`, `less` | `less`: pages, rows and the focus change at once, without springs, glides, the start's zoom or drifting pictures; the busy rings still turn. Also when the system asks for less motion |
| `sync` | `on`, `off` | the settings, the tracks chosen for a show and what was hidden kept alike on every device of the Jellyfin user ([features](features.md#everywhere)) |
| `sounds` | `medium`, `low`, `high`, `off` | a soft click for each step of the focus, and how loud |
| `soundkind` | `click`, `tap`, `wood`, `glass`, `soft` | what that click sounds like |
| `skipintro` | `offer`, `auto`, `off` | intros, recaps and previews: skipped on OK, right away, or not offered |
| `audiosync` | `{"<Bluetooth address>" or "default": ms}` | the sound earlier on that output (50 to 300 ms), for Bluetooth lag |
| `framerate` | `on`, `off` | switch the screen to the film's frame rate |
| `screensaver` | `10`, `5`, `30`, `off` | minutes until the clock |
| `saverweather` | `on`, `off` | the weather under the clock (`weather`: a place, chosen on the TV) |
| `sleepend` | `stop`, `poweroff` | what the sleep timer does at its end (power off: thin client only) |
| `scrobble` | `on`, `off` | tell Navidrome what was heard |
| `display` | `auto`, `native`, e.g. `1920x1080@120` | display mode on the thin client (see below) |
| `thin_client` | `false`, `true` | the thin client's settings on a desktop too (see below) |
| `simple` | set on the TV only | the simple settings: Settings → System turns them on with a four-digit code, which turning them off asks for again (only its salted hash is kept). Code forgotten: remove `simple` from `config.json` |
| `maxage` | `off`, `0`, `6`, `12`, `16` | the age limit (Settings → System): films and shows rated above it are left out of everything Matinee shows and are not played; episodes take their series' rating, titles without one stay. Cannot be changed while the simple settings are on. Of the device |
| `navidrome` | `{"server":…, "username":…}` | music ([setup](setup.md#signing-in)) |
| `audiobookshelf` | `{"server":…}` | audiobooks ([setup](setup.md#signing-in)) |

One's own channels need nothing in `config.json`; the one watched last is in `~/.config/kino/tv.json`. With `sync` on,
the settings, `tracks.json` and `hidden.json` are kept alike with the other devices of the Jellyfin user; `sync.json`
holds when each of them last changed here.

## Account

Moving the account to another device (Settings → Account) takes `config.json` (but for the device's own keys: `effects`,
`textsize`, `size`, `quality`, `framerate`, `display`, `thin_client`, `sleepend`, `autooff`, `autoupdate`, `audiosync`,
`simple`), and `tv.json`, `hidden.json`, `tracks.json` and the sign-ins to Navidrome, Audiobookshelf and
Podfetch. Jellyfin gets a token of its own for the device. Signing out removes all of that and keeps the device's own
keys and `language`.

It goes over Tailscale: only the device waiting for the account listens, on its Tailscale address and port 8766
(`KINO_TRANSFER_PORT`), while it shows the code (10 minutes; five wrong codes end it). The sending device only
connects out, so a Mac whose firewall lets nothing in can send but not receive.

## Language

`language` is `en` or `de`. Without it, Matinee follows the system locale. It sets the UI language, the player overlays
and the preferred audio and subtitle languages.

## Streaming quality

By default (`"auto"`) Matinee measures the connection to the server before each playback: about a second at home, two
and a half on a slow line. The first half second is not counted, since buffers let a burst through there. If the line
is too slow for the file, Jellyfin transcodes the movie to a bitrate the line can carry (H.264, stereo, the preferred
audio language; picture subtitles burned in), and the player shows a short note. A number such as `4` caps the
bitrate at that many Mbit/s without measuring. `"original"` always plays the file as it is.

## Updates

`"updatecheck": "auto"` (default) has Matinee look for updates by itself (the main menu now and then, the nightly update);
`"manual"` only when asked in the settings. `"autoupdate": "on"` installs them at night (only with `"auto"`).

## Other devices on the TV

`"hdmi_inputs": [{"name": "Fire TV", "port": 2}]`: the TV's other HDMI inputs, each a tile on the main menu (up to six,
a port once). Set on the TV in Settings → Other Devices on the TV.

## Display mode

By default (`"auto"`) Matinee keeps the screen's own mode, unless it is below 50 Hz. That happens with a 4K screen on a
computer with HDMI 1.4, which only gets 30 Hz there; Matinee then takes the highest refresh rate at 1080p or more (e.g.
1080p at 120 Hz). `"native"` always keeps the screen's mode, `"1920x1080@120"` sets exactly that one.

## On a desktop

`"thin_client": true` gives Matinee on a desktop (`kino --window`) what it otherwise only offers in cage on the thin
client: the display mode and frame rate settings (with `wlr-randr`), restart and shut down. They then act on that
computer.

## Updates and the network

These are environment variables, for testing or an own update server; a normal install needs none of them.

| Variable | Default | What it does |
|---|---|---|
| `KINO_RELEASES` | the newest GitHub release's `manifest.json` | where packages look for updates |
| `KINO_RELEASE_SIGNERS` | `deploy/release-signers` of the version in use | the keys a manifest must be signed with |
| `KINO_SUPPORT_URL` | first line of `deploy/support-url` | where reports to support go (HTTPS); empty: none |
| `KINO_RELEASE_TIMEOUT` | `30` | seconds a download (manifest, package) may stall |
| `KINO_JF_DISCOVER` | `255.255.255.255:7359` | where the sign-in asks for Jellyfin servers |
| `KINO_JF_DISCOVER_WAIT` | `1.5` | seconds it waits for their answers |
| `KINO_NET_CHECK_HOST` | `example.com` | a name that resolves whenever there is a network: tells "no network" from "server gone" |
