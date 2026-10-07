# Changelog

## Unreleased

### Phone: who gets a voice note nobody tagged

- skills phone.md: a free voice note plainly about another agent's work goes to that agent at once, verbatim, with
  its inbox number, id and time; an unclear one is kept and the person asked (Nate 10-07: "you should always send to
  an agent I've already asked you to work with before").

### Phone: the picture round opens on the phone

- The phone server now passes everything under `/eye/` through to the picture-round page server (127.0.0.1:8871),
  with its body, Content-Type and Range, so the eye exam page opens on the phone's https address and its sounds
  seek. With no round open it answers 503 "No picture round is open right now."
- `phone_panel_show(link='/eye/<round>?from=phone', link_label='Open the picture round')` puts a big button in the
  panel that opens a path on the phone server in the same tab. Anything that is not a path starting with one `/` is
  refused. `exam_picture_round` says so in its reply.
- The exam page (`words.html`) has 48 px controls on a phone, a "Peek (hold)" button for the Shift peek, plain-words
  hints instead of key names, a drag that is dropped cleanly when the phone cancels it, and a "Back to the phone"
  link when opened with `?from=phone`. Nothing changed on a desktop, and its ids and answer format are the same.

### Phone: a video on mobile data starts at once, and says when it cannot

- A player asking for the rest of a file gets it 2 MB at a time, so the first frames of a long video arrive at
  once on 4G (Nate 10-07 15:39: a 4:43 video "not loading on the phone"). A transfer that drops is one line in the
  server's log.
- A panel's video says "loading", then "still loading on this connection" after 15 s, or that it could not play,
  each with a link to open it on its own, and reports `video_ok`, `video_slow` or `video_error` to the events.

### First contact: hand-holding for plain words

- Someone on plain words no longer gets a test. `sketch` tells the agent to play version 1 at once with one line of
  delight; the other sketches are spares, for when they want something different. No "which is closest", no "what is
  missing", no three to compare. Musicians keep the rounds they had.
- Every round now ends with two or three playful choices in everyday words, made from that very sketch, plus "or tell
  me anything" ("Want it different? Try: add a beat you can nod to, faster and bouncier, or a bit spookier, or tell
  me anything."). `sketch.playful_choices` picks them and checks each one against `apply_words` first, so every choice
  changes the sketch when it comes back as the next round's words. New feeling words: bouncier, bouncy, peppier,
  perkier, zippier.
- A change plays at once, new version first, with one plain line on what changed, and now and then the agent slips in
  a small unasked flourish and names it after it plays. "Want to keep this as your song?" leads to `sketch_keep`.
  Live changes are not offered yet.
- `guide`'s FIRST SESSION block now has a musician branch and a plain-words branch (also returned by
  `guide(first_answer=...)`). AGENTS.md and `setup.md`: never say skill, plugin, MCP, uv or server to the person unless
  they do first, and a plain chat that cannot run commands says so in one sentence and names what works.

### Phone: scrolling past the talk key never starts a note

- The big talk key records only on a still press. A touch that moves (past about 10 px, or that the browser takes for a
  scroll) scrolls the page and records nothing; a quick still tap is still hands-free (Nate 10-07 15:13).

### Phone: panels can carry a video

- `phone_panel_show(video=<file>)` puts a video in a panel. It plays inline on the phone page, next to the panel's
  buttons, inputs and "Say more" voice reply (the video pauses when they start to record), so the person can watch it
  and ask questions about it. Any agent that controls the phone may use it.
- The agent's process first makes a phone-sized copy (H.264 and AAC MP4, at most 720 px tall, fast start) inside the
  machine's cpu slot, so it waits its turn like any heavy job (`video_wait='10m'`; a busy machine comes back as a
  plain message). The copy is kept in the phone's `videos/` folder, so one video is converted once. A small H.264 MP4
  is sent as it is. Without ffmpeg the error says what to do.
- The phone server's `/files/` now answers HTTP Range requests (206), which phone browsers need to play and seek a
  video, and knows `.webm` and `.mov`.
- Later on a panel only hides it: the corner key stays lit and keeps counting it (it went grey while a question was
  still open).
- `phone_panel_show` and `phone_say` take `priority`: 'needs you', 'normal' (the default) or 'low'. Notes and panel
  tabs sort by it (needs you first, then newest), and the corner key fills with the accent colour while an open item
  needs them.
### A newcomer's next round keeps their tune (ledger:M181, intro dry run 2)

- `sketch(..., base=)` keeps the picked sketch's tune and chords and changes only what the words name: the first
  take is the change, the second the change taken further (it was two new tunes, one of them sparser after "a bit
  happier"). The reply says the words back ("your version 3 with the same tune and chords, happier: faster,
  brighter") and names the before and after to play. `sketch_keep` and the guide now give the same route for a
  change.
- Version numbers go on across rounds (a next round after three starts at version 4), and nothing at the top of the
  song folder is replaced, so "play version 3 again" still works. The kept song is `song (version 3).mp3`, not the
  folder's name. `sketch_wait` and `sketch_keep` name versions, not letters.
- Plain-words versions say how they differ: a key change of the same mode is "pitched higher" or "lower" (it read
  "brighter"), "fewer notes" or "more notes" for sparser and busier, and the sound and mood are said once.
- An upright, saloon, player or honky-tonk piano in a brief is named as missing, with the grand piano standing in,
  and is on the showcase's "not covered yet" list.
- A sketch is checked after its loudness trim: a tune pushed to full scale brings the master down until it peaks
  under -1 dB (version 3 had reached -0.0 dB).
- `sketch` stands in line for a busy machine (`wait='2m'`), and a refusal opens with BUSY and the plain words to say
  ("the computer is busy, I'll try again in a moment") before the details.

### The live safety can no longer silence a set

- One huge but finite sample drove the level rider to -423 dB and the set was silent for minutes (Live DJ 10-07 12:31). A sample past +30 dBFS is now zeroed like a NaN, the rider's level reading is clipped, and the rider never pulls deeper than -40 dB.
- A track whose live effects blow up (NaN, inf, or past +30 dBFS) has that block silenced and is named in live_status news, once a second at most.

### Phone: the controls come first, and every note is one tap away

- Listen, talk and the keys sit at the top; "Since you left" and the latest note move below them, the summary three
  lines until tapped (Nate 10-07: "this could be a little bit more ergonomic").
- The corner key counts notes not seen yet ("1 question · 2 new"), including one that came in during a voice note,
  and opens a Notes list (the summary and the last twelve notes, the new ones marked) beside the open questions.

### Phone: answered panels close at once; the server's log is dated

- An answered panel closes on the phone at once, without waiting for the next state (Nate 10-07 12:01: a panel stayed open after his answer and he could not get out of it). A refusal from the server (the question already closed) is shown and closes it too, instead of being queued as offline and retried.
- Every line of the phone server's log starts with the UTC time, and a phone dropping off mid long-poll is one line
  ("dropped: ConnectionAbortedError") instead of a traceback.

### The phone sets where the sound goes (ledger:M179)

- The phone page shows the set's output and a Follow Windows switch: on moves the set to the Windows default output
  and follows it (a headset plugged in takes the sound), off keeps it where it is. It acts on the live engine
  directly, so no agent is needed; each change reaches the inbox as kind 'output'. `live_device` takes
  `reopen=False` to change following without a gap.

### A new person's words stay theirs (ledger:M175)

- On a machine where songs were already made, `guide(first_answer=...)` no longer writes the owner's machine-wide
  vocabulary: a new person's record goes beside their song (`person.json`, marked as a guest's), or waits in
  `~/.ismail/pending_person.json` (12 hours) until their first `sketch` moves it into the song folder.
- `lexicon_note`, `lexicon_find` and `lexicon_view` on a guest's song read and write that song's own
  `lexicon.jsonl`, so a guest never adds to the owner's lexicon or sees the owner's words. Marketing's dry run 2
  found both.

### Named effect stacks, with what they cost on this machine (ledger:M177)

- `fx_stack_save`, `fx_stack_load`, `fx_stack_list`, `fx_stack_measure`: an effect chain saved by name with notes,
  in the library (`~/.ismail/fx_stacks`) or a song (`<song>/fx_stacks`); `live_track(fx='stack:<name>')` uses one.
- Saving measures 10 s through the chain the way the live engine runs it (live processors in 1024-sample blocks,
  baked effects over the window) and keeps the times-realtime figure per device. `live_track` and `live_load` name
  each saved chain they meet with its cost here, and RISK under 4x.
- Five built-in stacks from the 10-07 freestyles: `kit70_glue`, `dirty_pbass`, `afrobeat_tenor`, `afrobeat_chank`,
  `tonewheel_organ`.

### Phone messages never pop up

- Panels and exams no longer open over the page (Nate 10-07: "these are modals that block me"). A new one buzzes
  once and shows on a corner key ("2 messages"); he opens it when he chooses, switches between open messages on
  tabs, and Later puts one back. The rest of the page stays usable.

### Phone panels wait their turn

- A new panel or exam no longer covers one the person is reading: it waits until the open one is answered or set
  aside, the open one says another is waiting, and `phone_panel_show` replies that it is queued (Nate 10-07: "this
  test interrupted my reading of another panel").
- Every panel has Not now: it arrives as a dismissal (`dismissed: true`, answer null), not as an empty answer. A
  choice input must be picked before a button sends, unless it is `optional` (Live DJ 10-07: Send with nothing
  picked came in as {"which": null}).

### Clarinet, bassoon and French horn in the showcase (ledger:M169)

- Three measured voices from VSCO 2 CE (CC0) passed a blind ear check against the real recordings: `clarinet`
  (not told apart), `bassoon` and `horn` (good enough). New families `winds` and `brass`; `sketch` now plays a
  clarinet, bassoon or horn when the brief names one instead of standing the violin in, and "brass" gets the horn.
  Flute, oboe, trumpet, trombone and tuba were measured too but heard as synthetic: they stay out until they pass.

### Feeling words change a sketch (ledger:M170 S-6)

- `sketch(base=...)` reads happier, sadder, calmer, more exciting, darker and dreamier (and their near words) as
  concrete moves: tempo, major or minor, busier or sparser, softer or brighter; "a bit" halves the move, "much"
  makes it bigger, and the reply names each ("happier: faster (95 BPM), major, busier, brighter"). Marketing's dry
  run: "a bit happier" changed nothing and two takes came back slower.
### The picture round: the eye exam page (ledger:M165 step 2, M12)

- `exam_picture_round(out, items, title)`: vox's word-picture page in main. One sound per card, the real picture and
  ours of the same window stacked (F flips, Shift peeks, B blinks), M / S / 0 about the picture showing, comments
  pinned at a time and frequency (a point or a dragged box), the key served only after a submit. Real-first and
  real-second are balanced and seeded. Pictures come from `spectrogram_png`'s eye mode (step 1).
- `exam_picture_score(out)`: per card right, WRONG, cant or none, with flips, blinks, plays, the note and each pin.
- `ismail.exampage` serves rounds (`python -m ismail.exampage serve <folder>`) with HTTP byte ranges, the hosted
  round of M12. Touch works too (pointer events).
- Parity: vox's answered rounds w1 and w2, moved into main's round format, score line for line the same as vox's
  work/score_words.py (11 and 101 lines).
- README: the tool count is 221 (113 music and live, 88 stage, 20 phone).

### A part at full scale is named (ledger:M170 S-1)

- `render` lists HOT on any track whose stem peaks over -0.5 dB, with how far to lower it. Marketing's dry run had
  a melody at -0.0 dB and no word of it (the master was fine, the limiter flattening the tune).
- The sketch balance never raises the tune past a -1 dB peak; when it needs more, the other parts come down.
### The person's words are kept, and versions play from the song folder (ledger:M170 G-2b, U-5)

- `guide(first_answer=..., project=)` keeps the vocabulary it decided (`<song>/person.json`, and the latest person
  beside the first-session mark). For plain words, `sketch` says "Version 1: a piano plays the tune, slow and
  bright, about 30 seconds" (no keys, BPM or bars, also in its change lines), and `sketch_keep` offers the next
  change in their words, not "a warmer bass from bar 5".
- Sketches are "version 1, 2, 3", starting again each round; `base=` and `sketch_keep` take the number. Each
  finished sketch is also copied to the top of the song folder as "version N", replaced by the next round's.
- The kept song gets a playable file at once (`<song>/<song name>.mp3`).

### A newcomer is a person, not a machine (ledger:M170 G-3)

- `guide(new_person=True)` opens with the first session for someone new at a computer where songs already exist
  (Marketing's dry run and the DJ's fresh-agent test both got no first session there); the plain `guide` reply on
  such a machine names it in its first line.
- `sketch_keep` marks the first session done only when the machine itself was new, so a guest's keep writes
  nothing for the owner.

### Floor pairs in phone exams (ledger:M173)

- `phone_exam(..., floor='auto' | 'full', floor_from=)`: device floor pairs, the real clip against a 64 or 128 kb/s
  MP3 copy of itself or against itself, keyed so neither side is the synth. The full set on the first floor round,
  then one rotating pair (history in the phone home, `floor_rounds.json`); seeded places, never first; neutral file
  names. The pairs are renumbered; the reply and `<answers_path>.floor.json` map them back. Reference: the Voice
  session's work/phone_round.py (hq:D-60).

### Three measuring rules (ledger:S56)

- listening.md, "Readings that mislead": a sum of medians runs short (fit lengths on a log scale, lengthen phrase
  ends, put pauses back); level and band charts cannot see phase (a blind pair for any timing or phase stage); one
  render is not a result (two seed sets before claiming a difference). From the Voice session's lessons (hq:D-60).

### Recording warnings before a take is measured (ledger:M172)

- `ismail.capture`: two capture faults found before a recording is measured. Peaks flattened into a plateau under
  0.98 (the browser mic chain stops near 0.894-0.899, so a clip test never fires; more than 20 samples a second
  within 0.006 of the maximum), and band-limited capture (8-12 kHz more than 60 dB under 0.5-2 kHz: a Bluetooth
  headset mic). From the Voice session's lessons (hq:D-60).
- `mimic_measure` lists RECORDING WARNINGS per take and keeps them in the profile (`capture`); `no_top=True` for an
  instrument with no top. `sound_import` warns the same way, before normalizing. Phone hums carry `capture` and
  `air_db` in their check and the inbox record.
- Parity: air_db equals vox's rec/server.py air_db to 0.0 dB on all 39 of its r39 (Bluetooth, all flagged) and r45
  (clean, none flagged) takes.

### Phone panels: choices, checkboxes, toggles, a voice reply, and never over a voice note (ledger:M167)

- `phone_panel_show(inputs=[...])`: choice (one of), check (any of), toggle and text inputs beside the buttons; the
  answer carries `values` and `for` (the sender). Nate 10-07: "checkboxes or toggles so that I could give more of a
  detailed response".
- Every panel has "Say more": tap to record, tap to send; the note carries `panel` and `for`, so it reaches the agent
  that sent the panel, and the panel stays open ("I felt like I should be able to say more").
- A panel that arrives while a voice note records waits, silent, until the note is sent; the talk hint says one is
  waiting (Nate 10-07 08:56: "Am I still recording right now? ... it was still recording geez").

### First session: the rest of ismail waits for its moment (ledger:M168)

- guide's first-session block (step 8) and user-experience.md: the phone page, live play and the VR stage are each
  offered in one sentence at the moment they answer something the person did (kept a sketch, wants to jam, has a
  headset, steps away), never up front; a no holds for the session.

### Phone page: catch up only while the phone holds sound ahead (the 4G cutouts)

- The page played 1.08x to catch up with the room whenever it was over 15 s behind; on 4G that drained its buffer and
  stalled every 4 to 9 s (Nate 10-07: "cutouts on the phone that are not on the speakers"; 32 stalls in 45 min, all at
  1.08x). It now speeds up only while 4 s are buffered, drops back under 2 s, and holds normal speed for 2 min after a
  stall. Each stall logs the rate and seconds buffered when it began.

### Hum a part into a voice note (ledger:M163, the Live DJ's handoff 52)

- The phone server keeps the last 150 s of the master in memory while an engine plays (page open or not), and every
  voice note saves the music under it (`<id>_ref.wav`, 3 s before to 2 s after, with beat stamps), so the bleed into
  the mic can line a sung part up with the beat heard, with no recording on. Heard from the room speaker with the
  stream off, the start is a guess from the note's arrival and the ref spans 20 s either side.
- A hum is told from the note itself, with no button (Nate): pitched, holding its notes, few words. It reaches the
  inbox as kind `hum`. `phone_hum(voice_id)` answers for any note.
- The page records raw by default ("Mic: raw"; "Mic: cleaned" turns the phone's echo cancelling, noise suppression
  and gain control back on): the processing stripped the bleed on Nate's first hum. Each note carries `mic` (the
  route and what the browser applied) and how long ago it ended, so an upload that waited offline still lines up. The notes, grid and swing come from the Live DJ's
  measuring once it holds on real hums. 213 tools.

### The phone page stays awake while the stream is down (ledger:M142)

- After a server restart a page in a pocket never reconnected: a hidden page whose audio stops is frozen by
  Android, so its retries never run. While the stream is down (an error, a restart notice, a stalled stream), the
  page now plays a loop far under hearing (40 Hz at -80 dBFS) until the stream plays again. Needs a check on a
  phone with the screen off.

### Spectrogram windows for eyes (ledger:M165 step 1, moved from vox)

- `spectrogram(seconds=, f_lo=, f_hi=, ruler=, words=)` and `analysis.spectrogram_png(...)`: one sound on a fixed
  plot box (two pictures of a window line up pixel for pixel), a ms ruler, words drawn and named, and a `.json` map
  from pixel to (s, Hz); `analysis.eye_address` names a spot the way the person and the agent both read it. Parity
  with vox's `eyeword.crop_png2` and its page's address: 20 of 20 pictures identical on its own files, every click
  on the plot box the same address. The mel view with bar lines is unchanged.

### Skill: loudness matching is one fixed gain, never ffmpeg loudnorm as a filter (ledger:S55, the Live DJ's handoff 51)

- instruments.md (A/B clips) and mastering.md: measure, then apply one fixed gain; `loudnorm` as the filter rides the
  level, even two-pass with `linear=true` when the true-peak ceiling would break.

### The phone page shows the set's notes as they reach the ear (ledger:M160 phase 2, Nate 10-06 14:48)

- `phone_vibe(react={track: reaction})`: flash, glow, burst, sparks, drops or ring on every note of that track
  ('qrq*' matches a prefix), each fired when the phone hears its beat (the stream's delay included). The engine's new
  `onsets` command lists the events it has placed (track, beat, level); the phone server passes them on only while
  a look reacts to them. No audio analysis on the phone.

### First session: one opening, the person's words, calmer setup (ledger:M151 G-1, G-2, U-1..U-4)

- One opening: guide's FIRST SESSION block is the only one. SKILL.md's step 0 no longer asks for a recording first
  (one is welcome, never required), and the skill's first-session section, README, AGENTS.md and setup.md point to
  the block. At most two questions before any sound: what it is for and whether they play, then a mood or a
  reference.
- `guide(first_answer=<their words>)` says which words to use from then on: musician (they name an instrument they
  play, a style they trained in, or reading music; a negated mention does not count), with their trade's words, or
  plain words. user-experience.md has the rule.
- setup.md: before the first command, "about 10 to 20 Allow boxes" (how it was counted is in a comment beside it);
  the plugin added from the shell when a `claude` command is there, otherwise both lines in one block with one
  sentence, or one line on Claude Code 2.1.275+; "Quit Claude from the tray" on Windows; Documents found through
  the known-folder path (OneDrive moves it), replacing the `%USERPROFILE%\Documents` setx line; every wait named
  with its time from the dress rehearsals (install, warm-up, first call, first sketch). The first-session block
  says the sketch times too.

### The update card lists every change waiting

- The left-wrist update card lists the changes waiting (up to five, the most important first, then "+N more") and
  grows to fit, instead of naming only one (the user, q44: panels "listing what changed in each update").
  `updates_waiting` carries the titles. The titles come from `stage_note`, one per change landed.
### Takes on the beat: sync, loop and warp as stage ops

- `stage_take_sync` measures how takes keep time with a song (pulse, own BPM, the rate onto the beat, the first
  low point, the seam) and with each other (lag in beats, movement match); `loops=True` lists the best whole-bar
  windows of a long take. `stage_take_loop` cuts a window (given, or the best at a bpm) into a silent loop whose
  last 0.4 s cross-fade into the frames before it. `stage_take_warp` makes a loop of exactly N bars with its hits
  moved onto the beats. Ported from the film session's sync_measure.py, loop_cut.py and beat_warp.py with the
  parameters tuned with the user (ledger:M145); on Nate's takes the port gives the same numbers, windows and
  frames as the scripts. New takes drop the performance link (no borrowed voice), the trim and the label.
### Takes play smoothly between their samples

- Take playback poses the frame between the two recorded samples around each render frame (positions lerp,
  quaternions nlerp the short way), instead of holding each sample until the next. A take recorded at 19.8 Hz
  (Nate's dance, 20261005_122454) played at 72 fps held the right wrist still on 73% of frames, then jumped
  (95th percentile 46 mm); mixed, 5% still and 21 mm. Samples more than 0.25 s apart (tracking lost) are held.
  Only what posing reads is mixed (head, hands), into a reused buffer: 32 microseconds a person a frame on the desktop.
### Load sets: unload a group of the room, with a placeholder

- `stage_set_define` names a group of the room (node names or globs, and people), `stage_set_load` unloads it or
  loads it again, and `stage_sets` lists the sets and what they match. Saved in world.json (`sets`, `unloaded`), so
  an unloaded set stays out across reloads. An unloaded set is hidden before the room is staged (no shader compile,
  texture upload, draw or shadow), what only it used is freed on the GPU, its people neither play, follow nor rest,
  and a ghost box per member with one label carrying the set's name stands in its place (two draw calls). Loading
  brings it back a piece at a time. The page state carries `sets`.
- A scene switch reads the new scene's world.json before its room is staged, not after (resting people and load sets
  read it).

### People rest in their start pose

- A person with a start pose (`stage_actor_start`) stands in it whenever nothing plays on them, at load and after
  every stop: their own body, posed once, with the statue baked into the scene hidden. `idle=False` keeps the statue.
  Setting or clearing a start pose re-poses the resting person on an open page at once (page command `actor_rest`).
  The six dancers had stood with their arms out since they were imported ("a pose to leave everything in").
- The pose read-back of a take-frame start pose (with nothing playing) no longer fails.
### The first sketches: mastered, balanced, the first one first, said plainly (ledger:M148, M150, M151 S-1..S-5)

- S-1 (M148): every sketch goes through a master stage for its style (a low cut, glue, mono low end, a limiter at
  -1 dB, the loudness its style wants: four-on-the-floor about -9.5 LUFS, classical about -17) and a hall bus; keys
  get a little warmth. Before, a sketch was the dry mix at about -16 LUFS ("is this mastered?").
- M150: a stems render weighs the tune against the other parts and moves its fader so it sits 4-6 LU over them
  (it sat +1 to +13 LU with fixed faders).
- S-2: the first sketch comes back as soon as it is rendered; the others render in a background process
  (`sketch_wait(project)` says when they land). background=False waits for all.
- S-3: the readings differ for real: within what the words fixed, the sparser one moves key and the busier one gets
  an intro and an ending (so another length). The reply says how each differs from A.
- S-4: the reply opens with SAY TO THE PERSON, in a musician's words: what each sketch is, what was swapped
  ("you asked for a flute; that isn't here yet, so a violin plays the tune"), words it can't make yet ("'gritty'"),
  with no voice IDs. The engine detail moves to FOR YOU lines.
- S-5: a voice that is not in the curated showcase never plays a first sketch; its part is left out and said.

### The blind crop check: a pre-exam gate by eye (ledger:M158, Voice's method, Nate's rotation idea)

- `exam_eye_crops(pairs, out, windows=)` cuts the same window from the real and the made clip of each pair as
  spectrograms, side by side in a random order, with the key hidden; `exam_eye_score(out, answers)` scores the
  agent's picks and says NOT READY when they beat chance (p < 0.05). blind-tests.md: the check, zooming two ways,
  and the reveal after the answer. 211 tools.

### A pause tap stops the set by itself (the Live DJ's HANDOFF 50, Nate 10-06 15:08)

- The phone server fades the playing engine out over 4 s and stops it when the person taps Pause or says "pause the
  set", then captions it and posts `{'kind': 'control', 'what': 'paused'}`. Before, the set played on until an agent
  read the tap (70 s on 10-06).

### phone_unsay: take captions back off the phone page (Nate 10-06 15:03)

- `phone_unsay(match=, since=, n=)` removes captions from the page, its history and its pinned line, and closes the
  matching notification. phone.md: nothing personal on the page (it may be on a screen recording). 209 tools.

### The phone page measures its gaps (Nate 10-06 14:56: "a profiler")

- Every stall the browser reports and every freeze (the stream not advancing for over 1.5 s) is logged as a `stall`
  page event with its length, whether a voice note was recording, the playback rate, the network and which mic was
  open; audio route changes log as `route`, and `note_start` says which mic and how long it took to open. They show
  in `phone_timeline` and `phone_listen(page=True)`.

### The phone page's background: layers, looks on the bar, scenes (ledger:M160 phase 1, Nate 10-06)

- `phone_vibe(layers=[...])`: up to three effects at once, each with speed, density, size, angle, opacity, colours
  and a blend mode; `hue_drift` turns the colours over time. New looks crossfade in.
- `phone_vibe(at='bar:N', ramp_beats=)`: a look lands on the bar the phone hears (the page applies it on its own
  stream clock; the server folds it into the standing vibe 8 bars later), and repeated calls build a list of moves.
- `save=` and `scene=` keep and recall named looks. The state carries `room` (the engine's bar) for a page that is
  not on the stream.

### phone_sounds: the page plays sounds an agent made, one per event (Nate, 10-06)

- `phone_sounds(event, path, gain_db)` attaches a short sound (wav, ogg or mp3, at most 5 s and 1 MB) to an event:
  message, note_start, note_end, note_sent, error, tap, love, change, mood, offer, panel, chapter. The page plays it
  on that event only; events without one are silent, and the four note tones fall back to the built-in ones. The
  sounds persist across restarts. phone.md: make them with ismail, then attach them. 208 tools.

### The phone page opens the mic only for a note, so earbuds keep music quality (Nate, 10-06)

- With the mic open the whole time the page played, Bluetooth earbuds (Nate's Dime 3) stayed in call mode and the
  set sounded bad. The mic now opens when a note starts and every track stops when it ends. "Mic: kept open" brings
  back the old way (earbud notes with the screen off, in call quality); "Record: phone mic" records with the
  phone's own microphone.

### The live engine renders lazily and says STARVING before the underruns (ledger:M156, hq:D-30)

- A clip queued more than 32 bars ahead renders nothing until it comes within 32 bars (PRELOAD_WITHIN_BARS). On
  10-06 a 21-minute piano set queued in one call rendered every clip's first pass at once: a backlog of 10,089
  renders and 82 underruns in 6 bars. Renders already went out earliest-needed first.
- live_status says STARVING (the bar, the track, the seconds of render work before it against the seconds until it
  sounds) when the line, earliest-needed first at the measured rates, cannot keep up.

### Docs: where the machine board lives; 207 tools (S54, hq:D-30)

- setup.md and development.md say the board follows `ISMAIL_SONGS` (`<ISMAIL_SONGS>/_machine`, or
  `ISMAIL_MACHINE_DIR`): right for a stranger, a trap for a test on a shared machine (first-run dress rehearsal 2).
- The README counts 207 MCP tools (82 `stage_*`).

### The governor holds a run to its threads and pauses a job far past its memory (ledger:M153, M154, hq:D-30)

- `machine run` pins its command to as many cores as its threads (the highest cores no other job holds; children
  inherit it, and the meter sets it again on a child that changed it), and the thread variables now include Numba's
  and Rayon's. A `run --cpu` at 2 threads used about 5 cores, because CTranslate2 ignores the BLAS variables.
- A job at 3 times its `--mem` while less than 10 GB of commit is free (or 15% of the commit limit, if smaller) has its processes paused, never killed: the
  board says SUSPENDED: OVER and how to go on, `python -m ismail.machine resume <job id, pid or name>`, after which
  it is not paused again. 10-06: an ffmpeg declared 3 GB and took 37 GB during a live set.
- The board reads a job's file again when its meter is replacing it (Windows), so a running job never drops off
  the board for a moment.
- A docstring's `D:\ismail` made an invalid escape warning; it reads D:/ismail now.

### render stands in line when the machine is busy (ledger:M152, hq:D-30)

- A plain `render` refused on WAIT and told a newcomer's agent "force=True only if the user says so" (first-run dress
  rehearsal 2). It now waits in line up to `wait='10m'` (the default), says how long it waited, and `wait='0'`
  refuses at once as before.

### phone_route survives a restart (ledger:M157, hq:D-30)

- The route is kept in the phone's state.json and loaded again when the server starts (when its folder still
  exists), so a restart no longer sends the person's notes back to the playing engine's project.

### Exams know the devices; exam_check hears a band-limited take and lopsided sides (ledger:M146, Nate's approval 10-06)

- vox:r39 was wasted: the takes went through Bluetooth earbuds' microphone (16 kHz audio, nothing above 7 kHz), the
  synth filled that band 45-50 dB above them, and exam_check said READY. It now fails classes whose 1/3-octave levels
  above 6 kHz differ by more than 10 dB and twice the spread within a class, warns on any clip with nothing above
  8 kHz, and warns when a class comes first in 80 % or more of 6 or more trials (all of them, as in vox:r41's first build,
  already fails).
- `phone_exam` asks what they listened on (earbuds, headphones, phone speaker, speaker; remembered) and sends it with
  the answers, with the output and microphone the browser can name. blind-tests.md: record the devices every round.

### A priority says when it matches no job; `--for 3d` (ledger:M143, Nate's approval 10-06)

- `who` defaults to the working folder's name, so nearly every job from the repo is `ismail` and a priority given to
  a session matched nothing unless that session set `ISMAIL_SESSION`. Setting or reading a priority, and the board,
  now warn when no job of that name ran or waited in 48 h, and list the names in use. SKILL.md tells every session to
  set `ISMAIL_SESSION`.
- Durations take days (`priority voice --for 3d`), and a grant beyond today shows its day.

### Takes on the music clock

- The page keeps the song's time as the person hears it (the audio context's clock, less the output latency):
  `stage_music_time`, `music` in the page state, and `music_start` / `music_stop` events. `stage_music(start=)` begins
  a song partway in.
- `stage_actor_play(at_music=<song seconds>)` plays a take on that clock: its first frame sits at that song second and
  every frame reads the song's time, so a beat-warped loop stays on the beat however late it started and wherever the
  song loops. The film's dancers were warped onto the beat, but a take started on a command landed 0.3 to 6 s late.
  Side server: the take followed the song within one take frame (median 16 ms, worst 41 ms at 30 Hz frames).

### The phone's Listen and Talk keys show their state with an icon (Nate, 10-06 09:59)

- Listen: play, stop while playing, a breathing arrow while it reconnects or buffers, Resume when the phone wants a
  tap. Hold to talk: a mic, the mic with sound while talking, an arrow while the note uploads, a check when it
  arrived, a struck mic when the mic is blocked or the note waits to send. A waiting icon breathes, and stops for
  reduced motion.

### Every mp3 carries ismail; downloads have names; the piece's position stays on screen (Nate, 10-06)

- `ismail/tags.py`: ID3 tags on every mp3 ismail writes (title, artist, album, date, encoder, the GitHub link as a URL
  frame and in the comment: "Made with ismail (https://github.com/newsbubbles/ismail)"). Nate: "very important for
  provenance whenever we're shipping out MP3s". `render(mp3=)` tags with the song's name; `phone_offer` sends a
  tagged copy with a readable name from its label (their own file is never changed). mutagen is a dependency.
- The Download key no longer runs off the right edge of a phone.
- `phone_now(length=, sections=, into=)`: the page keeps a position line on screen (elapsed / length, the section,
  the next one), in time or bars. Nate: "something that's always on screen ... that shows where we are in the song".
- When the playing piece began survives a server restart (ledger:M139: lines after a restart said 0:00 into it).

### The frame beat says where the head was

- The page's 5 s beat (clientlog.jsonl) carries `head` (position in Blender metres, yaw and pitch in degrees, yaw 0
  along +Y) and `worstAt`: the head and the triangles and draw calls of the worst frame in those 5 s, so a stall is
  read against the view it happened in (the frame budget: under 400k triangles and 300 draw calls per view). It also
  carries `shadowFrames` (frames in the window that re-drew the shadow maps), `playing` (people playing or following)
  and `eyes` (2 in the headset: triangles and calls count both eyes).

### Takes keep what was said while recording

- A take's audio is transcribed as it lands (`takes/<id>/voice.json`, a `take_voice` event), with word times on the
  take's clock, snapped onto the measured voice. A take kept from a Follow, or recorded during one, takes its words
  from the performance's voice clips that fall inside it.
- `stage_takes(scene, query, person, kept)`: takes newest first with what was said, label and notes; `query` finds
  takes by their words, label or notes, and shows each hit with its second. `stage_take_note(scene, take, label,
  note, at)` names a take or adds a note; `stage_take_transcribe(scene, take)` fills in an older take's words. The
  person asked for it: he says a take's name and notes while recording, and records many takes of one thing.

### The headset speaks when the server's voice is off

- While speakwright is off, `stage_say` was silent in VR (the server answers 502). The page now says the line with the
  headset browser's own speech engine instead (Web Speech), still waiting while the person talks; the event says
  `voice_spoken` with `via: "headset speech (server voice off)"`, or `voice_error` when the browser has no engine or
  voice. Lines in the speech cache still play from the server.

### The phone page in the README and the skills (Nate, 10-06: "this phone app feature should start gaining prominence")

- README: the tagline names it, and a section "The phone page: the set in your pocket" says what it does (listen
  with the screen off, earbud voice notes, taps and history, exams, the vibe, bars or time, install, the timeline).
  203 tools (17 `phone_*`).
- Skills: SKILL.md (away from the desk all day; answer in time to a casual listener), user-experience.md (bring the
  page to where they are; a new "Bars or time" section), blind-tests.md (exams on the phone), live.md (the page is
  part of the show: marks and vibe on every chapter change, the timeline), setup.md (optional: the phone page and
  Tailscale).

### The phone server restarts without dropping the person; a stream that fell behind catches up

- A restart after a merge dropped Nate's stream mid-set (10-06 08:38, "Why'd you stop?"), and a page whose command
  count was past the new server's missed every command after it. `phone_restart()` tells the page first; the page
  sees the server's boot change, starts its commands from 0, reconnects the stream at the live edge, and loads new
  page code (`build`) when it is next on screen and idle.
- The phone's player pauses on a weak network and carries on from there, so the delay grew (17 s, then 40 s). Past
  15 s behind it now plays 8 % faster with the pitch kept until it is within 6 s; the Live key shows the delay.

### The phone page takes the music's vibe, and reads in time as well as bars (Nate, 10-06)

- `phone_vibe`: an agent sets the page to fit the song (Nate: "change the colors ... how the headers look ... song
  covers in the background, blurred ... JavaScript effects ... I feel like you're there"). Presets (rain, calm,
  warm, night, peak), colours, a heading face from nine, an art layer with blur and dim, one ambient effect (rain,
  particles, pulse on the set's beat, grain, aurora) that stops while the page is hidden or for reduced motion.
  The server holds every vibe to a dark ground, ink 7:1 and accent 3:1, and refuses others saying what to change.
- Bars or time (Nate: "an option for the person to understand music in time"): lines from the page carry
  `into_s`, the page has a Bars/Time switch ("2:31 in" with the clock), and `phone_timeline` shows both.

### The phone session is a take: the page reports what happens on it, on one clock (Nate, 10-06)

- Nate: "can you see when I download stuff? where I'm scrolling ... like a VR take ... but for the mobile interface
  ... do you know if I'm on my phone?" The page now sends its own actions, timed (`/api/events`, kind 'page'):
  open with the device, Listen and Stop, hidden and visible, the section in view, downloads, clips, panels, note
  start and end, earbud presses. Every line from the page carries `room` (the engine's bar, also off the stream)
  and `now` (the piece); voice notes carry `dur_s` and `ended_by`.
- `phone_timeline(minutes=)` lays the session on one clock with what they said. `phone_listen(page=True)`
  includes the page's actions; without it they never wake a waiting agent.
- An open panel or exam, and the files it offers, survive a server restart (a merge dropped an unread one).

### The phone page: momentary keys and a tap history, notes that never cut off, installable (Nate, 10-06)

- Nate: a key "stays pressed even once that has been sent ... when I press it again, I'm unpressing the button".
  Every key is momentary now (Love this no longer rests in the accent, the mood detents flash SENT instead of
  staying selected), and the page lists what he asked for: each tap and mood with its time and the piece that
  played, today's count, and the mood he asked for the next chapter. The server keeps it (it survives a reload or
  a restart).
- Now and next carry a small mark: a heart for a piece he loved, a loop for one played again, NEW for one just made.
  `phone_now(now_mark=, next_mark=)` sets it; without it a love tap during that piece shows the heart.
- "The voice recording should not cut me off": notes stopped at 60 s mid-sentence. They now run until his press,
  30 s of quiet, or 10 minutes (a warning 20 s before).
- Installable from Chrome, after his tooler PWA: PNG icons (192, 512, maskable; `python -m ismail.phone.make_icons`
  redraws them from icon.svg), an Install app button when Chrome offers it, and optional notifications for what
  the DJ says while the app is in the background.

### The phone page picks the set back up after a reload, and a spoken answer reaches an open page

- On Nate's walk (2026-10-06) the page reloaded (the phone dropped it while locked, or it was reopened): the stream
  stayed off, his voice notes still came in, and a spoken `phone_say` was dropped ("nobody is listening"). The page
  now remembers it was listening (30 minutes) and reconnects by itself; when the phone wants a tap first it shows
  Resume, buzzes and says the set is still playing, instead of retrying silently.
- `phone_say(speak=True)` with nobody on the stream sends the words to the open page as a spoken clip; with no page
  open it says so. `phone_status` says whether a page is open and whether it is on the stream.

### A script under `machine run` can render; a slot caps its threads (ledger:M81, ledger:M132, hq:D-16)

- `machine run` hands its slot to the command it starts (`$ISMAIL_SLOT`): a script that renders or measures runs
  those ops in the run's slot instead of being refused, or deadlocking, on a second one. That happened three times
  on 10-05/06 (a paper harness, the DJ's salsa checks, the phone cue tones). A child's GPU step still takes the GPU
  slot; a live engine always takes its own.
- A slot counts jobs, not cores, so it now caps its threads everywhere: BLAS and OpenMP in its process, torch where
  ismail loads it (the perceptual model, demucs), and a command's environment under `run` (`--threads`, 2 by default
  for `--cpu`). One slot that ran a render, eq_match and the perceptual model on every core took the CPU to
  88-100 % and a live set dropped 359 buffers.
- While a set is on air, the CPU jobs beside it share half the machine's threads; a job that would pass that waits
  and says how many it may ask for. The board shows each job's threads.

### The skill says when to offer the phone page, and how it is hosted

- `references/phone.md`: when to offer it (a set plays and the person steps away: a walk, bed, another room), the
  one line to say, and the hosting (Tailscale today, with what the person installs; the home network later, which
  needs https for the microphone). SKILL.md's which-tool table and `live.md` point there. Nate asked that the skill
  know the page exists, not only the code.
- The earbud cue tones (a note starts, ends, arrived, blocked) are made with ismail on its measured grand piano
  (`songs/_phone_cues/make_cues.py`), set to -22 LUFS so they sit under the music. The page keeps its synthesized
  tones as a fallback until the files load.

### The phone page's keys get icons (Nate: "they could have their own little SVG icons too")

- Love this, change it up, calmer, more energy, quieter, louder, pause and resume set each have an icon drawn to
  the page's contract (24-unit grid, 2px strokes, square ends), with the word small beneath it. A tap lights the key
  and reads SENT for a moment, so it can be read at a glance with the phone half out of the pocket.

### The pose read-back says when a limb fell short

- `stage_actor_pose` replies `short`: the limb ends that could not reach their target in that pose, in metres
  (`{"hand_l": 0.066}`: the left hand stopped 6.6 cm short of where it was sent, at full reach). A hand that cannot
  reach the bar from a start pose is a number for the agent, not something found in the headset.

### A brief for a take helper agent

- `references/stage-takes.md`: when the person wants a fast take loop and the scene's agent is busy, a helper agent
  runs it from a brief (scene, who, goal, setup, takes, the stage tools it may use, trial moves only, when to stop,
  what to hand back). The person asked for it so the loop never waits on a busy agent.

### A set on air comes first, for now (ledger:M127, hq:D-11)

- While a live engine is on the board, the governor holds GPU jobs and Blender renders (also those registered as
  `--cpu`: blender, eevee, cycles) until the set ends: two real dropouts in a set tonight came from renders that
  started beside it. Audio renders and other CPU jobs still run. The board shows ON AIR and the policy.
- It is a policy, not a hard rule: `python -m ismail.machine on-air --policy set_first|off --by '<who>'`. Nate's
  words: "only for now during sets"; revisit after the laptop's repaste or a faster engine. `render_first` (the
  render runs when it is a burden and the DJ pauses the set, announcing it) is named as the future direction and
  refused until it is built.

### The phone page from the earbuds (Nate: "control everything through voice ... without taking the phone out")

- While a set plays, the earbud's press takes a voice note and the next press sends it, with tones you hear in your
  pocket (start, end, arrived, blocked). Short notes that are only a command act as one: "stop listening" stops the
  stream, "love this", "change it up", "calmer" and the rest arrive as taps with `via: 'voice'`. The microphone is
  held from Listen so a press works with the screen off. A note stops itself after 60 s.
- When the server does not answer, the page backs off to one try every 30 s instead of every 4 s.

### The phone page, designed (Nate: "check the style" with the taste skill)

- A deliberate look instead of the dark default: a field radio for one hand at night. The bar you hear is a tape
  counter, recording is an ON AIR lamp, the keys are labelled hardware keys (Archivo condensed, JetBrains Mono for the
  counter and the log, one accent derived from the name), and nothing is a symbol glyph Android could draw as emoji.
- The talk key shows your microphone's level while it records, so you can see it hears you. The DJ log has a time
  column. The mood control is a four-step selector, and the DJ's own buttons sit in their own section.

### A mirrored take plays back mirrored; the gallery stays put

- A take made while the Follow mirrors keeps `mirror` in its meta (at the start, on every change while it records, and
  when the last Follow is kept), and plays back mirrored, as does the playback right after the Follow.
  `stage_actor_play(mirror=)` overrides. The user, 2026-10-05: a take recorded with Follow's mirror played back the other
  way.
- The gallery's back and next change the picture on one panel where it is. Each answer used to fade the panel out
  while a new one opened 30 cm aside ("closes the whole gallery and opens it again"). Panels gain `stay` and
  `rewrite(id, {title, text, image})` for this.
- A start pose of 'rest' on a take played in place (or before the stand-in has loaded) stands at the take's anchor
  instead of failing.

### Loading without the long stalls (the "parallelograms" on the Quest)

- The Quest has no parallel shader compile, so the room's one precompile (`compileAsync`) stalled a frame for about a
  second, and the trees' growth shaders, patched after they were staged, all compiled in one drawn frame (1.6 s, 28
  programs). A stalled XR frame is reprojected: the user saw "vertical parallelograms on each eye".
- Now, without the extension, `reveal.js` compiles one material per frame before the reveal (the root compiled with the
  other meshes' materials set aside for the call), and waits for each program's link inside that frame, not at its first
  draw. With the extension it compiles at once off the frame, as before. `?paced=1` tests the Quest's path on a desktop.
- A tree's growth shader is patched before it is staged (`patchGrowth(root)`), so it is compiled with the rest.

### Pinch again to reach what is behind or inside

- In VR, a second pinch at the same point (within about 6 cm, or 4% of the ray's length, and 6 s) takes the next
  thing under it, then the next, round again: along the ray up to the first wall or locked part, and for a finger,
  every box the fingertip is in, smallest first. It works for selecting and for moving. Each pick among several emits
  `pick_cycle {how, item, n, of}`. A person's box covered the cup in their hand and the things near them, so a pinch
  there always gave the person.
### A performance always has a way out, and agents hear it while it runs

- The user spent seven minutes inside a performance an agent had started ("I'm basically just stuck in a
  performance"): its Follow had no panel, its voice was one clip transcribed only at the end, and nothing he said
  reached the agents. Now:
  - an agent's Follow (`stage_actor_follow`) opens the Follow panel too (Stop, Mic off);
  - `stage_perform(action="stop")` ends the whole performance: the Follow, and a take recording with it;
  - the person can always end it: both thumbs down held 1.5 s (the HUD says so), or saying "stop the performance"
    (or "stop the recording", "end performance"), which the server turns into the same stop;
  - a clip cuts itself at the first pause after 6 s, or at 25 s, with no gap (the next starts before it stops), so
    its words arrive while the performance goes on; a clip with no voice in it is not sent to the speech server;
  - a clip's words (`perform_clip` with text) reach `stage_listen`, the text first.
- A take made in a performance plays its recorded voice only on the person it was recorded for:
  `stage_actor_play(voice=)` True plays it on another body too, False keeps it silent. One clip sounds once, however
  many bodies play the take and however often it is played (a dance take on six dancers, played twice, built up "a din
  of just me").

### Follow counts down first

- The menu's Follow (and Take on a person) counts 3, 2, 1, GO in front of the user, with ticks, before the person
  starts following: the person holds their pose while the user takes it, and the Follow (and its performance clock)
  starts from the user's pose at GO. `stage_actor_follow(countdown=)` does the same for an agent. The user's take
  started from his pose when he pressed Follow, hands down while the man's were up ("kind of makes it impossible").
- A played take honours the pins: the ones it was made with (kept in its meta as `pins`, Blender xyz), else the
  session's. Sam's take played anchored by the feet though his hips were pinned to the stool.
- A recorded voice plays back at a conversational level (about -30 dBFS RMS), not the raw mic level ("super loud").

### Start poses (the person starts from their own pose, the user's motion as changes)

- `stage_actor_start(scene, person, pose, mode)` keeps a start pose in the actor's profile (`actors/<body>.json`
  "start"): `'rest'`, a frame of a take (`{"take": id, "frame": n}`), or a pose exported from Blender
  (`{"bones": {name: {"rest": {head, tail, x}, "pose": {head, tail, x}}}}`, Blender metres, rest in armature space,
  pose in world). With `mode="relative"` (the default) every Follow and every playback starts from it: at GO the
  person holds the start pose, the head and spine turn as the user's head turns, the hands move as the wrists move
  (scaled to the body) and turn as they turn, the hips and legs keep the pose. `mode="snap"` keeps the old behaviour.
  The user held the bar while Sam's hands were up; the film assistant asked for start poses as data.
- `stage_actor_pose(scene, person, t)` reads the joints back in Blender metres (pelvis, spine, head, elbows, hands,
  knees, feet): now while following, at `t` of a playing take, otherwise the start pose (frame 0 of the next
  Follow), so contact can be checked by number.

### exam_check: every exam's pre-flight (D-7: self-checks live in tools)

- `exam_check(page or clips, key, secrets, submit_url, answers_path)` before any exam reaches the person: every
  clip exists and decodes; loudness within 1 LU; no blind leak (the key's classes or secrets in file names, URLs,
  metadata tags or the page source and what it loads, a key file the page loads, formats, lengths, leading silence
  or order that separate the classes); a marked test answer posted to Submit lands where the agent reads answers.
  READY or NOT READY, each problem named with what to do.
- `phone_exam` runs it and refuses a NOT READY exam (`key=`, `secrets=` for the leak checks; `check=False` only when
  the person asks to see it anyway). `references/blind-tests.md` section 0 says when to run it.

### The phone page: a live set in your pocket

- `phone_start` serves a page over the tailnet that plays the live engine's master as an mp3 stream, which keeps
  going with the phone's screen off. The lock screen and earbuds work too: next = change it up, previous = love
  this. It has 30 s rewind and jump to live, reconnects by itself, keeps taps made offline, and has 64 or 128 kbps.
- Talking back: hold to talk, or tap once to talk hands-free. Notes are transcribed on this machine, waiting while
  the CPU is over the governor's limit. Taps on the page: love, change it up, calmer or more energy, quieter or
  louder, pause or resume the set, start a set. A mood hint: calm, steady, lift, peak. Every line is stamped with
  the bar the person actually heard and how far behind the room they are, and lands in an inbox the agent reads
  (`phone_listen`), in the song's `notes/phone_inbox.jsonl`, and in hooks.
- Agents drive the page: `phone_now` (now playing, next up, why recording is on or off), `phone_say` (a caption, a
  pinned "since you left", or a line spoken into the stream when answering), `phone_ask`, `phone_panel_show`,
  `phone_exam` (blind exams with Submit, answers written to the exam's file), `phone_offer` (downloads),
  `phone_buttons` (their own buttons as data), `phone_buzz`, `phone_status`. Reference: `references/phone.md`.
### voices_list names each voice's function (from the e002 pilots)

- `voices_list` shows `fn=perform` or `fn=voice` on every code voice; it used to say `'fn': 'voice'` for all of
  them, and electric, emily, kit70, rusty, rhodes and crackle only have `perform`, so an agent's first `track_add`
  failed. A track that names `fn: 'voice'` on a performer voice now plays it with `perform`, as leaving `fn` out
  already did.

### Setup a novice can sit through (M119, the dress rehearsal)

- `references/setup.md`: say the install takes about five minutes and looks frozen; prefer `uv pip install`
  (faster, shows progress); warm up the plugin's uvx command before the plugin lines (a first start that builds
  for minutes can time out); the app restart said once beforehand and done once; what the Allow prompts look like;
  winget lines runnable as written; reloading PATH inside a desktop app; the play command per OS; and
  `ISMAIL_SONGS` set to their music home so `guide`'s first-session check and the machine board see their songs.
- Whether they play or read music rides on the first session's first question: two questions, not three
  (`AGENTS.md`, `guide`).
- `machine`'s WAIT text no longer tells an agent to ask someone new to close things: wait quietly and say "the
  computer is busy, one moment" (a person who runs other work on the machine can still be asked).

### sketch hears a gentle classical or church brief (M118, the organist's dress rehearsal)

- A classical feel: "prelude", "hymn", "chorale", "church", "sacred", "baroque", "classical", "chamber", "adagio" and
  the like give a slow piece (56 to 72 BPM) of piano broken chords, a piano tune, cello and contrabass, in two
  phrases and a fading close; its chords move by function and every reading closes on a cadence (V I, or IV I for
  the second), so the chord readout ends on the tonic.
- "Gentle", "soft", "quiet", "calm", "slow" and the like play every part lighter and keep the tempo at 76 or under.
- "Strings" is a section (violin and cello, and contrabass when no other bass plays), and the reply says so; parts
  a classical brief fills in are named in the reply.
- In a classical or soft sketch the tune sits on top: every other part is at least 9 dB under it (the rehearsal's
  cello read 17 to 22 dB over the piano melody; now each part measures 4 to 6 LUFS under it).
- The readings sit in order inside the tempo range: sparser the slowest, busier the fastest. The reply names the
  closing cadence and a soft reading.

### Point an agent at the repository and it knows what to do (family first sessions)

- `AGENTS.md` (and `CLAUDE.md`, which loads it): a person says "set me up with ismail" and a link; the agent reads
  the playbook, sets it up itself, calls `guide` (the first session for someone new), learns whether the person
  plays or reads music, and knows where contributing and changing ismail are described.
- `references/setup.md`: setup done by the agent: look before installing, the package-manager commands per OS, the
  one-line warning before any prompt only the person can answer, the plugin / MCP / pip routes, proof by a sound
  they hear, and errors that are the agent's to solve (and to write down for the maintainer).
- `references/contributing.md`: contributing for someone who has never used git: in the studio (review, ask,
  HANDOFF.md, one sentence to start a new conversation), through GitHub (signup, `gh auth login --web` with the
  code read to them, fork and pull request done for them, their no-reply address) or without it (a bundle).
- README's agent steps start from AGENTS.md and set-up-yourself; the first questions are `guide`'s.

### sketch honours the brief's words (M021 run 1b)

- Hats play in the groove (16ths when crisp); a named ride rides the groove's second phrase and is the whole
  breakdown (no snare, no fill out of it); "fades out", "fade-out" and "fading out" make an outro that fades.
- "sparser" is sparser (the tune's passing notes out) and every chord loop starts on the tonic (i iv VII III read as
  G major); the reply says when no instrument was named for the melody, that the sketches are their own projects,
  and not to polish one before the person picks. Each sketch states its sounds (track_model), so renders stop
  flagging the sub as unstated.
- `ismail.machine`: the CPU reading is shared between processes on the board for 10 s; each CLI call used to
  sample the CPU for 2 s on its own (7 to 9 s of queue wait per heavy op on an empty board).

### Sampled voices: a Rhodes, a real kit, a clean guitar, vinyl crackle (from udio_ab_01)

- `rhodes` (keys): jRhodes3d by Jeff Learman, a 1977 Rhodes Mark I DI, 5 velocity layers. `rusty` (drums): Big
  Rusty Drums by Karoryfer, velocity layers, round robins and mics; a missing piece is skipped, not a crash.
  `emily` (guitar): Emilyguitar by Karoryfer, a clean DI guitar with legato, bends and vibrato lanes. `crackle`
  (fx): designed vinyl surface noise. Moved from the udio_ab_01 song, which made the same trip-hop brief.
- The samples stay out of the repo (`ismail/samples.py`): `samples_list` shows each set, its size and licence;
  `samples_fetch(name)` downloads it into `~/.ismail/samples` ($ISMAIL_SAMPLES) on the person's yes, or registers a
  folder that already holds it. A voice whose set is missing says what to fetch. Licences: Big Rusty and
  Emilyguitar are CC0; jRhodes3d samples are CC BY-NC 4.0 (the author grants CC0 for music made with them), so
  ismail ships the voice's code only.
- `sketch` reaches for them: a Rhodes, a breakbeat or live kit, a clean or bluesy guitar, vinyl or dust in a brief.
  When a set is not on the machine it plays the stand-in (grand_piano, kit70, the fitted strat) and says what to
  fetch, how big, under what licence. showcase.json lists them with their fx chains. 183 tools.

### `sketch` reads the brief (M110, from the first cold-start run)

- A brief's tempo, key, genre (trip-hop, hip-hop, house, jazz, rock, ambient ...), instruments and form (intro,
  groove, breakdown, build, return, fade) now shape the sketches: drum feel (break, four on the floor, jazz, rock),
  ride and crisp hats, seventh chords, a pentatonic melody for blues words, 4-bar sections with the right parts
  in each (a breakdown is keys and ride, an intro has no kick, an outro fades). Three readings: as asked, sparser,
  busier. A brief that names nothing still gets the three styles.
- What has no voice yet is said first ("SAY TO THE PERSON: asked for Rhodes: no electric piano voice yet:
  grand_piano plays its part"; vocals, winds, pads likewise).
- `sketch(..., base='<letter>')`: the next round is that sketch changed by the person's words (slower, no guitar,
  add a pad, D minor); each sketch keeps its spec in `sketch.json`. `n` sets how many.
- Each sketch prints a line when it is ready and reports its loudness and peak.
- showcase.json: a clean single-note guitar (the fitted strat through the clean rig) and `sub_bass` (a sine: the one
  synth a sketch uses); the gaps now list an electric piano, a singing voice, winds and brass.
- `ismail.machine`: a running job's memory counts what it added, not the holder process it runs in, and OVER needs
  0.5 GB past the declaration (every in-process render was flagged).

### A new person's first session: sketches in minutes (M110, S36)

- `guide` opens with a first-session block for a person who has made nothing with ismail yet (no finished render
  in the songs folder or beside the project, and no `~/.ismail/first_session_done`): two sentences on what this
  is, at most two questions, sound within about five minutes, short rounds, one "change just one thing" edit.
- `sketch(project, brief)`: two or three short contrasting sketches (solo piano, string trio and piano, a small
  band), about 30 s each, on measured voices only, rendered to mp3 in one call (about two minutes for three).
  Each sketch is a normal project: a motif and its answer in 4-bar phrases that vary and come home, chords that
  move, parts in their own registers and rhythms, levels trimmed to -16 LUFS. Key from the brief's mood or `key=`,
  chords from `progression=`.
- `sketch_keep(project, letter)`: the pick becomes the song (lineage to the sketch, the brief as its objective),
  and the first session is marked done.
- `ismail/voices/showcase.json`: the voices a first sketch uses, with ranges and why each is trusted; `guide`
  shows the list and `voices_list` marks them `*`. Not covered yet: a real-sample acoustic kit, a pad voice.
- SKILL.md: "A person's first session", and step 0 says the kept sketch is the example. 181 tools.

### The governor guards the disk and the commit (M72)

- `ismail.machine`: no new heavy job while a drive jobs write to (the songs folder's, the working directory's, a
  render's project) has under 15 GB free, or while under 6 GB of commit is free. The refusal gives the numbers and
  what to do. A live set on air is never held. D: filled four times in five days: render caches, takes, a game
  unpack, and the Windows pagefile, which grows into the disk when commit runs out.
- `run --disk <GB>` and `slot(..., disk_gb=)` declare what a job writes, and the check counts it. `render` declares
  its track cache and wavs itself, and a refusal says `cache=False` skips the cache.
- A running job's memory (private bytes, what the commit counts) is on the board every 15 s. Past its `--mem` by
  25% it shows OVER there, and its own output says so once (2026-10-05: a Blender job declared 7 GB, took
  10.7 GB, and the pagefile took D: to 0.2 GB in nine minutes).
- The board's new disk line shows each drive's free space and the pagefile's size. `live_status` ends with the
  machine's free commit and disk.
- History: each job's peak memory, its bytes written and the drive's free space at start and end. `history` sums
  the writes per song and counts the jobs that went past their memory.
- `machine_disk` (op) and `python -m ismail.machine disk`: the biggest folders under songs/ with their growth since
  the last day's snapshot, and every `_reclaim` folder. Freeing space means moving a project's finished
  intermediates into its `_reclaim/`; nobody deletes, the user clears it. 179 tools.

### Provenance: the string profiles credit their real sources (M73)

- `voices/strings/{violin,contrabass}.mimic.json` were measured from VSCO 2 Community Edition (Versilian Studios,
  CC0) and `cello.mimic.json` from "real cello notes" by flcellogrl (Freesound pack 12408, CC BY 4.0), both via
  tonejs-instruments; their `source` fields said Philharmonia Orchestra, which tonejs-instruments' own source list
  does not support. instruments.md says the same, and that the cello needs credit.

### What ran, and what it used: the machine's job history (M87)

- Every job that held a slot leaves one line in `<board>/history.jsonl` when it ends (append only): what, the
  session, the song (from its working folder), the kind, the estimate, start and end, how long it waited in line,
  its exit (a `machine run` command's exit code, or the error that ended an op), CPU seconds of the holder and every
  process it started, peak memory, the whole GPU's busy seconds and peak memory while it held the slot (one
  `nvidia-smi` loop per job), and the machine's state at its start (GPU temperature, clock, throttle reasons, CPU).
- `python -m ismail.machine history [--song slug] [--since 7d|2026-10-04] [--jobs N]` sums it per song.
- History starts with this change; jobs before it were never kept.

### Keyed moves that glide; trial moves that never save (M107)

- `stage_key_interp(scene, object, mode)`: "smooth" makes a keyed object glide through its keys (a cubic Hermite
  curve per segment, tangents from the neighbouring keys; the turn slerps without easing) instead of stopping at each
  ("stop", what an object with no mode does). Saved in anim.json `interp`. The same curves are in
  `ismail/stage/page/interp.js` (the page) and `ismail/stage/interp.py` (`sample_anim`, for a render), held within a
  millimetre by a test (the stage and the Blender render were 74 cm apart between camera keys; the Crossroads
  render now reads `interp` too).
- `stage_object_set(trial=True)`: a test move that never reaches edits.json, which the build reads; where the thing
  was before is what saves, until it is edited for real (an agent's test camera move autosaved into the build).

### Live control maps and actor profiles (M100)

- An actor's profile lives beside its body: `scenes/<scene>/actors/<who>.json` next to `<who>.glb`, so it goes
  where the body goes. `stage_actor_profile(scene, person)` reads it: the rig type (read from the body's bones), its
  named parts (bone chains, parents first: head, spine, arm_l, leg_r, fingers_l, index_r, ... on people; any other
  rig gets one part per unbranched chain, so a tail is a part) and its saved control maps.
  `stage_actor_map_save(scene, person, name, pins, drives)` stores one; a map named "default" applies by itself
  when a Follow starts. Every control op names the scene's person; world.json actors says which body plays them.
- The control map: which part of the user drives which part of an actor, changeable at any moment, during a Follow
  too. The built-in human map stays the base; `stage_control_set(scene, person, part, mode, joint, at, scale,
  touch)` takes one part over: `hold` (keeps its pose, riding with the body), `effector` (arms and legs reach for a
  target that moves as a user joint moves, relative to where both were when it bound: the user's hands act out a
  seated man's feet; `touch=True` shows a ball in front of the user and binds when that hand reaches it), `pin` (reach
  for a point and stay), `mimic` (copy a joint's turn, spread down the chain or 1:1 per bone with a list of joints),
  `default`. A hand that drives another part lets go of its own arm and fingers. `stage_control_map` reads the map,
  applies a preset, a list of drives, or clears it. The Follow panel shows the drives.
- An anchor partitions control (each part its own source and solver), it does not cut the body.

### A Follow is a performance; stage_batch (S32)

- The Follow button starts a performance and the same button ends it. While someone follows the person, no gesture
  acts (no travel, menus, phone, thumbs or grabs; a poke still presses a panel), and the Follow panel says so.
- The mic records from the first moment of every Follow, in clips on the Follow's clock (seconds since it began),
  uploaded a second at a time so a crash keeps what came in: `<scene>/performances/<id>/clip_<n>.<ext>` and
  `perf.json` (person, markers, clips with their words). A stopped clip is transcribed with word times, snapped onto
  the measured voice (whisper starts a phrase's first word up to half a second early, in the silence before it),
  into a `perform_clip` event with the words on the Follow clock.
- `stage_perform(scene, action)`: `state`, `stop_clip` (read it while the person goes on), `start_clip`,
  `next_clip`, `mic_off`, `mark` with a label. The Follow panel has Mic off / Mic on. `stage_performance(scene, perf)`
  reads a performance as text: markers, clips, each word at its time.
- While the person performs, `stage_say` shows the line and does not speak it (it would be in the recording);
  `aloud=True` speaks it. `stage_ask` waits for after the Follow (thumbs are off).
- After a Follow it plays back on the person first, with the voice, while the card asks Keep / Discard. A take kept
  from a Follow, or recorded during one, plays its performance's voice with it wherever it plays (meta `performance`,
  `perf_shift`: where the take's time 0 falls on the Follow clock).
- `stage_batch(scene, ops)`: many stage ops in one call. The page commands go to the page as one command and run
  back to back; an op that runs on the server first sends the page commands before it. With `stop_on_error` the
  first failure stops it and the files changed on the server go back.
- speakwright (the speech server) answers `response_format=verbose_json` with word times; an older one still gives
  the text, and the event says the words are missing.

### The machine board's lock holds under a crowd (M85); one test file is not a heavy run (M86)

- `ismail.machine`: a job waiting in line no longer dies when the board's lock changes hands under it (a holder
  let go between the waiter's failed create and its look at the lock: FileNotFoundError killed a queued render with
  three sessions waiting). A dead holder's lock is taken by an atomic rename, so two waiters cannot both take it,
  and a holder removes only its own lock. Test: eight threads take turns 480 times with no error and no overlap.
- tests/conftest.py: a run is a heavy job (it waits for a CPU slot) when it spans 4 or more test files, or includes
  a file that renders whole windows (live parity, live song parity, round trips); one touched file of any size runs
  without a slot. Before, any run of 30 or more tests counted, so a single 31-test file was refused on a hot machine.

### Live tests: notes go straight to the stage dev, and updates come back (S31)

- stage.md and stage-dev.md: during a live test the studio agent in the room sends the person's stage notes directly
  to the stage dev (and writes them in the handoff); when a change touches the page's code, the stage dev tells that
  agent the update is ready, and the person takes it with the update gesture. The scene's content is the studio
  agent's to change; how the stage works is the stage dev's.

### Roles: studio agents, the maintainer, the stage dev (S24); the intake lists open pull requests (M77)

- `python -m ismail.handoffs` lists the open pull requests after the handoffs (title, branch, author, checks,
  whether it merges cleanly), marked new or updated since the last `--mark`, so a pull request is never seen only
  because its author sent word. It uses the GitHub command line when it is there and says so when it is not;
  `--no-prs` skips it.
- The skill names two kinds of agent: studio agents work within ismail (a song, a sound, a live or DJ set, a film,
  a scene on the stage), dev roles work on it. A studio agent that finds something general asks the person whether
  to contribute it and suggests starting a dev session; it never turns into a dev mid-conversation.
- New references for the dev roles, from the sessions that held them: `maintainer.md` and `stage-dev.md` (VR is
  one way into the stage, so its developer is the stage dev). development.md gains the area dev role, the start-up
  routine (progress file, machine, intake, report; every 4 hours by default, the person sets it), and why proactive
  and reactive work cost differently.
- stage.md gains the headset lessons it lacked: queue spoken lines and keep captions up until spoken, one sound one
  meaning, nothing that hitches while the person is in, a purpose for every run, panels that stay put, everything
  touchable by hand, who answers when two sessions listen, listeners reconnect after a restart; the reload advice is
  corrected (the plain scene address).
- development.md: a public voice, instrument or profile stays compatible with the songs that use it (new behavior
  as a new param whose default is the old sound); when it cannot, the new one ships beside its ancestor under a
  findable name, with `derived_from` in its data and credit both ways.
- blind-tests.md: the listening device is part of the exam (record it, one per round, a device sweep when it
  matters, calibrate the listener's floor with codec lenses, log the floor) (S30).
- Skill text describes actions in plain words with synonyms (send a message to a session, conversation or agent;
  a browser pane, preview or web view) instead of one harness's tool names.

### Faster mimic renders (M76); placement exams (S23)

- `mimic.render` computes each partial only while it sounds, so a long tail after the release costs next to
  nothing (a cello C2 with a 3 s tail: 2.5 s to 0.4 s, the same sound within -70 dB). Channels that would be equal
  (width 0, one player) are computed once.
- New mimic params: `mono=True` computes one channel for a caller that sums to mono; `floor=<dB>` skips partials
  that far under the strongest (off by default: 60 dulls the top of low notes by 3 to 5 dB).
- blind-tests.md: a placement exam needs a reference point and a clear task.

### The VR stage (M46)

- `ismail.stage`: the VR stage that grew in the Crossroads video (songs/crossroads/video/vr) is now part of the
  engine. A three.js WebXR page for a desktop browser or a headset (Quest 3), a server that serves a song's scenes
  (`python -m ismail.stage.server --scenes <song>/video/vr/scenes`), and the live link between the page and agents.
- 63 `stage_*` tools: `stage_start` / `stage_stop` / `stage_status`, `stage_events` (what the person did and said:
  voice notes, gestures, edits), `stage_world` (a scene's world.json), `stage_scene_export` (a scene rebuilt from
  its Blender build script in a heavy-job slot), `stage_note` (the "updates ready" card), `stage_cmd` for a command
  with no op yet, and 55 typed page commands (`stage_object_set`, `stage_say`, `stage_panel_show`,
  `stage_waypoint_set`, `stage_actor_play`, `stage_stream`, ...). Each one waits for the page's answer and raises
  with the next step when no page shows the scene or the page refuses.
- Scenes are data: everything the runtime used to hard-code for one song (who plays whom, facings, partners, the
  floor, keep-out boxes, the default scene, the build script) lives in `scenes/<name>/world.json` and
  `scenes/stage.json`. Session files (client log, speech cache, bundle, update notes) go to `<scenes>/_stage/`.
- One server per port: a busy port is refused (Windows let two servers bind one port and split the live link).
- Pins while following: `stage_follow_anchor(scene, person, joint="hips", to=<seat object | [x, y, z] | "here">)`
  (and "📌 Pin hips" on the Follow panel) keeps a seated person's hips on the seat, facing their own way: the user's
  head only bends the spine (direction, so sitting lower or taller does not sink or lift them), the hands drive the
  arms, the feet plant in front of the seat or on a rung under a high one, or on their own pins (`joint="feet"`).
  Walking clearly away from the seat lets go. The Follow panel shows the pins and that talking is not recorded
  without the phone gesture.
- Every Follow is recorded, in memory: the newest 180 s of the last Follow wait in the page, and after "■ Stop" a card
  offers "💾 Keep as take" (then the usual review: play on them, trim, redo) or "🗑 Discard"; `stage_take_keep_last`
  keeps it for the person afterwards. A liked 61 s follow of the bartender was lost because a plain Follow kept nothing.
- `stage_events(who=)` names an agent that follows a scene; a listener with no name reads "an unnamed agent" in the
  headset, not "unnamed".
- Pins take the pointer ray: a to-do pin's card (and its diamond) stops the ray, lights its border, and a pinch opens
  its note, so pins can be worked from a few steps away (the ray went through the card onto what was behind).
- The version card in front of the person shows on entering VR, when the server restarts, and when listening flips
  between somebody and nobody; no longer each time one listener of several comes or goes (an agent polling every
  half minute kept bringing it back).
- Body panels hold still to be read: a panel the person looks at (within 32 degrees) stays where it is, and for 1.5 s
  after; looking at it never counts as the body turning; it moves only when its place has drifted 25 cm for 0.6 s
  (a walk, a real body turn), then eases there. It used to dart away as they turned to read it.
- The thumbs prompt rides on the answering hand: while a spoken question waits (`stage_ask`, now with `sender=`),
  and while the right hand forms a thumb at a panel, a card over the right wrist says who asks, the question, and
  what up and down will answer, filling as the gesture is held.
- Every panel and caption says who it is from: `stage_say(sender=)`, `stage_panel_show(sender=)` (a command's `from`)
  draw a chip and border in that name's colour, fixed per name, and a sender's body panels keep to one side. The VR
  card that goes with a spoken line rides with the person now, titled by its sender instead of "Claude". The
  server's own lines are from "stage".
- Pin touches land: a to-do pin's card takes a fingertip as well as its diamond, a pin re-arms after 1.2 s even
  if the hand stayed close (it needed the tip to leave by 18 cm), the gaze window is 60 degrees, and a tip at a pin
  that does not open it says why (`pin_touch_missed`). A finger gun at a pin does not travel.
- A poke at UI wins over travel: while a hand's fingertip is within 10 cm of a panel, menu or button, or for 0.8 s
  after it pokes one, the finger gun shows no travel arc and its thumb click does nothing (`travel_held` says so).
  Poking menu buttons with the same finger gun that aims travel had teleported the person twice.
- Panels that ride with the person: `stage_panel_show(anchor="body", side="right"|"left", seconds=)` keeps a message just
  out of view beside where their body faces (a new body heading from the head and hands: a held head turn or the
  hands held out turn it, a glance does not), following them as they move; dragging it moves its place around them.
  World panels stay for notes about a place. Speech and new panels wait while the person is talking (a note
  recording, the phone gesture, 3 s after a note): a reply arriving mid-thought had lost them their sentence.
- Presence: the stage knows who is listening. A listener is anything following the live log (`stage_listen` across
  every scene through GET /live/inbox, or `stage_events` with since= on one); the headset shows "listening: <who>"
  or "nobody is listening" on entering VR and whenever it changes. A voice note a listener was handed but did not
  answer within 6 s is announced ("Handed to <who>"); one nobody was handed is answered out loud ("Nobody is listening right now. I
  saved your note.") and kept in `_stage/unread.jsonl`. Hooks (`~/.ismail/stage_hooks.json`; a song's
  `_stage/hooks.json` only when that file trusts its folder) run commands on entered_vr, left_vr, voice_note and voice_unheard. GET /live/presence, `_stage/presence.json`,
  `stage_presence`. The page also says when the server behind it restarted. (After six notes went unheard.)
- Desktop movement: the wheel steps forward where you look (along the floor in walk mode), the orbit pivot
  travelling with the camera, so zoom no longer shrinks the orbit until the wheel stalls; walk and key speed scale
  with the scene (3.5 m/s in the Crossroads club, was 1.2), Shift 3x.
- Scene lineage (M75): a scene's world.json may say `derives_from` (and `pass`, `pass_env`, `assets`).
  `stage_scene_export` builds a derived scene from its ancestor's full build, then its line's passes in the bridge
  (`VR_PASS`), the line's edits merged (the variant's win) and the Quest diet in the bridge (`VR_DIET`), so a remodel
  or another era never starts from a blockout. `stage_scene_new(name, source=)` makes one: the lineage in
  world.json, the page's pieces copied (trees, names, cues, waypoints), actor bodies from the source. Exports set
  `STAGE_SCENE` so renders go in `renders/<scene>/`. stage.md: "Deriving a scene".
- The stage server stays up (M74): a fixed pool of 32 workers instead of a thread per request (Python 3.11 kept every
  finished request thread, and the page polls 2.5 times a second: the old server died of a MemoryError after about
  40,500 threads), long-polls capped per client, `GET /health` (shown by `stage_status`). A full disk no longer
  stops the live link (state and events stay in memory) or the headset's boot (the last good bundle is served).
- The Blender bridge and the Quest diet (`ismail/stage/bridge`: merged groups, triangle and texture budgets,
  MakeHuman skin and mask-map fixes) ship with it. three.js r180 is vendored (MIT).

## 0.3.0

### Verify from the person's side; small fixes

- SKILL.md non-negotiable: every piece of work names what the person will see, hear or feel, is checked from their
  side, and is reported "verified" with the evidence (or "not verified yet" and what to look at), never a bare
  "done". Ops that change what the person perceives reply with what verifies it.
- `api.call(name, /, **kw)`: the op name is positional-only, so ops that take their own `name` argument
  (`mimic_measure`, `live_stream`) can be called through it.
- blind-tests.md: check the answer key for patterns before the page goes up.
- instruments.md: VSCO 2 CE file names are one octave low (the harp excepted).

### Install and distribution

- On PyPI: `pip install ismail`, or `uvx ismail mcp` to run the MCP server without installing. New console
  commands `ismail` (the CLI, same as `python -m ismail`) and `ismail-mcp`; `ismail mcp` starts the server.
- Claude Code plugin: `/plugin marketplace add newsbubbles/ismail`, then `/plugin install ismail@ismail` loads the
  server and the composing skill together (`.claude-plugin/`).
- Listed in the official MCP Registry as `io.github.newsbubbles/ismail` (`server.json`); a tag push publishes to
  PyPI and the registry (`.github/workflows/publish.yml`).
- `Dockerfile` (stdio server) and `glama.json` for directories that build and inspect servers; `CITATION.cff`.
- The README, the plugin and the client configs install from GitHub (`uvx --from git+https://github.com/newsbubbles/ismail ismail mcp`),
  which works whether or not a release is on PyPI. README: an "If you are an AI agent" section.

### Roadmap

- ROADMAP.md: where ismail is going (worlds, visiting each other's, hands and 4D editing, agents in the room,
  sound, pictures, the person, a hub for shared work) with a status on each; a "Where it is going" section in the
  README.
- SKILL.md: share what would help others (a fork and a pull request, after the inclusion review); development.md:
  sharing back from any user's project; user-experience.md: pictures have a vernacular too.

### Live output follows the device

- The live output follows the system default (`device='default'`): a Bluetooth speaker that connects mid-set takes
  over within a few seconds, and a device that stops taking audio falls back to the default. `live_device` moves a
  running set to another output by hand. The timeline, queue and audio mixed ahead carry on (about a second's gap);
  `live_start(follow_device=False)` keeps the old behaviour.
- The handoff scanner skips `history_src/` backups.

### Live: stream to a scene, controls

- `live_stream`: the master, a bus or a deck as raw PCM (int16 stereo, 44.1 kHz) over a GET on the engine's
  localhost port, each with its own safety limiter; a listener that falls behind loses its oldest audio and never
  slows the set. For a VR stage that plays each object's own bus at its place (relayed by the page's server).
- `live_map`, `live_control`, `live_controls`: a named control (a knob, slider or switch) mapped once to a track,
  bus or deck volume, a deck EQ or an effect param, with linear, log, switch or raw scaling; moves apply in the
  engine at once and are logged by bar, and read back as `[bar, value]` automation points.
- A streamed bus or deck that goes quiet sends silence instead of nothing, so every listener stays in step (the
  Crossroads stage had to fill the gaps itself), and `live_stream(project, name=...)` works (the op's `name` clashed
  with the dispatcher's).
### The stage in the skill

- `references/stage.md`: building a scene with a person inside it, from the Crossroads build: which surface for
  which decision, eye exams for pictures, bodies and contact, contact with a person in a headset (every interaction
  a sound, the person's names for things, a first-time tutorial one gesture at a time), shared-editing rules that
  never lose their work (no save before the scene has loaded), agents first.
- user-experience.md: when the person is inside the work. SKILL.md points to stage.md.

### Placement offsets

- Sounds land by their start; music lands on an anchor. A note can now be nudged off its beat in milliseconds:
  `'0 C4 1 100 @-40ms'` (negative = earlier), and a whole part with `track_set(offset_ms=-35)`. The note keeps its
  beat: `notes_read` shows the `@`, the roll stays on the grid, quantize and copy keep the nudge, and
  `notes_transform(offset_ms=)` sets it on many notes. Automation stays on the song's time; audio clips move with
  the track offset. A nudged note is the same as a note written at its nudged time, in the studio, in any window
  (bit-identical), on a deck (live parity) and in the video sync. A note nudged before 0 s starts at 0 s and the
  render reply says so. Live clips read `@` the same way. From tambopata, whose player was time-warped to land on
  the beat and sounded synthetic.

### The person: lexicon, objectives, user-experience reference

- The lexicon: `lexicon_note`, `lexicon_find`, `lexicon_view` keep a two-way map between the person's own words for
  what they hear and see ("boxy", "too clean") and ismail's terms (ops, parameters and their direction, effects,
  measurements), with the song, the moment, the craft the word belongs to (composer to colourist) and whether the
  change worked. Lookups go both ways; the view reads as a learning curve (the share of trade words by month, the
  crafts a vocabulary grows in). One local, append-only file shared by every session (`songs/_user/lexicon.jsonl`
  or `$ISMAIL_LEXICON`), never committed; a `who` per entry for other people's feedback. Words about the work only:
  never emotion, mood or health.
- Objectives: `project_new(objective=)` and `project_set(objective=, objective_by=)` record what a piece is for, in
  the person's words, with history; `project_info` shows it. `project_new(derived_from=)` carries the original's
  objectives and lineage into a version (intent provenance).
- Skill: `references/user-experience.md` (the lexicon, exams and pages, objectives, consent, what never to
  record), a SKILL.md non-negotiable, and a THE PERSON section in `guide`.

### Measure first, in the tools

- Every track says what its sound is modeled on: `project_info` lists each as measured (a mimic profile, a measured
  library voice, a sample imported or extracted from a recording, a fit), designed (on purpose) or unstated, and
  `render` names the unstated ones with the ops that measure. Fits record it themselves (`instrument_fit`
  apply_to_track, `track_fit` apply); the new `track_model` op records an example the tools could not see or marks
  a sound designed. Agents skipped measuring when the rule was only in the skill, most of all after a context
  summary; the tool replies keep it in view.
- Library voices state their provenance in INFO (`measured` or `designed`).
- `guide` opens with MEASURE FIRST.

### The machine: a line, and priority from the user

- `python -m ismail.machine run --wait 30m` (and `slot(..., wait=)`) stands in line for a heavy-job slot instead
  of being refused, and a job that does not wait yields to every waiter ahead of it. The board lists the line.
- `python -m ismail.machine priority <session> --for 3h --by "the user"` puts that session first in line until it
  expires (`--clear` ends it); it needs `--by`, since only the user gives it. Priority orders the line only: the
  heat limit, the busy CPU and the memory reserve hold for everyone (`--force` skips the heat limit and was the
  only way to go first; the voice session asked for this to finish its blind exams).

### What to reach for when

- SKILL.md: "What to reach for when", one table from the situation to the op, reference or rule (the start of a
  task and every compaction, measuring, real instruments, eye and blind exams, the person's words, sources and
  credits, versions, the shared machine, live sets, sharing back). The tools say back what is missing; the table
  says what to reach for.
- README, "If you are an AI agent": measure first, then ask the person's senses with exams (an eye exam for a
  direction, a blind exam to know when you are done, from one note up to the full mix).
- blind-tests.md: why exams (the person's senses as data, their words into the lexicon) and climbing from the
  smallest unit, since a test pitched too hard gives no direction.
### Credits name what the piece uses

- `credits` credits only the sources the piece uses: a row is used when a part's model names its file, its folder
  of takes or its id (a video id naming a stems folder), a mimic profile or a sample came from it, or its table's
  'in the song' column says so (prose like "**in the song**: the drum kit" or "not yet" reads as yes or no).
  `consulted=True` adds the rest under "Also consulted". A plain list that repeats a table's rows (a SOURCES.txt of
  video ids) is left out, and a table under its own heading keeps it.
- `track_model(on=[...])` takes several sources for one part (a forest bed from two recordings, a voice
  cross-synthesized from bird takes and a player's stem), and a folder of takes (`on='ref/birds/potoo'`).
### Live: the set says when it runs out

- `live_status` adds `RUNWAY ENDED N bars ago` when a set that has played has had nothing new queued for 8 bars
  (what loops on unchanged, or that it rings out), and `THIN for N s` when one track is left where the set has had
  several, or the mix sits 20 dB under its usual level, for 30 s. A set ran 10 minutes on one hat loop after its
  outro forgot to stop it, and `SILENT ON AIR` never fired.

### Sources and credits in the tools

- `project_info` lists a song's sources: the recordings, videos and scores in its `ref/` folders, which of them
  have a row in a SOURCES file, and rows without a licence or an author. `render` names files with no row. Stems,
  separations and analysis projects are skipped; a video's id in a SOURCES row covers its download and the wav
  made from it.
- `credits` (new op, 98 tools) writes `CREDITS.md` at the song root from the SOURCES tables (approval, local path,
  date and note columns left out), each part's model grouped by source, and the lineage. It says whether the piece
  plays recorded audio (imported samples, audio clips) or holds only measurements, and lists what it could not
  credit.
- `track_model(on='ref/birds/x.mp3')` takes a file in the song's `ref/` folder: any `on` starting with "ref" was
  read as the project reference and refused, and paths were looked for only under `proj/`.

### Skill: provenance, the deliverable, the machine-aware performer

- SKILL.md: provenance is kept from the first download to the public page. Every source a song uses, measuring-only
  ones included, gets a `ref/SOURCES.md` row (who made or played it, licence, what was measured); every track says
  what it is modeled on; performers and communities measured from recordings are credited; public pages take their
  credits from `SOURCES.md`. instruments.md and music-video.md log sources and assets the same way.
- SKILL.md: long work stays on its deliverable. `PROGRESS.md` at the song root, read at every start and after every
  compaction; reread the story before designing for it; a tool is a side track unless it unblocks the next step.
  music-video.md: the cast comes from the story, never from the track list, and effort follows the cast's order.
- live.md, "Watch the machine as well as the room": lessons of a 16-hour set (guards keyed on audio trouble,
  tracked watchdogs, shrinking the rig under load, one deck on a busy machine, the set on the person's clock, pause
  and resume as a move, borrowed voices checked before they go on air).

### Fixes from song handoffs

- Render memory: a track's whole output stays in memory only while an effect reads it (a sidechain or vocoder
  source), and stems are kept from their first to their last sound as float32, built full length when read. A
  40-bar song of 16 one-note tracks: peak 2856 -> 404 MiB, 1835 -> 73 MiB held after. A 146-bar song of ~70 tracks
  (tambopata) had needed ~20 GB and ran out of memory.
- `mimic_measure` measures a short note (a 0.3 s panpipe): its attack calibration read the note's level over a
  window that started after the note ended and produced a non-finite buffer.
- `analyze_timbre` (and every analysis reading a source in stereo) works on a mono file: both channels read the same.
- Live decks run the song's master chain (its limiter, compressor, any master effect), as its studio render does:
  `live_load` dropped it, so a deck played about 4 dB quieter and unlimited. `live_status` shows it on the deck line.
- A looping performer clip no longer hands its voice a negative song time: the context carried over the loop point
  sat at negative clip beats, and a voice that seeds on the song time (the birds call voice) failed on it.
- `live_transition` takes the old deck off air when it ends, so the next song loads onto it (it was refused, and a
  helper that crashed on the refusal left a set silent for 9 minutes). `live_status` says `SILENT ON AIR` when a set
  that has played sounds nothing for 10 s. `live_deck(cue=False)` describes the deck as it will be. `live_load`'s
  doc says a cued deck plays from `at`.
- `djkit.gap` brings each target back to a level (`level_db`, a number or per target) instead of 0 dB, and
  `Set.gap` uses the gains the set has given each track: a set running its tracks at +4 to +8 dB had every drop land
  under its build.
- The machine governor: `run --est` takes a unit (`10m`, `600s`, `1.5h`; a bare number is minutes and one above 240
  is refused, since `--est 600` meant as seconds showed a 10-minute render as 585 min). The board shows a `python -c`
  job by its first line and how far past its estimate a job runs.

### Roles and the migration process

- `development.md`: the roles as a multi-agent system (the user, song agents, the dev agent, subagents: what each
  owns and writes, and the contracts between them); the inclusion review before anything goes into the public repo
  (general beyond its song? measured from the user, who must say yes first? fitted to a commercial record or
  carrying a brand name, held for the user's decision? provenance recorded); preparing a voice or an engine for
  ismail in eleven steps (copy never move, strip the song out, render the same anywhere, live parity, INFO as manual
  and provenance, nothing existing changes by accident, tests, cost, docs, evidence in the PR); how to take an API
  change a song asks for (find the workaround and its cost, check it does not exist already, extend before adding,
  design the text first, close the loop, keep old names, prove it on the asking song); lessons from running the loop.

### The shared machine

- `ismail.machine`, the governor for one computer shared by many sessions (2026-10-02: six sessions stacked heavy
  jobs on a laptop GTX 1080 until it sat at 92 C pinned at 139 MHz and the user stopped everything). Op
  `machine_status` (and `python -m ismail.machine`): GPU heat, clock and throttle reasons (an idle card at 139 MHz is
  not trouble; a thermal bit or 85 C is), CPU, free commit, and every heavy job running in any session. Heavy jobs
  take slots on a board in `songs/_machine/`: one GPU job and two CPU jobs machine-wide, a live engine on air holds
  one. render, separate (GPU slot when it runs on CUDA), mimic_measure, instrument_fit, track_fit and live_parity
  take a slot and refuse with what is running, whose it is, when it should end, and what to do; a render whose
  memory estimate does not fit the free commit is refused instead of dying. Commands outside ismail run in a slot
  with `python -m ismail.machine run --gpu|--cpu -- <command>` at below-normal priority. Heavy jobs cap numeric
  threads at 2. The test suite takes a slot too.
- The governor also refuses a new CPU job while the CPU is 80% busy or more over 2 s, whoever is using it, and names
  the top processes: most load on this machine is not on the board (the desktop app, a node server). The board shows
  the top processes.
- Skill: "The machine is shared" is a non-negotiable (check before anything over a minute, one heavy job of your own,
  a hot GPU is not a free CPU, size jobs to the question, no sleep-poll loops, never leave heavy jobs running
  untold); development.md: touched tests locally, the full suite in CI; announcements never ask for work.

### Roles and collaboration

- Skill: "Your role, and where things live": making music is the default role and writes only inside
  `songs/<slug>/` (no edits to `ismail/`, `skills/`, tests or other songs, no git in the ismail repo); a missing
  capability is built in the song and listed in its `HANDOFF.md`; one folder layout for every song; song
  checkpoints with a git repository inside the song folder. The `guide` op opens with the same rule.
- New `references/development.md` for changing ismail itself (only when the user asks): a worktree per topic, the
  collaboration must-haves (what is not yours is not touched, nothing unmerged is deleted, no stash in a shared
  repo, proof and docs in every commit, the user merges), migrating from a song's `HANDOFF.md`, its format,
  and studio/live parity: every engine change says which side it touches, how live follows, and what proves it.
- `live.md`, from a 40-minute blues set: only proven sounds go on air (no voice written during set prep and never
  fitted or ear-tested), first sound within minutes, never downgrade a sound in silence, a song plays live through
  `live_load` and is never rebuilt by hand, and anything that writes runs on a copy, never in another song's folder.
- `blind-tests.md`: ear-test pages are always hosted on localhost and opened in the app's browser pane, take answers
  with a Submit button that writes them to a file, and are checked in the pane before the user sees them.
- Skill brought up to date with the live parity work: phrase voices no longer run an amp inside the voice (a
  guitar is `electric` with its rig on the track, live as in the studio); the performer signature takes `beat0`;
  how to write a performer's expression in the studio (`inst.lane.<name>` automation); the rig effects, presets,
  `electric`'s half-step-down tuning and `kit70`'s drum map; five rules for a voice that renders the same in a
  window, live and in the whole song (from `songs/tambopata`); `live_parity` for checking a song on a deck; what
  `live_status` and a deck's "not live" list now say; render speeds of the library performers. Fixed: the limiter
  ceiling (-1.0 dB for mp3 everywhere), the eye-exam page (Submit, not a copy button), wah as an effect sweep, not
  a lane, and where the engine looks for a song's voices. SKILL.md: call `guide` before writing a measuring
  script. The `guide` op's live text matches.
- The migration loop (`references/development.md`), the dev role's standing job: intake with
  `python -m ismail.handoffs` (every handoff section new or changed since the last mark, `--full`, `--mark`),
  triage into `songs/_migration/LEDGER.md`, the user decides, build on a branch, announce merges to every ismail
  session, the song closes the item. SKILL.md tells song agents their handoffs are read and how to close an item.
- The intake names sections by their heading path (one subheading under two elements stays two sections), skips
  caches and backups, and lists a reorganized handoff's old text only with `--all` (a section whose words were
  already in the file is "moved", not new).

### Guitar rig and performers

- Rig effects (`ismail/rig.py`): `fuzz` (Fuzz Face bias shift), `univibe` (four-stage LDR phaser with lamp lag),
  `amp` (Marshall-style tone stack after Yeh and Smith 2006, push-pull power stage with sag), `cab` (min-phase
  cabinet with a mic blend), `rotary` (Leslie with ramping rotors), `tape` (head bump, wow, flutter, hiss), `wah`
  (resonant band-pass). `fx_help(type=...)` documents each. Live runs them as block-by-block twins (below).
- Performer voices in the studio: a voice module with `perform()` and no `voice()` renders a whole part at once
  (strings that ring on, legato, slides, whammy), with expression lanes from automation `inst.lane.<name>`. Live
  already played them from clip `expr` lanes; one mechanism now: a deck turns a song's `inst.lane.*` automation
  into its clip's `expr`, and `live_load` marks a song's performer tracks (decks had played them note by note).
- Library voices `guitar/electric` (electric guitar or bass: waveguide strings, pickup comb and resonance, lanes
  for bend, vibrato, mute, slide, level) and `drums/kit70` (a 1970 kit as modal resonator banks), both performers,
  fitted in a late-60s guitar record study (20 of 22 single lead notes passed a blind exam). Presets
  `strat70_lead`, `strat70_rhythm`, `strat70_rotary`, `pbass70`, `kit70`; each voice's INFO `rigs` holds the fx
  chain its presets were fitted with, and `voice_help` prints them one line each.
- Skill: `references/blind-tests.md` (the eye exam and the blind exam: fair clips, reading the answers, the
  song-level checks single notes miss), and the guitar study's lessons in sound design (register first, harmonic
  profile, stem bias, rig order, fast rig fit), composition (phrase placement, band feel, bends to chord tones),
  recreate (straighten a drifting record, round-trip every sensor) and listening (spectrogram pairs; long-window
  numbers are blind to fakeness).

### Live, from a one-hour set

- DJ kit, `ismail.live.djkit`: step strings to notes, cycles built bar by bar inside one clip, notes on another
  metric grid, effect moves (sweep, throw, gap, pump) addressed by effect type, and a `Set` that logs every call
  with the clip ids per section so a queued section can be cancelled.
- `live_fx`: `index` may be the effect's type (`'filter'`, `'delay:2'`); `moves=[...]` schedules a whole
  choreography in one call; `clear=True` cancels a track's scheduled moves (each param holds). A sweep can target
  a chain scheduled with `live_track(fx=..., at=...)`; the swap drops only the old chain's sweeps and keeps volume
  moves (it used to drop everything, a loaded song's volume curve included).
- One `at` vocabulary: `now` and `asap` mean the same in every live op.
- Problems between calls (a note dropped as too loud, a warm-up error, a stalled device) are added to the next
  reply of any live op. `live_status` shows each level's loudest over the last 10 s and a STALLED line when the
  device stops asking for audio.
- `live_start` names other engines still running on the machine (a registry in `~/.ismail/live`); `live_stop`
  waits for the engine to exit and kills it with its workers when a dead device would hang it.
- Skill (`references/live.md`): reading the audience, your latency, the pre-flight, dynamics with the kit, a set's
  folder layout, playing for a screen recording.
- Fix: the live engine crashed at startup since the rig effects were registered (its effect warm-up tried to
  build a live block for studio-only types); it now skips them, and they bake in the workers as before.

### Live and studio parity

- Fix: a note still sounding when a window starts (a drone, a pad, a held string) now plays in a studio render of
  a bar range and on a deck loaded with `bars`; both dropped it, and the deck said "silent in range". A note that
  rings on at least a beat into the window comes in on its first beat.
- A deck loaded straight on air is held off air until a whole bar of it is rendered, then goes on air on that bar
  line; it used to go on air with nothing rendered and lose its first bars in silence. `live_status` shows the
  hold, and the next reply says when it slipped and when it went on air.
- Renders go to the workers earliest-needed first, from an engine-side list, and a render no queued clip wants any
  more is dropped before a worker spends time on it. A clip queued for later renders its first pass from the
  moment it is queued, so loading ahead buys render time.
- Notes that never sounded because their render came back too late are counted (`live_status`: "never sounded")
  and said per track in the next reply; the late counter used to read 0 while bars played silent.
- Warm-ups render a short tail: each one had rendered 8 s through the track's baked rig on every worker, ahead of
  every real render, and a 23-track deck spent half a minute warming up.
- Fix: a deck's cue flips (`live_deck(cue=...)`, a transition's on-air) now land on the bar line the listener
  hears; they came about 90 ms early.
- The guitar rig runs live: fuzz, univibe, amp, cab, rotary, tape and wah have block-by-block twins
  (`ismail/live/rig_blocks.py`) running the studio kernels, held to the studio by `tests/test_live_parity.py`.
  They were baked into every rendered note: a guitar rendered at 0.8x realtime and a deck of Crossroads never got
  on air (219 notes lost); now it plays from its first bar with nothing late and the mixer at about 31%. Per-note
  baking also stacked one amp's hiss per note (+7 to +9 dB on the noise floor; now equal to the studio's), and a
  song with `tape` on a bus can load on a deck. Rig params move with `live_fx` (wah `pos`, rotary `speed`).
- Studio rig changes so a live twin can be exact (all inaudible): amp and tape hiss draw one noise stream per
  channel (a different noise, same level); tape wow is a causal running delay (the output sits 14 samples later);
  auto-wah follows its recent peak instead of the whole part's 98th percentile (within 0.1 dB).
- Performer voices play live in bar chunks: each bar renders with the second of the part before it as context
  (held notes from their real start), cut and crossfaded at the bar line; a looping clip takes context from its
  previous pass, a deck's section from the 8 beats before its window (a held note is no longer struck again on the
  window's first beat). Crossroads on a deck against the studio: envelope correlation 0.999-1.000 and bands
  within 0.5 dB on bass, rhythm, lead and drums (0.96-0.99 before; the lead lost 21 dB at 125 Hz at the window
  edge). kit70 renders at 5x realtime instead of 0.5x (no 8 s tail per hit).
- `electric` and `kit70` key their randomness per note on the song beat (`perform(..., beat0=)`), so any slice of
  a part, a studio bar range included, plays exactly as the whole part. One generator drawn note after note gave
  every slice a different performance (a kit's hi-hat changed timbre with the number of notes). Studio renders of
  songs using them get a new realization of the same humanization. `skills/.../sound-design.md` says how to write
  a performer this way.
- Deck notes keep 10 significant digits (6 rounded long sections' timing).
- A track whose amp or tape hisses keeps hissing through rests up to 8 s before it sleeps, as in the studio.
- A big `live_queue` no longer stalls the mixer: the batch's render estimates and event groups are planned before
  the engine lock is taken, the scheduler places at most 128 events per pass (earliest first), everything alive at
  engine start is frozen out of the garbage collector's full sweeps, and the GIL switch interval is 1 ms. An
  8000-note queue: the mixer's longest wait for the lock 256 ms -> under 25 ms, collector pauses 133 ms -> 20 ms,
  mixer peak about 1000% -> 130-180% of real time.
- The live mixer keeps every effect's state out of denormal floats (a fuzz left in silence decayed into them and
  took a guitar chain to 100% of real time), and a track whose amp only hisses goes dormant like a silent one.
- A standing parity check: op `live_parity(song, bars)` renders the bars in the studio and plays them on a silent
  engine of its own, then compares each track, bus and the mix (level, envelope, octave bands, residual), judged
  from the window's second bar. `tests/test_live_song_parity.py` holds every instrument type and library voice to
  it: voices that are not performers match the studio sample for sample, performers by ear.
- Synths and drums seed their random phase and noise on where a note sits in its bar, not where it sits in the
  render buffer: a studio render of a bar range now sounds as those bars do in the whole song, and live plays it
  sample for sample (it was a different take of the noise and phases each time). Studio renders of synth and drum
  parts get a new realization; nothing else changes.
- Code and mimic voices get each note whole even when a render window ends first: a voice whose noise draws or
  filters depend on the length it is given (the `sfx` voice) sounded different near a window's end and live.
- Fix: an engine rendering inline (`workers=0`) now finds a loaded song's sampler sounds; its samplers were silent.

## 0.2.0 (2026-09-30)

### Live

- **Live engine** (`ismail/live/`): a separate process per folder plays a queue of clips in real time while the
  agent edits it. Ops: `live_start`, `live_stop`, `live_status`, `live_track`, `live_bus`, `live_fx`,
  `live_queue`, `live_cancel`, `live_view`, `live_listen`, `live_record`.
- Clips loop until replaced; launches quantize to the beat, bar or phrase (`next_4`, `bar:N`, `after:<id>`,
  `after:#k` for a whole arc in one batch). The reply gives each clip's landing bar and the runway.
- Render ahead: worker processes render each note or phrase before the playhead (identical notes render once);
  a clip that cannot render in time lands later and the reply says so.
- Effects, send buses, sidechain, duck and vocoder from live tracks; `live_fx` ramps any automatable param.
- Safety chain on every output: trim, loudness rider, lookahead limiter, ceiling, set from the environment.
- `live_listen` analyses the last bars played, a deck, or a finished `live_record` take.
- Decks: `live_deck`, `live_load` (a whole ismail song on a cued deck), `live_transition` (blend, bass swap,
  filter, cut), a DJ strip per deck (LR4 isolator, filter knob, fader, transpose).
- Studio and live: studio code is the source of truth. Effects run live as block-by-block twins
  (`fx_blocks.py`, `dsp_blocks.py`) held to the studio versions by `tests/test_live_parity.py`; an effect with no
  twin is baked into each rendered note or phrase. Voices with `perform()` play whole phrases live, with `expr`
  lanes (bend, vibrato) on clips.
- mimic profiles and code voices play live.
- Install `.[live]` (sounddevice) to play to speakers; `device='none'` runs without audio out.
- A deck plays a song the way it renders: a bus that tracks play through keeps its reverb or delay as an insert
  (dry passes; live buses had been wet-only, which took the drums out of a song with a reverb on its drum bus),
  and the song's automation comes over (fx params and track and bus volume as ramps, instrument params rendered
  with the notes, the master fade), repeated every pass on a looping deck. A track with instrument automation
  renders its section as one event, sent to a worker one pass ahead.

### Measurement

- `analyze_grid`: the snare on 2 and 4 votes for bar 1; reports the record's tuning offset and swing.
- New ops: `ref_retune` (retuned analysis copies of an off-pitch reference), `analyze_swing`, `analyze_kit`
  (the pieces of a drum kit and each one's pattern, by NMF), `analyze_sections`, `levels_from_ref` (faders from
  the reference's stem balance).
- `analyze_sections` flags a build (2 dB or more under the climax on average, peaking within 3 dB of it), not a
  drop that goes on at the same level.

### Voices

- mimic: instruments measured from recordings (`mimic_measure`, the `mimic` instrument type), with leave-one-out
  checks; wandering vibrato, swells, measured room, attack maps, sympathetic strings.
- Voice library in family folders; measured string profiles ship built in.

### Skill

- Live reference (`references/live.md`): running a set as a DJ loop, small edits over whole sections, a runway
  before every question, metric modulation for style changes at a fixed tempo, performers and phrase voices.
- Recreation lessons: measure tuning, swing, kit pieces and fader balance; a record's drum kit as measured
  models; phrase length before tiling; one-part-at-a-time A/B.

## 0.1.0 (2026-09-26)

First public release: an agent-operated DAW with a CLI, an MCP server and a Python API. Notes, patches,
effects and automation in as text; levels, spectra, drum lanes, piano rolls, chords, structure and stored
reference comparisons out as text. Code voices, drum synths, samplers, stem separation, instrument and track
fitting, the optional music-video pipeline, and the agent skill.
