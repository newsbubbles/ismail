# The phone page: a live set in the person's pocket

The person listens to a live set on their phone, away from the computer, with the screen off. They talk back the
way they do in VR, tap feedback without unlocking, and answer what you put on the page: questions, blind exams,
downloads, buttons. It runs over the tailnet; nothing is public.

## When to offer it

Offer it when a live set plays and the person is about to step away from the computer: a walk, bed, the kitchen, a
drive as a passenger. Offer it too when they say they want to listen elsewhere, or when the set should keep taking
their feedback while they are away. One line is enough: "Want it on your phone? You can talk to me from the earbuds
and tap feedback without unlocking." Don't offer it when they are at the computer and the room speakers serve them.

## Hosting

The phone reaches the page over a private network, never the open internet.
- **Tailscale (today).** Tailscale must be on the computer and the phone, signed in to the same tailnet: that is the
  person's to install and sign in to, so say what they will see. `phone_start` gives the https address
  (`https://<computer>.<tailnet>.ts.net:8870/`) when `tailscale serve` proxies the port, or the one line that sets it
  up (ask the person first; it stays, tailnet only). It works from anywhere the phone has internet: home wifi, mobile
  data, a cafe.
- **The home network (later).** A plain LAN address (`http://192.168.x.x:8870/`) would play the set, but phones only
  allow the microphone on https pages, so talking back would not work without a certificate. It is not built yet:
  say so if someone asks, and use Tailscale.
- **Other ways** (a cloud relay, a public link) are not built, and a set is never published without the person's yes.

## Start it

1. `phone_start()`. It relays the newest live engine's master as an mp3 stream and never starts a set or plays sound
   on this machine. The reply gives the address.
2. If the reply says the tailnet doesn't reach it yet, ask the person once, then run the line it gives
   (`tailscale serve --bg --https=8870 http://127.0.0.1:8870`). It stays, and it is tailnet only.
3. Tell them: "Open <address>, press Listen, put the phone away. Hold the big button to talk, or tap it once to talk
   hands-free and tap again to send." The first time they hold it, the phone asks for the microphone.
4. Read what they send (below) for as long as the set runs. If nobody reads, the page tells them nobody is
   listening.

## What they send, and where it lands

Every line is JSON in `~/.ismail/phone/inbox.jsonl`. It is also written to the routed inbox: the playing engine's
`<project>/notes/phone_inbox.jsonl`, or the file named with `phone_route`. Each line carries `heard`, the bar they
actually heard (`{bar, beat, of: 'bar 213 beat 3'}`), and `behind_s`. The phone runs a few seconds behind the room,
so act on `heard`, not on the engine's now.

| kind | what it means | what to do |
|---|---|---|
| `tap` `love` | they love what they heard | cut a highlight at `heard` (live.md: highlights on praise) |
| `tap` `change` | change it up now | the next change-up, now |
| `tap` `energy_up` / `energy_down` | more energy / calmer | steer the next phrase |
| `tap` `louder` / `quieter` | the set's level | move the master a few dB |
| `tap` `pause` / `resume` | pause or resume the set | gracefully: never a hard stop |
| `tap` `start_set` | they want a set and none plays | start one (ask nothing more) |
| `tap` `rewind` | they went 30 s back to hear something again | often a sign they liked it |
| `mood` | a standing hint: calm, steady, lift, peak | read it when you pick the next chapter |
| `voice`, then `voice_text` (same `id`) | a voice note, then its words | answer it |
| `answer` / `exam` / `button` | replies to your panel, question, exam or buttons | act on them |

Read it with `phone_listen(who='<your name>', since=...)` (a long-poll, which also shows them who is listening), or
watch the routed file with your harness's file watcher, so a line wakes you even between turns. Hooks in
`~/.ismail/phone/hooks.json` (`{"voice": ["cmd"], "tap": [...], "any": [...]}`) run on every line, with
`PHONE_EVENT`, `PHONE_TEXT`, `PHONE_LINE` and `PHONE_INBOX` in their environment, for an agent that isn't running.

Voice notes are transcribed by the speech server on this machine. While the CPU is over the governor's limit they
wait in a queue: taps still arrive at once, and the page tells them their note is waiting.

## Earbuds and the voice, phone in the pocket

Earbuds send one media key: play/pause (on Skullcandy's Dime 3, double and triple presses only change the volume
in the bud, and a long press opens the phone's assistant). So, while a set plays and "Earbud: talk" is on:
- a press starts a voice note (a rising tone; the music ducks) and the next press sends it (a falling tone, then a
  short chirp when it has arrived). A note never cuts them off mid-sentence: it runs until their press, 30 s of
  quiet (a note left running in a pocket) or 10 minutes (a buzz and a tone 20 s before);
- a short note that is only a command acts as one: "stop listening" stops the stream (a press starts it again),
  "love this", "change it up", "more energy", "calmer", "louder", "quieter", "pause the set", "resume the set" arrive
  as taps with `via: 'voice'` and the note's `id`. The words also arrive as `voice_text`: act once, not twice.
- the microphone opens when a note starts and closes when it ends ("Mic: per note", the default): while a mic is
  open, Bluetooth earbuds switch to call mode (mono, narrowband) and the music sounds bad, so a mic held open the
  whole set ruins it (Nate's Dime 3, 10-06). The switch takes a second or two at each end of a note. "Mic: kept
  open" holds it while the set plays, so a press can record with the screen off, in call quality; "Record: phone
  mic" records with the phone's own microphone, so the earbuds may stay in music quality throughout. If the phone
  will not open the mic (the screen off, or blocked), a low tone and a line say so.

## A hum is a part: the music under every note

Every voice note also says what was on the page while it was said. `media` lists each video and clip on the open
panel when the note began: which file, the second they were at (`t`), its length, and whether it was playing (the page
pauses a panel's video when a note starts, so `t` is where they stopped it). `acts` lists what they did on the page
while it recorded (a seek, a tab, a clip played), each `ms` from the note's start. Both ride on the `voice_text` row
and on the exam's answers file, so "this bit" in a note can be found in the video it was about.

Every voice note keeps the master the page played under it, from 3 s before the person began to 2 s after they
stopped (`<id>_ref.wav` beside the note, with the beat at points through it in `<id>_ref.json`). The server keeps the
last two and a half minutes of the master in memory for this, whenever an engine plays, page open or not, so nothing
has to be recording (`ISMAIL_PHONE_KEEP_MASTER=0` turns that off). When they listen on the room speaker with the
stream off, the note's start is a guess from when it arrived, and the ref spans 20 s either side
(`start_is_guess`, `search_s`): search that window, and the room route gets its own latency.

The page records raw by default ("Mic: raw"): the phone's echo cancelling, noise suppression and gain control strip
the bleed, and the first real hum would not line up through them (PSR 5 to 7, against 17 to 51 on clean tests).
"Mic: cleaned" turns them back on. Every note says what it was recorded with (`mic`: 'phone raw ec0 ns0 agc0', what
the browser actually applied), so keep each route's numbers apart. The music bleeds into
their mic a little, and that is the sync signal: cross-correlate the note with its ref and every sample of what they
sang maps to the beat they heard, with the stream delay and the latency of their input folded in.

There is no hum button. A note that is mostly pitched, holds its notes and has few words is a hum: it arrives in the
inbox as kind `hum` (with the pitch it sits around and the ref), right after its transcript. `phone_hum(voice_id)`
answers for any note, the latest by default, so you can look at one the check let pass. Answer a hum by playing it
back on an instrument at the next loop line and asking whether that is what they sang. The thresholds are first
guesses: measure them on the phone mic first, then on each headset, and keep the latency of each input.

## Panels: a fuller answer, said or tapped

`phone_panel_show` takes `inputs` beside the buttons when one tap is not enough: `choice` (one of `options`),
`check` (any of them), `toggle`, `text`. The answer arrives as kind `answer` with `values` by input id. Every panel
also has a "Say more" button: what they say there arrives as `voice` and `voice_text` with `panel` and `for` (your
`sender`), so always pass `sender`, and listen for both. `video=<file>` plays a video inline in the panel
(a 720 px phone copy is made once, through the machine gate; `video_wait='30m'` stands in line longer): send one to
let them watch and ask questions. `priority='needs you'` (also on `phone_say`) sorts it first and lights the corner key;
`'low'` sorts last. `link='/eye/<round>?from=phone', link_label='Open the picture round'` adds a big button that opens
a path on the phone server in the same tab (only a path starting with `/`; the phone server passes `/eye/` through to
the picture round that `exam_picture_round` hosts, so the eye exam works on the phone while that page server runs). A panel never interrupts a voice note: it waits, silent,
until the note is sent.
In a team of agents, each one with something waiting on the person posts its own panel (`sender=<its role>`, a
`priority`, one topic per panel) and reads its own answers by `for`; nobody relays for another. A free voice note
with no `for` that is plainly about another agent's work (one the person already asked to work on it) goes to that
agent at once, word for word, with its inbox number, id and time; whoever's listener caught it passes it on and
does not hold it. When the owner is unclear, keep it and ask the person. Between agents the same economy holds: news a
team's lead only needs to know (merged, live, done) goes to a shared feed it reads at its check-ins, and a direct
message is kept for a decision, a blocker, a conflict, or something the person must hear now, since every message
wakes the agent it is sent to. Watch with `phone_listen` (every line, in
order) and `phone_status` (open panels, what the page shows, the exam in progress); never filter by owner in a
script, because a line for another agent can still change what you should do. Go quiet between an `exam_round`
line with state `started` and the one with state `finished` (no sound over someone's listening exam). Panels never pop up
(they wait on the corner key), so the person's own taps do the pacing. A video that stalls or fails says so in the
panel, with a link to open it on its own, and the server's events carry `video_ok`, `video_slow` or `video_error`.

## Watching the phone: one loop that sees everything

The person's model, in his words: the watcher "should tell you when shit goes down and give you state info you ask
for"; the agent makes every call (Nate, 10-07, after a watcher that skipped lines for other agents missed his
listening exams and two notes).

1. **One loop.** `phone_listen(who=<your role>, since=<the last since>, wait=25, page=True)`, nothing filtered by
   owner. Drop only page telemetry (scroll, stall, note start and end, visible and hidden, clip plays) and the
   `voice` line that only says a note is being transcribed (its words follow as `voice_text`). For every other line
   you decide: act on it, pass it to its owner, or leave it. Handle a burst of lines in one turn.
2. **Passing on.** A note with no `for` that is plainly about another agent's work goes to that agent at once, word
   for word, with its inbox number, id and time. When the owner is unclear, keep it and ask the team's lead (or the
   person, when there is no lead).
3. **Listening exams mean silence.** From the first exam card, or an `exam_round` started line, silence the music:
   stop every track at the next bar and stop anything that queues new passes. Moving the laptop's output to no
   device is not silence, because the phone's stream keeps playing. The round ends on the person's "done", on
   `exam_round` finished, or after 90 s with no activity from them (clip plays and notes count as activity). Then
   bring the output back and start a fresh pass.
4. **Stay alive.** A watcher run in the background may be stopped by its host after a time limit. Give it a shorter
   life of its own (for example 100 minutes where the limit is 2 hours), and when it ends, read `phone_status` and
   start it again, so a note sent hours later still reaches you ("no matter what if I haven't answered in like two
   hours ... you can still get the voice notes").
5. **Their clock.** The machine's clock may not be the person's. Any clock time shown or said to them is in their
   own time zone.

## Pause means stop, now

A pause from the person (the Pause key, or "pause the set" said in a note) is acted on by the phone server itself:
it fades the set out over 4 s and stops the engine, then says so on the page and posts a `control` line
(`what: 'paused'`) to the inbox. No agent has to be awake for it (10-06: the DJ was mid-task and the set played on
for 70 s after Nate pressed pause). Starting again is yours, when they say or tap Resume.

## Nothing personal on the page

The page may be on someone's screen recording or a shared video: never put where the person lives, their name, or
other personal details in a caption, a panel or a title (10-06: a story named Nate's town while he recorded).
`phone_unsay(match='text')` (or `since='HH:MM'`, or `n=`) takes lines back off the page, its history, its pinned
line and its notification; it cannot reach a recording already made.

## The page's sounds: make them, attach them

Every sound the page makes is crafted (Nate, 10-06: "the same thing applies to everything as like a design
philosophy"). Make each one with ismail like any sound (short, under 5 s, its peak well under the set: about
-14 dBFS), render it with `mp3='also'`, and attach it: `phone_sounds(event='message', path=...)`. Events: `message`
(a `phone_say` caption arrives), `note_start`, `note_end`, `note_sent`, `error` (these replace the built-in tones),
`tap` (any key that sends, unless it has its own), `love`, `change`, `mood`, `offer`, `panel` (a panel, question or
exam opens), `chapter` (the piece changes). An event without a sound is silent, so nothing plays that nobody chose;
`phone_sounds(menu=True)` lists what each plays, and `path=''` clears one. Sounds stay across server restarts.

Only `phone_listen` counts as listening on the page; watching the inbox file does not show them anyone is there.

## Restarting the server

After a change to the phone code, restart with `phone_restart()`, never `phone_stop` then `phone_start` by hand:
the page is told first ("updating, back in a few seconds"), reconnects the stream as soon as the server answers,
and loads the new page code the next time it is on screen and idle. A plain stop drops the person's stream mid-set
(10-06: "Why'd you stop?"). `phone_restart(when_idle=True)` waits for nobody on the stream.

A stream that fell behind the room (the phone's player pauses on a weak network and carries on from there) catches
up by itself: past 15 s behind it plays 8 % faster, pitch kept, until it is within 6 s; the Live key shows how far
behind it is.

## Set the page's vibe to the music

The page is part of the performance, as the stage is in VR: set its look with the music so the person feels you
there. `phone_vibe(preset=)` starts from rain, calm, warm, night or peak (or default), then any part over it:
ground, ink and accent colours, the face of the titles (`heading`), an art layer (`image=`: a cover, a Blender
still, art another agent made, blurred by `blur` and darkened by `dim`) and one ambient effect (rain, particles,
pulse on the set's beat, grain, aurora) at an `intensity`. Change it with the mood and on chapter changes, not on
every bar; it fades over `transition_ms`. The server keeps every vibe readable on a walk (a dark ground, ink 7:1,
accent 3:1) and refuses one that is not, saying what to change. `phone_vibe(menu=True)` lists the choices.

For more than one effect, give `layers`: up to three, drawn in order, each with its own `speed`, `density`, `size`,
`angle` (rain's slant), `opacity`, `color`/`color2` and `blend` (normal, add, screen, multiply, overlay), e.g.
`layers=[{'effect': 'aurora', 'speed': 0.5}, {'effect': 'rain', 'density': 0.9, 'angle': 25, 'blend': 'add'}]`;
`hue_drift` turns the colours a few degrees a minute. Let the set's own notes drive it: `react={'kick': 'glow',
'qrq*': 'sparks', 'stab': 'flash', 'piano': 'drops'}` fires each track's reaction (flash, glow, burst, sparks,
drops, ring) on every note, on the beat the phone hears it, from the engine's own schedule, so the page shows the
arrangement, not a guess at the beat. Land a look on the music: `at='bar:65'` puts it on bar 65 as
the phone hears it (the stream's delay included), `ramp_beats=` fades into it over that many beats, and each call
with `at=` adds a move after the last one, so a drop flashes on its downbeat. `cancel_moves=True` drops them. Keep a
look for a chapter with `save='gnawa_drop'` and bring it back with `scene='gnawa_drop'`.

## Bars or time

Every line from the page carries `into_s` (seconds into the piece) beside the bar, and the page has a Bars/Time
switch: in Time it reads "2:31 in" with the clock. Someone listening casually talks in time ("that bit at two
minutes"); answer in their unit, and say bars only to someone who works in bars.

## The phone session is a take

Like a VR take, the page reports what they do on it, timed: opened (which device, installed or in the browser),
Listen and Stop, hidden and back, the section they scrolled to, downloads, clips played, panels opened and closed,
voice notes with their length and what ended them (their press, 30 s of quiet, the 10 minute limit), earbud
presses. Every line from the page, taps and notes included, carries `room` (the engine's bar then, there even
when the page is off the stream) and `now` (the piece). `phone_timeline(minutes=15)` lays it all on one clock with
what they said, so you can tell what they meant by "this" or "that bit". `phone_listen` leaves the page's own
actions out unless you pass `page=True`, so a scroll never wakes you. All of it stays on this computer
(`~/.ismail/phone/inbox.jsonl`).

## What you can put on the page

- `phone_now(now=, next=, recording_why=)`: the title, next up, and why recording is on or off. The page always
  shows whether it is, read from the engine. On every chapter change, say what each piece is to them with
  `now_mark=` / `next_mark=`: 'loved' (one they loved before, played again), 'replay' (played earlier, back
  again) or 'new' (just made). A small mark sits beside it; without one, the page shows a heart when they tapped
  Love this while that piece played.
- The piece's shape: `phone_now(length=<seconds>, sections=[{'at_s': 0, 'label': 'intro'}, {'at_s': 64, 'label':
  'drop'}], into=<seconds in, if it started before its name went up>)` on every chapter change. The page keeps a
  position line on screen under now/next (elapsed / length, the section, the next one and when), in time or bars as
  they chose. Send it from the chapter's form map; without a length the line hides.
- What they tapped is kept: the page lists their taps and moods with the time and the piece that played, and
  today's count (it survives a reload and a server restart). The keys are momentary: a press sends, lights SENT,
  and the key is plain again.
- Installing: the page installs as an app from Chrome (Install app in the footer, or Chrome's menu, Install). With
  Notify on, what you `phone_say` while the app is in the background also arrives as a phone notification.
- `phone_say(text)`: a caption and a toast. Use `pin=True` for the "since you left" summary when they come back
  (three lines: what changed and why). `speak=True` says it into the stream with the music ducked under it, so
  they hear it in their pocket. When the page is open but off the stream (it reloaded, or they use the phone as
  a remote beside the room speakers), the words go to the page as a clip; `phone_status` shows which. Speak only
  to answer something they said, never unprompted. `buzz=True` vibrates.
- `phone_ask(text)`: yes or no. `phone_panel_show(title, text, image, buttons)`: anything else (the
  stage_panel_show shape). Either one with `wait=N` blocks for the answer.
- `phone_exam(title, clips, question, chips, choices, answers_path)`: a blind exam. Label the clips blind (A, B). The
  live stream pauses while a clip plays. Submit writes to `answers_path`, so no "done" is needed. That one file holds
  everything about the exam: the answer (kind 'answer'), and every voice note said on the card (kind 'voice_note',
  with `text`, `audio_path` and `field`, the box it was said into, or 'card'), before or after the answer. An answer
  row with `voice_notes_pending` has more rows coming. The answer's note says whether it was typed or said
  (`note_source`, and `note_audio` for the recordings). The reply lists each clip's url, path and length, so the
  post can be checked against what was meant.
- Every text box on a panel or exam card has a mic: they say the note, its words go into the box to edit, and the
  answer carries the recording too (`values_audio` on a panel).
- `phone_offer(path, label=, auto=True)`: a download (a render, a take, a PDF). The label is the card and the file's
  name, so write it for a person ("Clair de lune, rain bed (highlight)"), not a slug. An mp3 goes out as a tagged copy:
  title, artist, album (`title=`, `artist=`, `album=`), the date, and ismail with its GitHub link (provenance travels
  with the file; their own file is never changed). Every mp3 `render` writes is tagged the same way.
- `phone_buttons([...])`: your own buttons, as data, for this moment of the set ("darker", "drop it now"). Clear
  them when the moment passes.
- `phone_buzz()`, `phone_status()` (who listens, the bar they hear, how far behind the room), `phone_stop()`.

## Their phone

- **Pocket mode:** the screen can be off and the set plays on. The lock screen and earbuds control it: next = change
  it up, previous = love this, play/pause = their own playback.
- **Network:** 64 kbps is the default (about 30 MB an hour); they can switch to 128 on the page. It reconnects by
  itself when the network drops, and taps made offline are sent when it's back.
- **Rewind and Live:** "30s" replays the last 30 seconds; "Live" jumps back to now.
- **Install:** "Add to home screen" makes it an app icon with the same address.
