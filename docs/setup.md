# Setting up the thin client

The short version is in the [README](../README.md#install). This page covers what happens after that.

## Starting and crashing

After `install.sh` the computer boots straight into Matinee. If something crashes, the service starts Matinee again after 3
seconds: Chromium or the backend, either one ending ends the other too. If the network or the server is not up yet at
boot, Matinee says so and connects as soon as it can; it does not wait for the network before starting.
Matinee ended while something plays (switched off, an update, Cmd+Q on a Mac) keeps where it was, as Back would.

`deploy/boot.sh` (optional; Arch, Debian, Fedora or openSUSE, starting with GRUB) installs Plymouth with Matinee's own theme: the logo on black, its cards opening and closing a little while the computer starts. It also asks for the disk
password if unlocking with the TPM fails. GRUB then starts at once with its menu hidden; hold Esc while the computer
starts to get the menu. The script keeps a `.kino-backup` of every file it changes.

## When the network or a server goes away

Without help, mpv tries again for a few seconds and then takes a lost stream for the end of the file. Matinee tells the
two apart. When a movie, episode, video, song or book stops well before its end (or a live channel or radio station
stops at all), Matinee shows "Connection to the server lost, waiting for it…" and goes on by itself once the server
answers:

- a movie, video, song or book a few seconds before where it stopped (a book with a new listening session),
- a live channel or a radio station as it is now.

Nothing is marked as watched or heard, and no next episode or song starts. Back stops waiting. After ten minutes Matinee
gives up and says so.

Without any network (the computer asleep, the Wi-Fi gone) the log says so once, and again when it is back (or an
hour later) with how many failures it left out, instead of a line for each server, channel and feed tried meanwhile.
One server gone while the rest answer is logged as before.

## Turning it off at any time

Pulling the plug is meant to be harmless:

- Matinee writes its files whole or not at all.
- On the thin client, Chromium's profile lives in memory (`$XDG_RUNTIME_DIR/kino-chromium`), so there is nothing of it
  on the SSD to damage.
- If the UI stops answering for two minutes (Chromium hangs, or shows a blank page after starting), Matinee starts again
  by itself, but not while something plays. If it is still silent right after such a restart, the TV is most likely
  off (Chromium then runs no page at all), and the next check comes only after half an hour.
- Chromium falls over when the TV is switched off or to another input (cage loses its only screen). Then only Chromium
  starts again, once the TV is back: the music or book playing goes on. Falling over a fourth time within ten minutes,
  Matinee starts again as a whole.

`deploy/harden.sh` (optional) adds:

- a btrfs snapshot before and after every update; start the one from before a bad update in the GRUB menu (hold Esc
  while it starts). On Arch with snap-pac (snapper needs a "root" config); openSUSE takes them by itself on btrfs, which
  `harden.sh` only checks. Debian and Fedora get none: their GRUB cannot start a snapshot.
- the hardware watchdog: a hung computer restarts after 30 seconds,
- a restart after a kernel panic,
- a power button that shuts down cleanly, also when held,
- a weekly SSD trim, no old packages kept (Arch: pacman's cache cleared weekly; Debian: apt keeps none it installed;
  Fedora and openSUSE keep none anyway), and the system log kept below 200 MB.

## Remotes and Bluetooth

Anything that shows up as a keyboard works: USB and RF remotes, air mice, a Flirc receiver with any infrared remote.

An Amazon Fire TV remote (Bluetooth, e.g. the CV98LM) needs a Bluetooth adapter. OK sends Enter; Back, Home, Menu, the
arrows, rewind, play/pause and fast forward all work in Matinee and in the player. Its volume and power keys are infrared,
for the TV itself, so up and down set the volume while a video plays, Menu while browsing. Before pairing, hold Home for
10 seconds.

Matinee and `deploy/pair-remote.sh` turn on BlueZ's pairable mode before pairing. Without it BlueZ keeps no key, and the
remote (or a speaker) is gone after a restart. `pair-remote.sh` asks before it pairs and trusts a device: a device
names itself, and a trusted one types into Matinee like a keyboard.

> **Security note:** `install.sh` sets `ClassicBondedOnly=false` in `/etc/bluetooth/input.conf`, because some remotes
> pair without a key. The price: someone within Bluetooth range pretending to be the remote could type on the TV
> computer. If your remote pairs with a key, set it back to `true`.

Whenever Matinee starts it connects the speaker or headphones chosen last again (if that one does not answer, the first
paired one that does), one only, as the sound plays on one. Most speakers play from one device
at a time: one still connected to a phone or a laptop does not answer the TV computer, and Settings → Devices shows it
as not reachable. Disconnect it there (or switch that device's Bluetooth off), then connect it in Matinee.

Speakers and headphones play in A2DP only, their full stereo sound: Matinee puts `deploy/wireplumber-bluetooth.conf` in
`~/.config/wireplumber/wireplumber.conf.d/` when it starts (and starts WirePlumber anew when it changed), so a speaker with a microphone
for calls (many call themselves a headset, which Matinee then calls neither speaker nor headphones) never falls to the
telephone profile, mono at 16 kHz. Each output keeps its own volume: WirePlumber remembers it.

## Everyday commands

- Logs: `journalctl -t kino -f`, without sudo (`install.sh` puts the user in the `systemd-journal` group, `wheel`
  reads it too; on a computer set up before: `sudo usermod -aG systemd-journal $USER`, then log in again). Not
  `-u kino`: Matinee runs in a login session, filed under that, and the service only has systemd's starting and stopping.
- A quick look whether all is right: `~/kino/deploy/check` says line by line whether Matinee runs and is signed in, the
  volume can be set, a screen, the remote and the sound output are there, the clock is synchronized, cage uses the
  legacy DRM interface and has had no failed atomic commits, and there is room on the disk. Its exit code is the
  number of lines marked `!!`. It changes nothing.
- Everything at once, to look at from afar: `~/kino/deploy/diag` writes `/tmp/kino-diag-….tar.gz` (logs, services,
  versions, disk, network, Tailscale, Bluetooth, sound, screens, settings). It changes nothing and leaves out tokens and passwords.
- A stable channel (installed from the source): the updates follow the branch the checkout tracks. To have the TV computer get only what you
  push to a `stable` branch (not every push to `main`): `git -C ~/kino switch -c stable --track origin/stable`.
- Restart: `sudo systemctl restart kino`
- Update: `~/kino/kino --update` gets the newest version and restarts Matinee, without a password (over ssh too). See
  [Updates](#updates) for where it comes from.
- Nightly updates: `install.sh` offers them. They never restart Matinee in the middle of a film or a song. After the
  restart the update waits up to a minute for Matinee's backend to answer (it runs with the TV off too); if it does not,
  it goes back to the last version that started and leaves the new one out until a newer one comes
  (`journalctl -u kino-update` says so). The script runs from a copy in `/usr/local/libexec`: after it changes,
  run `install.sh` again (or copy `deploy/nightly-update` there as root) for the thin client to use the new one.
- Probation: a new version, from the nightly update or from Settings → System, is on probation until its page has
  come up on the TV. Matinee counts each start of it until then; after three starts without the page (the backend falls
  over, or the page never loads and the watchdog starts Matinee again) it goes back to the version before by itself and
  leaves the new one out until a newer one comes. With the TV off nothing is started again, so nothing is counted.
  The state is in `.git/kino-update` in the checkout, or `~/.local/state/kino/update` with packages (`from`, `to`, `tries`, `bad`); `journalctl -t kino | grep -i
  probation` shows what happened. Delete `bad` to have that version tried again.
- Time: `install.sh` sets the clock by the PTB's atomic clocks (`ptbtime1.ptb.de` and two more, through
  systemd-timesyncd). `timedatectl` shows whether it is synchronized; so does Settings → System.
- Console: Ctrl+Alt+F2

## The first start

A device without a Jellyfin server that has not been through it yet starts with its steps instead of the main menu:
the language (both named in their own), the network (whether there is one, and Wi-Fi), Jellyfin (the servers found on
the network, the sign-in, or taking over another device's account), a Bluetooth remote (only where there is
Bluetooth) and Start. Back goes to the step before. Once through (or left with Later), `"welcomed": true` in
`config.json` keeps it from coming again; Settings → System → First-Start Setup runs it again.

## Updates

**Packages** (installed with `get-matinee`): each version is a folder in `~/.kino-versions`, named by its commit, and
`~/kino` a link to the one in use. An update fetches the newest release's `manifest.json` and its signature from
GitHub, checks the signature against `deploy/release-signers` of the version in use (`ssh-keygen -Y verify`), then the
package's SHA-256, unpacks it beside the others and switches the link at once; the last three versions stay for going
back. A manifest or a package that does not check out is not installed, and Settings → System says so. Going back
(probation, below) switches the link to the version before. `python3 ~/kino/backend/release.py check` shows what
is installed and what is new.

**From the source** (`git clone`): `git pull` on the branch the checkout tracks. For a private repository, give the
thin client a read-only deploy key and point its `origin` at the SSH address.

**Only when asked:** Settings → System → Software Updates → Look for Updates: Only When I Ask. Matinee then asks GitHub
neither on the main menu nor at night (`deploy/nightly-update` reads the setting too); the settings' button still
looks. Security updates then do not come by themselves either. A restart still owed for an update made before
(something played that night) comes all the same. The nightly update runs a copy of its script that only
`deploy/install.sh` puts in place: on a computer set up before this setting came, run `install.sh` once more.

Publishing a release (for whoever makes Matinee): commit and push on `main`, then `deploy/make-release` builds the package
from `HEAD`, signs its manifest with the release key (`~/.ssh/kino-release`, or `KINO_RELEASE_KEY`) and puts it on
GitHub as the newest release of the packages' repository (`gh`, signed in). `--dry-run` builds it only. Devices take
it at their next look: the nightly update, or Settings → System.

## Wi-Fi and finding the server

`install.sh` installs NetworkManager and lets the user Matinee runs as manage the network without a password (a polkit
rule, `/etc/polkit-1/rules.d/50-kino-network.rules`). If another network service is already running (systemd-networkd,
iwd, dhcpcd, netctl, wpa_supplicant), it asks before switching it off. Settings → Wi-Fi then lists the networks nearby:
OK joins one (the letter grid for its password), and OK on the one in use or a known one offers to forget it.
Without NetworkManager or a Wi-Fi device the line is not shown.

On the sign-in page Matinee asks the local network for Jellyfin servers (Jellyfin's own discovery, UDP port 7359; the
server answers by default) and lists the ones that answer: OK puts one's address in. Below them is the Wi-Fi in use,
OK opens the Wi-Fi settings. With no network at all, the sign-in says so instead of only that Jellyfin is not
reachable.

## A report to support

Settings → System → Report to Support makes `deploy/diag`'s file (logs, the system, versions, network, Bluetooth,
sound, the settings with every secret blanked) and sends it over HTTPS to the address in `deploy/support-url` (or
`KINO_SUPPORT_URL`), only when Send is chosen. The TV then shows a code such as `K7F-29Q` for the person to read out.
Without an address (the file empty, as in the repository) the line is not shown; plain `http://` is refused except to
this computer itself.

Receiving them (for whoever supports Matinee): `deploy/support-inbox --dir ~/kino-reports` on a server, behind a reverse
proxy that does HTTPS, for example with Caddy:

```
support.example.com {
  reverse_proxy /report/* 127.0.0.1:8780
}
```

then `https://support.example.com/report` into `deploy/support-url`, committed before the next release. Each report
lands as `<date>-<code>.tar.gz`, readable only by the user running the inbox; it takes at most ten reports an hour
from one address and 20 MB each. Delete them once the case is closed.

## HDMI-CEC

Matinee switches the TV to another input (a streaming stick, [features](features.md#other-devices-on-the-tv)) through the
kernel's CEC framework: `cec-ctl` (v4l-utils, from `install.sh`) on `/dev/cec0`, which the `video` group may use.
The HDMI ports of most PCs' graphics chips do not carry CEC, so one of these is needed:

- a **Pulse-Eight USB-CEC adapter** between the computer and the TV (some Intel NUCs have one inside). The kernel takes
  it only through `inputattach`; `install.sh` sets up a udev rule and the service `kino-pulse8@ttyACM0` that does this
  whenever it is plugged in (Arch, Debian, Fedora; openSUSE has no `inputattach` package);
- a **DisplayPort to HDMI adapter that passes CEC on** (not all do), for a computer with DisplayPort: nothing to set up.

`cec-ctl -d /dev/cec0` shows Matinee's physical address (`1.0.0.0`: the TV's input 1; `f.f.f.f`: no TV answering). The
switch is a Set Stream Path to `N.0.0.0` for the TV's input N; back is Image View On and Active Source. Through an AV
receiver, give the TV's input the receiver is on.

## Signing in

**Jellyfin:** the easiest way is on the TV. Without a server in `config.json`, open Movies & Shows and enter the server,
user name and password there. Matinee signs in and writes the server and user name to `config.json`. Settings → Movies &
Shows → Server and Sign-In changes them later. In the terminal, `set-password` asks for the password, signs in and
stores only the access token; the password itself is never written to disk. To take it from the clipboard (on a
desktop): `./set-password --clipboard`; from a password manager: `pass show jellyfin | ./set-password --stdin`. A
running Matinee picks up the new login by itself.

**Music and audiobooks** (optional): their tiles are on the main menu from the start. Until a server is set up (or
while it rejects the sign-in), the tile opens the same sign-in page as Movies & Shows: type the server's address, the
user name and the password or API key there and choose Sign In. Matinee keeps them only if the sign-in works, and the
start page comes up in its place. Settings → Music and Settings → Audiobooks change the server later or remove it.
The terminal works as well, as below.

**Music from [Navidrome](https://www.navidrome.org/)**: in the settings, or add
`"navidrome": {"server": "https://…:4533", "username": "me"}` to `config.json`, then sign in with
`./set-password --navidrome` (also with `--clipboard` or `--stdin`). Navidrome speaks the Subsonic API, which signs
every request with md5(password + salt). Matinee keeps only that and the salt (`~/.config/kino/navidrome.json`), not the
password. Anyone who has that file can still use the account, as with the password itself.

**Audiobooks from [Audiobookshelf](https://www.audiobookshelf.org/)**: in the settings, or add
`"audiobookshelf": {"server": "https://…:13378"}` to `config.json`, create an API key in Audiobookshelf
(Settings → Users → API keys) and give it to Matinee with `./set-password --audiobookshelf` (also with `--clipboard` or
`--stdin`). It is kept in `~/.config/kino/audiobookshelf.json`, readable only by you. Delete the key in Audiobookshelf
to take it back.

**Podcasts from [Podfetch](https://github.com/SamTV12345/PodFetch)**: in the settings (Settings → Podcasts), with the
server's address (`http://` if Podfetch is reached without TLS) and its user name and password, or neither for a
Podfetch without sign-in. The password is kept in `~/.config/kino/podfetch.json`, readable only by you. Matinee reads
Podfetch's own API; the episodes' files are fetched with the user's API key, which Podfetch puts on their addresses.

**All at once:** Settings → Account → Sign In Everywhere signs in to every server set up on the device with one user
name and password, for instance for another person on their own TV computer ([features](features.md#everywhere)). A
server refusing them stays signed in as before.

## Trying it on a desktop

```sh
./kino --window
```

Matinee runs full screen in Chromium; close it with the window manager.
