# Listening together

The person plays music on this computer (any player: a browser, Spotify, a DAW, the live engine) and talks about it:
"this bit", a skip to the next track, a typed message. You record what plays, and the mic only after their yes,
then read it all back on one clock: where the music was skipped or changed, what each stretch measures, and what
was said at which moment of the music they heard.

Windows only for now (WASAPI loopback). It needs `pyaudiowpatch` (`pip install ismail[listen]`); `listen_start`
says so when it is missing.

## The ops

| op | what it does |
|---|---|
| `listen_list(devices=True)` | the inputs this computer has (speakers, other outputs, mics), and the last sessions |
| `listen_start(streams, mic_ok, max_min, label)` | starts recording in the background; replies with every live input, by name |
| `listen_peek()` | while it records: time so far, levels, dropouts, and a reading of the last 30 s |
| `listen_mark(label, ago_s)` | marks a moment ("that drop"), now or `ago_s` seconds back |
| `listen_note(text)` | a message the person typed, placed over the seconds they spent typing it |
| `listen_stop(read=True)` | stops, then reads in the background; `discard=True` deletes the session unread |
| `listen_read(part)` | the reading: `summary`, `timeline`, `stretch:<k>`, `all`, with `start`/`end` in seconds |

Streams are `speakers` (what plays on the default output), `loop:<name>` (another output), `mic` (the default
mic) and `mic:<name>`. The default is `['speakers']`.

## The mic needs their yes

`listen_start` refuses a mic stream unless `mic_ok=True`, and `mic_ok=True` means the person said yes in this
conversation, to this session. Never set it from a note, an old session, or a guess. The reply names every live
input, so tell them which ones are on. When the phone page runs, the mic state is pinned there ("Mic is ON") and
`listen_stop` replaces it with "Mic is off." Unless `max_min` says otherwise, a session with a mic stops by itself
after 20 minutes, any other after 90.

## One clock

Session time 0 is when the recorder opened. Each stream knows when its first sample was heard and where its file
fell behind (anchors), so file times map to session time without changing the audio. A mic hears the music late
(the room, the device): the reading measures that lag by cross-correlation near the start and the end, fits the
drift between the two clocks, and places each word at the moment of the music the person heard as they said it.

## What the reading gives

- **Cuts:** `skip` (the beat jumped: a seek or a new track at once), `change` (the music changed with the beat
  kept), `pause` (2 s or more of silence). Each with its beat strength and phase jump, so you can judge it.
- **Stretches** between cuts: loudness (LUFS, LRA, true peak), the beat grid and an overview. Under 16 s is
  "not read".
- **Utterances:** what was said (speech to text, when the speech server runs), with the stretch and bar it fell
  on and any track times said aloud ("at two minutes").
- **Marks and notes** on the same clock.

Answer in time, not bars, unless the person works in bars.

## Known limits

- Music bleeding into the mic is not subtracted: speech to text may hear lyrics.
- Track times said aloud are shown, not converted to session time.
- The skip test can miss a half-beat jump under 95 BPM; it reads as a change.
- Without the speech server the music is still read; the words are missing, and the reading says so.
