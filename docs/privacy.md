# Privacy

What Matinee connects to besides the servers you set up, and what they learn. Settings → System → Licenses and Privacy
shows the same on the TV. This is a description of the software, a basis for a privacy policy, not one itself.

Matinee has no account of its own, sends no usage statistics, shows no advertising and sends no crash reports by itself.
What it knows (the servers, sign-ins, settings, what was watched where) stays on the device, in `~/.config/kino` and
`~/.local/state/kino`, and on your own servers.

| What | Where to | When | What it learns |
|---|---|---|---|
| Your servers | Jellyfin, Navidrome, Audiobookshelf, Podfetch: the addresses you entered | while Matinee runs | what you watch and hear, as with any of their apps; the sync of settings goes through your Jellyfin account |
| Updates | GitHub (`github.com` and its download servers), USA | at night, on the main menu now and then, in the settings; with "Look for Updates: Only When I Ask" only when asked | the device's IP address and that it asks for Matinee's newest version |
| Weather | Open-Meteo (`api.open-meteo.com`, `geocoding-api.open-meteo.com`) | only with a place chosen for the screensaver (and while searching for one) | the place's coordinates (or the name typed), the IP address |
| Library check | The Movie Database (`image.tmdb.org`) | only while the library check shows proposals | the IP address and which posters are shown |
| Internet radio | the stations' own servers | only the stations added in Navidrome, while one plays | the IP address, as any radio player |
| Time | Physikalisch-Technische Bundesanstalt (`ptbtime1.ptb.de` …), Germany | systemd-timesyncd, regularly | the IP address (NTP) |
| Network check | DNS lookup of `example.com` (`KINO_NET_CHECK_HOST`) | when a server does not answer | nothing is sent to it; the DNS resolver in use sees the lookup |
| Finding Jellyfin | the home network (UDP broadcast, port 7359) | on the sign-in page | stays in the local network |
| Moving an account | another Matinee in the same network or tailnet | only when started in Settings → Account | stays between the two devices |
| Phone remote | phones in the home network open its page here (port 8767) | only when turned on in Settings → Phone Remote | they get the remote's files and Jellyfin's address, nothing else of Matinee; the remote then talks to Jellyfin |
| Report to support | the address in `deploy/support-url` (whoever sells and supports Matinee) | only when Send is chosen in Settings → System → Report to Support | logs, system details and the settings without passwords or tokens (see [setup](setup.md#a-report-to-support)); the IP address |
| Jellyfin on this device | Docker Hub (`docker.io`) for its image, once a day; the metadata providers Jellyfin uses (TMDB, MusicBrainz, …) for titles, posters and descriptions | only with `deploy/server.sh` | the IP address, and which titles are looked up (Jellyfin's own, see its documentation) |
| Tailscale | Tailscale's coordination servers | only if installed, under your own account | as Tailscale describes it |

The installer (`install.sh`, `get-matinee`) fetches packages from the distribution's mirrors and, on Fedora and openSUSE
if chosen, RPM Fusion or Packman; `get-matinee` fetches Matinee from GitHub.
