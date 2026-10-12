# Roadmap

ismail began as a DAW an agent can use without ears. It is becoming an **experience engine**: agents build sound,
pictures, rooms and whole scenes, run them live, and adapt them with the people inside them. The method stays the
same at every scale. Measure the world instead of guessing it; read everything back as text; ask the person's senses
only what a measurement cannot settle; keep what they say and decide; let them set the goal.

This page is the bird's-eye view: where it is going, and how far along each road is. It changes as work lands
(last checked against what is merged on 10-12).
**Status:** done · building · next · planned · idea.

## Next up

| | status |
|---|---|
| **Time in the system.** A clock of the hours a person spends inside ismail (the stage first), reachable from a menu and saved by the server. The human side of the work, made visible. | next |
| **The stage is in ismail.** The browser and VR editor (Blender round trip, hands, voice, panels, the live link) is part of ismail (`ismail/stage`, since 10-04), with a desktop path for people without a headset and `stage_*` ops for agents. | done |
| **The exam op.** One hosted page for any sense, answers written to a file, long audio seekable. Audio pairs are hosted on the phone with answers to a file, picture rounds on a page with seekable media, and a check runs before any exam is sent; clips and a view of the stage are still to come, and they are separate ops rather than one. | building |
| **Working machinery.** Knobs, switches and sliders on the stage wired to the live engine: the engine side (streams, mapped controls, recorded moves) is built, and stage objects now switch and light up by behaviours; wiring those behaviours to the engine's controls follows. | building |

## Worlds

A scene you can walk into is a world. A world is built by an agent and a person together, plays music and video,
and can be visited.

| | status |
|---|---|
| Scenes from Blender in the browser and the headset, edited by hand and saved back into the build | done |
| A grade and light sheet per scene, applied by the build | done (in one project) |
| Spawn points, walk paths, loading between scenes, a continuous world of nearby scenes (the first three are in; the continuous world is not) | building |
| Comfort settings for people prone to motion sickness | planned |
| Objects that work: a radio you carry, a jukebox you turn on, a pool you can play, electronics, simple physics, game furniture | building |
| Workspaces as well as playspaces: panels onto your computer, media and live tools inside a world | idea |
| Lighter copies of models for headsets, the originals untouched | done |
| A native VR client | idea |

## Together: visiting each other's worlds

| | status |
|---|---|
| Visit a friend's world from their headset, and let them visit yours, over a private network where you grant access (a tailnet invite, sign-in you control) | planned (first test soon) |
| Presence: see and hear each other, with the agent as the world's host | planned |
| Shared editing with locks, so two people and an agent never undo each other | planned |
| Each person's things stay theirs: what was placed, recorded or measured from someone carries their name and consent | planned |

## Hands, bodies and time

| | status |
|---|---|
| Hand-tracked grab, carry, drop and travel; a two-hand frame for screenshots | done |
| Takes: record head and every finger joint, review them in 4D, keep, play | done |
| Puppet maps: fingers as legs, your head as the camera, any rig driven by any part of you (control maps and follow mode are in; the head as the camera is not) | building |
| 4D editing: paths drawn as ghost trails, a scrub cursor, the same edit by mouse, keys or hands (the scrub bar and keys are in; editable ghost trails are not) | building |
| Physics you act: a thrown hat lands, stays editable as keys | planned |
| Faces from a phone video onto a character's expression units | idea |
| Full-body tracking hardware | idea |

## Agents in the room

| | status |
|---|---|
| Voice notes from the headset with what you were looking at and pointing to; spoken replies; instant acknowledgements | done |
| Panels an agent puts in front of you, answered by a gesture | done |
| Every gesture is also a call an agent can make; every take is data it can read and edit | building |
| Renders delivered to your wrist; pictures you can place in the world | done |

## Sound

| | status |
|---|---|
| Measured instruments from a few recordings (mimic), with a record of what each sound is modeled on | done |
| Notes nudged off the beat so a late attack lands on it | done |
| Anchors: a measured sound lands by its attack or a syllable; a voice's takes quantized to any rhythm without warping | planned |
| Played drum kits from multisample libraries, shaped toward a record (the first kit plays; shaping toward a record is not in) | building |
| Sampled library voices (keys, guitar, a drum kit, vinyl crackle), fetched only on the person's yes | done |
| Auto-tune: a voice moved onto the notes, as an effect or on a take | done |
| Named effect stacks, each with its cost on this machine | done |
| Provenance on three axes: what an instrument was measured from, whether any recording is kept, where a performance came from (the first axis is in) | building |
| Live: follow the speaker you connect, stream any bus into a world, knobs mapped to the mix | done |
| Live: pause and resume, a warning before the music runs out of changes, the cost of each track, priority for a set with an audience (the warning and priority are in; pause and resume as ops are not) | building |
| Soundscapes per place: a world sounds like where it is | idea |

## Pictures

| | status |
|---|---|
| Music videos synced to the notes | done |
| Look development as a method: eye exams for pictures, story frames, grade sheets | done (in the skill) |
| Grade as data applied by one function; colour kept by object, never by hue | planned |
| Animation from VR takes (frame-locked shots of takes at song time) | building |

## The person

| | status |
|---|---|
| The lexicon: your words for what you hear and see, mapped to what ismail does, kept as your culture and your learning curve | done |
| Objectives: what each piece is for, in your words, carried into every version made from it | done |
| Consent before anything measured from you leaves your machine | done |
| Readings of how people move through a world (where they stay, what pulls them back), for the person's own goals | idea |
| A first session for someone new: sound within minutes from a sketch, kept and mastered when they like it | done |

## The phone

| | status |
|---|---|
| A live set in your pocket: the music on your phone, with taps that steer it | done |
| Voice notes from the phone to the agents, and their replies | done |
| Panels on the phone: questions, pictures and video an agent puts in front of you | done |
| Listening exams on the phone, answers written to a file | done |
| Hum a part into the phone | done |

## Listening, and a hub for what people share

| | status |
|---|---|
| ismail as an ear: its measurements classify and compare sounds, so an agent can check what is submitted | planned |
| A hub of voices, instruments, presets and scenes that passed review, each with its provenance | idea |
| Review that combines measurement with people's ears | idea |

## The engine underneath

| | status |
|---|---|
| A shared-machine governor: heavy jobs take turns, a hot machine waits | done |
| Live plays a song exactly as the studio renders it (a standing check) | done |
| Render only what changed; a queue that waits instead of refusing (the queue is in) | building |
| A native live engine held to the studio by golden tests | planned |

## Join in

ismail is MIT licensed. Fork it, change it, and send a pull request. If your agent builds something that would help
others (a voice, an op, a scene, a fix), it should suggest that you share it, after checking it works beyond your
project and contains nothing you have not agreed to publish (`skills/ismail/references/development.md`, "The
inclusion review").
