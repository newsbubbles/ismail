# A jam runner: reading takes in code, in about a second

A worked recipe for an interactive jam, where the person plays parts in from the phone and you turn each one into a
loop. ismail has no jam op on purpose: every jam is different (the instruments, the room, how the person talks), so
you build a small runner in the song's own folder from this recipe, shaped to that person. Read `live.md` ("Three
paces" and "In a jam") and `phone.md` first.

## Why a runner

A take that needs a reader you don't have yet is never quick for you: classifying it, syncing it and reading its
notes by hand takes minutes, and in a jam minutes are loops gone by with the person waiting. Deterministic code does
that reading in about a second, every time the same way. You only talk, choose and adjust. The runner is the
studio's job done at the speed of the loop.

## The flow, take by take

1. **Watch.** The runner reads the phone inbox (`phone_watch` rows: `voice` notes and `hum` takes), one read per take
   id, so nothing is read twice and nothing is missed while you are busy.
2. **Decode** the recording to mono at a modest rate (22050 Hz is plenty for reading).
3. **Classify** it from cheap features: speech (a voice activity detector), percussive (beatbox: flat spectrum, energy
   above 4 kHz), strum (chords: a triad fits the chroma at each hit) or melody (a steady single pitch). Say why in
   the output, so a wrong guess can be fixed by a rule, not a hunch.
4. **Sync** the take to the engine's beats. Best is the engine's own beat marks for the bars the person heard. Without
   them, use the beat the page measured at the take's start and slide the take's onsets along what was playing
   (the kick the microphone hears is the easiest anchor). Take the speakers' sound out of the take before reading
   hits.
5. **Read** it into a part: beatbox hits to a kit line (loud high hits the snare, quiet high ones the hat, little high
   energy a kick, kept only when heard in two or more cycles), a strum to chords, a melody to notes on the grid.
6. **Propose**, don't play: write the proposal next to the take (`<take>_proposal.json`) and print one line. The main
   thread decides what goes in.
7. **Queue** a proposal with `live_queue`, from the bar the person is hearing plus a few, moved on to the next bar
   that keeps the loop where they played it, so their part lands on the beat they meant.

## Modes: sample, match, ask

A take can be used two ways, and the person decides which, not you:

- **Sample**: their recording itself, cut into slices on the beat, loaded as a sampler track and looped (a new track
  takes over from the last one). Their real voice or instrument, gated and in a room, usually lands better.
- **Match**: the read part played on an instrument (their hum as a bass line, their strum as chords).
- **Ask** (the default): a phone panel with two buttons (`phone_panel_show` with SAMPLE / MATCH). Keep the answer in
  the song's jam settings file and use it for the next takes, and let a voice note ("jam mode sample") change it at
  any time.

The worked example: in a uke jam on 2026-10-12, a "hey hey hey" voice note was first matched to a guitar melody. The
person hated it: they wanted their own sample, not an imitation of it. Cut into 16 slices of their real voice on the
psytrance grid, it became the part they liked most. That is why ask is the default and sample comes first.

## Building yours

- Put it in the song folder (`set/jam_runner.py` or similar): it is song code, not engine code. Start from the
  readers ismail has (`analyze_drums`, `analyze_chords`, `analyze_melody`, `sound_extract`) and add only what this jam needs.
- Give it a dry mode that reads one saved take and prints the proposal, and a test mode that reads every saved take
  and prints a table. Prove the readers on the takes before the next jam, not during it.
- Keep it quiet by default: propose only, and queue or ask the phone only when the main thread runs it with a play
  flag. Two things writing to the engine at once is how loops get lost.
- When a reader is wrong live: pull the part at the next bar, fix the rule in a helper, and rerun the dry mode on that
  take before it goes back in.

## From the jam to a song

Record from the first take (`live_record`), and write the story as you go (one line per take: what they sent, what
changed, what they said). When the jam ends, the story and the takes are the score: a studio version is the same
parts, rebuilt in its own song folder from their takes and the runner's proposals, arranged to tell the session in
order. Copy it as a whole song (`SKILL.md`, "One piece, one folder"), render it, and ask whether it kept what they
loved about the jam.
