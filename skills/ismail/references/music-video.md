# Music videos for ismail songs

`ismail.video` (optional: `pip install -e .[video]`, plus Blender 5.x and ffmpeg on PATH) turns a finished song into a music video whose every cut and glitch is placed from the song's own notes. You never watch video. You review stills and contact sheets, and the sync is exact because the event list comes from `proj/project.json`, not from audio detection.

Use it when the user asks for a music video, visualizer, or clip for a song made with ismail.

## Layout (everything lives next to the song)

```
songs/<slug>/video/
  config.json    title, master wav, named note families, blender path, pct, samples, font
  plan.md        the treatment (write it first)
  cut.py         the cut list + effects (ismail.video.edit.Cut)
  shots/*.py     one Blender script per shot (ismail.video.blender.kit), plus song-local helper modules
  rigs/*.json    characters: bone map, poses, eye textures, mounts
  assets/        models and textures (.dae / .obj folders)
  build/         x/ (extracted models), events.json, features.npz, look/ (stills, sheets), cache/, logs/
  renders/       shots/<name>_vN.mp4 (never overwritten), <Title> vN.mp4
```

`songs/` is git-ignored, so models and per-song work stay private. If another song already has a `video/` folder, read its `plan.md`, `cut.py` and two shot scripts before starting: that is the worked example.

## The CLI

```
python -m ismail.video init    -s songs/<slug>                    scaffold video/ (never overwrites)
python -m ismail.video rip     -s songs/<slug> video/assets/<dir>  .dae files or folders -> build/x
python -m ismail.video sync    -s songs/<slug>                    events.json + features.npz (rerun after any song change)
python -m ismail.video still   -s songs/<slug> s04_foyer.py 120 [-- --top cx,cy,span,z | --dbg nofog | --pct 25]
python -m ismail.video posesheet -s songs/<slug> s04_foyer.py [f,f]   each character alone, 4 sides, key frames + clipping
python -m ismail.video render  -s songs/<slug> s04_foyer.py s05_insert.py ... [--wait 2h]   (refuses on clipping; -- --allowclip)
python -m ismail.video contact -s songs/<slug> video/renders/shots/s04_foyer_v1.mp4 16
python -m ismail.video edit    -s songs/<slug> [-- --sheet 33 41 16 | -- --range 33 41]
```

`render` waits for the machine's GPU slot (`ismail.machine`, SKILL.md "The machine is shared") before each shot, so the board's rules hold for it as for any Blender render: one GPU job on the machine, none while the GPU is hot or a drive is nearly full, and none while a live set is on air. It stands in line up to 4 hours by default, long enough to outlast a live set, because a render queue is work the person walks away from (`--wait 12h` longer, `--wait 0` refuses at once). When the slot is refused it says why, names the shots not rendered and exits 75: read the board with `python -m ismail.machine`, then run those shots again. Do not wrap it in `machine run`; it takes the slot itself. `still` and `posesheet` take no slot (seconds of work). Run the module from a directory that is not the repo's parent, or the source folder can shadow the installed package.

## The loop

1. **Listen to the arrangement as data.** `sync`, then read `build/events.json`: tracks, notes as frames, families. Know the form map (intro, build, dropout, drops, break, the last-bar moment) by bar number before writing a single shot.
2. **Treatment** in `plan.md`: one-line story, hook words and the picture for each, look, timeline by bars, shot list with frames and start bars, and for each set and person what is missing (`stage.md`, "Ask what is missing, before the person does"): what you found, and the question to the user. Show it to the user before rendering anything expensive.
3. **Assets.** Models come from the user or from sources they approve. Always ask before downloading, and log each one (where from, author, licence) next to the song's `ref/SOURCES.md` so the video's credits can be written from it. `rip` the .dae files, put .obj folders under `assets/`.
4. **One shot at a time:** write the script, run `posesheet` until every row says clean (see Animation below), then take stills at 2 or 3 frames at `--pct 25`, read them, fix, and repeat. For a black or empty frame, use `--top` (an ortho plan view with the camera as a red dot and its target as cyan) and `--dbg nofog`. Only a shot whose stills are right goes to `render`.
5. **Render the queue** in the background (one `render` call with every approved shot). Contact-sheet each result as it lands.
6. **Cut** in `cut.py` against the bar grid, then `edit -- --sheet a b n` on every section and read the sheets. Fix, then run the full `edit`.
7. **Review the final** with `contact` over the whole render (about 40 frames), and report the file, its size and the sheet.

## Sync math

Frames per beat = fps * 60 / BPM (150 BPM at 30 fps: beat 12, 16th 3, bar 48, triplet quarter 8). `Cut.B(bar, beat)` and `Shot.events(track)` do this for you. A voice's articulation codes become note families, for example the built-in `growl` voice's `vel // 10` (1 yoi, 2 wub, 3 screech, 4 metal, 5 dive, 6 zap, 7 grind, 8 chop, 9 talk, 11 robot, 12 howl). Other named families go in `config.json`, for example `"families": {"growl": {"90": "name"}, "gun": {"10": "bang"}}`, and appear as `e['fam']`.

## Shots (Blender kit)

```python
import os, sys; sys.path.insert(0, os.environ['ISMAIL_VIDEO_KIT'])
import kit
S = kit.Shot('s04_foyer', 384, start_bar=9)       # frame 1 = the song frame of bar 9
room = S.room('Foyer'); Z = S.floor(0, 50)
L = S.character('hero', loc=(0, 34, Z), yaw=180)  # rigs/hero.json
L.pose(L.poses['scared'], frame=1); L.eyes('wide', 1)
S.flashlight(L); S.fog((0, 0, 40), (400, 400, 120)); S.light('SPOT', ...)
for e in S.events('kick'): S.flicker(lamp, [e['lf']])
S.camera(1, (0, -80, 30), (0, 30, 20), lens=18); S.camera(384, ...); S.handheld(0.3)
S.go()
```

- **Units.** Ripped rooms are often about 20x metres, sometimes 10x more. Write light energies as if metres and let `Shot.gain` (40) scale them (the sun is not scaled). Check scale with `S.bounds()` before placing anything.
- **Floors** are not the bounding-box bottom. Use `S.floor(x, y)` and `S.top(x, y)` (raycasts).
- **Posing** is by direction, never by guessed Euler angles: `('p', bone, child, [x, y, z])` points a bone at a direction in armature space, `('r', bone, axis, deg)` rotates. Write the axes convention of the rig in its `notes`.
- **Eyes** are texture swaps, keyed per frame (`L.eyes(state, frame)`).
- **Props** mount in world space along bone to child and are then parented (`S.mount`, `S.back_mount`). A light inside a glove is shadowed by it: push it forward (`fwd`).
- **Mirrors:** `S.cut_faces(room, pred)` removes the glass (test `abs(normal)`, since rip normals face either way), then add a mirrored copy of the room and a double under an empty scaled (1, -1, 1).
- **Sky domes** block lightning and sun: split the dome off by material and set it `visible_shadow = False`.
- Props that belong to one video (a tool from the source's world, a creature that must always face one way) go in a helper module in `shots/`, not in the kit.
- **Build in metres.** If a rip is thousands of units across (cm-like units), load it with `S.room(stem, scale=0.01)`, scale characters to real heights, pass `gain=1.0` and write lights in real watts. Blender reads 1 unit as 1 m: a 9000-unit arena is 9 km and no sane light reaches across it. `Shot.gain` is only for rooms near metre scale.
- **Raised stages:** `S.floor(x, y)` casts up from below and can return a platform's underside; `S.top(x, y, z_from)` from just above the floor is safer. `--top` markers scale with the view span.
- **A 2D layer inside the scene** (a screen, a UI, a sign): render it once for the whole song as a PNG sequence driven by the events (text typed on the song's blip notes, bars that drain on hits), then use it twice: as a screen texture (image sequence, `frame_offset` = the song frame of the shot's start, Closest interpolation) and as a flat full-screen source in the edit. Write each frame's brightness to a json and key the screen's glow light from it, so a flash on the screen lights the room. Area lights shine down their local -Z: flip one to face out of a screen.
- Song-local helper modules in `shots/` import directly (the kit puts that folder on the path).
- **GPU budget.** Shots render at `pct` 50 (960x540) with 16 EEVEE samples and are upscaled in the edit. A throttled consumer GPU manages about 1 to 2.5 s per frame, so a 384-frame shot takes 10 to 15 minutes. Budget the whole shot list before rendering, and check the GPU clock if times double.

## Animation (what separates a video from a slideshow of poses)

The first video posed characters by eye and shipped hands inside thighs and a head inside a backpack. The kit now checks
and fixes that, and makes motion read as motion (what a rig itself allows, wrists without twist bones, a posed body
that exports in its rest pose, and the clip checks are in `rigged-characters.md`):

- **Clipping is checked on the deformed mesh.** `C.clip(frames)` measures how deep body parts pass into each other (and
  into mounted props) as a fraction of the character's height, minus what already overlaps at rest. Joint contact the
  camera never sees (upper arm and chest, the thighs) is skipped (`JOINTS`, rig json "joints"). Arms against the torso
  get a softer tolerance (`SOFT`, 4.5%): a hanging arm should rest on the body and trace its contour, like flesh would.
  Hands in thighs and heads in props stay strict (1.2%).
- **`pose()` unclips by default:** the part that can move swings its mover bone a few degrees at a time and stops the
  moment it is back on the surface: contact, not clearance, and never more than 25 degrees per bone. What it cannot fix
  inside that budget is printed; fix the pose, do not raise the budget (a head that cannot look up past a backpack
  should lean from the chest).
- **Props are seated, never pushed.** `S.back_mount` slides the prop along the mount axis until it just touches the
  torso (overlap-based; ripped props often have flipped normals). Mount props BEFORE posing, so the pose respects them.
- **Corrective keys before every render:** interpolating between two clean poses can still swing a limb through the body, so `render` first samples up to 120 frames, runs `unclip` on any that clip and keys the fix on that frame (`FIXED n frames` in the log; `-- --nofix` skips it).
- **The render refuses** while any key frame clips (`-- --allowclip` to accept it knowingly). `posesheet` shows every
  keyed pose from four sides with the hits under each row: read it before any still.
- **Moves land on the note.** `C.hit(frame, windup, strike, after)`: windup held `antic` frames before, an ease-in
  snap so the fastest motion is the frame before the note, an overshoot two frames after, a settle.
- **Follow-through and life.** `C.spring(chain, [(frame, amp)])` rings ears, tails and capes after hits, each link
  lagging its parent; `C.breathe()` keeps a held pose from freezing. Both bake on top of the keys: call them last.

## The edit (Cut)

```python
from ismail.video.edit import Cut
C = Cut(__file__); B, ev = C.B, C.ev
C.shot('FOY', 's04_foyer', 384, gain=1.2)            # newest complete renders/shots/s04_foyer_vN.mp4
C.sections([(1, 17, .25), (33, 65, 1.0), ...])       # glitch intensity per section
C.seg(B(9), B(17), 'FOY')                             # bars 9-16, source frame 0 onward
for e in ev('lead', 12, 17): C.seg(e['f'], e['f'] + e['len'], 'INSERT', 20)   # an insert for exactly one note
C.cycle_cuts(B(25), B(29), [e['f'] for e in ev('snare', 25, 29)], ['A', 'B'], {'A': 0, 'B': 100})
C.tape_stop(B(134), B(136, 3), 'END', 240); C.crt_off = frame; C.end_title = (B(137, 2), 'TITLE')
C.auto_fx(); C.text(t0, t1, 'WORD', 96); C.main()
```

Sources are decoded on demand (forward reads, a seek on a jump, the last frames kept in memory), so a full-song source costs no disk. Placed audio clips (speech, samples) sync as events too, with `fam` = the sound's name. `C.hold(effect, t0, t1, amp)` holds an effect over a stretch; `palette` maps every pixel to the nearest shade of `C.palette` (a list of RGB colours, dark to light; default four greys), for a limited-colour look. Effects follow the note families (`C.fx_map`; the defaults in `FX_MAP` are keyed to the `growl` voice's families): wub remap, dive melt, grind datamosh, robot pixelation, screech tear, metal posterize plus edges, zap invert, howl ghost double exposure (`C.ghost_key`), talk chroma roll, chop stutter. Kicks zoom-punch, sub shakes, snares and crashes flash. Everything sits under a VHS grade (lifted blacks, scanlines, vignette, grain), and `rain=True` adds a rain layer to a shot. Output: 1080p H.264 crf 18 with maxrate 24M plus the master as AAC 320k. Grain must be in the frames, not from an ffmpeg noise filter, or the file triples in size.

## What made it work (creative rules)

- **The cast comes from the story, never from the track list.** A song's tracks (rhythm guitar, strings, pads)
  describe the mix: in one video the strings were the power lines singing and the rhythm guitar a layer nobody
  plays on screen. Spend by the cast's order: the main character gets the most exams and shots, the secondary
  one full design, background people the cheap path (stock variation, silhouettes, faces lit away). Reread the
  story before designing anyone; `PROGRESS.md` names who matters.

- **The story comes from the source's own lore**, so each shot means something to someone who knows it. The song's hook words each get one picture, shown exactly for the note's length.
- **Cut rate is the arrangement.** Long shots in the intro, snare rolls set the cut rate in builds, triplet hooks cut on every hit, a drop's own rhythm sets its cadence, and a final section can strobe on a few frames.
- **The dropout bar before a drop** is black, then one held image and the title.
- **The last bar of the final build** gets its own shot.
- **The ending pays off the opening**: an image or a device from the first shot comes back, changed, in the last.
- **Subliminals:** single frames of the payoff image on a steady pulse in the break.
- **The person's words steer the edit.** Note what they say about a cut verbatim (`lexicon_note`, in
  `user-experience.md`, "The lexicon: their words, our terms") and say changes back their way.
- **A live set's best moments are already marked**: the highlights cut when the person praised something
  (`live.md`, "Highlights on praise") and their index are where a video of a set starts.
- **The music is the master as it is.** When the bed must sit at another level, one fixed gain, never a loudness
  normalizer (`loudness.md`).
- **Brightness:** the first cut was too dark to read on a phone. Aim for readable mids and let the grade darken the edges.
- **Avoid:** real people's likenesses and depicted violence against people. Keep the menace in the music, the monsters and the edit.
