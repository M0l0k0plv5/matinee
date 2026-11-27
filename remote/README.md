# Matinee Remote

A remote for the phone: the Matinees of a Jellyfin account, controlled through Jellyfin's remote control
(see [Jellyfin's remote control](../docs/controls.md#jellyfins-remote-control)). On the remote, swipe on the surface to move, tap to
select (at its edge: one step that way, held: again and again); Back, Home, Menu, play/pause and the volume below it
(held, the volume goes on); above it what plays, with its cover, its place, winding, stop, the next and the one
before, its audio and subtitles; a search typed on the phone's keyboard goes into Matinee's search.

It opens with the library: what Jellyfin has as covers to swipe through, Continue Watching, Up Next, the movies and the
shows. A tap plays one on the Matinee, where it was left or from the beginning (a show: its next episode). What plays
stands below it; a tap there, or Remote, opens the remote.

Below Jellyfin's rows come those of the parts the phone is not signed in to: the music played last and what is new
(Navidrome), the audiobooks begun and new (Audiobookshelf), the podcast episodes begun and new (Podfetch). A Matinee
puts these short lists with the Jellyfin account every few
minutes, with small covers in them, and only while "Same on Every Device" is on; no address, key or password of the
other servers goes there. A tap sends the kind and the id, and Matinee gets the rest from its own servers.

It is a page of its own, without dependencies, that talks to Jellyfin only: nothing to install on the phone, and
nothing on the TV computer is opened to the network unless it serves the page itself (below). Open it in Safari and choose Share → Add to Home Screen: it then
opens full screen, like an app.

## Served by Matinee itself

The easiest way: Settings → Phone Remote → On. Matinee then serves this folder in the home network on port 8767 and shows
its address as a QR code; the phone's camera opens it, and Share → Add to Home Screen keeps it. Only these files and the
Jellyfin address can be reached there, nothing else of Matinee (backend/phone_remote.py); off, nothing listens. It comes
over plain HTTP, so anyone in the same network could change it on its way: in a home network that is fine, otherwise
put it on a server of one's own as below.

## Putting it on a server

Any web server that serves static files over HTTPS will do; best one that is only reachable where Jellyfin is (the same
home network or VPN), so nobody else can change the page. Copy this folder there, and optionally put a `config.json`
beside `index.html` with the Jellyfin address to prefill:

```json
{ "server": "https://jellyfin.example:8920" }
```

The page asks Jellyfin from its own address, so Jellyfin must allow that (it does by default: any origin).

Signing in keeps a Jellyfin access token on the phone, as Jellyfin's own apps do; Sign Out ends it, and Jellyfin's
Dashboard → Devices can end it from there.

## Who can control what

That is Jellyfin's to say: a user controls their own Matinees; another user's only with the right to control others'
sessions (Dashboard → Users). Give that right only to whoever helps the others.
