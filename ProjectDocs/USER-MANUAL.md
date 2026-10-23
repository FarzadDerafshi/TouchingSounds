# User manual

Open the app at **https://touchingsounds.example.com** (anywhere, after logging in), or
**http://192.168.100.51:8000** on the office network.

## Layout

- **Phone:** one screen at a time, switched with the bar at the bottom: **Player**,
  **Search**, **Favorites**, **Recent**. While you browse, a mini-player above the bar
  shows what's on; tap it to go back to the Player, or use its play/pause button.
- **Tablet (landscape) and computer:** the player on the left, the library (Search /
  Favorites / Recent) on the right.

## Choosing the speaker

The button at the top shows the speaker you're controlling, with a dot that's green
when it answers and red when it doesn't. Tap it to switch between **F&F Touching Sounds**
(the SoundTouch 20 at home) and **OfficeSounds** (the Wireless Link Adapter in the
office). The player, volume, source and the six presets follow the chosen speaker;
favorites, recent and search are the same for both. Each browser remembers its last
choice.

## Adding a speaker

Tap the speaker button, then **Add or manage speakers**. With more than one speaker you
first see the list; **Add a speaker** starts the search.

1. **Turn the new speaker on.** If it's already on your Wi-Fi it shows up in the list
   within a few seconds: tap **Add**.
2. **Not in the list?** Pick the easiest way in:
   - **Plug it in with a network cable** (for speakers with a network port, like the
     SoundTouch 20). Once it shows up, choose your Wi-Fi from the networks *the speaker*
     hears, type the password, then unplug the cable when asked.
   - **No cable: use setup mode.** Pick the model; the app shows its buttons as they sit
     on the speaker, which ones to hold, a countdown, and the light to wait for. This
     **resets the speaker** (its old Wi-Fi, presets and Spotify login). Then give it your
     Wi-Fi from your phone: join the speaker's own "Bose SoundTouch …" network, tap
     through the steps, and switch back. If that doesn't work, Bose's SoundTouch app can
     do this one step; then come back and tap **Add a speaker**.
   - **Enter its address** if you know it.
3. **Leave the rest to the app.** It connects the speaker to your server and restarts
   it (about a minute). Meanwhile, name it and choose its preset buttons (the same as
   another speaker, or keep its own).
4. **Do you hear music?** A station plays quietly on the new speaker. **No** plays it
   again, louder, and says what the speaker reports.

Tip: give the speaker a fixed address in your router, so the server always finds it.

**Change Wi-Fi** (in the speakers list) teaches an online speaker a further network. It
keeps its current one and joins whichever it reaches first, which is how you prepare it
for a new router. A speaker can't forget a network; only a factory reset clears them.

**Remove** takes a speaker out of the app with its alarms (not the first speaker). Tick
*Also put back its original settings* only when you're giving it away: it restarts
and stops working with TouchingSounds.

The theme button cycles **auto** (half-filled circle: follows your phone or computer's
light/dark setting, the default), **light** (sun) and **dark** (moon).

## Player

- Station logo, name and details, with **Favorite** and **Save to preset** buttons when an
  internet-radio station from the app is playing. The glow behind the player takes its
  colour from the station's logo.
- When you pick a station, the player shows it straight away with *Tuning in…* until the
  speaker starts playing it (a few seconds).
- **Song and cover:** for many stations the player shows the song that's playing: the
  station name moves to the small badge, the song becomes the title with the artist
  under it, and the album cover replaces the logo (the station's logo then sits in the
  cover's corner). It updates by itself when the song changes. Stations that don't
  publish their songs (e.g. Number One FM, Europa Plus) show the station as before.
- **Big cover and sharing:** tap the cover to see it full screen. *Share* sends the
  cover picture with the song, artist and station through your phone's share menu
  (WhatsApp, Messages, …); *WhatsApp* opens WhatsApp with the text only. *Find this
  song* searches Spotify, YouTube or Apple Music. Long-press the picture to save it.
  Swipe down or tap ✕ to close.
- **Sleep timer:** the moon button next to stop. Pick 15, 30 or 45 minutes, or 1, 1.5 or
  2 hours; the button then shows the time left. Over the last minute the sound fades
  out, then the speaker switches to standby (its volume is set back for next time).
  It runs on the server, so you can lock the phone or close the app. Tap the button to
  pick another time or *Turn off*. Each speaker has its own timer; a new app release
  cancels a running one.
- **Alarms:** the alarm-clock button next to the moon. *New alarm*: pick the time, the
  days it repeats (no days = once, then it switches itself off), the station (from your
  presets, favorites or recent) and the wake-up volume. At that time the speaker comes
  out of standby, starts the station quietly and gets louder over a minute and a half.
  If that station won't play, one of your presets plays instead. The button shows the
  next alarm's time. Tap an alarm to change it, *Try it* to hear it now, or *Delete*;
  the switch turns it off without deleting it. Each speaker has its own alarms, and
  they survive app releases. If an alarm couldn't play (speaker unplugged or offline,
  server off), the button gets a red dot and the alarm list says why.
- Play/pause, stop, volume and mute. Previous/next appear only for sources with tracks
  (Spotify, Bluetooth).
- **Presets:** the speaker's six buttons. Tap one to play it. The one currently
  playing has an orange border. Tap an **empty** button (dashed, with a +) to pick a
  station for it: the app opens Search (Favorites and Recent work too) with *Choose a
  station for preset N* at the top; tap a station and it's saved to that button.
  *Cancel*, or going back to the Player, stops choosing.
- **Source:** only the inputs the selected speaker has: AUX and Bluetooth on the
  SoundTouch 20, AUX on the adapter. Spotify starts from the Spotify app as usual
  ("Connect to a device"), on the SoundTouch 20. The adapter plays through the system
  it's plugged into.
- **Speaker unreachable:** if the speaker doesn't answer (switched off, off Wi-Fi, or the
  link to it is down), the player says so and its controls are greyed out. The app keeps
  trying every few seconds and switches back by itself; *Try again* checks at once.

**Playing OfficeSounds on a Bluetooth speaker:** SoundTouch devices only *receive*
Bluetooth; they can't send audio to a Bluetooth speaker. Connect the adapter's wired
output (3.5 mm or optical) to a speaker's AUX input, or to a Bluetooth **transmitter**
(set to TX) paired with the speaker. Then set the adapter's volume high in the app and
adjust on the speaker.

## Finding a station

Type a name in **Search**, e.g. *Europa Plus*, *BBC* or *jazz*. Results come from
radio-browser.info, a free directory of 50,000+ stations, most-listened first.
Each result shows country, format and genre tags.

- Tap the row or the **▶** button to play it on the speaker.
- **♡** adds it to Favorites (tap again to remove).
- The **grid** icon saves it to a preset button.

You can also paste a stream address (starting with `http://` or `https://`) into the
search box to play it directly. Both `https` and `.m3u8` (HLS) streams work; the
server converts them for the speaker.

## Favorites and Recent

**Favorites** is your own station list, the same on every device. **Recent** lists the
last 30 stations played from the app; *Clear history* empties it.

## Changing a preset button

On any station (search result, favorite, recent, or the one playing) tap
**Save to preset**, then pick the button 1-6 (or tap an empty button in the Player, see
above). The speaker's physical button plays that
station from then on, and the speaker shows its name. Long-pressing a button on the
speaker while a station plays works as well.

## Install as an app

Installing needs the **https** address (touchingsounds.example.com).

- **Android (Chrome):** menu ⋮ → *Install app* (or *Add to Home screen*).
- **iPhone/iPad (Safari):** Share → *Add to Home Screen*.
- **Windows/Mac (Chrome or Edge):** the install icon at the right of the address bar, or
  menu → *Install TouchingSounds*.

The app then opens in its own window with its own icon. It updates itself whenever a new
version is released.

## Version

The bottom of the page shows the version and build, e.g. `TouchingSounds v1.0.0 · 1533c8f`.
