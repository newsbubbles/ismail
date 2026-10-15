# Playing live

The live engine plays a queue of clips in real time while you edit it: a jam with the user, a set, a stream,
music that answers something happening now. A finished song is still made offline (render, master); a live set
is performed, and `live_record` keeps a take.

You are slow. A turn takes 5 to 30 seconds, which is 4 to 20 bars. So you never play notes as they happen: you
queue clips that loop until replaced, pre-program arcs, and schedule sweeps. Everything faster than your turn
lives inside the clips and the ramps.

The engine is fast; you are the latency. A clip lands at the next quantize point and renders 3 to 80x realtime,
so the delay between a decision and the sound is mostly the tokens you write and the thinking before them. A
fader move, a filter sweep, one layer in or out, a fill or a new pattern for one part costs seconds; a whole new
section costs a minute or more. Compose whole sections only for real changes, and steer with small edits.

## The loop, live

0. **Set Sheet** (in your reply, before any tool call): the Session Sheet plus an **arc**: which clip plays on
   which bars, what changes at each boundary, and how long the queue runs before it needs you (the runway).
1. **Rig**: `live_start(project, bpm)` (tempo is fixed per run), `live_bus` for a shared reverb, then one
   `live_track` per part with its instrument, `fx` chain and `sends`. A track plays one clip at a time: to layer
   two patterns on one sound, use two tracks.
2. **Queue the arc in one `live_queue` batch.** Chain with `at='after:#k'` (item k of the same batch) and loop
   counts; phrase starts with `next_4` / `next_8`. End on clips that loop `forever` or on a final chord. The
   reply's landing bars are the truth: when a clip is "moved" later, its first notes needed the render time.
3. **Sweeps and builds**: `live_fx(target, index, params, ramp_beats, at='bar:N')`, with N taken from the queue
   reply. `index` may be the effect's type (`'filter'`, `'delay:2'` for the second delay), which survives edits
   to the chain. Ramps on one param form a schedule: a later ramp does not erase an earlier one. A snap then a
   sweep = two moves, the second a beat later (`bar:N.25`). A whole cycle of moves goes in one call:
   `live_fx(moves=[...])` (the DJ kit builds them). `at` takes the same words everywhere (`now` = `asap`).
4. **Listen every turn**: `live_status` (levels per track and bus, runway, late events, notes that "never
   sounded" because their render came back too late, a deck held off air until it is rendered, underruns, safety
   gain reduction) and `live_listen(bars=4, view=...)` (on the live output `bars` is a count of the last bars; a
   `[a, b]` range only works with `recording=`). Write the Listening Report lines as for a render.
5. **Change on phrase boundaries** (`next_4`, `next_8`), not mid-phrase, unless the cut is the point.
6. **Record** with `live_record`: the take starts on a downbeat and has a `.json` sidecar;
   `live_listen(recording='rec_....wav', bars=[a, b])` analyses it in the set's bar numbers, after the set too.
7. **Ask the user** what they heard, as always, and log it in `notes/feedback.md`.

Write a short set as a script, `songs/<slug>/set.py`, like a `build.py`: rerunnable, and the notes and arc are
readable later. A long set with feedback between parts runs from a control module instead (see Running a set). `examples/disco_set.py` (in this skill) is a worked example (intro, groove, breakdown with a filter snap and
sweep, drop, ending; 17 clips in one batch).

## Three paces: studio, set, jam

How fast you answer depends on what the person is doing:

- **Studio work and exams are turn-based.** They ask, you work, you answer. A long fix inside one turn is fine.
- **A set runs in real time underneath.** The engine plays on its own and you steer between turns (the loop
  above).
- **A jam is threaded.** The person plays parts in (takes: voice notes, recordings) and you turn them into loops.
  They lead; you are the DAW. The main thread answers everything they send within about a minute, before any long
  work, even if the answer is only "got it, it goes in at the next loop". Any work longer than a couple of minutes
  (fixing a transcription, sync, sound matching, levels) goes to a helper (a subagent, or a second session when
  your harness has no helpers) with full context: the song folder, the take, what the person said, and what done
  looks like. The main thread keeps listening and talking, and says when the helper's result goes in.

### In a jam

- **The deadline is the loop, not a clock.** The time for a change is what is left of the current loop. A change
  that will not land in this loop or the next goes to a helper, and you say which loop it lands in.
- **Watch first.** After every wake, re-arm the inbox watch (`phone_watch`) before anything else, then answer, then
  work. Notes left unanswered read as a broken tool.
- **Record from the first take.** The best moments come early and unannounced, and what was not recorded is gone.
- **Their own sound first.** Their real voice, instrument or beatbox, chopped and gated into the loop, usually lands
  better than a transcription played on a synth. Transcribe when they ask for it or the sound cannot work.
- **Ask what a take is for.** When a take could be a sample of them or a melody to play on an instrument, ask once,
  keep the answer, and let them change it by voice.
- **Pull first, fix later.** When a part made from a take sounds wrong to them, mute it at the next bar, within the
  minute, and fix it in a helper. Never debug it live while it plays.
- **A loop is not a resting state.** When they go quiet, the jam becomes a set: keep developing it, a change every
  few minutes, a progression rather than a repeat, until they play in again.
- **Write the story as you go**, one line per take: what they said, what changed. At the end, offer a studio version
  built from the jam, in its own song folder.
- **Read takes in code, not by hand.** A small runner in the song folder classifies, syncs and reads each take in
  about a second and asks sample or match on the phone: `references/jam-runner.md` is the recipe.

## Sounds first

A live set is only as good as its sounds, and the rules of the main skill hold here too: no genre parts on bare
sprite patches. For a style with a reference, match the sounds offline first (`references/recreate.md`: drums,
bass and lead one at a time, the user's ear on each A/B) and take the matched instruments into the set; a set of
console sounds is not rescued by arrangement. Set faders from the reference's stem balance (`levels_from_ref` offline,
then carry the faders into the set), and swing from `analyze_swing`, not by feel.

**A chain the person liked gets a name.** `fx_stack_save(name, chain, notes)` keeps it for every set (or one song,
with `project=`), with their words in the notes; `live_track(fx='stack:<name>')` uses it, and `fx_stack_list` shows
what there is. Saving runs 10 s through the chain the way the engine does and keeps how many times faster than
realtime it ran on this machine: `live_track` and `live_load` say it again where the chain is used, with RISK under
4x (the built-in `afrobeat_chank` measured 1.2x here: a univibe is costly). `fx_stack_measure` measures again after a
machine change or when a set runs behind.

**Only proven sounds go on air.** That means library voices with their fitted rigs, measured mimic profiles, and the
instruments of finished songs. A voice written while preparing the set and never fitted to a recording or ear-tested
does not play live. In a 40-minute blues set, a 12-string and a harmonica built from textbook numbers, with their
references downloaded and separated but never used, drew "sounds like a kids piano... we have no guitar style,
nothing". When the set needs an instrument the project does not have (an acoustic guitar, a harmonica), say so
before the set and offer the choice: measure it first from the references (mimic, an ear test), or play the
nearest proven instrument and name it.

**First sound within minutes.** Open with a short arc built from proven parts, then build the later sections while it
plays and the audience reacts. The same blues set spent 40 minutes preparing seven eras before the user heard a
note, and one sentence of feedback condemned all of it.

**Never downgrade in silence.** When a constraint forces a worse sound (a voice that renders too slowly for the
set, a voice that fails its warm-up), say so before it plays and offer the options: fewer tracks on that voice, a
simpler patch, a pre-rendered clip. The blues set swapped the fitted guitar for an untested one without a word, and the
user caught it by ear.

**A song plays live through `live_load`, never rebuilt by hand.** A hand port drops the song's automation (a growl's
filter sat open at 20 kHz), its buses, its master chain and its held notes, and then it "sounds nothing like the
original". Load the song, or a `bars` window of it, on a deck. When its reply lists parts as "not live", when it
cannot keep up, or when a part sounds different, that is an engine gap: run `live_parity(song, bars=[a, b])` (the
studio against a silent deck of its own, per track, bus and the mix) and write what it marks DIFFERS, with its
numbers, in the song's `HANDOFF.md`.

**Other songs stay untouched.** Loading a song on a deck reads it, and so does `live_parity`. Anything that writes
(a test render) runs on a copy in your own song folder: a render in the original's `proj/` overwrites its
`latest.wav`.

## Running a set: the DJ loop

A long set (a DJ set, a party, a 30-minute jam) is a loop between you and the audience, and the user's messages
are the audience. Later the audience may be a text stream from other devices (motion on the dance floor from a
camera, facial expression categories); treat it the same way.

- **The phone first, not negotiable.** In the same turn you start any set, start `phone_watch` in the background
  (`python -m ismail phone_watch "who=<your role>"`). When it wakes you, re-arm it before you act on what it
  returned, and never put housekeeping (logs, notes, tidying) before the phone. A hand-made watcher lost after a
  context reset once missed six of the person's notes in 36 minutes. `phone.md`, "Watching the phone", has the rest.
- **Read the room.** When the audience is into it, lean in: keep the feel and add an element or two, build, take
  it down, drop. When it goes flat or repetitive, move on: a switch-up, a breakdown, a new song.
- **Think in energy, not song form.** Bring elements in over time (a percussion layer, 808 slides, string stabs,
  a riser), strip them for a breakdown (drums and bass out, filter the keys down, a snare roll into the last
  bar), then drop with more than before (double-time hats, the hook instrument). Every 16 to 32 bars something
  should change.
- **Small edits by default.** Steer with one-element changes between bigger moves: `live_track(volume_db=)`,
  `live_fx` sweeps, `{track, stop: true}`, a one-bar roll or fill clip (`loop: 0`), one part's new pattern.
- **Queue a runway before every question.** Whatever is queued loops while the user answers, and answers can
  take minutes (a static loop ran 9 minutes once). Before asking, queue changes that keep moving for longer than
  you expect to wait, and near the end of a timed set queue a fallback ending, so the set lands on time without
  you. New clips replace queued ones on their tracks, so the runway costs nothing when you override it.
- **Keep a control module, not one script**, laid out as in "A set's folder" below, and build it on the DJ kit
  (`ismail.live.djkit`): its `Set` logs every call with the clip ids per section, and its note and move builders
  replace the helpers every section used to rewrite.
- **Check after every addition**: the new track's level (a track reading -120 dBFS while "playing" is silent,
  see below), the master `limiter` (layers add up: pull faders when it reads more than ~3 dB), late events.

Changing style at a fixed tempo: the tempo is fixed per run, so change the feel by **metric modulation**. At a
house tempo T, 1.5T is a triplet grid: trap at 141.9 over G-funk at 94.6, with 6 trap bars = 4 house bars and
every trap beat = 2/3 of a house beat (write in trap beats, multiply by 2/3). A breakdown whose hats move to
triplets first announces the new grid; then drop. 2T and T/2 (half-time) work the same way.

Phrase boundaries: `next_16` and friends count from bar 1 of the run, not from where a playing part's phrase
began. When a new part must line up with a 16-bar phrase that started on bar 37, use `bar:<37 + 16k>`.

### Reading the audience

Learned from a one-hour set steered by one listener; the same reading applies to any audience feed.

- **They judge change over time, not static balance.** "It didn't really change much" was about contrast, not the
  mix. A drop lands when the bar before it is emptied: the one that worked measured -27 dB with no sub in the bar
  before, then -19 dB with the sub 30 dB up, plus a silence gap. Meter the bar before a drop against the drop and
  aim for 6 to 8 dB of difference and a clear swing in the sub band.
- **Dynamics come from effects, not EQ and not more layers:** delay throws, reverb washes, filter sweeps, stutter
  gates, bitcrush, sidechain pumping, and silence. Faders and EQ are housekeeping; effects are the performance.
- **Invented melodies fail; derived ones win.** A generic pentatonic acid line was "all over the place". A call
  taken from the reference's vocal, callbacks to earlier sections' bell, cello and violin lines, and the opening
  progression reused all worked. New melodic material comes from the reference or from earlier in the set.
- **Measured beats guessed, and the listener hears it.** A guessed gnawa groove (four strokes on clean triplets)
  was "cool"; the measured one (three strokes at 0, 0.32 and 0.74 of a beat, its timbres fitted from the stems)
  was "I love it... closer". Measuring mid-set is fine at idle priority.
- **Feedback is short and directional** ("busy", "more bass", "move on", "sounds like shit"). Answer with one or
  two named moves, not a menu. "Move on" means a new section, not a tweak. Something bad comes out at once (the
  acid line was gone within seconds), then the bigger fix follows.
- **Ask about the culture, not the knobs:** "does it read as gnawa?" got a useful answer where a technical
  question would not.
- **Endings are emotional arcs** ("full circle", "crescendo", then peace). Plan the last 15 minutes early as an
  arc, but keep the ending swappable: do not queue it until it is close.

### Highlights on praise

When the person praises what is playing ("I love this", "so hot", a `love` tap from the phone), keep that moment
as a highlight. The full takes run for hours; the highlights are the record of what the set can do, and the
showcase.

1. **Close the running take** so its file is complete: `live_record(on=False)`, then `live_record(on=True)` at
   once. The music keeps playing and a new take file starts. Never cut from the take that is still recording.
2. **Cut generously** from the take that covers the moment: the whole section around it (from the build to the
   end of the drop), not the one bar. A phone tap carries `heard`, the bar they heard: cut around that bar, not the
   engine's now. The take's `.json` gives `first_bar`, `bpm` and `beats_per_bar`, so bar b starts at
   `(b - first_bar) * beats_per_bar * 60 / bpm` seconds into the file. A short fade at each end.
3. **Save it as an mp3** in `<project>/live/highlights/<slug>.mp3`: cut to a temporary wav outside the project,
   encode, remove the temporary file. Never overwrite a highlight.
4. **Index it** in `live/highlights/INDEX.md`: when, what was playing, their words verbatim, and the section
   script and engine settings that make it. Keep every section's code in `set/` so a highlight can be rebuilt.
5. **Space is the person's call.** A take is about 1 GB an hour. Delete a closed take's wav after its highlights
   are cut only when they asked for that, and keep its `.json`.

### Your latency is the risk

A section written from scratch took 5 to 10 minutes; a request for a new ending arrived after the queued section
it replaced had already started.

- When a message arrives, read `live_status` (heard bar, runway) before writing anything, and cancel what no
  longer fits: `Set.cancel('<section>')` takes back the clips logged for it.
- Build each move from the kit so the visible work is short calls, not long file writes.
- Judge a sparse part by `live_status`'s "max 10 s" level, not the snapshot: a vocal call read -70 dB when the
  snapshot fell in its rest.

### Watch the machine as well as the room

A 16-hour run (prog house into a sleep session, a sunrise, a morning groove and a video-game set on decks) held
because the performer watched the computer as closely as the audience. The machine is shared with other
sessions' renders, Blender and video jobs.

- **Read `python -m ismail.machine` before the set and before every slow step.** It names the other jobs. Go on
  when it says go; the person can overrule the heat.
- **Guard on audio trouble, never on machine CPU alone.** A guard that polls `live_status` every ~15 s and pauses
  on new underruns past a small allowance, late notes, a mixer above ~65% twice, or STALLED holds a set safely.
  Four pauses triggered by whole-machine CPU came with zero underruns.
- **Every watchdog runs as a tracked background task**, so its exit wakes you. A detached guard paused a set and
  the person sat in 50 minutes of silence; a helper loop that crashed left 9 more. `live_status` flags what a
  guard should wake you for: `SILENT ON AIR` (nothing sounding for 10 s), `RUNWAY ENDED` (nothing new queued for 8
  bars) and `THIN` (one track left, or the mix 20 dB under the set's usual level, for 30 s: a set once ran 10
  minutes on a lone hat loop after its outro forgot to stop it).
- **Under load, shrink the rig instead of stopping.** Dropping idle tracks and insert effects (bitcrush,
  compressor, chorus, distortion) while keeping the parts the person named took the mixer from 85% to 47% with
  the same music.
- **One deck at a time on a busy machine.** A cued deck of a 20-track song costs the mixer as much as one on air,
  and its first section renders in a burst: every breakup in one morning lined up with a deck load. Switch songs
  with a stop and a start (a short gap) until the machine has room.
- **The set follows the person's clock.** Read the time at every checkpoint and shape the arc to their day:
  energy, a let-down, a sleep session queued whole so it runs with no agent and ends by itself, silence, a sunrise
  (a dawn chorus entering in the order birds wake), a morning groove. Plans move when they say so.
- **Pause and resume are a performance move.** Snapshot the status to a file, stop the recording, a short fade,
  stop. Resume with a short ease-in at a level that fits the hour, never the exact old state. Each resume is a new
  take file, logged in `notes/feedback.md`.
- **Keep a runway, always.** Queue 8 to 16 minutes ahead, set a checkpoint timer that fires well before the runway
  ends, and never let a section end into silence.
- **Change it before they ask.** One chord for an hour read as repetitive. Progressions with every part built from
  its chord, a new hook every 2 minutes and alternating section forms (full, a drive with no break, a deep one that
  starts underwater) kept it moving. Builds escalate every 4 bars and drops land louder (`Set.gap` brings each track
  back to its own level).
- **Use what the person is doing elsewhere.** Other sessions' work makes material: their birds, a gnawa groove, a
  song's villain, a series on decks, a song built by a background agent while the set plays. Check each borrowed
  voice's blind-test notes and provenance before it goes on air; two unproven voices once had to be pulled.
- **Log their words verbatim** (`lexicon_note`, `notes/feedback.md`) with what was playing and what changed.

### Dynamics with the kit

```python
from ismail.live.djkit import Set, steps, per_bar, notes, metric, sweep, throw, gap, pump, with_gain
S = Set('songs/<slug>')
S.start(84)                                       # set loudness + the pre-flight lines
S.track('kick', instrument={'type': 'kick'}, fx=with_gain([{'type': 'filter', 'cutoff': 18000}]))
r = metric(126, 84)                               # house beats on the 84 grid
S.q([{'track': 'kick', 'notes': notes(per_bar(24, lambda b: [] if 16 <= b < 20 else steps('X...X...X...X...')), r),
      'bars': 16, 'loop': 'forever', 'at': 'next_8'}], section='s04_house')
o = S.next_boundary(start=41, every=16)           # the next cycle bar that can still land in time
S.moves([sweep('kick', 'filter', 'cutoff', 400, 30, o + 8), gap(['kick', 'bass', 'stab'], o + 16),
         sweep('kick', 'filter', 'cutoff', 18000, 0, o + 16), throw('stab', at=o + 15.5), pump('pad', o + 16)])
```

`steps` turns step strings into notes (X x o g, swing, lag), `per_bar` builds a cycle bar by bar inside one clip,
`notes(..., ratio)` places a feel on another metric grid, and the move builders (`sweep`, `throw`, `gap`, `pump`)
return `live_fx` moves addressed by effect type. `with_gain` puts a gain first in a chain, which gaps and fades
use.

## A set's folder

```
songs/<slug>/
  project.json          project_new; the engine runs in this folder
  SET.md                the Set Sheet and the running order, kept as the set goes: one row per section
                        (# | name | start bar | grid, e.g. 84 or 126 = 1.5x | key | tracks added / stopped | what
                        the user said that caused it)
  set/
    ctl.py              plumbing only, no notes: a djkit Set, the rig helpers
    s01_triphop.py      one module per section, numbered in play order, named by style: rig() makes its
    s02_minimal.py        tracks, clips(start) returns the queue batch, choreo(start) the moves, run(start)
    ...                   does all three; seeded, so it regenerates exactly
    setlog.md           written by the kit: every call, landing bars, clip ids per section
  voices/               code voices made for the set; each docstring cites the measurement it came from
  ref/
    SOURCES.md          per source: title, link, who made or played it, licence, what was measured,
                        who supplied or approved it, date, what was sampled (if anything)
    <name>.wav          the reference
    <name>/             its analysis project (excerpt, stems)
  work/                 measurement scripts and their saved output (groove.txt, timbre.txt): voices and
                        sections cite these numbers
  notes/feedback.md     bar or time | the user's words verbatim | what changed | the measured result
  live/                 live_record takes (rec_<date>_<time>.wav and its .json), and highlights/ with its INDEX.md
```

- **A rebuilt part gets a new track name with the section number** (`gmb2` -> `gmb3`), and each section module
  lists the old tracks it stops. To reuse a name, `live_fx(track, clear=True)` first: scheduled moves outlive
  clip changes.
- Temp files go to your scratchpad, not `set/`. Measurements are saved as files, not only printed.

### A new piece while the engine runs

A new set or jam starts in its own folder (SKILL.md, "One piece, one folder"), even when an engine already plays
in another one. An engine belongs to the folder it was started in: the ops and a djkit `Set` find it by that
folder, and its `live_track` voices and `track:<name>` instruments, `live_record` takes, `live/listen.wav`, controls
and the phone inbox all come from or go to that folder. No op moves a running engine to another folder, so the
hand-over is a stop and a start:

1. Make the new folder first: `project_new` there, copy or import what it reuses (voices into its `voices/`, the
   control module into `set/`) and name each source in its notes.
2. Finish the current pass or fade it, and tell the person the music stops for a moment. Then
   `live_stop(project=<old folder>, fade_sec=...)` at a phrase end; it closes any recording.
3. `live_start(project=<new folder>, bpm=...)`, or `Set('<new folder>').start(bpm)`; its reply names any engine
   still running elsewhere. Turn `live_record` on again if it was on.
4. The phone follows the newest engine by itself. `phone_watch` and `phone_listen` read the phone's own inbox, so
   they keep hearing everything across the restart; only a script reading the old folder's
   `notes/phone_inbox.jsonl` would go deaf, which is one more reason not to write your own watcher. An inbox set
   with `phone_route(inbox=...)` stays where it points; `phone_route()` sends it back to the playing engine.

Without a restart, a finished song from another folder plays on a deck by its path (`live_load(deck=,
song=<its folder>)`), and its tracks render with that song's own voices and sound bank. That plays a piece inside a
set; it does not make a new one: tracks made with `live_track` and every take still land in the engine's folder.

## Performers and phrase voices

A code voice renders one note at a time, so legato, slides and bends between notes need another shape.

**Performer voices.** A voice module with `perform(notes, total_n, sr, bpm, lanes, beat0, **params)` and no
`voice()`
plays a whole part (a guitar with hammer-ons and slides, strings that ring on, a kit that resonates). `live_track`
detects it, and live renders it a bar at a time, each bar with the second of the part before it as context, cut and
crossfaded at the bar line, so legato, slides and ringing strings carry across bars; a looping clip takes its
context from the previous pass, and a deck's section from the 8 beats before its window. A voice that keys its
variation on the song beat (`electric`, `kit70`) plays live as in the studio render, sample for sample. Bends and
vibrato come from the clip's `expr` lanes, `{"bend": [[beat, semitones], ...], "vib": [[beat, cents], ...]}` with
beats from the clip start; the voice's INFO lists its lanes. A guitar is the `electric` performer with its rig in
the track's `fx` (`voice_help(name='electric')` lists the fitted rigs): the rig runs live block by block, as in the
studio. A voice you write for a performer follows the five rules in `windows.md`, or its bars live will not match
the studio.

**Phrase voices** are the older trick for a performer that is not written as one: make **one note = one whole
phrase**. The voice takes a `phrases` param, `{"<velocity>": {"notes": [...], "bend":
[[beat, semitones], ...], "vib": [[beat, cents], ...]}}`, and the note's velocity picks the phrase; the note's
pitch can transpose it against a `root`. Inside, call the performer on the phrase's notes and lanes. Keep effects
on the track, not inside the voice: an amp run on each phrase stacks one amp's hiss per phrase and renders slowly
(a guitar baked that way played at ~1x realtime and lost a deck's first bars).

The instrument, params included, is captured when the track is made. After you add a phrase to the dict,
re-send `live_track(name, instrument=...)`; a note whose phrase is missing plays silence.

## Decks: prepare the next part while this one plays

A deck is a group of tracks with a DJ strip: fader, 3-band isolator (250 Hz / 2.5 kHz, a band at -40 dB or
less is killed), a filter knob (-1 low-pass .. 0 off .. +1 high-pass) and transpose. A cued deck plays off the
air; only you hear it, through `live_listen(deck=...)`.

1. `live_load(deck='B', song=<ismail project folder>, bars=[a, b])` puts a song (or a section) on deck B: its
   tracks, effects and buses come over as `B.<name>`, its notes play at the house tempo (re-rendered, not
   stretched), with its automation (effect sweeps, volume curves, instrument filter and pitch moves, the master
   fade), its group buses and its master chain (the limiter runs on the deck). It is cued while another deck is
   on air, and a cued deck plays from `at`: a song loaded early with `at='next_bar'` is bars into itself when it
   comes in, so load it with `at='bar:<the transition's bar>'` to bring it in from its top. The reply lists what
   did not come over (placed audio clips, effects whose source track was muted). A track with instrument
   automation renders its whole section as one event: load the deck several bars before it plays. A performer
   voice's studio lanes (`inst.lane.bend` ...) arrive as its clip's `expr`, so a guitar keeps its bends; other
   `inst.*` automation on a performer, and `inst.lane.*` on a voice that is not one, stay behind (listed under
   "not live"). A drone
   or pad already sounding at the window's first bar comes in on its first beat, as in a studio render of those
   bars.
   A deck loaded straight on air waits off air until a whole bar of it is rendered, then goes on air on that bar
   line: load too close and the audience hears it start bars late, and the next reply says so. A big song (20+
   tracks) wants 10 to 20 bars of lead; load it while the other deck plays, and keep it cued.
   Before a set, check each song's sections with `live_parity(song, bars=[a, b])`: a drum bus that had lost its
   dry signal left only the bass audible, and only the user heard it.
2. Listen to deck B while deck A plays; fix it there (`live_deck` eq/transpose, `live_fx` on `B.<track>`).
   Key-match with `live_deck(transpose=...)` (drums stay).
3. `live_transition(to='B', style=..., bars=16, at='next_8')` queues the whole mix: `blend` (B up without bass,
   bass swap half-way, A out), `bass_swap`, `filter`, `cut`. It starts at the first boundary after B is
   playing, puts B on air, and when it ends stops A and takes it off air (cued), ready for the next `live_load`.
   `live_status` says `SILENT ON AIR` when a set that has played sounds nothing for 10 s. The reply is the timeline; `live_status` shows each deck's
   fader and eq as they move.
4. Watch `live_status`'s mixer load: two full songs is about 50%; above ~70% risks dropouts.

Put the swap where the incoming deck's bass plays: a 16-bar section whose loop restarts on a sparse bar leaves a
hole at the bass swap. Pick `bars` so the section starts on its downbeat hit.

## Rules

- **Runway before a slow job.** Before anything that takes time (`mimic_measure` 25 to 60 s, `instrument_fit` and
  `track_fit` minutes, a long think), queue an arc longer than the job, with change in it (a clip with a fill
  in its last bar, an evolving chain, a ramp), not one bar repeated.
- **Variation inside the clip.** A 4-bar clip with a fill in bar 4 beats a 1-bar clip plus four tool calls.
- **Balance with faders.** Read the per-track levels in `live_status` and set `volume_db`. The safety chain is
  a floor, not a mixer: its limiter and rider should read 0 dB.
- **Live output is quiet by default** (trim -6 dB, sustained cap -16 dBFS rms, ceiling -1 dBFS; about -21 to
  -23 LUFS for a balanced mix). That is too quiet for a listener at a normal volume: one user heard "nothing".
  For a set, start the engine with `live_start(..., trim_db=4, cap_db=-12)`: about -16 LUFS. The arguments set
  this set's level and win over the environment. Left out, the engine reads `ISMAIL_LIVE_TRIM_DB` and
  `ISMAIL_LIVE_CAP_DB` from the process that calls `live_start`, which a tool server cannot change between calls,
  so use the arguments. `live_status` prints the trim and cap in use. Then keep the limiter near 0 dB with faders.
- **Sounds you are unsure of**: try them on a muted or quiet track (`volume_db=-40`, then fade with a ramp on a
  gain fx) rather than straight onto the main part.
- **Tempo change** = `live_stop`, then `live_start` with the new tempo (a gap). Inside a set, change the feel
  with metric modulation instead (above).
- **Gliding parts** (a portamento synth lead, a Moog bass) belong on the synth engine (`mono`, `glide`) with
  measured harmonics (an `additive` osc or a fitted filter): mimic renders each note alone and does not glide.
- **Pre-flight.** `live_start` names any other engine still running on the machine (a forgotten set held a
  Bluetooth speaker for hours and froze the next one): stop it unless it should play. After the first clip,
  check that the heard bar moves; a `STALLED` line in `live_status` (or in any reply) means the device stopped
  asking for audio. With `device='default'` the engine moves by itself: to a speaker that becomes the system
  default (a Bluetooth speaker connecting), and back to the default when a device stops taking audio; the next
  reply says "output moved". To move by hand mid-set, `live_device(device='JBL')` (part of a name works): the
  timeline and queue carry on with a gap of about a second, nothing is reloaded. `live_stop` waits for the engine
  to exit and kills it with its workers when a dead device would hang it.
- **Heavy jobs before the set.** Stem separation during a set caused 1,654 underruns; measurement scripts at idle
  priority were fine.
- **Mixer budget.** About 18 tracks in a crescendo peaked at 71% of real time and dropped out. Share reverbs on
  buses (a reverb per track is expensive) and stop parts you no longer hear; removing tracks barely helps.
- **A new voice's first pass**: check its level at once (a first qraqeb sat 30 dB under the part it answered). A
  single event above +12 dBFS is dropped as a broken voice, and the next reply of any op says so ("NEW since
  your last call"). A code voice's randomness is seeded by pitch and velocity: vary them for stroke variety.
- **Chain swaps and sweeps.** `live_fx` at or after a scheduled `live_track(fx=...)` swap targets the new chain;
  the swap drops the sweeps written for the old one and keeps volume moves. A sidechain or duck source must be a
  live track before the chain that reads it.
- **Recording a long set** takes disk: 24-bit stereo is ~16 MB a minute (~480 MB for 30 minutes). Check free
  space first, or skip the take and use `live_listen` on the live output.

## Streams and controls: the set inside a scene

A live set can play inside a VR stage or any page, and the stage's objects can play it back.

- **Streams.** `live_stream(name='bus:lucy')` gives a localhost URL that streams that bus (or `master`, or
  `deck:A`) as raw PCM, int16 stereo at 44.1 kHz, with its own safety limiter. Route the tracks an object should
  play to their own bus (`live_bus('lucy')`, then `live_track(track, instrument=..., output='lucy')` as you create
  each track; a track's output is set when it is created) so each object plays its own sound at its own place; one
  master stream would put everything in one spot. The engine listens on localhost only: a page on a headset reaches it
  through the page's own server, which relays the stream (same origin, https). A listener that falls a second behind
  loses its oldest audio and never slows the set. A stream plays sound wherever it is received: tell the user
  before anything starts.
- **Controls.** A knob turned in VR must answer in milliseconds, which a model cannot. So map it once and let the
  engine apply it: `live_map(control='lucy.volume', target='bus:lucy', param='volume_db', range=[-40, 0])`,
  `live_map(control='lucy.tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], curve='log')`, a power
  switch with `curve='switch'` and `range=[-120, 0]`. The stage (or you) sends `live_control(control, value)` with
  0..1; the move applies at once. You watch the moves and act at phrase scale: a new song on Lucy, a new mapping.
- **Recording controls.** Every move is logged by bar. `live_controls` reads them back as `[bar, value]` points,
  ready to become `automation_set` lanes in a studio project: a take's knob moves land in the score, on the song's
  clock, and replay the same way in a render and a video.

## Played keys: the person plays a part

When the person wants to play a part themselves (a bassline in a jam, a lead over the set) instead of you writing
it, patch their keyboard into the engine. Your job is the patching and the sound; the playing is theirs and goes
straight to the speakers.

- `midi_sources()` lists the MIDI inputs (keyboards, pads, virtual ports). A running engine also says when one is
  plugged in or out, and a patched one plays again when it comes back.
- `live_midi(source='keys', track='bass')` plays the input with a live track's sound (its baked effects included),
  or `instrument='preset:pluck'` (anything live_track takes). Call it again with another track or instrument to
  swap; the old sound plays until the new notes are ready. The notes render once on the workers (C2 to C7, a kit's
  pads, or one hit for a drum), then a key sounds one output buffer after the press. Velocity, note-offs, the
  sustain pedal, `transpose` and `channel` are followed. Tell the person before their keys first sound.
- Keys join at the speakers, not the mixer: they skip the track's live effects and the safety limiter (a soft clip
  above -1 dB guards them), and they are not in `live_record` recordings or streams yet. `live_listen` hears them.
- **What they played is text.** Every key is logged, and `live_played()` reads it back as notes lines
  (`bar | +beat | pitch | d= | v=`, the format tracks take). Read it to answer what they played or quote a phrase
  back in their words. `bars=[a, b]` reads those bars; with a number it reads the last ones played.
- **The clock.** A key sounds one output buffer after the press (about 180 ms on a laptop's speakers), and the music
  reaches the speakers through the same buffer. Someone playing by ear lines up the note they hear with the beat they
  hear, so each note is placed where it sounded against the music: the press plus the keys' delay. The header of
  `live_played` names that delay in ms; the press times stay in the log (`press`). A log written before this clock
  holds the press, about one key delay early, and the header says so.
- **Looping their take back.** Never round the raw lines to the grid: a take that sits near half a 16th off rounds
  the wrong way ("it sounds like a half off", 2026-10-13). In order:
  1. **Ask which bars.** The player says which part was good (aloud with the mic on, or by bar number); quote the
     bars back. Never guess where the good part starts.
  2. `live_played(bars=[a, b], fit=True)` groups the hits (keys within 0.15 beat are one), fits a 16th grid with a
     free offset, and says the offset, the rms error in ms, the drift per bar and the rhythm's shape in 16ths
     (3-3-2). It prints the snapped notes twice, ready for `live_queue` (beats from the clip start, bar 1 = the
     take's first bar, or `from_bar=`): **A as played**, and **B with the shape's groups from beat 1**. When they
     are the same it prints one.
  3. **Offer both by ear.** Where the take sits against the kick is a musical choice the data cannot make. Say each
     in words ("A: as you played it; B: the groups start on the beat") and play them so the answer is one word.
     A snare on 2 and 4 gives the player something to hear the placement against.
  4. **Keep the choice for the run.** Once the player says a take is right, later takes in the same run usually need
     the same placement (same keys, same engine): use the same letter first.
- **Flat velocities.** A cheap keyboard sends every key at one velocity (often 80); `live_played` says so. The lines
  keep it. When looping it, write an accent shape and say you did: the bar's downbeat loudest, then the beats, then
  pushed 16ths, offbeat 8ths softest; in a chord the top note leads and the lower notes sit well under it (about 30
  under sounded right in one jam; 14 was too close).
- **No MIDI keyboard?** Optional, only for someone who wants to play and has no instrument; someone with a
  keyboard needs none of this. `live_keys_page()` gives an on-screen keyboard that plays straight into the running
  engine as the input `'screen keys'`, no virtual port: patch it like any input,
  `live_midi(source='screen keys', track='bass')`, and `live_played(source='screen keys')` reads it back. It is
  served on this computer (`http://127.0.0.1:<engine port>/keys`: mouse, touch screen, or the computer keyboard's
  Z and Q rows) and on the phone page's address while it runs (`live_keys_page(phone=True)` posts a panel with a
  button that opens it). Two to three octaves, octave shift, a sustain toggle, multi-touch chords, and velocity
  from where the key is pressed (near the top is soft); its top line says whether it is connected and what it
  plays. The keys sound from this computer's speakers, so it is for someone in the room.
- **Other software as the keyboard** (a DAW, a sequencer, a Web MIDI page): on Windows, loopMIDI makes a virtual
  port (name it, e.g. "ismail keys"); whatever sends MIDI to that port plays through `live_midi` like a real
  keyboard.

## Playing for a screen recording

When the session itself is the show (the viewer sees your turns and hears the set):

- Each turn, one short line: what is queued and which bar to listen for what ("bar 97: the kick drops out, the
  guembri goes into the delay; bar 105: the drop").
- One concrete question at a time, with the default you will take if there is no answer.
- Visible work stays short: kit calls, not long file writes.

## What is and is not live yet

Live: every instrument type including mimic profiles and performer voices, every effect but `autotune`, send buses,
sidechain/duck/vocoder from live tracks, ramps on every automatable fx param.

Effects run two ways. An effect with a live processor (every built-in type but `autotune`, the guitar rig included) runs
block by block on the mixer and matches the studio version (held by tests): a wah's `pos`, a rotary's `speed`, a
univibe's `rate_hz` move with `live_fx` like any filter. An amp or tape with hiss on keeps hissing while its track
plays, as in the studio, and sleeps with the track when its notes stop. An effect that exists only in the studio
(a new type nobody has written a live twin for) is **baked**: the render workers run it on each note or phrase
before the mix. `live_status` marks it `(baked)`. A baked effect cannot be moved with `live_fx`, restarts its LFOs
and tails per note, and hears each note alone (a fuzz on a chord distorts each note, not the sum). Everything up to
the last studio-only effect in a chain is baked, so order is kept; a sidechain or duck cannot sit before one, and a
bus cannot bake at all (put the effect on the tracks).

`live_parity(song, bars=[a, b])` checks a song section: it renders the bars in the studio, plays them on a silent
engine of its own (no `live_start`, nothing late) and compares each track, bus and the mix, from the window's
second bar (the first is the window's edge: the studio rings in what came before, a deck starts clean). A part
marked DIFFERS sounds different on a deck than in a render: write it in the song's HANDOFF.md for the contributor dev;
do not change the song to hide it. Known gaps it shows: a mono synth's glide between phrases, a drone that began
long before the window (it comes in with a fresh attack), a track with no notes in the window (its tail and hiss
from earlier bars are not played).

Not yet: master-bus effects (the safety chain is the master; how a set, a deck and a stream reach their level is in
`loudness.md`), vocoder modulators from sound-bank sounds, instrument-param automation on your own live tracks (a
deck's song has it; otherwise use fx params, or expr lanes on a performer), tempo changes inside a run, and placed
audio clips on a deck.

### Sung takes, tuned

The person can sing over the set, from the phone (a voice note) or a microphone. `sound_autotune(path, key=,
scale=, speed_ms=)` tunes the take to the set's key and scale, or to `notes=[[start_s, midi, dur_s], ...]` (the
part it should follow, timed from the take's start), and writes `<take>_tuned.wav` beside it, never over it. A
phone note keeps the master it was sung over (`<id>_ref.wav`, with the beats in `<id>_ref.json`; phone.md, "A hum
is a part"), so the tuned take can go back in on the beat it was sung to, the way the set plays any recorded
phrase. Say it is coming before it plays. Ask which sound they want first: `speed_ms` 0 is the hard robotic effect,
slower keeps their own wobble. Then ask what they hear.

Tune to the set's key and scale whenever you know them: `chromatic` pulls each note to the nearest semitone, so a
note sung between two lands on whichever is closer, and the take locks less firmly. Three settings cover most asks:
0 (robotic), 40 (firm, the pop sound), 150 (natural). Autotune has no live processor: tune the file first, then play
it like any recorded phrase. The studio side (autotune first in the chain, one per voice) is in `sound-design.md`.

## Numbers worth knowing

- Output trails the mix by ~96 ms (a fixed 4096-sample alignment budget for effect lookahead, plus the safety
  limiter). Tracks stay aligned with each other; `live_listen` and recordings compensate.
- A chain may need at most 4096 samples of lookahead along track + bus (a hall is 1024, a limiter its
  lookahead, oversampled distortion 20). The error says which effect to drop.
- A replaced chain rings out (its reverb tail keeps sounding) for up to 12 s.
- `live_status` shows each track's render speed ("renders 3x realtime"). Drums and code voices run 20 to 50x,
  synths 4 to 7x, mimic 1 to 3x (low notes are the slowest: more harmonics). A clip's first pass waits for its
  renders, so the reply moves slow first launches a bar or two later; every later pass reuses them, and
  identical notes (a repeated chord) render once. Performers render a bar at a time with a second of context:
  `kit70` about 5x realtime; check `live_status` for `electric` with your part. Queue them a phrase ahead.
  `toms` is slower than realtime (about 2 s per hit, more the first time a drum and stroke is retuned): render
  its part in the studio and play it as audio rather than live.
- `live_start` takes ~20 to 30 s: the render workers warm up every instrument kind and every mimic profile in
  the folder before it returns, so the first clips land on time.
- Mimic profiles come out quiet next to synths and code voices (about 9 dB): check their level in
  `live_status` and raise `volume_db` before judging the balance.

## Other sessions' renders while you play

While your engine is on the board, the governor holds GPU jobs and Blender renders until the set ends (a rule
made after two real dropouts; `python -m ismail.machine on-air` shows the policy). Audio
renders and other CPU work still run beside you, sharing half the machine's threads (a job that asks for more
waits; after a 359-underrun dropout from one slot's render plus the perceptual model on every core).
`live_start` sets the engine's priority in code (above normal, render workers normal); its `priority` option
(`'above_normal'`, `'high'`, `'normal'`) changes it. Watch underruns. Your own checks during a set: run them as
`python -m ismail.machine run --cpu --threads 2 -- python check.py`; the renders and comparisons inside it use the
run's slot and its threads.

## Away from the computer

Offer the phone page whenever a set is playing and the person is about to leave the computer: a walk, bed, the
kitchen, the car. Say it in one line: "Want it on your phone? You can talk to me from the earbuds and tap feedback
without unlocking." Then `phone_start` and give them the address. `references/phone.md` covers the rest: reading
their taps and voice notes by the bar they heard, answering on the page, and the hosting.

When they listen on the phone, the page is part of the show. On every chapter change: `phone_now(now=, next=,
now_mark=, next_mark=)` ('loved' for a piece they loved before, 'replay', 'new'), and `phone_vibe` with the mood
(a preset or colours, a heading face, the cover or a render behind it, one ambient effect). Read
`phone_timeline` when they say "this" or "that bit": it lays their notes over what played. If they work in time,
not bars, answer in minutes and seconds.
