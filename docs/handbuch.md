# Matinee – Handbuch

Matinee macht aus einem kleinen Computer am Fernseher einen Player für deine eigenen Filme, Serien, Musik, Hörbücher und
Podcasts. Alles kommt von deinen eigenen Servern zu Hause. Matinee braucht kein Konto bei irgendeinem Anbieter und sendet
nichts an Dritte.

Dieses Handbuch erklärt die Bedienung am Fernseher. Technische Einzelheiten stehen in den englischen Dokumenten im
Ordner `docs` (Einrichtung, Konfiguration, Server).

## Inhalt

1. [Was du brauchst](#was-du-brauchst)
2. [Der erste Start](#der-erste-start)
3. [Noch kein Medienserver?](#noch-kein-medienserver)
4. [Filme auf das Gerät kopieren](#filme-auf-das-gerät-kopieren)
5. [Die Fernbedienung](#die-fernbedienung)
6. [Das Hauptmenü](#das-hauptmenü)
7. [Filme und Serien](#filme-und-serien)
8. [Musik, Hörbücher und Podcasts](#musik-hörbücher-und-podcasts)
9. [Netflix, Prime Video und andere Dienste](#netflix-prime-video-und-andere-dienste)
10. [Für Kinder: Altersfreigabe und einfache Einstellungen](#für-kinder-altersfreigabe-und-einfache-einstellungen)
11. [Lautsprecher, Kopfhörer und Fernbedienungen per Bluetooth](#lautsprecher-kopfhörer-und-fernbedienungen-per-bluetooth)
12. [Updates](#updates)
13. [Wenn etwas nicht klappt](#wenn-etwas-nicht-klappt)
14. [Datenschutz](#datenschutz)

## Was du brauchst

- **Den TV-Computer** am Fernseher, per HDMI angeschlossen. Ein gebrauchter Mini-PC mit Intel-Prozessor (ab 6.
  Generation) und 8 GB Arbeitsspeicher reicht gut. Matinee läuft auch mit 2 Kernen und 2 GB, wichtiger ist ein
  Grafikchip, der Videos selbst entschlüsselt.
- **Einen Jellyfin-Server** mit deinen Filmen und Serien. Hast du noch keinen, kann Matinee ihn gleich mit auf dem
  Gerät einrichten (siehe [Noch kein Medienserver?](#noch-kein-medienserver)).
- **Eine Fernbedienung.** Fast jede funktioniert: USB- und Funkfernbedienungen, Air-Mäuse, Bluetooth-Fernbedienungen
  wie die von Fire TV und Xbox-Controller.
- **Netzwerk:** am besten per Kabel, WLAN geht auch.
- Wenn du willst: **Navidrome** für Musik, **Audiobookshelf** für Hörbücher, **Podfetch** für Podcasts.

### Matinee selbst installieren

Hast du ein Gerät ohne Matinee, installierst du zuerst Linux, zum Beispiel Debian 13. Danach gibst du als der Benutzer,
mit Matinee laufen soll, im Terminal ein:

```sh
curl -fsSL https://github.com/M0l0k0plv5/matinee-releases/releases/latest/download/get-matinee | sh
```

Das lädt Matinee, prüft die Signatur und richtet alles ein. Dabei fragt es einmal nach deinem Passwort. Gegen Ende fragt
es, ob Jellyfin mit auf dem Gerät laufen soll. Danach den Computer neu starten: Er startet ab jetzt direkt in Matinee.
Welche Linux-Versionen unterstützt werden, steht in der [README](../README.md#what-you-need).

## Der erste Start

Schalte den Fernseher und den TV-Computer ein. Matinee führt dich durch ein paar Schritte:

1. **Sprache:** Deutsch oder English.
2. **Netzwerk:** Mit Kabel ist nichts zu tun. Sonst **WLAN** wählen, dein Netz aussuchen und das Passwort eingeben,
   mit dem Buchstabenfeld auf dem Bildschirm oder einer Tastatur.
3. **Jellyfin:** Matinee zeigt die Jellyfin-Server, die es im Heimnetz findet. Wähle deinen mit OK oder gib seine
   Adresse ein (zum Beispiel `http://192.168.1.10:8096`). Dann Benutzername, Passwort und **Anmelden**.
4. **Fernbedienung:** Hier koppelst du eine Bluetooth-Fernbedienung oder einen Xbox-Controller. Eine
   USB-Fernbedienung braucht nichts.

Jeden Schritt außer der Sprache kannst du mit **Später** überspringen. Unter **Einstellungen → System →
Ersteinrichtung** lassen sich die Schritte jederzeit wiederholen.

Musik, Hörbücher und Podcasts richtest du über ihre Kacheln im Hauptmenü ein: Kachel öffnen, Server und Anmeldung
eintragen. Brauchst du sie nicht, lässt du sie einfach weg.

## Noch kein Medienserver?

Wurde Matinee mit eigenem Server eingerichtet, zeigt der Jellyfin-Schritt **Jellyfin auf diesem Gerät**:

1. Einen Benutzernamen und ein Passwort wählen. Das wird dein Jellyfin-Konto, mit allen Rechten.
2. **Auf diesem Gerät einrichten** wählen.

Matinee richtet Jellyfin ein, legt die Bibliotheken **Filme** und **Serien** an und meldet dich an. Mit demselben
Benutzer kannst du auch die Jellyfin-Apps auf Handy und Tablet verwenden: Sie finden den Server im Heimnetz von
selbst.

## Filme auf das Gerät kopieren

Unter **Einstellungen → Filme & Serien → Filme auf dieses Gerät** zeigt Matinee die Adresse, den Benutzernamen und das
Passwort für den Ordner „Matinee“ im Heimnetz.

- **Windows:** Im Explorer oben in die Adresszeile `\\192.168.1.20\Matinee` eingeben (deine Adresse steht auf dem
  Fernseher), dann Benutzername und Passwort.
- **Mac:** Im Finder **Gehe zu → Mit Server verbinden** (⌘K), `smb://192.168.1.20/Matinee` eingeben, dann
  Benutzername und Passwort.

Dort liegen zwei Ordner, einer für Filme und einer für Serien. So findet Jellyfin am meisten:

```
Filme/
  Der Film (2019)/
    Der Film (2019).mkv
Serien/
  Die Serie/
    Season 01/
      Die Serie S01E01.mkv
      Die Serie S01E02.mkv
```

Jellyfin bemerkt neue Dateien selbst. Nach ein paar Minuten tauchen sie in Matinee auf, Bilder und Beschreibungen holt
Jellyfin aus dem Internet.

Matinee spielt, was auf deinem Server liegt: eigene Aufnahmen, Filme ohne Kopierschutz, Scheiben ohne Kopierschutz. Einen
Kopierschutz zu umgehen (das betrifft die meisten gekauften DVDs und Blu-rays) ist in Deutschland nicht erlaubt
(§ 95a UrhG). Matinee hilft dabei nicht.

## Die Fernbedienung

Alles geht mit den Pfeiltasten, **OK** und **Zurück**.

| Taste | Im Menü | Während eines Films |
|---|---|---|
| Links / Rechts | bewegen | 10 Sekunden zurück / vor (gehalten: schneller) |
| Hoch / Runter | bewegen | lauter / leiser |
| OK | öffnen, abspielen | Pause; Intro überspringen; im Abspann die nächste Folge |
| OK gedrückt halten | was man damit tun kann: Meine Liste, als gesehen markieren, zur Serie … | – |
| Zurück | eine Seite zurück | Leiste oder Menü schließen, sonst Film beenden |
| Home | Anfang des Bereichs, dann das Hauptmenü | – |
| Menü | Lautstärke | Infos: Kapitel, Tonspur, Untertitel, Tempo |
| Wiedergabe/Pause | Film oder Folge direkt starten | Pause |
| Vor- / Zurückspulen | – | 30 Sekunden vor / zurück |

**OK gedrückt halten** ist der wichtigste Trick: Es zeigt überall, was mit dem gewählten Titel noch geht. Wo du gerade
bist, führt es auch zu den Einstellungen und zu dieser Tastenliste (**Einstellungen → Bedienung**).

Beim Spulen wandert erst eine Marke über die Leiste. Kurz nach dem letzten Druck springt der Film dorthin. So lädt er
nicht bei jedem Druck neu.

**Lautstärke:** Matinee regelt die Lautstärke des Computers. Das wirkt auch bei Bluetooth-Kopfhörern. Die
Lautstärketasten einer Fire-TV-Fernbedienung steuern dagegen den Fernseher direkt, nicht Matinee.

**Xbox-Controller:** A ist OK, B ist Zurück, das Steuerkreuz bewegt, X ist Wiedergabe/Pause, Y die Suche.

## Das Hauptmenü

Unten liegen die Kacheln, darüber zeigt ein Regal, was in der gewählten Kachel steckt. **Hoch** geht ins Regal, **OK**
öffnet, **Zurück** kommt wieder herunter.

- **Als Nächstes:** alles, was du angefangen hast, das Neueste zuerst. Filme und Serien, danach Hörbücher und Podcasts.
- **Filme & Serien:** deine Jellyfin-Bibliothek.
- **Musik, Hörbücher, Podcasts:** sobald eingerichtet.
- **Suche:** sucht in allem auf einmal. Mit der Fernbedienung über das Buchstabenfeld, mit einer Tastatur einfach
  lostippen.
- **Einstellungen**

## Filme und Serien

- **Bibliotheken** oben auf der Startseite: sortieren (A–Z, neu hinzugefügt, Jahr, Bewertung), nur Ungesehenes,
  nach Genre filtern. Matinee merkt sich die Auswahl.
- **Weiterschauen:** Angefangenes, danach die nächste Folge jeder Serie, die du schaust.
- **Meine Liste:** OK gedrückt halten auf einem Titel, oder der Knopf auf seiner Seite.
- **Sender:** Matinee macht aus deiner Bibliothek Fernsehsender, zum Beispiel nach Genre oder Jahrzehnt, mit
  Programmübersicht. Einschalten, und es läuft gerade etwas. Hoch und Runter schalten um. Du findest sie als Knopf nach
  den Bibliotheken.
- **Intros überspringen:** Während eines Intros zeigt der Player „OK ▸ Intro überspringen“, im Abspann
  „OK ▸ Nächste Folge“. Dafür braucht Jellyfin die Markierungen, etwa vom Plugin Intro Skipper.
- **Nächste Folge:** Am Ende einer Folge startet die nächste nach 5 Sekunden. Nach drei Folgen ohne Tastendruck fragt
  Matinee „Schaust du noch?“: OK spielt weiter, Zurück beendet. Ohne Antwort hört die Wiedergabe nach einer Minute auf.
- **Tonspur und Untertitel:** mit **Menü** während des Films. Was du für eine Serie wählst, gilt auch für ihre
  nächsten Folgen. Fehlen Untertitel, findet **Untertitel suchen** welche im Internet, wenn Jellyfin dafür ein Plugin
  hat (etwa OpenSubtitles).
- **Bibliothek prüfen** (**Einstellungen → Filme & Serien**): findet Filme, die Jellyfin falsch erkannt hat, und
  korrigiert sie nach deiner Bestätigung. Braucht ein Jellyfin-Konto mit Administratorrechten.

Was du in Matinee schaust, kennt auch Jellyfin. Auf dem Handy geht es an derselben Stelle weiter.

## Musik, Hörbücher und Podcasts

- **Musik (Navidrome):** Alben, Künstler, Playlists, Zufallswiedergabe. **Läuft gerade** zeigt Cover, Fortschritt und
  Warteschlange. Unten in Matinee zeigt eine Leiste, was läuft. Ein Film beendet die Musik.
- **Hörbücher (Audiobookshelf):** Ein Buch läuft im Hintergrund wie Musik, mit Kapiteln, 30 Sekunden vor und zurück
  und einstellbarem Tempo. Audiobookshelf merkt sich die Stelle, die App auf dem Handy macht dort weiter.
- **Podcasts (Podfetch):** Sendungen nach Kategorie, die neuesten Folgen, angefangene Folgen. In der Podcast-Suche
  findest du auch neue Podcasts zum Abonnieren.
- **Einschlaf-Timer:** der Mond-Knopf in **Läuft gerade** oder die Wiedergabe-Taste gedrückt halten: 30, 60 oder 90
  Minuten, oder am Ende des Films, der Folge oder des Kapitels.

## Netflix, Prime Video und andere Dienste

Diese Dienste laufen in voller Qualität nur auf zertifizierten Geräten. Steck darum einen Streaming-Stick (Fire TV,
Google TV, Apple TV) in einen anderen HDMI-Eingang des Fernsehers und trag ihn ein:

**Einstellungen → Andere Geräte am Fernseher → Gerät hinzufügen**, mit dem HDMI-Eingang und einem Namen.

Danach hat er eine Kachel im Hauptmenü. OK darauf schaltet den Fernseher auf den Stick. Jede Taste der
Matinee-Fernbedienung schaltet zurück. Dafür muss am Fernseher HDMI-CEC eingeschaltet sein (bei Samsung heißt es
Anynet+, bei Sony Bravia Sync, bei LG SimpLink). Der Computer braucht oft einen kleinen CEC-Adapter. Fehlt er, sagen
die Einstellungen das.

## Für Kinder: Altersfreigabe und einfache Einstellungen

**Altersfreigabe** (**Einstellungen → System → Altersfreigabe**): Filme und Serien mit höherer Freigabe zeigt Matinee
nicht und spielt sie nicht ab, auch nicht in der Suche, in „Weiterschauen“ oder bei ähnlichen Titeln. Zur Wahl stehen
„Nur ohne Altersbeschränkung“ sowie „Bis 6 Jahre“, „Bis 12 Jahre“ und „Bis 16 Jahre“. Folgen gelten wie ihre Serie.
Titel ganz ohne Freigabe bleiben sichtbar, wie bei Jellyfin selbst. Prüfe also, ob deine Bibliothek Freigaben hat.

**Einfache Einstellungen** (**Einstellungen → System → Einfache Einstellungen**): Danach zeigen die Einstellungen nur
noch Alltägliches (Sprache, Bildschirmschoner, Video und Audio, Geräte, Neustart und Ausschalten). Server, Konten und
Netzwerk sind ausgeblendet. Zum Einschalten wählst du einen vierstelligen Code, zweimal. Den Code braucht man, um
wieder **Alle Einstellungen** zu zeigen. Solange die einfachen Einstellungen an sind, lässt sich die Altersfreigabe
nicht ändern.

Code vergessen? Dann hilft nur ein Eingriff am Computer selbst; wende dich an den Support.

## Lautsprecher, Kopfhörer und Fernbedienungen per Bluetooth

**Einstellungen → Geräte** sucht nach Geräten in der Nähe. Bring Lautsprecher oder Kopfhörer vorher in den
Kopplungsmodus. OK auf einem Gerät verbindet es, OK gedrückt halten entfernt es. Der Ton läuft immer über ein Gerät:
Ein neues löst das vorige ab.

Hängt der Ton dem Bild hinterher, gleicht **Einstellungen → Video und Audio → Ton angleichen** das aus (50 bis 300
Millisekunden). Matinee merkt sich den Wert für jeden Lautsprecher einzeln.

Eine Fire-TV-Fernbedienung koppelst du, indem du ihre Home-Taste 10 Sekunden hältst, während Matinee sucht.

## Updates

Matinee aktualisiert sich nachts selbst, nie während eines Films oder Lieds. **Einstellungen → System →
Softwareupdates** zeigt die Version und was neu ist, und installiert ein Update sofort, wenn du willst. Startet eine
neue Version nicht richtig, kehrt Matinee von selbst zur vorigen zurück.

## Wenn etwas nicht klappt

| Meldung | Was tun |
|---|---|
| **Kein Netzwerk** | Kabel prüfen, oder unter **Einstellungen → WLAN** das Netz neu wählen. |
| **Jellyfin ist nicht erreichbar** | Das Netzwerk geht, aber der Server antwortet nicht. Ist er eingeschaltet? Matinee macht von selbst weiter, sobald er antwortet. |
| **Vom Server abgemeldet** | Das Passwort wurde geändert oder das Gerät in Jellyfin entfernt. Neu anmelden. |
| **Verbindung zum Server verloren, warte auf ihn…** | Matinee wartet und spielt an derselben Stelle weiter, sobald der Server wieder da ist. Zurück beendet das Warten. |
| **Diese Seite kann gerade nicht geladen werden** | Matinee versucht es nach ein paar Sekunden selbst noch einmal, OK sofort. |
| Ein Film ruckelt | Dein Computer kann das Video nicht selbst entschlüsseln. Jellyfin wandelt solche Videos eigentlich um. Hilft das nicht, unter **Einstellungen → Filme & Serien → Streaming-Qualität** eine kleinere Qualität wählen. |
| Kein Ton über Bluetooth | Unter **Einstellungen → Geräte** den Lautsprecher noch einmal mit OK verbinden. |
| Die Fernbedienung reagiert nicht | Batterien prüfen; eine Bluetooth-Fernbedienung unter **Einstellungen → Geräte** neu koppeln (mit einer Tastatur oder einer zweiten Fernbedienung). |
| Alles hängt | Unter **Einstellungen → System → Neu starten**. Oder einfach den Stecker ziehen und wieder einstecken: Das schadet Matinee nicht. |

Kommst du nicht weiter: **Einstellungen → System → Bericht an den Support → Bericht senden**. Matinee schickt einen
Bericht über das Gerät (ohne Passwörter) und zeigt einen Code. Nenne dem Support diesen Code.

Den Stecker zu ziehen ist ausdrücklich erlaubt: Matinee ist dafür gebaut und startet danach normal.

## Datenschutz

Matinee hat kein eigenes Konto, zeigt keine Werbung und sendet keine Nutzungsdaten. Was es weiß (Server, Anmeldungen,
Einstellungen, was du geschaut hast), bleibt auf dem Gerät und auf deinen eigenen Servern. Ins Internet geht nur:

- die **Suche nach Updates** (bei GitHub), nachts und ab und zu im Hauptmenü. Mit **Nach Updates suchen: Nur auf
  Wunsch** nur, wenn du selbst nachsiehst.
- das **Wetter** für den Bildschirmschoner, nur wenn du einen Ort gewählt hast: dessen Lage geht an Open-Meteo.
- ein **Bericht an den Support**, nur wenn du ihn selbst sendest.
- die Uhrzeit, wie bei jedem Computer.
- Läuft Jellyfin auf dem Gerät, holt es Bilder und Beschreibungen zu deinen Filmen aus dem Internet.

Alles im Einzelnen steht unter **Einstellungen → System → Lizenzen und Datenschutz**.
