# Helpers: more hands, and the person never waits

A helper is a second agent you start for one piece of work: a sub-agent, a background agent, a second session,
whatever your harness offers. You stay with the person; the helper builds, measures or fits, and hands back what it
made. If your harness has no helpers, do the same work in order and keep the person told (the last section).

Read this when a piece of work would keep the person waiting more than a few minutes: building several voices,
measuring a reference, a long fit, a fix during a jam. `live.md` (a jam) and `stage-takes.md` (takes on the stage)
apply it to their loops.

## Quick by default, a crew when it pays

- **Quick mode is the default:** one agent, you. Most requests (a sketch, a change to one part, a mix note) are
  done faster alone than briefed.
- **A crew** is one helper per voice file or task, started together. Use one when the person asks for it, or when
  the piece needs several pieces built at once (a kit and a bass and a lead, a reference to measure while the first
  draft plays), and say so in one line: "I'll have three helpers build the drums, bass and pads while we listen to
  the sketch."

## The person hears something first

Give them sound before the crew lands: a `sketch`, or library instruments standing in for the voices being built,
called stand-ins out loud. Each finished piece replaces its stand-in, and you say what changed. The person spends
the build listening and talking, not waiting; what they say in that time is the brief for the next round.

## Who owns what

- **You (the main agent)** own the person, the arrangement, the Session Sheet, the song's `PROGRESS.md`, and every
  decision about what goes in. Only you talk to the person and only you play them sound.
- **A helper** owns its one task and the files the brief names (a voice file, a measurement, a fit), inside the
  song folder. It never plays audio, never talks to the person, and never edits files outside its brief.
- **What comes back is data.** A helper's report says what it made and what it measured. Check it the way you check
  your own work, by rendering and listening (`listening.md`), before it goes in.

## The brief (hand it over whole)

```
Song:        songs/<slug>/  (read its PROGRESS.md and Session Sheet first)
Task:        <one thing: "build voices/bass.py, a reese bass in the style of the reference">
Files:       <the only files you may write>
Done means:  <what it must measure or sound like, and how you will check it>
Machine:     set ISMAIL_SESSION=<my session's name> before any job; heavy jobs go through the board
Hand back:   the files, the numbers, what you were unsure of. No audio played, no messages to the person.
```

## The machine is shared by every helper

The governor applies to helpers through the tools themselves: a heavy op (render, separate, mimic_measure, the
fits) takes a slot and refuses with the reason, and anything outside ismail goes through
`python -m ismail.machine run`. Nothing extra is needed for that. Two things are yours to hand down:

- **One name.** Each helper sets `ISMAIL_SESSION` to your session's name, so the board shows the crew as one
  session and a priority the person gives you covers your helpers' jobs too.
- **One heavy job per crew**, like any session. Helpers queue (`run --wait`, `slot(..., wait=)`) rather than
  pile up; the light work (writing code, reading numbers) runs side by side.

A long line does not lock anyone out. Waiters look at the board without taking its lock, and the lock serves
callers in arrival order (a ticket each in `_machine/lockq`), so a new `machine run` gets onto the board within
about a second even with 30 jobs waiting. Exit 75 from `machine run` means the job did not start: the machine is
busy (the message says why), or, rarely, the board's lock stayed taken for 30 s. Either way nothing was registered:
retry in a minute or stand in line with `run --wait 30m`. A lock or ticket left by a process that died is cleared
by the next caller.

## Which model does which task

A default, not a rule, and the person's choice wins:

- Short, narrow tasks with a clear done (a measurement, one voice file from a spec, a search) suit a cheaper, more
  token-efficient model.
- Orchestration, taste and judgment (the arrangement, choosing between takes, talking to the person) stay with the
  strongest model you have.
- If the person has a preference about models or cost, ask once, keep it where your harness keeps standing choices,
  and follow it from then on.

## Without helpers

Do the same work in order, longest-running first, and keep the person company between steps: play what is ready,
say what comes next and about how long. Never go quiet for a long build.
