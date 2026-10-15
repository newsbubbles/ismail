# ismail reference

Install, setup per client, concepts, the voice library, and how each part works. The front page is the
[README](../README.md).

## Install

Python 3.10 or newer. ismail installs from GitHub:

```bash
pip install "ismail @ git+https://github.com/newsbubbles/ismail"                # engine, analysis, CLI, MCP server
pip install "ismail[perceptual] @ git+https://github.com/newsbubbles/ismail"    # optional: CLAP perceptual metric (torch + transformers, model about 600 MB)
pip install "ismail[separate] @ git+https://github.com/newsbubbles/ismail"      # optional: demucs stem separation for reference tracks
pip install "ismail[live] @ git+https://github.com/newsbubbles/ismail"          # optional: play live to your speakers (sounddevice)
pip install "ismail[voice] @ git+https://github.com/newsbubbles/ismail"         # optional: autotune and formant shifts on a voice (pyworld)
pip install "ismail[video] @ git+https://github.com/newsbubbles/ismail"         # optional: the music-video pipeline (opencv, pycollada, pillow)
```

Or run the MCP server without installing anything, with [uv](https://docs.astral.sh/uv/):
`uvx --python 3.12 --from git+https://github.com/newsbubbles/ismail ismail mcp`. The `--python 3.12` keeps uv on
a Python the compiled libraries have wheels for; the first start builds them and can take minutes.

To work on ismail itself (or to have the skill and examples on disk), clone it and install in place:

```bash
git clone https://github.com/newsbubbles/ismail
cd ismail
pip install -e ".[dev]"
```

If `demucs` fights your torch install, use `pip install --no-deps demucs` and then `pip install dora-search einops julius lameenc openunmix`.

MP3 previews need `ffmpeg` on your PATH (or set `ISMAIL_FFMPEG` to the binary).

Runs on Windows, macOS and Linux; CI tests all three on every push. The one OS-specific op is `sound_speak` (text to speech for vocal samples), which uses the engine the OS already has:

| OS | Engine | Voices |
|---|---|---|
| Windows | SAPI via PowerShell | `David`, `Zira`, any installed |
| macOS | `say` | `Samantha`, `Alex`, anything in `say -v ?` |
| Linux | `espeak-ng` or `espeak` | `en-us`, `en+f3` ... (`sudo apt install espeak-ng`) |

## Use it with Claude Code

**As a plugin (tools and skill in one step).** Needs [uv](https://docs.astral.sh/uv/). In Claude Code, these two
lines add ismail's marketplace and install the plugin from it (send them one at a time):

```
/plugin marketplace add newsbubbles/ismail
/plugin install ismail@ismail
```

On Claude Code 2.1.275 or newer one line does both: `/plugin install ismail --marketplace newsbubbles/ismail`. From
a shell (no pasting): `claude plugin marketplace add newsbubbles/ismail`, then `claude plugin install ismail@ismail`.
The plugin runs the server with uvx (from this repo) and loads the composing skill. The first time each ismail tool
runs, Claude asks to allow it: expect about 10 to 20 Allow boxes over setup and a first session
([setup.md](../skills/ismail/references/setup.md) says how to count them).

**By hand:**

1. **Tools.** Open Claude Code in a clone of this repo and the bundled `.mcp.json` registers the server; the tools show up as `mcp__ismail__*`. To use ismail from any folder instead:

   ```bash
   claude mcp add -s user ismail -- uvx --python 3.12 --from git+https://github.com/newsbubbles/ismail ismail mcp
   # or, after pip install: claude mcp add -s user ismail -- ismail mcp
   ```

2. **Skill (recommended).** `skills/ismail` teaches the agent how to compose with ismail: plan a Session Sheet before writing notes, write a Listening Report after every render, and judge reference matches with the comparison tools instead of by feel. Link it into your skills folder:

   ```bash
   # macOS / Linux
   ln -s "$(pwd)/skills/ismail" ~/.claude/skills/ismail
   ```
   ```powershell
   # Windows
   New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\ismail" -Target "$PWD\skills\ismail"
   ```

3. **Ask for music.** For example: "make a 16 bar deep house loop in F minor in songs/demo and render an mp3". The agent calls `guide` once for the conventions (it is a tool and a CLI op), then works through the tools.

## Use it with Cursor

1. **Tools.** Opening this folder in Cursor picks up `.cursor/mcp.json`, which runs the server from this checkout (`python -m ismail.mcp_server`). To use ismail in other projects, add this entry to `~/.cursor/mcp.json`:

   ```json
   {"mcpServers": {"ismail": {"command": "uvx", "args": ["--python", "3.12", "--from", "git+https://github.com/newsbubbles/ismail", "ismail", "mcp"]}}}
   ```

2. **Skill.** `.cursor/rules/ismail.mdc` is an agent-requested rule that points Cursor's agent at `skills/ismail/SKILL.md`. Copy that rule (and the `skills/ismail` folder) into another project to use it there.

Any other MCP client works the same way: run `ismail mcp` (or `uvx --python 3.12 --from git+https://github.com/newsbubbles/ismail ismail mcp`) over stdio.

## Quick start (CLI)

Every tool is also a CLI op. Arguments are `key=value` pairs (values parsed as JSON when they can be) or one JSON object.

```bash
python -m ismail guide                                # read first: workflow and conventions
python -m ismail ops                                  # list operations
python -m ismail help notes_write                     # one op's arguments and docs
python -m ismail -p songs/demo project_new bpm=124 length_bars=8
python -m ismail -p songs/demo track_add name=bass instrument='"preset:acid_bass"'
python -m ismail -p songs/demo notes_write '{"track": "bass", "bar": 1, "notes": "0 E2 0.5 110; 0.5 E3 0.25", "repeat": 8}'
python -m ismail -p songs/demo render stems=true out=v1 mp3=also
python -m ismail -p songs/demo analyze_melody source=track:bass bars=[1,2]
```

`render` writes `renders/latest.wav` (every analysis tool reads it), plus `renders/<out>.wav` when you name the render. `mp3='also'` adds `renders/<out>.mp3` for listening; `mp3='only'` writes the named render as mp3 only.

Keep your projects under `songs/` (git-ignored) or anywhere else; a project is just a folder.

## What's built today

Find any tool by what you need or by role with `ops_find`.

- **The DAW.** Songs as code (notes, instrument patches, effect chains, automation in a project file), rendered and
  read back as text. Named effect stacks (`fx_stack_*`, `stack:<name>`) and an `autotune` effect. A second part
  measured out of a mix (`stem_parts`), melodies read with a confidence. Renders wait in line on a shared machine
  (`render(wait=)`, `machine_status`) and flag tracks running hot. Every mp3 is tagged with its provenance
  (`python -m ismail.tags`), and `track_model` / `credits` say where each sound came from.
- **Live.** Decks, transitions and requests while the music plays (`live_*`), with lazy rendering that warns before a
  track starves. Recording (`live_record`), streams (`live_stream`), output devices per set or per track
  (`live_device`, `live_track(output=)`), controls a page or a scene can turn (`live_map`, `live_control`), and a
  DJ kit for whole cycles of moves (`ismail.live.djkit`).
- **The VR stage** (`ismail/stage`, the `stage_*` tools): a three.js WebXR page for a desktop browser or a headset,
  and a small server that carries the live link between the page and the agent. Scenes live in the song's folder.
  The agent drives the page with ops and reads what the person did and said with `stage_events`. Objects carry
  their own behaviour; dance takes recorded in the headset play on a character, synced, looped or warped; lip sync
  (`stage_face`); frame-locked video of a scene (`stage_capture`). The guides: [stage.md](../skills/ismail/references/stage.md),
  [stage-takes.md](../skills/ismail/references/stage-takes.md), [rigged-characters.md](../skills/ismail/references/rigged-characters.md).
- **The phone page** (`ismail/phone`, the `phone_*` tools): live listening with the screen off, voice notes sent back
  with the bar being heard, and panels the agent puts on the page (buttons, inputs, pictures, video), queued rather
  than popping up. Hum a part (`phone_hum`), pick where the sound goes, pause to stop the set. An agent keeps a
  standing watch on its inbox with `phone_watch`.
- **Exams**: blind exams on the phone (`phone_exam`, with floor pairs: the real clip against a 64 or 128 kb/s MP3
  copy of itself, or against itself, so a device limit can be told from a real difference); eye exams as picture
  rounds (`exam_picture_round`, `exam_eye_crops`); `exam_check` before any exam is shown; and recording warnings for
  soft-limited peaks and band-limited (Bluetooth headset) microphones.
- **Feedback pages** (`feedback_page`, `feedback_read`, `feedback_list`, `feedback_stop`): questions the person
  answers by typing or by voice at the desk, served on this computer, opened in their default browser (an app's
  browser pane has no microphone), with every typed answer and voice clip saved in the song and transcribed, and
  all of it back when they reopen the page.
- **The first session** for someone new: `guide(new_person=True)`, `sketch` versions 1, 2 and 3 on the showcase
  voices (`sketch(base=)`, `sketch_keep` for the next round), and feeling words ("happier", "calmer", "darker") that
  change a sketch by named moves.
- **Showcase voices**, each measured from recordings or fitted to a record: piano, violin, cello, contrabass,
  clarinet, bassoon, horn, a 1970 kit, a fitted electric guitar and bass, and sampled Rhodes, drum and guitar sets
  (see [Voices](#voices-instruments-as-code) and [Sample sources](#sample-sources)). Clarinet, bassoon and horn
  were measured from VSCO 2 CE (CC0) and checked blind against the real recordings on 2026-10-07 (clarinet not told
  apart; bassoon and horn told apart but rated good enough; one listener, one pair each). Flute, oboe, trumpet,
  trombone and tuba were measured too and heard as synthetic, so they stay out until they pass.
- **Developing it with agents**: roles for studio agents, contributor devs, area devs and helpers
  ([development.md](../skills/ismail/references/development.md),
  [contributor-dev.md](../skills/ismail/references/contributor-dev.md)), one worktree per change
  (`python -m ismail.worktrees`, [worktrees.md](../skills/ismail/references/worktrees.md)), and an intake of what
  songs need from the engine (`python -m ismail.handoffs`).

## The agent team

ismail is built by a team of AI agent sessions, each with one role, and one person deciding:

- Director: keeps the roster, the board and the decision queue, and carries decisions to the agent they concern.
- Maintainer: maintains the repo (core, the op table, tests, CI, the skill) and reviews and merges pull requests.
- VR Dev: owns `ismail/stage`: the page runtime, the server, the stage ops, headset tests.
- Film: makes a film on the stage and is the stage's main user.
- Live DJ: plays live sets and tests the `live_*` ops under real-time use.
- Voice: voice modeling research, checked by blind ear exams.
- Multiplayer: research on shared worlds for the VR stage.
- Research: experiments and measurement.
- Business: go-to-market work.
- Marketing: launch videos, posts, the site's story and the first-session product shape.
- Steward: keeps the shared machine healthy (GPU, CPU, memory, disk) and the heavy-job queue moving.
- Historian: records whose ideas, senses and labor built ismail, and the provenance of what goes public.

## Sample sources

Measured or sampled voices come from these sets. The audio itself stays out of the repo (mimic profiles hold measurements only; sampled sets are fetched on the person's word with `samples_fetch`).

| set | used for | licence |
|---|---|---|
| [VSCO 2 Community Edition](https://github.com/sgossner/VSCO-2-CE) (Versilian Studios) | the `violin`, `contrabass`, `clarinet`, `bassoon` and `horn` mimic profiles | CC0 1.0 |
| "real cello notes" by flcellogrl ([Freesound pack 12408](https://freesound.org/people/flcellogrl/packs/12408/)) | the `cello` mimic profile | CC BY 4.0 |
| [jRhodes3d](https://github.com/sfzinstruments/jlearman.jRhodes3d) by Jeff Learman | the `rhodes` voice | samples CC BY-NC 4.0; the author grants CC0 for music made with them |
| [Big Rusty Drums](https://github.com/sfzinstruments/karoryfer.big-rusty-drums) by Karoryfer Samples | the `rusty` and `toms` voices | CC0 1.0 |
| [Emilyguitar](https://github.com/sfzinstruments/karoryfer.emilyguitar) by D. Smolken, Karoryfer Samples | the `emily` voice | CC0 1.0 |

## Concepts

- **Project**: a folder with `project.json` (tempo, grid offset, tracks, buses, master, sound bank, reference) plus `sounds/`, `renders/`, `cache/`, `history/` (undo snapshots) and `comparisons/`.
- **Time**: bars are 1-indexed; note times are beats relative to the bar you write at. `offset_sec` is the time of bar 1, so a project can sit exactly on a reference recording's grid.
- **Notes**: `'<beat> <pitch> <dur> [vel] [@offset]'`, one per line or `;`-separated; `@-40ms` nudges a sound off its beat so a late attack lands on it (`track_set(offset_ms=)` for a whole part). Drum and step patterns: `pattern_write` with strings like `X...x...X...x...` (X 127, x 100, o 70, - 45, `_` ties).
- **Instruments**: two synth engines. **sprite** (`"type": "synth"` or `"sprite"`: saw, square, pulse, triangle, sine, additive, wavetable and noise oscillators, unison, FM, drive, SVF and ladder filters, envelopes, LFOs, mono glide) is right for synth sounds. **mimic** (`"type": "mimic"`) plays instruments measured from recordings (see below). Plus `sampler`, drum synths (`kick`, `snare`, `hat`, `clap`, `tom`, `noise_hit`), `kit` (pitch to instrument map) and `code` (a Python voice function for anything else). `presets_list` has starting points.
- **Effects**: gain, eq, filter, distortion, bitcrush, compressor (with sidechain), duck, gate, delay, reverb, hall, chorus, flanger, phaser, tremolo/autopan, width, limiter, vocoder, formant, autotune, a guitar rig: fuzz, univibe, amp (tone stack, power stage with sag), cab, rotary speaker, tape, wah, and spatial (a sound moved in 3D for stereo speakers or headphones, with distance, Doppler, occlusion and its own room; its path can be coded as a trajectory). Tracks, buses and the master fader can be automated. A chain can be saved and reused by name (`fx_stack_*`, `stack:<name>`).
- **Voices**: engineered instruments kept as Python modules, so a project stores a name instead of code (see below).
- **Sound bank**: sounds made from any instrument and effect chain (`sound_make`), speech (`sound_speak`), imported files, and averaged events cut from a recording (`sound_extract`). Bank sounds work as sampler sources, wavetables, vocoder modulators and audio clips.
- **Undo and batch**: every edit snapshots the project (`undo`); `batch` applies a list of ops atomically.

## Voices: instruments as code

Some instruments are easier to write than to patch: a measured grand piano, a dubstep bass whose note velocity picks the articulation, a set of sound effects. These live as voice modules, Python files that define `voice(freq, t, vel, gate, sr)` and return a mono `(n,)` or stereo `(2, n)` array. `freq` is in Hz, `t` is an array of seconds from the note start that covers the held time plus the instrument's `tail`, `vel` is 0 to 1, `gate` is how long the note is held in seconds, and `sr` is the sample rate.

The library is grouped in family folders under `ismail/voices/`; names stay flat, so a track says `"voice": "grand_piano"` whatever folder it lives in, and `voices_list` shows the family.

| family | voice | what it is |
|---|---|---|
| keys | `grand_piano` | grand piano calibrated from measured notes (partials, decay times, inharmonicity, stereo image, hammer knock, dampers); `fn: voice_sym` is an undamped sympathetic string |
| keys | `additive_piano` | a lighter additive piano with no data file |
| keys | `rhodes` | a 1977 Rhodes Mark I, sampled (jRhodes3d); fetch the samples with `samples_fetch` |
| strings | `violin`, `cello`, `contrabass` | mimic profiles measured from real recordings (use `{"type": "mimic", "profile": "violin"}`); open strings, measured room and vibrato included; `params.players` makes a section |
| winds | `clarinet` | mimic profile measured from VSCO 2 CE (CC0); not told apart from the real note in a blind pair (one listener, 2026-10-07) |
| winds | `bassoon` | mimic profile measured from VSCO 2 CE (CC0); told apart in a blind pair but rated good enough (one listener, 2026-10-07) |
| brass | `horn` | mimic profile measured from VSCO 2 CE (CC0); told apart in a blind pair but rated good enough (one listener, 2026-10-07) |
| bass | `growl` | dubstep bass engine: velocity 1x yoi, 2x wub, 3x screech, 4x metal, 5x dive, 6x zap, 7x grind, 8x chop, 9x talk, 11x robot, 12x howl; the LFO rates follow the song tempo |
| fx | `sfx` | one-shots by velocity: gunshot, reload, shell casing, bone crunch, punch, rip, gong |
| fx | `crackle` | designed vinyl surface noise: clicks and hiss |
| guitar | `electric` | performer: electric guitar or bass as waveguide strings (pick, pickup comb, pickup resonance) playing a whole part, with legato, slides, bends, whammy, vibrato and mutes as lanes. Presets `strat70_lead`, `strat70_rhythm`, `strat70_rotary`, `strat70_clean` (the clean guitar a sketch reaches for) and `pbass70` (a bass fitted to a record's bass stem); it is the DI signal, so `voice_help` lists the rig each preset was fitted with |
| drums | `kit70` | performer: a 1970 acoustic kit as modal resonator banks that keep ringing across the part (a ride builds wash); preset `kit70` and its fitted EQ |
| drums | `rusty` | performer: Big Rusty Drums (Karoryfer, CC0), a 1980s kit sampled with velocity layers, round robins and mics; fetch with `samples_fetch` |
| drums | `toms` | performer: the Big Rusty toms (Karoryfer, CC0) retuned to the note, so the note is the drum's pitch (F#1-A3 heard as real); lanes `pos` (0 center .. 1 rim) and `stroke` (stick, mallet, brush, rimshot, rim click, shell, dead) per hit; velocity picks the recorded layer. Not told apart from the real drum in a blind exam of 5 pairs (one listener, 2026-10-14). About 2 s of rendering per hit. Finds its samples in `$TOM_SAMPLES`, the project's `ref/`, or `samples_fetch('big_rusty')` |
| guitar | `emily` | performer: Emilyguitar (Karoryfer, CC0), a clean electric guitar sampled DI; fetch with `samples_fetch` |

Presets are not voices: `sub_bass`, the one plain synth a sketch uses, is a sine patch in `presets_list`. Use a voice with `instrument={"type": "code", "voice": "grand_piano", "tail": 4.0}` or `"preset:grand_piano"`. `voices_list` shows what is available and `voice_help(name)` explains a voice's velocity mapping, functions and parameters.

Voices are looked up in this order:

1. `<project>/voices/<name>.py`: the song's own. Same name as a built-in overrides it; a song voice can also extend one (`from ismail.voices.growl import *`, then add words or articulations).
2. Each folder in `$ISMAIL_VOICES` (a path list): your personal library, outside any repo.
3. `ismail/voices/`: the built-ins.

Each of these may have family subfolders (`strings/`, `keys/`, `drums/` ...), searched too; mimic profiles (`<name>.mimic.json`) are found the same way.

A voice function may take extra keyword arguments: `bpm` is passed automatically, and the track's `"params"` dict is passed as keywords (`{"type": "code", "voice": "mine", "params": {"brightness": 0.3}}`). A module-level `INFO` dict documents it for `voice_help`; every key is optional: `summary` (one line for `voices_list`), `range`, `velocity` (what velocity does), `functions` (name to description), `params` (name to description), `lanes` (a performer's expression lanes), `rigs` (fx chains it was fitted with, each with its `preset`) and `tail` (recommended tail).

A **performer** voice defines `perform(notes, total_n, sr, bpm, lanes, **params)` instead of `voice()`: it gets the whole part at once, so strings ring on under the next note, legato notes slide or hammer on, and a lane bends everything that sounds. Lanes come from automation `inst.lane.<name>` in the studio and from clip `expr` live (a deck converts one to the other). Data files sit next to the module (`grand_piano.json`) and are found through `__file__`. Editing a voice file invalidates the render cache for the tracks that use it.

To add a voice to the library, move it from a song's `voices/` folder into the right family folder under `ismail/voices/` (a new family needs an empty `__init__.py` and a line in `pyproject.toml`), give it an `INFO` dict, and add a line to the test that renders every built-in (`tests/test_library_performers.py` for performers).

## mimic: instruments measured from recordings

A synth patch pretending to be a violin sounds like a 1990s game console playing a violin. mimic starts from recordings instead. Give it a few isolated notes of an instrument and it measures, per note:

- every harmonic's level and envelope (attack, sustain or two-stage decay, release), inharmonicity, and the beating between unison strings;
- a body curve fixed in frequency (the resonances that color each note differently), separated from each note's source slope;
- the noise between the harmonics and a short map of the attack (bow scrape, hammer knock), both calibrated by rebuilding the note and matching the recording; attack timing from short windows, so a player easing into the string eases in;
- vibrato cycle by cycle: rate, depth, how much each wanders over about a second, and how it builds up; the slow swells of a bowed or blown note;
- the room the recordings were made in, from how every harmonic dies away after the bow stops.

Then it plays any pitch: notes between measured ones blend their two neighbours, every harmonic reads the body at its current frequency (so vibrato moves the color the way a real instrument does), unison strings start in phase and beat, and each note varies a little. Optional: open strings ringing in sympathy, the body ringing, noise skirts around the harmonics.

```bash
python -m ismail -p song mimic_measure name=violin folder=samples/violin 'defaults={"strings": ["G3", "D4", "A4", "E5"]}'
python -m ismail -p song track_add name=fiddle 'instrument={"type": "mimic", "profile": "violin", "tail": 1.0}'
```

`folder` holds one note per file named by pitch (`A4.wav`, `Fs3.mp3`); `notes=[[source, pitch], ...]` takes sound-bank names, paths or windows of a longer file. The profile is written to `<project>/voices/violin.mimic.json` and found like a voice (`voices_list` shows it). `mimic_measure` rebuilds every measured note from the others and reports how close each lands, which is the honest estimate for pitches you did not record, and it uses that test to choose how sharp the body curve can be for your data. `instrument_help(type='mimic')` lists the playing parameters.

Tested leave-one-out on violin, cello and double bass recordings, a mimic note rebuilt without ever hearing that note lands as close to the real one as a real neighbouring note repitched, or closer (violin 11.2 vs 14.6, cello 12.5 vs 13.0, double bass 10.9 vs 12.8 on `sound_compare`'s distance). The defaults were then tuned by ear over four rounds of eye exams (open, not blind: the real note next to versions that each change one named thing); the current default was picked closest on every note of the last round. Struck and plucked instruments (piano) are harder: with 12 notes across 7 octaves a piano's note-to-note colour can't be predicted and mimic stays behind a sampler there (16.3 vs 11.6); the `grand_piano` voice remains the better piano. A few notes at one dynamic teach one dynamic: record soft and loud notes if velocity matters, and more notes than you think for instruments whose colour changes from note to note.

## Hearing: audio as text

| Question | Tool |
|---|---|
| Tempo, where bar 1 is, tuning, swing | `analyze_grid` (bar 1 voted by kick, harmony, section changes and the snare on 2 and 4), `align`, `analyze_swing` |
| Song form, what plays where | `analyze_structure` (arrangement map, sections, loop length, root per bar) |
| Levels, bands and chords per bar | `analyze_bars`, `analyze_chords`, `analyze_key` |
| Drum pattern | `analyze_drums` (step strings you can paste into `pattern_write`) |
| What is in a drum kit | `analyze_kit` (splits a drum stem into its pieces, with each one's pattern and audio) |
| Loudness per section, dynamic range | `analyze_sections` (warns when a build is as loud as its climax) |
| Notes | `analyze_pitches` (per beat), `analyze_roll` (piano roll), `analyze_melody`, `analyze_notes` |
| Rhythm of level (pumping, gating) | `analyze_envelope` |
| What a sound is | `analyze_timbre`, `analyze_spectrum`, `sound_compare` |
| Vowels of a voice | `analyze_formants` |
| A picture, if you really need one | `spectrogram` (PNG), `sound_zoom` (one window time-sharp and frequency-sharp) |
| Where yours differs from the real takes | `sound_zoom_diff` (hot spots beyond the takes' own spread), `region_measure` (power in a box) |

Sources are `render`, `track:<name>` (after `render(stems=True)`), `ref`, `ref:<stem>`, `sound:<name>` or a file path.

## Recreating a reference track

Bring your own reference audio (`project_new(..., reference=<file>)`); none is included here.

1. `analyze_grid(source='ref')`, then `align` a rendered drum track against `ref:drums` and correct `offset_sec`. If it reports the record 15 cents or more off A440 (a sped-up sample), run `ref_retune()` first: otherwise every note reads as a pair of semitones.
2. `separate(source='ref')` (demucs) and read `analyze_structure(source='ref')`. Measure the drums before writing them: `analyze_swing` and `analyze_kit(source='ref:drums')`.
3. Transcribe with `notes_from_audio_loop`. Raw transcription copies echoes, leakage and distortion partials as hard notes, so it filters in three layers the agent can tune: partials are attributed to the note they belong to, quiet notes fall under level thresholds (`rel_db`, `min_rel_db`, `floor_db`), and a vote across loop repetitions drops what changes between them. A loud echo locked to the tempo repeats every loop and can survive all three; then the agent tightens the thresholds or asks. If the song alternates versions of its loop, transcribe each from its own repetitions and pass `base_bars` so the shared notes stay identical.
4. Design sounds: `sound_extract` a repeated hit or stab, then `instrument_fit` (evolution strategy over instrument and effect parameters, scored on spectrum, envelope, width and pitch clarity). `track_fit` tunes a part in context against the reference stem.
5. `stem_map_set`, `render(stems=True)`, `levels_from_ref` (faders from the reference's stem balance), `cmp_run`, then drill down: `cmp_summary`, `cmp_arrangement`, `cmp_sections`, `cmp_worst`, `cmp_bars`, `cmp_zoom(bar)`. `cmp_list` tracks progress across runs.

### How comparisons score

Every metric sits between two baselines computed from the reference alone: the reference against itself one loop later (its own natural variation, closeness 1) and against itself half a loop out of place (plausible but wrong, closeness 0). Metrics are grouped, and the groups count equally:

- **notes**: F1 of sounding notes per 16th step, exact pitch and pitch class
- **rhythm**: F1 and precision of note starts, drum lane hits
- **clean**: clutter (attacks sharper than the reference), loop self-consistency, loudness share of extra note starts
- **sound**: band levels, level, transient sharpness, level contour inside the bar
- **perceptual**: CLAP audio embedding similarity per 2-bar window

The perceptual group exists because the others can all look fine while the result still sounds different, and the clean group exists because note metrics reward clutter. Use `cmp_run(stems='demucs')` at checkpoints so your render goes through the same separation as the reference. Any change to the scoring should be checked against a known-bad and a known-good draft before you trust it.

## Playing live

The same instruments and effects play in real time while the agent edits the music: a jam, a DJ set, a
soundtrack that follows a game or an audience. [Hear a recorded set](https://newsbubbles.github.io/ismail/#liveset):
three whole songs played live on decks, the set written as one script (`live_load`, `live_deck`, `live_transition`). `live_start` runs a separate engine process per folder (a local
control port, render workers, a mixer and a safety chain), and the agent drives it with ops:

```text
live_start(bpm) -> live_track(track, instrument, fx) -> live_queue([{track, notes, bars, at: 'next_4'}, ...])
  -> live_status / live_listen(bars) -> more live_queue, live_fx ramps -> live_stop
```

- **Clips loop until replaced**, so the music keeps going between the agent's turns. A clip lands on the next
  beat, bar or phrase; `after:#k` chains a whole arc in one call; `live_fx` ramps sweeps and fades.
- **Render ahead.** Notes render in worker processes seconds before the playhead and the audio callback only
  copies, so heavy voices (mimic, code voices, guitar performers) play live. A clip whose first notes cannot
  render in time lands a bar later, and the reply says so.
- **It listens to itself.** `live_listen` runs the same analysis as a render on the last bars played.
- **Safety.** Every output passes a trim, a loudness rider, a lookahead limiter and a ceiling; each set's level
  is set at `live_start(trim_db=, cap_db=)` (else `ISMAIL_LIVE_TRIM_DB` / `ISMAIL_LIVE_CAP_DB`, else quiet defaults),
  inside fixed ranges, and the ceiling stays at -1 dBFS.
- **Decks.** `live_load` puts a whole ismail song on a cued deck while another plays; `live_transition` queues
  the mix (blend, bass swap, filter, cut) with a DJ strip per deck (isolator, filter knob, fader, transpose).
- **Studio and live.** The studio code is the source of truth. Effects run live as block-by-block twins held to
  the studio versions by tests; an effect with no live twin is baked into each rendered note. A voice with
  `perform()` plays whole phrases live (legato, slides), with bends and vibrato from the clip's `expr` lanes.

The skill reference `skills/ismail/references/live.md` has the method for running a set: read the audience,
steer with small edits, queue a runway before every question, build and drop.

## The phone page: the set in your pocket

`phone_start` serves a small page on your own private network (Tailscale today) that you open on your phone:
the live set plays with the screen off, and you talk back without unlocking. It is the main channel when you are
away from the desk: a walk, bed, the kitchen, a day spent on your phone instead of at the computer.

- **Listen** with the screen off; the stream reconnects by itself after a dropout or a server update, and catches up
  when it falls behind the room.
- **Talk back**: press the earbud (or hold the big key) to record a voice note; it is transcribed and reaches the
  agent with the bar and the minute you were hearing. Short notes are commands ("love this", "louder", "stop
  listening").
- **Tap feedback**: love this, change it up, calmer, more energy, louder, quieter, the mood for the next chapter. The
  page keeps a list of what you asked for, and marks a piece you loved when it comes back.
- **Answer exams and questions** from the agent: blind ear tests (`phone_exam`, checked by `exam_check` before you
  see it), yes or no, panels with buttons, downloads.
- **The agent sets the mood of the page** to the music (`phone_vibe`: colours, the face of the titles, a blurred
  cover or render behind it, rain, particles or a glow on the beat), within contrast limits that keep it readable.
- **Bars or time**: read the set as bars, like a DAW, or as minutes and seconds.
- **Installs as an app** from Chrome, with optional notifications for what the DJ says.
- **Agents see what happened, on one clock**: every tap, note and page action (Listen, downloads, where you
  scrolled, which device) with the bar and piece playing, so "that bit" can be found (`phone_timeline`). It all
  stays on your computer.

The agent offers the page when a set plays and you step away. The method is in
`skills/ismail/references/phone.md`.

## Music videos (optional)

`ismail.video` makes a music video from a finished song, with every cut and glitch placed from the song's own notes (the event list comes from the project, so the sync is frame exact). It needs `pip install -e .[video]`, Blender 5.x and ffmpeg.

```bash
python -m ismail.video init   -s songs/<slug>        # scaffold songs/<slug>/video/
python -m ismail.video sync   -s songs/<slug>        # notes -> frames
python -m ismail.video still  -s songs/<slug> s01_example.py 48
python -m ismail.video render -s songs/<slug> s01_example.py   # waits for the machine's GPU slot
python -m ismail.video edit   -s songs/<slug> -- --sheet 33 41 16
```

Shots are Blender scripts built on a small kit (rooms, rigged characters from JSON, lights, fog, cameras), the cut list is Python written in bars, and review is by stills and contact sheets. Per-song work lives in `songs/<slug>/video/`. The skill reference `skills/ismail/references/music-video.md` has the full method.

## Tests

```bash
python -m pytest tests -q
```

Round trips: write known material, render it, read it back through the analysis tools. [.github/workflows/tests.yml](../.github/workflows/tests.yml) runs them on Ubuntu, macOS and Windows.

## Layout

```
ismail/
  notation.py    note text, step patterns, piano roll
  dsp.py         oscillators, filters, dynamics, delay lines, reverb (numba)
  instruments.py synth, sampler, drums, kit, code
  fx.py          effects; rig.py the guitar rig (fuzz, univibe, amp, cab, rotary, tape, wah)
  spatial.py     the spatial effect: 3D position, trajectories, head model, crosstalk cancellation, distance reverb
  render.py      project to audio, dependency ordering, per-track cache, wav/mp3 writers
  analysis.py    audio to text (grid, bars, chords, melody, drums, timbre, formants, compare)
  features.py    16th-step feature grid shared by structure and comparisons
  structure.py   arrangement map, sections, loop detection
  cmp.py         stored comparisons and their views
  perceptual.py  CLAP similarity
  sounddesign.py one-shot rendering, sound distance, parameter fitting
  trackfit.py    in-context fitting against a reference stem
  live/          the live engine: timeline, render workers, mixer graph, decks, safety, live_* ops,
                 block-by-block effect twins (fx_blocks.py, dsp_blocks.py)
  video/         optional music-video pipeline: sync, edit engine, Blender shot kit, CLI
  mimic.py       instruments measured from recordings (partials, body, noise, vibrato, room)
  voices/        the voice library in family folders: keys (grand_piano, additive_piano, rhodes), strings
                 (violin, cello, contrabass mimic profiles), winds (clarinet, bassoon), brass (horn), bass (growl),
                 fx (sfx, crackle), guitar (electric, emily), drums (kit70, rusty, toms); showcase.json lists the voices
                 a first sketch uses, fx_stacks.json the built-in effect stacks
  api.py, api_cmp.py, api_sound.py, api_measure.py, api_exam.py, api_fxstack.py   the operations (CLI and MCP tools)
  api_ops.py     ops_find: the op for a need, by purpose or role
  presets.py     instrument presets; samples.py the sample sets some voices fetch; tune.py autotune (WORLD)
  separate.py    demucs stem separation; sources.py where a song's references came from; backup.py a project backup
  tags.py        ID3 tags on every mp3; lexicon.py a person's own words mapped to ismail's terms
  mcp_server.py, guide.py
  stage/         the VR stage: a WebXR page, its server and the live link, scenes, takes, presence; the stage_* ops
  phone/         the phone page: live stream with the screen off, taps and voice notes, panels, exams; the phone_* ops
  exampage/      the eye exam page: picture rounds, one sound per card, the key served only after an answer
  feedback/      feedback pages answered by typing or voice, kept and transcribed; the feedback_* ops (api_feedback.py)
  capture.py     faults in a recording found before it is measured (soft-limited peaks, band-limited capture)
  sketch.py      first sketches from a brief on the showcase voices, versions 1, 2, 3, feeling words
  machine.py     the shared machine's governor: one board for GPU, CPU and memory, heavy jobs run in its slots
  exam_check.py  the pre-flight every exam page runs before the person sees it (leaks, silent or quiet clips)
  provenance.py  what each track's sound is modeled on: measured, designed or not said
  handoffs.py    intake of the songs' HANDOFF files for the migration loop
  worktrees.py   dev copies (git worktrees) of ismail: who owns each and how far it is from main
skills/ismail/   the agent skill (SKILL.md + references)
.mcp.json, .cursor/   MCP and rule config for Claude Code and Cursor
songs/           your projects (git-ignored)
```

