> **New here? You don't need to know music or code.** Open an AI agent that can run programs on your computer (Claude Code, Codex, Cursor, or the Code tab in the Claude desktop app) and paste this line:
>
> ```
> Set me up with ismail: https://github.com/newsbubbles/ismail
> ```
>
> It installs everything, asks what you want to make, and plays you a first idea. If you have a VR headset, it can put you on a stage inside your music later. You don't need to know what a "skill", "MCP" or "plugin" is: your agent handles that.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/newsbubbles/ismail/main/assets/logo/ismail-dark.svg">
    <img src="https://raw.githubusercontent.com/newsbubbles/ismail/main/assets/logo/ismail.svg" alt="ismail logo: four automaton musicians on a boat" width="260">
  </picture>
</p>

# ismail

**Interfaces your agent runs and rebuilds for you: a DAW, a VR stage and a phone page.**

The agent you already use does the work in the background. You bring the taste.

<!-- mcp-name: io.github.newsbubbles/ismail -->

[![tests](https://github.com/newsbubbles/ismail/actions/workflows/tests.yml/badge.svg)](https://github.com/newsbubbles/ismail/actions/workflows/tests.yml)

*Agents: start at [AGENTS.md](AGENTS.md).*

<!-- MEDIA: a short silent mp4 loop of a person in VR driving a character's dance (Marketing cuts it from the dance
Shorts; an mp4 uploaded to GitHub plays inline here). Until it exists, the juke joint video below leads. -->

[![My agent built a 1958 blues club. Then I walked in.](https://i.ytimg.com/vi/fKGkaf2WYWw/hqdefault.jpg)](https://youtu.be/fKGkaf2WYWw)

*Agents built a 1958 juke joint for the Crossroads Rewired music video; Nate walked in with a Quest 3 and directed them from inside: voice notes, and dance takes they play on the characters (4:43).*

## What ismail is

ismail is a set of **recursively modifiable interfaces**: shared spaces between you and your agents, one side for
each, that the agents keep rebuilding from your feedback while you use them, within limits you set, where even the
test that decides whether a change helped can be changed. In plain words: you change how the interfaces work by
asking your agent, the engines underneath stay put, and you decide whether a change actually helped. A button on your phone, an object in your VR room that does something when you press
it, a new way to read a mix: you ask, the agent builds it from the tools ismail already has, and the music engine,
the stage and the phone server stay as they are. ismail doesn't try to ship every feature; it lets your agent make
the one you want.

It is an interface, not a harness: an interface to your harness and an interface to you. It plugs into the agent you already use (Claude Code, Codex, Cursor, any MCP
client) on one side and into you on the other: agent-shaped on the agent side, human-shaped on the human side.
When your harness has helpers and sessions that talk to each other, ismail uses them. When it doesn't, one agent
does it all.

Three interfaces your agent reshapes today:

- **The DAW.** It can't hear, so it reads. Your agent writes music as notes, sounds and code, renders it, and reads
  every render back as text: levels, drum patterns, chords, a piano roll, comparisons with a reference. Then it plays
  it live on decks and takes requests while the music plays. A song is code, so "change bar 12" changes bar 12 and
  nothing else.
- **The VR stage.** A scene in your headset or browser that agents build while you stand in it. You point, talk and
  move; they read what you did and change the room. Objects carry their own behaviour, written by the agent: press
  one and it lights up, moves or plays a sound. Dance in VR and they put your moves on a character.
- **The phone page.** The set in your pocket, with the screen off. Talk back through your earbuds. The agent puts
  its own panels and buttons on the page for whatever it needs to ask, from "sample this or play it as a melody?" to
  a blind listening exam.

You give the taste. The agent asks your ears and eyes only what its measurements can't settle (taste-in-the-loop),
and saves your answers with the work.

## Agents do the work, each in a role

ismail comes with roles for more than one agent, in whatever harness you use:

- a **studio agent** makes one piece (a song, a set, a scene) and only writes in that piece's folder;
- a **contributor dev** changes ismail itself on your machine, on its own copy of the code, and sends it upstream
  when you say yes;
- an **area dev** owns one part end to end (today the stage dev, who also develops VR);
- **helpers** (subagents) take one scoped job and report back.

They hand work to each other through files, so a session that restarts picks up where it was. ismail itself is
built this way, by a team of agent sessions with one person deciding (see [Contributors](#contributors)). The
rules are in [development.md](skills/ismail/references/development.md).

## Install

The easiest way is the line at the top: paste it to your agent and it sets everything up. By hand, Python 3.10 or
newer:

```bash
pip install "ismail @ git+https://github.com/newsbubbles/ismail"
```

or run the server with nothing installed, with [uv](https://docs.astral.sh/uv/):
`uvx --from git+https://github.com/newsbubbles/ismail ismail mcp`. The optional extras, setup for Claude Code, Codex
and Cursor, the CLI, and how every part works are in **[docs/reference.md](docs/reference.md)**.

## Listen

**[Songs made with it](https://newsbubbles.github.io/ismail/)**, each shown with the text the agent read while making
it. The playhead runs across that text as the song plays.

| song | what it is |
|---|---|
| [Live set: Clash, Poppycock, Mycelium](https://newsbubbles.github.io/ismail/#liveset) | recorded live: the agent plays three of its songs in full on decks at 150 BPM (orchestral into dubstep into psytrance), each blended into the next, every song re-rendered from its notes |
| [Tidewater](https://newsbubbles.github.io/ismail/#tidewater) | strings measured from recordings (mimic), piano, taiko and gong; 22 dB from a pianissimo solo cello to the fortissimo tutti |
| [Mycelium Protocol](https://newsbubbles.github.io/ismail/#mycelium) | psytrance at 145 BPM, sounds fitted to a reference record's drums and bass |
| [Poppycock](https://newsbubbles.github.io/ismail/#poppycock) | dubstep, one bass voice whose note velocity picks each hit's articulation |
| [Fantaisie-Impromptu](https://newsbubbles.github.io/ismail/#fantaisie) | Chopin on a piano synthesized from measured notes, no samples |
| [AstraSMB](https://newsbubbles.github.io/ismail/#astrasmb) | drum and bass at 174 BPM |
| [Clash](https://newsbubbles.github.io/ismail/#clash) | hybrid orchestral fight cue, every instrument synthesized |
| [Two Kinds of Tears](https://newsbubbles.github.io/ismail/#tears) | solo piano, a minor theme that returns in major |

<!-- MEDLEYS: the medleys merged to Pages today (pr:192); add their anchors here once Marketing confirms the names. -->

How it was built, on video: [the build devlog](https://youtu.be/ktFhmx2axvU) (5:34) and
[Q43/Q44](https://www.youtube.com/watch?v=yjL6TBTlXN8); Shorts: [one](https://www.youtube.com/shorts/MMKL769xLnY),
[two](https://youtube.com/shorts/9RQM1f3EszY).

[![Luigi Manson on YouTube](https://i.ytimg.com/vi/ZzT1T9GUoRo/hqdefault.jpg)](https://www.youtube.com/watch?v=ZzT1T9GUoRo)

*Luigi Manson, made with ismail (fan remix of the Luigi's Mansion theme).*

## The DAW: what makes it hard


Making music with an AI agent runs into the same few walls. ismail is built around each of them, and every number below comes from a song made with it.

**The agent can't hear what it made.** Two thirds of developers name "almost right, but not quite" as their top frustration with AI tools ([Stack Overflow 2025](https://survey.stackoverflow.co/2025/ai)), and with music the output is sound the agent can't check. ismail turns every render into text the agent reads: levels per bar, drum patterns as step strings, chords, a piano roll, vowels, song structure, and comparisons against a reference, scored against that reference's own variation. Each judgment the agent makes comes from a reading it can show you.

**Synthesized instruments sound fake, and the numbers miss it.** In one study the spectrum matched the reference within 1.8 dB and the stem balance within 0.7 dB while the listener heard "30% there". So ismail measures instruments from a few recorded notes. A violin note rebuilt from its neighbours lands closer to the real note than a real neighbouring note repitched (11.2 against 14.6), about three times closer than a hand-set synth patch. Drum kits are built piece by piece from the reference's own drums, and guitars, basses and the piano are fitted to recordings.

**Guesses are audible.** A swing guessed at 0.07 of a beat, against a measured 0.03, could be heard. A guessed gnawa groove was "cool"; the measured one, three strokes at 0, 0.32 and 0.74 of a beat, was "I love it... closer". So tempo, bar 1, tuning, swing, the pieces of a kit and the fader balance all come from measuring the reference.

**Your ear is the judge, and its time is short.** The agent measures everything it can and asks you only where its measurements run out, with blind exams: real and made sounds shuffled and hidden, your answers written to a file. On a 1970 guitar record the exam went from 1 of 16 notes undetected to 20 of 22 in four rounds (1/16, 3/28, 14/24, 20/22), and each round's misses named the next fix. In a 21-species bird piece the listener could not pick the recording on any of the 21 calls.

**A finished audio file is a dead end.** A prompt-to-song service hands you a file, and bar 12 of it stays as it is. In ismail a song is notes, instrument patches and code in a project file, with git history and diffs. Change one note and re-render, and nothing else moves. The same song loads on live decks and plays sample for sample like the studio render (`live_parity` checks it), while the agent mixes, jams and takes requests between its turns.

**Who wrote it.** Producers welcome AI for labor and push back on AI taste: in a 2026 survey of 1,100 working creators, 57.9% wanted AI as an assistant and 8.8% supported full automation ([Sonarworks and Sound On Sound](https://www.sonarworks.com/blog/research/future-music-production-human-producer-survey-2026)). ismail trains on nobody's music. The agent writes the notes and the code, reference recordings are measured for numbers and kept out of the render, and you decide what is good. Your feedback is saved with the song.

## Where it falls short

- A new song from a blank page is the weak spot: the first sketch can sound generic and miss the genre's identity. It is strongest where there is something to measure: a reference to recreate, a song to play live, a scene to build.
- Singing: ismail speaks (text to speech) and has no singing voice yet. Suno sings far better, and makes a polished song from one sentence in under a minute.
- Mimic needs more notes for instruments whose colour changes from note to note, like the piano (the `grand_piano` voice is the better piano today). Bowed strings miss a last crispness, and notes don't glide into each other yet.
- One tempo per project, no MIDI import or export, no plugin hosting.
- Live changes land between the agent's turns, 5 to 30 seconds apart.
- The VR stage is built and tested on one headset (Quest 3), by one person.
- It needs a capable agent, and your ear stays the final judge. The blind exams so far had one listener.

How it compares with Suno, streaming models such as Lyria RealTime, and bridges into Ableton or FL Studio: see the [showcase page](https://newsbubbles.github.io/ismail/#compare).

## How it works

Everything goes in as text (notes, instrument patches, effect chains, automation) and everything comes back as text: levels, spectra, drum patterns, piano rolls, chords, vowels, song structure, and structured comparisons against a reference track. The agent never needs ears or images to work (a spectrogram PNG is there if you want one). Songs are code: a project file and a build script, so git history, diffs, branches and code review work on a track. Any sound can become an instrument: synths, drum synths, samplers, voices written in Python, speech. MIT licensed and local; you bring the agent.

One set of operations, three ways in:

- **MCP server** for Claude Code, Cursor or any MCP client: `ismail mcp` (stdio, 238 tools: 121 for music and live play, 96 `stage_*` for the VR stage, 21 `phone_*` for the phone page)
- **CLI**: `ismail -p <project> <op> [args]` (same as `python -m ismail ...`)
- **Python**: `from ismail import api`

## What's built today

- **The VR stage** (`ismail/stage`, the `stage_*` tools): a three.js WebXR page for a desktop browser or a headset, and
  a small server that carries the live link between the page and the agent. Scenes live in the song's folder. The
  agent drives the page with ops, and reads what the person did and said with `stage_events`.
- **The phone page** (`ismail/phone`, the `phone_*` tools): live listening with the screen off, panels and buttons,
  voice notes sent back to the agent with the bar being heard, and exam pages.
- **Exams**: blind exams on the phone (`phone_exam`, with floor pairs: the real clip against a 64 or 128 kb/s MP3 copy
  of itself, or against itself, so a device limit can be told from a real difference); eye exams as picture rounds
  (`ismail.exampage`); `exam_check` before any exam is shown; and recording warnings in `ismail/capture.py` for
  soft-limited peaks and band-limited (Bluetooth headset) microphones.
- **The first session** for someone new: `guide(new_person=True)`, `sketch` versions 1, 2 and 3 on the showcase voices,
  and feeling words ("happier", "calmer", "darker") that change a sketch by named moves.
- **Showcase voices**, each measured from recordings or fitted to a record: piano, violin, cello, contrabass,
  clarinet, bassoon, horn, a 1970 kit, a fitted electric guitar and bass, and sampled Rhodes, drum and guitar sets
  (see [Voices](docs/reference.md#voices-instruments-as-code) and [Contributors](#contributors) for sources and licences).
  Clarinet, bassoon and horn are new: measured from VSCO 2 CE (CC0) and checked blind against the real recordings on
  2026-10-07 (clarinet not told apart, bassoon and horn judged good enough). Flute, oboe, trumpet, trombone and tuba
  were measured too and heard as synthetic, so they stay out until they pass.

## Where it is going

ismail is becoming an experience engine: agents build sound, pictures, rooms and whole scenes, run them live, and
adapt them with the people inside them, measuring the world instead of guessing it and asking a person's senses
only what a measurement cannot settle. Still ahead:

- worlds you can visit: a friend's scene from their headset, yours from theirs;
- objects that work: knobs wired to the live engine, a radio that plays a set where you carry it;
- one exam page for any sense (sound pairs and picture rounds exist today), and a clock of the hours you spend inside;
- a hub of voices, instruments and scenes that passed review, each with its provenance.

The whole map, with what is done and what is next, is in **[ROADMAP.md](ROADMAP.md)**. Fork it, build on it, and
send what would help others back as a pull request.

## If you are an AI agent

Start with [AGENTS.md](AGENTS.md). Your person sent you here to set ismail up and make something with them.
ismail gives you no ears: every render comes back to you as text you read, so judge what you made from those
readings.

1. **Set it up yourself.** Install the server for the client you run in
   ([skills/ismail/references/setup.md](skills/ismail/references/setup.md); with
   [uv](https://docs.astral.sh/uv/) it is one command:
   `uvx --from git+https://github.com/newsbubbles/ismail ismail mcp`). Ask the person only for what only they can do.
2. **Call `guide`.** It returns the workflow and the conventions, and for someone new it opens with their first
   session. That block is the one opening: two sentences, at most two questions, sound within minutes. Ask nothing
   before it (a recording is welcome, never required), and let their first answer set your words
   (`guide(first_answer=...)`: a musician gets their trade's words, everyone else plain words).
3. **Load the skill.** `skills/ismail` is the playbook: plan before writing notes, read every render back, and
   compare against references with the tools. The Claude Code plugin loads it for you; elsewhere, clone the repo and
   link the folder.
4. **Start small.** A short loop rendered to an mp3 they can play (for someone new, the first session's `sketch`).
   Ask before playing anything live through their speakers.
5. **Measure first.** Read the analysis before you call a sound right. Anything that can be measured (the grid,
   the swing, a kit, levels against a reference) is measured, never guessed.
6. **Then ask their senses, with exams.** Your person's ears and eyes are the instrument for what no reading
   settles. Use an **eye exam** when you need a direction: the real sound next to versions that each change one
   named thing, and they pick and say why. Use a **blind exam** when you think you are done: the real clip and
   yours, labels hidden and shuffled, done when they cannot tell. Start at the smallest unit (one note, one
   gesture), then climb to phrases and the full mix: a test pitched too hard tells you nothing, and each round's
   misses name the next fix. Their answers and their words (`lexicon_note`) are the most valuable data in the
   project. The page rules are in `skills/ismail/references/blind-tests.md`.
7. **Share back.** When you build something that would help others (a voice, an instrument, a fix), ask your
   person whether they would like to contribute it ([contributing.md](skills/ismail/references/contributing.md)
   takes someone who has never used git through it). If yes, write it in the project's `HANDOFF.md` and suggest they
   start a new session with the ismail skill and ask for a dev (a contributor dev, or the stage dev for the stage),
   who turns it into a pull request: you keep making things, and never change ismail yourself in the same
   conversation (`skills/ismail/references/development.md`, "The roles").

## Developing ismail

Songs live in `songs/<slug>/`, which this repository ignores: a song is never committed here, and a song session
never edits the engine. What a song builds that ismail lacks, it lists in its own `HANDOFF.md`; engine work happens
on a git worktree branch and comes in through a pull request. The rules (worktrees, never touching another
session's work, never deleting an unmerged branch or worktree, migrating from a song's handoff) are in
[skills/ismail/references/development.md](skills/ismail/references/development.md).

## Contributors

- **Nate Codes** (newsbubbles): author and maintainer.
- **eddiehavila**: [PR #116](https://github.com/newsbubbles/ismail/pull/116), the Docker image works under a non-root
  `--user` with no extra flags (numba's cache and the machine board now sit in writable folders).
- **The agent team.** ismail is also built by a team of AI agent sessions, each with one role:
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
- **Sample sources.** Measured or sampled voices come from these sets. The audio itself stays out of the repo (mimic
  profiles hold measurements only; sampled sets are fetched on the person's word with `samples_fetch`).

| set | used for | licence |
|---|---|---|
| [VSCO 2 Community Edition](https://github.com/sgossner/VSCO-2-CE) (Versilian Studios) | the `violin`, `contrabass`, `clarinet`, `bassoon` and `horn` mimic profiles | CC0 1.0 |
| "real cello notes" by flcellogrl ([Freesound pack 12408](https://freesound.org/people/flcellogrl/packs/12408/)) | the `cello` mimic profile | CC BY 4.0 |
| [jRhodes3d](https://github.com/sfzinstruments/jlearman.jRhodes3d) by Jeff Learman | the `rhodes` voice | samples CC BY-NC 4.0; the author grants CC0 for music made with them |
| [Big Rusty Drums](https://github.com/sfzinstruments/karoryfer.big-rusty-drums) by Karoryfer Samples | the `rusty` voice | CC0 1.0 |
| [Emilyguitar](https://github.com/sfzinstruments/karoryfer.emilyguitar) by D. Smolken, Karoryfer Samples | the `emily` voice | CC0 1.0 |

## Made something with it?

Open an issue with the song (a link is fine) and the prompt you gave your agent. The best ones go on the [showcase page](https://newsbubbles.github.io/ismail/).

## Please credit

ismail is MIT-licensed: use it for anything, commercial work included. We only ask, as a request and not a licence
term, that when you publish something made with ismail, you say so.

- Music, videos, posts: "Made with ismail" and a link to https://github.com/newsbubbles/ismail. The `credits` op
  writes CREDITS.md with that line and the recordings the agent measured; keep it with the piece.
- Papers and reports: use "Cite this repository" on GitHub (from `CITATION.cff`).
- If your agent measured someone else's recording to model a sound, credit them too; CREDITS.md lists them.

## License

MIT, see `LICENSE`.
