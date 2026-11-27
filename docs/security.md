# Security

## The backend

- It only listens on `127.0.0.1`. The one exception: while a device waits to take over the account from another one
  ("Take Over from Another Device"), it also listens on its Tailscale address, port 8766, for at most 10 minutes. The
  sending device needs the six-digit code shown on the screen, and five wrong codes end it. See
  [Account](configuration.md#account).
- Only Matinee's own page can use it. Requests with a foreign `Host` header (DNS rebinding) or a foreign `Origin` are
  rejected, and commands must be sent as JSON, which other websites cannot do without a CORS preflight.
- The page gets a secret cookie (new with every start, `HttpOnly`, `SameSite=Strict`), and every other request needs
  it. Only the page itself goes without. No other website may
  show Matinee in a frame. Not kept out: programs on the computer itself, which can load the page and take the cookie from
  it, as Chromium does.
- On macOS (`kino --window`) Matinee's Chromium answers DevTools on a free port of `127.0.0.1`, for `newtab-guard`
  to close the empty windows Chromium opens beside Matinee's. Like the cookie above, that is open to programs on the
  Mac itself, not to websites (Chromium refuses DevTools connections from web pages).
- What the media servers send on through the backend (covers, pictures) is shown, never run: `nosniff` and a
  sandboxing `Content-Security-Policy`.

## Accounts

- The Jellyfin token never reaches the browser. It is stored in `~/.config/kino/state.json` (readable only by you)
  together with the device ID. To revoke it, remove the device under Dashboard → Devices in Jellyfin. Matinee then says so
  on the TV and carries on by itself once `set-password` has run again.
- No password on disk. A `password` left in `config.json` by an older version is used once and then removed. A
  rejected password is not retried, so Jellyfin does not lock the account.
- Navidrome, Audiobookshelf and Podfetch: see [Signing in](setup.md#signing-in). Podfetch's password and the API key
  on its files' addresses stay in the backend; the UI gets pictures and episodes only through it.
- What "Same on Every Device" keeps alike (the settings, the tracks chosen for a show, what was hidden)
  is stored on the Jellyfin server, among the user's display preferences for the client "kino": the server's
  administrators can read it there. No passwords, tokens or sign-ins to other servers go with it.

## Bluetooth

- `deploy/pair-remote.sh` asks before it pairs and trusts a remote: a device names itself, and a trusted one types
  into Matinee like a keyboard.
- `install.sh` sets `ClassicBondedOnly=false` for remotes that pair without a key. Someone within Bluetooth range
  pretending to be the remote could then type on the TV computer. See
  [Remotes and Bluetooth](setup.md#remotes-and-bluetooth).
- `install.sh` sets `JustWorksRepairing = always` and `Privacy = device` in `/etc/bluetooth/main.conf` for Xbox
  controllers, which lose their key when paired with another device and then must pair again by themselves. A device
  pretending to be one paired before could then pair in its place without being asked. The old file is kept as
  `main.conf.kino-backup`.

## Updates

The nightly update (optional) runs a copy of its script owned by root, in `/usr/local/libexec`; the copy in the
checkout belongs to its user and changes with every pull, so it never runs as root.

The packages (installed with `get-matinee`) are checked before anything is unpacked: the manifest's signature with
`ssh-keygen -Y verify` against the keys in `deploy/release-signers` of the version in use (namespace `kino-release`),
then the package's SHA-256 from the manifest; its files are unpacked with Python's `data` filter (no links out of the
folder, no device files, no setuid bits). A signed manifest older than the version in use (by its commit's time,
and not listing that commit) is refused: whoever controls the releases' place but not the key cannot bring back a
version with what was fixed since (going back by hand, to a version still on the device, still can). A new signing key therefore comes in only with a version signed by the old
one. The private key belongs to whoever publishes: lost, no device takes an update any more; stolen, anyone can give
every device any code. Keep it offline and backed up. `deploy/release-signers` therefore trusts a second, spare key
from the start, kept only in an encrypted backup: with the main key lost, a version signed by the spare one still
comes in (and brings a new spare); with one stolen, a version signed by the other takes it off the list. `get-matinee` itself is fetched over HTTPS and trusts GitHub for its
first download.

## Wi-Fi

- The polkit rule from `install.sh` lets the user Matinee runs as (and only that user) change NetworkManager's settings
  without a password: join, add and forget networks.
- A Wi-Fi password is handed to `nmcli` on its command line, so other users on the computer could see it in the
  process list for the second it runs; NetworkManager then keeps it in `/etc/NetworkManager/system-connections`,
  readable only by root. Matinee does not write it anywhere.
- The server discovery only lists what answers on the local network; nothing is used before OK is pressed on it, and
  the sign-in then goes to that address like one typed in.

## Reports to support

- Sent only when the person chooses Send, only over HTTPS (or HTTP to the computer itself), only to the address the
  release carries (`deploy/support-url`): a page cannot send one elsewhere, the address is not taken from a request.
- The report is `deploy/diag`'s: no tokens or passwords; of `config.json` every key that sounds like a secret
  (`pass`, `token`, `secret`, `key`, `pin`, `salt`, `hash`) blanked; of the other files in `~/.config/kino` only their
  names and sizes. The logs can name servers, devices, networks and titles played.
- `deploy/support-inbox` stores what it gets with mode 600, takes only gzip under 20 MB at a code's address, and at most
  ten an hour from one address. It answers nothing else, so it shows no one what has come in.

## Jellyfin on the TV computer

- `deploy/server.sh` runs Jellyfin in a container on the host network (port 8096 in the home network, discovery on
  7359/udp), its media folder mounted read only; its startup wizard is done by `server.sh` at once (until then anyone on the home network could make themselves
  its administrator), with a first user and a made-up password in `~/.config/kino/local-admin.json` (mode 600); the TV
  renames that user and gives it the person's password, and removes the file.
  "Remote access" is on so that Jellyfin's apps reach it over Tailscale too; nothing is forwarded on the router.
- The Samba share "Matinee" lets in only Matinee's user with a password of its own (three groups of four letters and
  digits, made by `server.sh`), not the login's. It is kept in `~/.config/kino/share.json` (mode 600) and shown on the
  TV in the settings, so anyone at the TV can read it: whoever has the remote can copy files onto the media folder.
  `sudo smbpasswd <user>` sets another one (then change `share.json` to match).
- On Fedora and openSUSE `server.sh` opens firewalld's zone for Samba, 8096/tcp and 7359/udp.

