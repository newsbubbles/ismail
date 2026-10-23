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
| `listen_start(streams, mic_ok, max_min, label, project)` | starts recording in the background; replies with every live input, by name, and where the session went; a mic needs `mic_ok=<their note's id>` |
| `listen_heard(since)` | with a mic: their words as they say them, a few seconds behind, each with its time and the live set's bar |
| `listen_peek()` | while it records: time so far, levels, dropouts, and a reading of the last 30 s |
| `listen_mark(label, ago_s)` | marks a moment ("that drop"), now or `ago_s` seconds back |
| `listen_note(text)` | a message the person typed, placed over the seconds they spent typing it |
| `listen_stop(session, read=True)` | stops, then reads in the background; `discard=True` deletes the session unread. `session` is an id or a full path |
| `listen_read(part)` | the reading: `summary`, `timeline`, `stretch:<k>`, `all`, with `start`/`end` in seconds |

Streams are `speakers` (what plays on the default output), `loop:<name>` (another output), `mic` (the default
mic) and `mic:<name>`. The default is `['speakers']`.

## Where sessions go

Never the system drive by default. In order:

1. `<project>/listen/` when `listen_start(project=<the song's folder>)` names one.
2. The running live set's folder (`<project>/listen/`), so a jam's session sits with the jam.
3. `$ISMAIL_LISTEN_HOME`, when set.
4. `_listen/` in the installed songs folder, on the checkout's drive.

An id resolves wherever its session went (an index in `_listen/`, plus the old home `~/.ismail/listen`), so
`listen_stop('<id>')` works after the home changed; a full path always works.

**The free-space floor.** Each input takes about 1.4 GB an hour, and the reading copies each stretch once more.
`listen_start` refuses a drive with under 2 GB free (`FREE_FLOOR_GB` in `ismail/listen.py`) and says what to do:
free some space, name a project on a drive with room, or set `ISMAIL_LISTEN_HOME`. A running session checks every
10 s; under the floor it stops by itself, keeps what it recorded, and the next listen op says so once (under
"NEW since your last call", with the mic off on the phone page). `listen_stop` does not read on a short drive.
The background processes keep their temp files in the session's own `tmp/` folder.

## Words as they are said

With a mic on, the recorder also cuts the mic at pauses (2 s, as the reading groups utterances), or at 8 s at
most, and sends each chunk (16 kHz, under 300 KB) to the speech server the reading uses (`ISMAIL_SPEAK`, by
default `http://127.0.0.1:8765`). Nothing starts or installs that server: when it does not answer, `listen_start`
and `listen_heard` say so, and the recording, `listen_peek`, `listen_mark` and `listen_note` still work;
`listen_stop` reads the words from the recording once it runs.

`listen_heard()` gives the last 20 lines, `listen_heard(since=<n>)` only the newer ones; each reply ends with the
`n` to pass next. A line is `#n m:ss bar 17.3: the words`: the time since the session began, placed as the reading
places words (the mic's lag behind the speakers is measured every minute when the speakers are recorded too), and
the bar the live set had sounding then, from the live engine's clock (the same heard beat its played keys use).
Without a live set, or with one started before this op existed, there are no bars and the reply says why. A chunk
that fails is tried once more, then shown as "not heard" with the reason: never dropped.

## The mic needs their yes

`listen_start` refuses a mic stream unless `mic_ok` is the person's own yes to it, one of:

- a phone note: a voice note's `id`, or the inbox number `n` of a panel answer they typed (`phone_watch` lists
  both). Only a panel's text fields count as their words, never an option they picked or a box they ticked; an
  answer taken by a phone server from before that rule cannot tell them apart and is refused (ask again).
- a VR voice note: `vr:<scene>:<event id>`, the id of its `voice_message` in `stage_events`. Their voice in VR is
  their yes; the note counts only when its recording landed in the scene's `voice/` folder from the headset.
- their words in chat: `mic_ok='chat: <their exact words>'`, quoted as they wrote them. This is weaker than a note
  they sent, so each one is logged on this computer (`ts`, `op`, the first 300 characters of the quote) in
  `$ISMAIL_MIC_CONSENT_LOG`, else `~/.ismail/mic_consent.jsonl`, never in the repository.

A note must have come from them (not an agent's line, not a button tapped on your question), within the last 12
hours; every yes must name the mic, microphone, record, recording or listening, and not read as a no ("delete that
recording" is a no). `mic_ok=True` is refused: ask them in words first, then pass the note. The recorder checks the
yes again before it opens any device (a chat yes by its log line, so it is logged once). The reply names every live
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
- `listen_heard` hears the mic with the music in it, so lyrics can come through as words, as in the reading.
