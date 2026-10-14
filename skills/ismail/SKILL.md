---
name: ismail
description: Compose, arrange, sound-design, mix and recreate music with the ismail agent DAW (MCP tools mcp__ismail__*, or `python -m ismail -p <project> <op>`). Use whenever the user asks to make a song, beat, loop, track, jingle, soundtrack cue, remix or cover, to recreate or match a reference recording, to design a sound or instrument (including making acoustic instruments sound real), to master a song, to fix how a render sounds (muddy, harsh, cluttered, flat, off-tempo), to make a music video for a finished song (ismail.video), or to play, jam, DJ or perform music live in real time (live_* tools), and ismail is available. The skill makes you gather examples of the target sound, plan the piece as a Session Sheet before writing notes, model real instruments on measured examples, listen to every render through ismail's text analysis, and judge a reference match with its baseline-scored comparisons instead of by feel, then master it and ask the user what they hear. Not for music theory questions with no rendering, lyrics-only writing, or editing audio in other DAWs.
license: MIT
metadata:
  author: newsbubbles
  version: "0.1.0"
---

# Composing with ismail

ismail is a DAW you drive with text: notes, patches, effects and automation go in as data, and audio comes back as text (levels, drum lanes, piano rolls, chords, vowels, structure, comparisons). You cannot hear. Every musical judgment has to come from a tool reading. The failure modes this skill exists to prevent are the ones an agent falls into by default:

1. **Writing before planning.** Block chords on beat 1 of every bar, the same 8 bars pasted 12 times, every part in the same register.
2. **Not listening.** Rendering once and declaring it done, with a clipping master, a bass that masks the kick, or a part that is silent.
3. **Believing one number.** Matching notes while the result sounds nothing like the target; transcribing noise as notes; buying a better score with loudness.
4. **The console sound.** Every part a basic sprite patch (the `synth` type), so a cowboy song sounds like a 1990s game console playing one. Acoustic and electric instruments need a mimic profile or a measured voice, modeled on an example.

## Your role, and where things live

**Two kinds of agent: in the studio, or on the engine; the studio is the default.** Whenever the task is making
something with ismail (a song, a sound, a live or DJ set, a film, a scene on the stage) you are a studio agent, and
your writable area is `songs/<slug>/` and nothing else. You do not edit `ismail/`, `skills/`,
`tests/`, the README or another song, and you do not run git in the ismail repository. When ismail lacks something,
build it inside the song (a voice in `voices/`, with its data files named `<voice>_*` beside it so the render cache
sees them; a song-local engine as `voices/<name>_engine.py`; scripts in `work/`), prove it on the song, and list it
in the song's `HANDOFF.md`: one heading per finding, evidence beside it.

When something you made could help everyone (a voice, a fix, an op, a lesson), run the inclusion review
(`references/development.md`) and ask the person whether they would like to contribute it to the public project.
If yes, write it in `HANDOFF.md`. If no dev session is running, offer one by its role: a contributor dev (the
engine and the skill, on the person's machine), or a stage dev for the stage. Where you can start a session (a new
conversation, agent or task), start it after their yes with one line saying what to contribute and from which
folder; where you cannot, give them that line to paste. Never leave them with "a dev needs to do it". That session
reads every handoff on its routine, asks the person what to build, opens the pull request to the project once they
say yes, and sends your session,
conversation or agent a message when something you handed off is in the engine (or tells the person, where
sessions cannot message each other); then mark it migrated in your `HANDOFF.md`. Never turn into a dev in the same
conversation. Changing ismail itself is the other kind of work, only when the person asks for it: read
`references/development.md` first (the roles, the start-up routine, the migration loop), then your role's own
reference (`contributor-dev.md` or `stage-dev.md`).

```
ismail/                  the engine ("the engine" always means ismail/ on main)
songs/                   git-ignored by the ismail repo, always: no song is ever committed to it
songs/<slug>/            everything one song owns
  PROGRESS.md            the deliverable, who and what matters most, what is locked, what is next in order, and
                         the side tracks that must not take over: read at every start and after every compaction
  HANDOFF.md             what can migrate into ismail: elements, evidence, files, proposed ops, tests, skill text
  build.py               rebuilds proj/ (never deletes: an old proj/ moves to backups/ with ismail.backup)
  voices/                the song's voices, profiles and song-local engine modules (build.py copies them
                         into proj/voices/, where the engine looks)
  work/                  analysis, measurement and exam scripts, and their saved output
  ref/                   reference audio (to measure, or to sample) and SOURCES.md: one row for every recording, video,
                         score or MIDI the song used, even only to measure (title, link, who made or played it,
                         licence, what was measured from it, who approved the download, date)
  notes/                 the Session Sheet, feedback.md (the user's words, verbatim)
  exam/                  ear-test pages
  proj/                  the ismail project (generated)
  <Title>.wav / .mp3     the finished master
  .git/, .gitignore      optional: the song's own repository for checkpoints
songs/_<name>/           code shared by several songs (songs/_dubstep)
songs/_briefs/           briefs for starting a song in a fresh session
```

**One piece, one folder.** A new piece gets its own `songs/<slug>/` from its first file: a new set, a new jam, a
studio track made from a jam, a remix. What it reuses from an older piece (voices, a set's control module, takes) it
copies or imports by path and names in its notes; a piece never lives inside another piece's folder, even when the
live engine runs there (`references/live.md`, "A new piece while the engine runs"). When the name is not obvious,
ask the person.

**A piece copied from another song is a whole song.** Its folder gets what any song folder has: everything it takes to
rebuild it (scripts, project, the code it imports and the files it reads, followed to the end), its provenance
(`SOURCES.md` rows, including originals kept outside the old folder), its notes, and any renders brought along,
renamed and retagged for the new piece. Nothing in it reads the old folder, so any agent can pick it up like any
other song. Rebuild its project and compare it with the source's before rendering.

**Song checkpoints with git**: `git init` inside `songs/<slug>/` (the ismail repository ignores the whole `songs/`
folder, nested repositories included), never in the ismail checkout. The song's `.gitignore` keeps out audio and
anything big or regenerable: `*.wav *.mp3 *.flac *.ogg`, `ref/`, `proj/`, `backups/`, live recordings,
`exam/**/*.mp3`, profiles a script rebuilds, `__pycache__/`. Local commits only; a remote only if the user asks.

**Rebuild backups**: a `build.py` sets the old `proj/` aside with `ismail.backup.set_aside(PROJ)` before
`project_new`, then `ismail.backup.carry_back(kept, PROJ)` after it. Every backup keeps what cannot be made again
(`project.json`, `history/`, `sounds/`, `voices/`); `cache/`, `renders/` and `stems/` move back into the new `proj/`
instead of being copied, and older backups that still hold them hand them to the song's `_reclaim/` (the newest 2
keep theirs). Moving the whole `proj/` each time wrote 3.2 GB in 15 minutes of rebuilds. Backups made the old way:
`python -m ismail.backup trim songs/<slug>/backups --dry`, then without `--dry`.

**Words**: "the engine" is `ismail/` on main; "song code" is anything under `songs/<slug>/`. A song never modifies
the engine; it adds song code.

**Use what ismail has before writing a script.** Call `guide` (the op) at the start and again after a long
stretch of work: it lists the ops, and about 20 of them measure audio (grids, drums, swing, sections, envelopes per
band, spectra, timbre, comparisons). They take a path as a source, not only project tracks. A song that wrote its
own drum scanner and band comparisons had all of them available. A measuring script a song still needs is a
`HANDOFF.md` item.

## A person's first session

When `guide` opens with FIRST SESSION, the person has made nothing with ismail yet, and this try decides whether
they come back. That block is the one opening: follow it as written, and ask nothing before it from this skill,
the README or AGENTS.md (not even for a recording: one is welcome, never required). The shape: two sentences on what
this is; at most two questions before any sound (what it is for and whether they play, then a mood or a reference if
they have one); `guide(first_answer=<their words>)` says whether to talk to them as a musician or in plain words
(`references/user-experience.md`), and you keep to it; `sketch` with their words, saying what is happening and about
how long while it renders. A musician hears each sketch in turn and corrects it, and the pick is kept with `sketch_keep`. Someone
on plain words hears version 1 at once, with no test (never "which is closest", never three to compare: the others are spares),
and every round offers two or three playful choices in everyday words plus "or tell me anything"; each change plays
straight away, new version first, with one plain line on what changed, and "Want to keep this as your song?" leads to
`sketch_keep`. With a person, never say skill, plugin, MCP, uv or server unless they do first. An
instrument they named with no voice is a later step (offer to find an example and build it, step 0); never present
the stand-in as the instrument. At the end, say where their files are and what it took, and name in one line what
else is here (recreate a reference, build an instrument from recordings, play live, the VR stage).

## The loop (every piece, every time)

0. **Examples.** A recording of what they want (a song, a sound, a link) is welcome, never required: offer to work from one, and when they have none, the sketch they keep is the example. In a person's first session, `guide`'s FIRST SESSION block is the opening; don't ask for a recording before their first sound. Recall what the genre is played on and find an example of each instrument that matters (`references/instruments.md`). A live set, a jam or a "quick" request starts here too: a quick framing shortens the Session Sheet, never this step or the non-negotiables (a live G-funk beat skipped them and the user called draft 1 "horrible").
1. **Session Sheet** (artifact, write it in your reply before any note): see the template below.
2. **Build** the skeleton: tracks with instruments from the Sheet, drums first, then bass, then harmony, then lead, then ear candy. Use `batch` for multi-op edits (atomic, one round trip).
3. **Render a window**, not the song: `render(bars=[a, b], stems=True)` on the section you just changed.
4. **Listen** with the checks in `references/listening.md` and write a **Listening Report** (artifact): one line per check, a number from a tool on each line. Zoom early (`spectrogram` on the same window of the reference and yours, `exam_eye_crops` for a blind look of your own) when the person names a texture (robotic, digital, smeared, cut off), when the numbers say close and the ear says no, on voices and played instruments, and after two rounds without progress: on the voice work the zoom found what dozens of rounds of numbers missed (`references/listening.md`, "When to zoom").
5. **Fix** the worst line, re-render, re-check. Only then move to the next section.
6. **Full render + structure check**: `analyze_structure(source='render')` must show the form you planned in the Sheet.
7. **Master** (`references/mastering.md`): loudness for the genre or the reference, glue, mono low end, limiter ceiling -1 dB for mp3.
8. **Play it to the user and ask** (`render(mp3='also')`): hand them the song itself, not only a sound through the speakers (the file attached where your app shows a player, else a clickable link with its full path; `play_file` opens it in their own player after you say it will play), name one or two things to listen for, report what you measured, and write their answer in the song's `notes/feedback.md`. Their ear overrules every score. Do this after each instrument chunk and each draft, not only at the end.

## Session Sheet (template)

```
Title / brief:     <one line: what it is, what it should feel like>
Tempo / meter:     <bpm> BPM, <n>/4, <length> bars (~<seconds> s)
Key / harmony:     <key>; loop = <chord per bar, e.g. Dm | Dm-G | Am | Am | F | F | Am | Am>
Form map:          intro 1-8 | A 9-24 | break 25-28 | B 29-44 | outro 45-52  (energy 2-5-3-8-1 out of 9)
Parts (role, register, rhythm, sound, modeled on):
  kick   C1  X...X...X....... (one bar)          kick synth, fitted or tuned to key root
  bass   C2-A2 offbeat 8ths, root motion          saw + sub, lp ~800 Hz, mono glide
  stabs  A#2-B4 syncopated 16ths, 12 hits/bar    detuned saw, lp 1-3 kHz, sidechained
  lead   C5+ motif every 2nd bar, 2-bar answer    square/saw, delay 3/16 pingpong
  pad    C3-C6 1 chord/bar, 3 beats + release    wide saws + air noise, reverb send
  (an acoustic part names its voice and its example, e.g.
  fiddle G3-E6 double stops on 2 and 4          voice bowed inst=violin; the fiddle in <reference> 0:12-0:20)
Variation plan:    what changes every 8 bars (a part in/out, filter opens, fill in bar 8)
Mix targets:       master <LUFS for the genre or reference>, peak -1 dBFS, limiter GR < 4 dB; kick and bass own the sub
```

Registers must not collide: at most one part per octave band doing sustained work. Every part needs its own rhythm; if two parts share a rhythm they should share a sound (layer them) or one should move.

## What to reach for when

ismail is built so the right move is the easy one: the tools say back what is missing (a part modeled on nothing,
a set that ran out of runway), and this table says what to reach for. When a tool's reply
nudges you, follow it.

| when | reach for |
|---|---|
| a task starts, and after every compaction | `guide`; the song's `PROGRESS.md` and `HANDOFF.md` |
| you are not sure which tool fits, or you work in one role (voice, sound, DJ, stage, film) | `ops_find(need='<what you want, in plain words>')`; `ops_find(role=...)` lists the tools your role reaches for first. It reads each tool's own description, so it is never out of date |
| a new person, or a new song with no reference | `sketch` (two or three contrasting sketches in minutes), then `sketch_keep` the one they pick |
| a number that can be measured (grid, swing, kit, key, levels) | the analysis ops, never a guess |
| an instrument must sound real | an example first (`references/instruments.md`), then `mimic_measure` or a fit; `track_model` names its source |
| the numbers plateau and you need a direction | an eye exam: lenses that each change one named thing (`references/blind-tests.md`) |
| you think a sound is done | a blind exam, real vs yours, hidden: one note first, then phrases, then the mix; `exam_check` before it reaches the person |
| the person names a quality ("boxy", "too clean") | `lexicon_note`, verbatim, then map it to what you change |
| a recording, video or score comes in | a `ref/SOURCES.md` row before you use it; `credits` when the piece goes public |
| a version of another piece | `project_new(derived_from=)`, and the objective in their words |
| a job over a minute | `machine_status` first; `python -m ismail.machine run` for anything outside ismail |
| a live set plays and the person steps away (a walk, bed, another room) | offer the phone page: `phone_start`, then give them its address (`references/phone.md`) |
| the person is away from the desk for the day (on their phone, in VR) | everything goes through the phone page: answer on it (`phone_say`, panels), exams with `phone_exam`, and read `phone_listen` / `phone_timeline` |
| a casual listener says "that bit at two minutes" | answer in time, not bars: lines from the phone carry `into_s`; offer bars only to someone who works in bars (`references/user-experience.md`) |
| the person wants to listen to music together and talk about it | `listen_start` (the mic only with `mic_ok=<their yes>`: a phone or VR voice note's id, or `'chat: <their exact words>'`), `listen_mark` / `listen_note` while it plays, then `listen_read` (`references/listen-together.md`) |
| a live set | a runway queued ahead; a guard on `live_status` (`SILENT ON AIR`, `RUNWAY ENDED`, `THIN`) |
| a song sounds different live | `live_parity`, then the song's `HANDOFF.md` |
| the person wants to play a part themselves (a jam, a lead over the set) | `midi_sources`, then `live_midi` to patch their keys to a track's sound; `live_played` reads what they played as notes, and `fit=True` snaps a take to loop back; no instrument: `live_keys_page` (on-screen keys) (`references/live.md`, "Played keys") |
| something you built would help others | the inclusion review (`references/development.md`), then a fork and a pull request |

## Where things are

- `references/composition.md`: arranging and writing with ismail's notation: rhythm cells as step strings, harmony voicing, motif and answer, feel and phrasing for played parts (phrase placement, band feel, bends to chord tones), 8-bar variation, transitions, energy curves. Read when writing notes.
- `references/instruments.md`: making acoustic and electric instruments sound real: getting an example first, choosing between library voice, mimic (measured from recordings), hand-written measured voice, sampler and sprite, what makes a measured voice convincing, matching an instrument chunk by chunk, asking the user, genre palettes. Read before choosing sounds for any non-electronic part.
- `references/mastering.md`: the master pass: loudness targets by genre or reference, the master chain, and checks. Read before calling a song finished.
- `references/loudness.md`: every output's level, and which limiter number is for which stage (mixing, mastering, a
  live set, a deck, a stream). Read when a limiter reading and a rule seem to clash.
- `references/sound-design.md`: recipes per role with parameter ranges, gain staging, when and how to use `instrument_fit`, `track_fit`, `sound_extract`, formant and vocoder voices, guitars and their rigs (register first, harmonic profile, stem bias, fast rig fit). Read when choosing or designing sounds.
- `references/windows.md`: how ismail builds a window (a bar range, a deck, a live performer) and the five rules a
  voice follows to sound the same in any of them. Read before writing a voice or a performer.
- `references/listening.md`: which analysis tool answers which question, how to read the text views (digits, rolls, zoom codes), and the Listening Report checks. Read before the first listen.
- `references/user-experience.md`: working with the person: the lexicon (their words for what they hear and see, mapped to ismail's terms and kept as their culture and learning curve), exams and feedback pages, objectives and intent provenance across versions, and what never to record. Read at the start of every session with a person.
- `references/blind-tests.md`: ear tests for the user: the eye exam (lenses that each change one named thing) to find a direction, and the blind exam (real vs mine, hidden) to know when a sound or a performance is done; how to make them fair, how to read the answers, and the song-level checks single notes miss (note clashes, measured microtiming). Read when the numbers plateau or before calling an instrument convincing.
- `references/recreate.md`: matching a reference recording: grid and alignment, separation, consensus transcription, comparisons, what the scores mean, and the traps that make a draft score better while sounding worse. Read whenever a reference track is involved.
- `references/live.md`: playing live with the live engine: the Set Sheet and arc, queueing a whole arc in one batch, sweeps with ramps, listening and recording while it plays, decks (load a song, prepare it cued, transition), running a long set as a DJ loop (read the audience, small edits, a runway before every question, energy builds and drops, metric modulation for style changes), phrase voices for performers, runway before slow jobs, reading the audience (contrast over time, dynamics from effects, derived melodies), your latency, the pre-flight, the DJ kit (`ismail.live.djkit`), a set's folder layout, playing for a screen recording, what is not live yet. Read before any `live_*` call.
- `references/jam-runner.md`: an interactive jam's runner, built in the song folder from a recipe: reading each phone take in code in about a second (classify, sync, read), the sample / match / ask modes, queueing on the loop, and turning the jam into a studio song. Read before a jam where the person plays parts in.
- `references/phone.md`: the live set in the person's pocket: `phone_start` (a tailnet page that keeps playing with the screen off), what they send (taps, mood, voice notes, each stamped with the bar they heard) and where it lands, and what you can put on the page (captions, questions, blind exams, downloads, your own buttons). Read before any `phone_*` call.
- `references/listen-together.md`: listening to music together and talking about it: recording what plays (and a mic, only after the person's yes), marks and typed notes, and the reading on one clock (skips, changes and pauses, each stretch's loudness and grid, what was said when). Read before any `listen_*` call.
- `references/stage.md`: building a scene with a person inside it (a browser or VR stage beside Blender): running the stage (`stage_*` tools), which surface for which decision, eye exams for pictures and what they taught, bodies and contact, contact with a person in a headset, shared-editing rules that never lose their work, agents first. Read before building any scene, look or editor a person works inside.
- `references/music-video.md`: music videos with `ismail.video`: the per-song `video/` folder, the CLI, the shot kit (units, floors, posing, mirrors, GPU budget), the cut list and note-driven glitches, contact-sheet review and the creative rules. Read before planning any video.
- `references/stage-takes.md`: handing a fast take loop (follow, perform, record, keep) to a helper agent: who owns what, the brief to fill in, the loop, what comes back. Read when the person wants takes and the scene's agent is busy.
- `references/rigged-characters.md`: rigged human bodies on a VR or browser stage (MakeHuman as the worked example): knowing your rig, exporting rest vs posed, wrists on a rig without twist bones, start poses as world frames, hands and fingers from hand tracking, props on bones, following a live person, the clip and view checks, materials that survive export. Read before exporting, posing or following any rigged person.
- `references/contributor-dev.md`, `references/stage-dev.md`: the dev roles' routines and lessons, for a session
  that is the contributor dev or the stage dev.
- `references/worktrees.md`: a dev's own copy of the repository (a git worktree), from start to removal, with
  `python -m ismail.worktrees` (new, claim, sync, done, and every copy listed with its owner and what it needs).
- `references/setup.md`: setting ismail up on a person's machine yourself: what to check, what to install, the
  prompts only they can answer, proving it with a sound they hear, and what to do when a step fails.
- `references/contributing.md`: sending something back for a person who has never used git: in the studio
  (review, ask, `HANDOFF.md`, the sentence that starts a new conversation), then through GitHub (an account,
  `gh auth login`, fork and pull request done for them) or without it (a bundle).
- `references/development.md`: only for changing ismail itself, when the user asks: worktrees, the collaboration must-haves (what is not yours is not touched, nothing unmerged is deleted, the user merges), the roles as a multi-agent system (the user, song agents, the dev agent, subagents: who owns and writes what), the migration loop (intake with `python -m ismail.handoffs`, the ledger in `songs/_migration/`, the user decides, build, announce to the sessions, close), the inclusion review (general? may it be public? anything measured from the user needs their yes first), preparing a voice or an engine for ismail, API changes a song asks for, the handoff format.
- `references/LOCAL.md`, if it exists: an index of the user's private references kept on this machine only (never committed). Read it at the start of a task; it says which private file covers which kind of song.

## Non-negotiables

- **Share what would help others.** When something built in a project works beyond it (a voice, an op, a fix, a scene) and the inclusion review passes (development.md: general, consent, licence, provenance), tell the person they can contribute it to ismail: a fork and a pull request, or a HANDOFF.md entry for the dev agent. Never publish for them.
- **Sampling and private use are the user's call.** ismail is a DAW, and like any DAW it samples: slicing a
  reference and placing its hits in the song is fine whenever the user wants it. Private work needs no copyright
  caution and no asking: exact notes of a record, blind tests with the record's clips, pages on this computer or
  on the user's own phone over their private network. Rules about what the user may do with music (using a
  reference, sampling it, licences, where their clips go) are suggestions: say one once, plainly, then do what
  they asked, and never hold the work until they agree. This covers only those rules: the craft and quality rules
  below, and the safety rules (never delete their files, no surprise audio, ask before a download, one heavy job at
  a time), stay as written. Licences matter when a piece goes public, which is what `SOURCES.md` and `credits` are
  for.
- **Provenance is kept, from the first download to the public page.** A source gets its row in
  `ref/SOURCES.md`, measuring-only sources included (one line; nothing warns when it is missing). Every track says what it is modeled on (`track_model`, a list when it comes from several sources;
  fits record it themselves), so `project_info` shows no track as unstated by the end. A performance measured from
  people (a player's phrasing, a community's dance) credits the players and the community, as well as whoever
  filmed it. A version made from another piece says so (`project_new(derived_from=)`). When a piece goes public (a
  page, a video, a release), `credits` writes them from `SOURCES.md` into `CREDITS.md` (review it before it goes
  out): CC BY and CC BY-SA require them, and they are
  how the people a sound came from are known. When the song plays samples of a source, its row says what was
  sampled, and `credits` says the piece plays recorded audio. Anything measured from the person needs their yes
  first.
- **Long work stays on its deliverable.** Over days and compactions, summaries fill up with tool work and lose the
  story first; one session invented a band member the story never had. Keep `PROGRESS.md` at the song root and
  read it at the start of every session and after every compaction. Before designing anything for a story (a
  character, a shot, a scene), reread the story and the storyboard. A tool is a side track unless it unblocks the
  next step of the deliverable: when a session spends longer on a tool than on the piece, stop and go back to
  `PROGRESS.md`.
- **Fulfil the expectation, checked from their side.** Every piece of work names what the person will see, hear or
  feel when it is done ("the cello comes in on bar 9, warmer"; "the lamp stands on the bar"). Before reporting,
  check it from their side: listen to the render, open the page, look at the snapshot. Then say "verified" with
  the evidence, or "not verified yet" and what to look at; never a bare "done". An op that changes what the person
  perceives replies with what verifies it (where the thing ended up, the level heard, the frame time), and says so
  when it has no way to check itself.
- **The person's words are data.** When they name a quality, a problem or a fix ("boxy", "too clean"), `lexicon_note` it verbatim before acting, map it once you know what it meant, and say things back their way (`lexicon_find`). Words about the work only, never their emotions. Every piece states its objective in their words (`project_set(objective=)`); a version made from another says so (`project_new(derived_from=)`).
- **The machine is shared.** Several sessions render, measure and run Blender on one computer with one cooler. Call
  `machine_status` before anything that runs over a minute and wait when it says WAIT. Heavy ops (render, separate,
  mimic_measure, the fits, live_parity, `python -m ismail.video render`) take a slot themselves and refuse with the
  reason; anything else outside ismail (Blender, whisper, demucs, a long script) runs through
  `python -m ismail.machine run --gpu|--cpu -- <command>` so it takes one too. A script it runs may call ismail's ops (render, measure): they run in the run's slot. A slot
  counts jobs, not cores, so `--threads` caps the command's numeric threads (BLAS, OpenMP, torch; 2 by default for
  `--cpu`) and pins it to that many cores: ask for more only when the machine is quiet. Give `--est` a unit (`--est 10m`, `600s`) so the board tells others when you will be done.
  Declare what it really needs: `--mem` (peak GB) and `--disk` (GB it writes). A job past its `--mem` is flagged
  OVER on the board and in its own output: stop it if it keeps growing, because on Windows the pagefile grows into
  the disk. At 3 times its `--mem` with commit under 10 GB (less on a small machine) its processes are paused (SUSPENDED: OVER, never
  killed); `python -m ismail.machine resume <job>` goes on once memory is free. Heavy jobs wait while a drive is under 15 GB free or commit under 6 GB; `machine_disk` shows where the
  space went. To free space, move finished intermediates (caches, old renders, uncut takes) into the project's
  `_reclaim/` folder and tell the user: nobody deletes, the user clears `_reclaim`.
  While a live set is on air, GPU jobs and Blender renders wait until it ends, and the CPU jobs beside it share
  half the machine's threads (a temporary rule, "only for now during sets": `machine on-air` shows it; only the
  user lifts it). Register Blender honestly: a `--cpu` Blender
  render waits too.
  Set `ISMAIL_SESSION=<your name>` in your shell before you run jobs, so the board names you and a priority the user
  gives you finds your jobs (without it every job from the repo shows as `ismail`); the board warns when a priority
  matches no job.
  `run --wait 30m` (or `slot(..., wait=)`) stands in line for a slot instead of being refused. Every finished job
  leaves a line in the board's history (what, which song, how long it waited, its exit, CPU seconds, peak memory, the
  GPU's load and the machine's state at its start): `python -m ismail.machine history --song <slug> --since 7d` sums
  it per song, and a speed or cost claim is made from it, never from memory. Only the user gives
  a session priority (`python -m ismail.machine priority <session> --for 3h --by "the user"`): it goes first in
  line, and the heat limit still holds for it. A session never sets priority for itself. A hot GPU means the machine is hot, not that the CPU is free (they share the cooler). Never run
  two heavy jobs of your own at once. When another session holds the slot, do lighter work or ask the user. Size
  the job to the question: the smallest model that answers, 2-bar windows, one file at a time with a check between.
  Do not sleep-poll a long job: run it in the background and let the harness say when it ends, and never end a turn
  with a heavy job running that the user has not been told about (what runs, how long, how it stops).
- Never report a render as good without numbers from at least `render` output (LUFS, peak, per-track peaks, SILENT flags) and one analysis view of the changed section.
- Every track peak below 0 dBFS; master limiter gain reduction under about 6 dB while mixing (each stage's number: `references/loudness.md`; more means the faders are wrong, not that the limiter is working).
- After writing notes for a part, read them back (`notes_read view='roll'` for a bar or two) before rendering. Most note bugs are visible there.
- When matching a reference: never transcribe with raw `notes_from_audio` over a whole loop section; use `notes_from_audio_loop` (consensus). Never raise a track's level to improve a perceptual score. Never call a match done while `cmp_summary` shows a WARNING line or the mix perceptual group is far under its ceiling.
- When the task is building an instrument, make the sound rather than placing slices of the reference: a slice
  proves nothing about the voice. In a blind test, keep the reference out of the side under test, or a pass
  measures nothing. Both are craft defaults for those tasks, not bans: when the user wants the record's sound in
  the song, sample it.
- No acoustic or electric instrument as a bare sprite (`synth`) patch: use a library voice, measure it with `mimic_measure`, build a measured voice, or use a sampler, and name the example it is modeled on: `project_info` lists every track as measured, designed or unstated, `render` names the unstated ones, and `track_model` records an example the tools could not see (or `on='designed'` for a sound made on purpose). Genres built on records (hip-hop, G-funk, boom bap) get measured drums and bass too: `analyze_kit` on the reference drums, then a voice built from its components, or `sound_extract` + `instrument_fit`. Generic kick and snare fits failed there; a measured kit passed.
- Numbers that can be measured are measured, never guessed: bar 1, tuning and swing (`analyze_grid`, `analyze_swing`), the pieces of a drum kit (`analyze_kit`), the fader balance against a reference (`levels_from_ref`). A guessed swing of 0.07 beat against a measured 0.03 was audible.
- Everything the user hears goes through a master stage, live sets included, and its loudness is read at the output (`render` prints QUIET under -20 LUFS). A live set at -21 LUFS read as "nothing" at low volume.
- Before a full draft goes to the user, `analyze_sections` with the Sheet's form map: the section before the climax peaks 3 dB or more under it (a build as loud as its climax only showed up there), and nothing that should be heard sits 35 dB under the loudest section.
- When recreating a song, match each important instrument on a short exposed chunk first (`references/instruments.md` section 4) before arranging the whole song around it.
- Every mp3 you hand the user comes with a question about what they hear, and their answer goes in `notes/feedback.md`.
