# Why ismail works the way it does

[Back to the README](../README.md)

## Recursively modifiable interfaces

ismail is a set of recursively modifiable interfaces. The definition we use: a recursively modifiable interface is a
shared space between a person and their agents, one side for each, that the agents keep rebuilding from the
person's feedback while they use it, within constraints the person sets, and where the test that decides whether a
change helped can itself be changed.

In plain words: you change how the interfaces work by asking your agent, the engines underneath stay put, and you
decide whether a change actually helped. ismail doesn't try to ship every feature; it lets your agent make the one
you want, out of the tools ismail already has.

## The walls, and what ismail does about each

Making music with an AI agent runs into the same few walls. ismail is built around each of them, and every ismail number below comes from a song made with it.

**The agent can't hear what it made.** Two thirds of developers name "almost right, but not quite" as their top frustration with AI tools ([Stack Overflow 2025](https://survey.stackoverflow.co/2025/ai)), and with music the output is sound the agent can't check. ismail turns every render into text the agent reads: levels per bar, drum patterns as step strings, chords, a piano roll, vowels, song structure, and comparisons against a reference, scored against that reference's own variation. Each judgment the agent makes comes from a reading it can show you.

**Synthesized instruments sound fake, and the numbers miss it.** In one study the spectrum matched the reference within 1.8 dB and the stem balance within 0.7 dB while the listener heard "30% there". So ismail measures instruments from a few recorded notes. A violin note rebuilt from its neighbours lands closer to the real note than a real neighbouring note repitched (11.2 against 14.6). Drum kits are built piece by piece from the reference's own drums, and guitars, basses and the piano are fitted to recordings.

**Guesses miss.** A swing guessed at 0.07 of a beat; the record measured 0.03. So tempo, bar 1, tuning, swing, the pieces of a kit and the fader balance all come from measuring the reference.

**Your ear is the judge, and its time is short.** The agent measures everything it can and asks you only where its measurements run out, with blind exams: real and made sounds shuffled and hidden, your answers written to a file. On a 1970 guitar record the exam went from 1 of 16 notes undetected to 20 of 22 in four rounds, with one listener (1/16, 3/28, 14/24, 20/22), and each round's misses named the next fix. In a 21-species bird piece the listener could not pick the recording on any of the 21 calls in one blind round.

**A finished audio file is a dead end.** A prompt-to-song service hands you a file, and bar 12 of it stays as it is. In ismail a song is notes, instrument patches and code in a project file, with git history and diffs. Change one note and re-render, and nothing else moves. The same song loads on live decks and plays sample for sample like the studio render (`live_parity` checks it), while the agent mixes, jams and takes requests between its turns.

**Who wrote it.** Producers welcome AI for labor and push back on AI taste: in a 2026 survey of more than 1,100 working creators, most saw AI as an assistant for routine tasks, with people keeping creative authority ([Sonarworks and Sound On Sound](https://www.sonarworks.com/blog/research/future-music-production-human-producer-survey-2026)). ismail trains on nobody's music. The agent writes the notes and the code, reference recordings are measured for numbers and kept out of the render, and you decide what is good. Your feedback is saved with the song.

## How it works

Everything goes in as text (notes, instrument patches, effect chains, automation) and everything comes back as text: levels, spectra, drum patterns, piano rolls, chords, vowels, song structure, and structured comparisons against a reference track. The agent never needs ears or images to work (a spectrogram PNG is there if you want one). Songs are code: a project file and a build script, so git history, diffs, branches and code review work on a track. Any sound can become an instrument: synths, drum synths, samplers, voices written in Python, speech. MIT licensed and local; you bring the agent.

One set of operations, three ways in:

- **MCP server** for Claude Code, Cursor or any MCP client: `ismail mcp` (stdio, 238 tools: 121 for music and live play, 96 `stage_*` for the VR stage, 21 `phone_*` for the phone page)
- **CLI**: `ismail -p <project> <op> [args]` (same as `python -m ismail ...`)
- **Python**: `from ismail import api`
