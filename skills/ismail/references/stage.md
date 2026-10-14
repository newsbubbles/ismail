# The stage: building a scene with a person inside it

A scene for a video or an interactive world gets made by two kinds of work at once. The agent builds, measures and
renders; the person stands in the scene (a browser, or a headset) and places, judges and acts. This reference is
what one long build of a 1958 bar and a club, with a VR editor beside Blender, taught about doing that well. Read it
before building any scene, look or editor a person will work inside, and with `music-video.md` (shots and the cut)
and `user-experience.md` (their words, kept in the lexicon with `lexicon_note`, their senses and consent).

The stage itself (a browser and WebXR editor with a Blender round trip, hands, voice and panels) is part of ismail:
`ismail.stage`, driven by the `stage_*` tools (below). Everything after that section holds for any editor where a
person and an agent change the same scene.

## Running the stage

- **Scenes live in the song:** `<song>/video/vr/scenes/<name>/` holds `scene.glb` and `manifest.json` (from the
  Blender bridge), `edits.json` (what the person moved), `world.json` (who plays whom, facings, partners, the floor,
  keep-out boxes, spawn, credits, and `build`: the room script that exports it), waypoints, cues, takes, voice
  notes and snapshots. `scenes/stage.json` may name the default scene. Takes, voice and snapshots are the person's:
  never share them by default.
- **Start:** `stage_start(scenes="<song>/video/vr/scenes")` replies with the address. Open
  `<address>?scene=<name>` on the desktop, or in the headset through `tailscale serve` (https). One server per port;
  `stage_status` lists servers, scenes, which pages are live and the server's health (workers busy, long-polls,
  threads, free disk; GET /health).
- **Drive:** each page command is a typed tool that waits for the page's answer: `stage_object_set`,
  `stage_object_select`, `stage_say`, `stage_panel_show`, `stage_waypoint_set`, `stage_actor_play`, `stage_stream`,
  `stage_scene_go` and the rest. A tool fails with the next step when no page shows the scene; `stage_cmd` is only
  for a command that has no tool yet.
- **Live edits go through named ops only.** From a shell that is `python -m ismail <op> key=value ...` (e.g.
  `python -m ismail stage_say scene=room text="..."`), or several at once with `python -m ismail stage_batch
  @moment.json` (the file holds `{"scene": ..., "ops": [...]}`). Never an inline script (`python -c`, a scratch
  file) that posts to the page or edits the scene's files: it skips the checks, the undo, the save and the events
  the op gives, and it needs a fresh permission every time. If the edit you need has no op, that is the gap to
  fix: add the op (and its page command), rather than scripting around it.
  **Every off-path form has a named op**; a blocked command prompts for permission, and a prompt in the headset
  throws the person out of VR:

  | Blocked form | Named op |
  |---|---|
  | `python -c` (or a heredoc) with ismail code | `python -m ismail <op> key=value` (`python -m ismail ops` lists them) |
  | `cp`, `mv`, `Copy-Item`, `robocopy` or `shutil` of a file into a scenes folder | `stage_asset_install(scene, file, kind=scene, manifest, actor or tree, name=)` |
  | `Stop-Process`, `taskkill` or `kill` of the stage server or a live engine | `stage_restart(port=)` (waits while they are in VR; `now=True` after a yes from `stage_ask`), `stage_stop`, `live_stop(project=)` |
  | editing `world.json` by hand | `stage_world(scene, update={...})`: only the keys given change (`world=` replaces the whole file and resets keys left out) |
  | reading voice notes by tailing files | `stage_listen` or `stage_events(scene, types=["voice_message"], wait=)` |

  `stage_asset_install` keeps the file it replaces in the scene's `history/` and tells an open page (a new room or
  piece list reloads the room, so ask first in VR; a replaced body shows at the next page load). `stage_restart`
  restarts the server with the arguments it was started with, refuses while a page is in VR (and says so in the
  headset), and replies once `/health` answers. An optional guard for Claude Code refuses these forms before they
  run and names the op instead: `skills/ismail/hooks/README.md`. Other harnesses get the same rule from this table.
- **Edits apply in place while they are in the room; nothing needs a reload for them.** Objects
  (`stage_object_set`, `stage_object_drop`), lights (`stage_light_set`), material colours (`stage_material_set`:
  every mesh with that material changes), captions and cards, the sky, actors, load sets, props and `stage_world`
  (world.json: an open page reads it again at once and redoes what was built from it). A piece the headset export
  merged into its group (the Quest diet joins each group into one mesh) is split back out the first time an op
  names it, with its children, and then moves, saves and undoes like any object. What still needs the room
  rebuilt (`stage_scene_export`, swapped in under the construct, so only when they are out or ask): new or changed
  geometry, modelling, modifiers, textures and any material change other than the base colour, a new object, and
  a new merged piece list (a room exported before pieces were numbered splits nothing until its next export).
- **Listen:** `stage_events(scene, since=, types=[...], wait=)` is what the person did and said: `voice_in` the
  moment a voice note lands (answer "got it" then), `voice_message` with its text and what they pointed at,
  `gesture`, `select`, `transform_end`, `cmd_done`, `page_error`, frame beats in the client log.
- **Verify from their side** before saying done: the reply's position, a `stage_view_snapshot`, or the event that
  proves it. Say "not verified yet" otherwise.
- **Rebuild a room:** `stage_scene_export(scene)` runs `world.json` `build.script` in Blender in a heavy-job slot.
  An open page swaps it in under the construct, never while the person is in VR.
- **Blender or a script outside the stage tools** runs through `python -m ismail.machine run --gpu -- <command>`, so
  it takes the machine's GPU slot like the stage's own jobs (SKILL.md, "The machine is shared").
- **Changes they will notice:** `stage_note(scenes, title, level)` feeds the "updates ready" card in the headset.
- **Capture a shot on the PC:** `stage_capture(scene, t0, t1, camera=|shot= cameras=|path=|view=, size=, fps=,
  setup=, audio=)` draws the stage frame-locked (frame n is the song at t0 + n / fps, however slow the frame) in a
  headless browser with its own server, in the background on the GPU slot, and writes
  `<scene>/captures/<job>/capture.mp4` (PNGs with `keep_frames`). No headset, and the stage the person uses is
  untouched: the capture's page saves nothing and takes no live commands, so per-shot changes (a take playing, the
  sky, a light, a set unloaded) go in `setup` as live commands. Every load set is in the shot unless
  `sets=False`. `stage_capture_status(scene, job)` follows it. Timers inside behaviours (setTimeout) still run on
  the wall clock.

## Listening while the person is in

The person once spent nine minutes in the headset, sent six voice notes and finally asked why nobody was talking
back: no agent was polling, so nobody heard. The live link is pull only, so listening is
something you do.

- **Listen before you tell them to go in.** `stage_listen(who="<your name>")` in a loop (pass `since=` the `last`
  it returned; it waits up to `wait=` seconds), or `stage_events(scene=, since=)` on one scene. While you call it at
  least every 40 s the headset shows "listening: <your name>" on entering VR and whenever that changes; with nobody
  it shows "nobody is listening" in amber. `stage_presence()` says whether they are in, where, the last note and
  who is listening. Read every event since the last one you handled, and reconnect on your own: a server restart
  drops every listener.
- **Keep an ear running while you work.** A loop you run between other calls stops whenever a call blocks: one
  session sat ten minutes on a render while four notes went unanswered, and the person left the headset.
  `python -m ismail.stage.ear --who "<your name>"` keeps listening on its own: run it in the background and read its
  output as it comes (it prints one line per voice note or error and logs the rest); it survives server restarts
  and resumes where it stopped. `--once` exits after the first printed line, for a harness that only wakes you when
  a background job ends; then start it again at once.
- **Answer within a minute, or say you are busy.** The headset shows "heard by <your name>" the moment you are
  handed a note. Before anything that can wait on something you do not control (a queue, the GPU, the network, a
  long search), set a busy line: `stage_busy(who=, text="rendering the drums, in line, about 5 min")`; it shows
  beside the receipts, and `text=""` clears it. A note no agent answered (`stage_say`, `stage_ask`, a panel; the
  "got it" ack does not count) within 2 minutes, with no busy line from those who took it, gets "<your name> has
  not answered for 2 minutes. It may be stuck." out loud and a `session_stuck` event.
- **No silent notes.** A note you were handed but did not answer (`stage_say`, `stage_voice_ack`) within 6 s is
  announced as "Handed to <your name>", so they hear it landed and was not yet answered; a note nobody was handed gets "Nobody is listening right now. I saved your
  note." out loud and goes to `_stage/unread.jsonl`. Read the unread notes when you start listening.
- **Hooks** start an agent when nobody is running one: `~/.ismail/stage_hooks.json`,
  `{"entered_vr": [...], "left_vr": [...], "voice_note": [...], "voice_unheard": [...], "session_stuck": [...]}`,
  each a shell command (or an argv list) run with `STAGE_EVENT`, `STAGE_SCENE`, `STAGE_TEXT`, `STAGE_FILE`,
  `STAGE_EVENT_ID`, `STAGE_URL`, `STAGE_SCENES` (and `STAGE_WHO` for session_stuck) in its environment (a toast, a webhook, `claude -p "..."`, `codex exec "..."`); output goes to
  `_stage/hooks.log`. A song's own `_stage/hooks.json` runs only when the home file lists its scenes folder in
  `"trust": [...]`: a scenes folder travels, and must never run commands for whoever opens it.

## Deriving a scene

A remodel, another era, the same place at dawn: a scene made from another is derived, never rebuilt. One build of a
club "today" was dressed on the first grey blockout of the 1958 bar instead of the bar as built, and the person saw
crude stools, no bottles, block trees and cylinder people: every lesson already learned in the built room was lost.

- **Start from the built version, and say which base you use.** `stage_scene_new(name, source=, pass_script=)`
  writes `derives_from` into the new scene's world.json and copies the page's own pieces (trees, names, cues,
  waypoints; the actor bodies come from the source through `assets`). The person's takes, voice notes and snapshots
  stay with the source.
- **The variant is a pass on the built room,** a script run after the source's full build and before the Quest diet
  (`pass` in world.json, `pass_env` for its switches). It removes, adds and repaints what changed; everything else
  is the source's. `stage_scene_export` runs the whole line (the source's build, every pass oldest first, the
  edits of the line merged with the variant's winning, the diet). Room scripts exec `os.environ["VR_BRIDGE"]` at
  their end so the passes and the diet run. A room with its own Quest merge defines `stage_diet()` and skips that
  merge in its build when `VR_DIET` is set: a pass that runs after the merge finds one joined mesh, not the stools.
- **Check it beside its source from the same camera** before the person sees it: the same view of both, side by
  side, and look for anything that went back to a blockout.
- **Renders of a scene go in `renders/<scene>/`** (a script reads `STAGE_SCENE`), so a camera both scenes share
  never overwrites the other's still.
- **A direction note is a direction, not an open question.** If the person said the place expanded, build it
  expanded; ask only what the notes leave open.

## Choose the surface for the decision

Each kind of decision has a place where the person can judge it fastest. Use that one, and say why.

| decision | surface |
|---|---|
| a look (skin, a material, a grade, light) | a sheet of stills, each lens changing one named thing, in the final grade |
| placement and layout (where a camera, a chair, a person goes) | the stage, in the browser or the headset |
| the final look of a shot | a real render, never the stage's preview |
| a set of numbers with sliders (a grade, a zone readout) | a hosted page whose Save writes the numbers the build applies |
| a quick verdict from inside the headset | a panel answered by a gesture or a button |

When the app's own preview panes are full, serve the page from your own local server and give the person the link.

## Looks: the eye exam, and what it taught

The method is the eye exam in `blind-tests.md`: lenses that each change one named thing against a fixed base, the
current state included, two or three numbered questions, answers kept word for word, a lock carried into the build.
For pictures:

- **Show the thing before asking about it.** A question about an object the person has not seen wastes the round.
- **Say what a sheet holds fixed on purpose.** A sheet that kept one light so only the face changed was read as a
  failed lighting test; lighting had its own exam.
- **Judge in the final context.** Combine the leading lenses into the shots they serve, in the era's grade (a red scar
  vanishes in black and white), at the real framing. A swatch is not a shot.
- **Look at your own sheet first.** Fix what you can see (a bald patch, a camera inside a dancer, weeds through a
  floor) before sending it, and write what you saw.
- **Big moves.** Small differences read as the same picture. A change of mood needs big moves in space and light,
  not in one material.
- **Lock as a constant, keep the lenses.** A locked choice becomes a named constant in the build, and every old lens
  stays renderable by its name, so a later "go back to C" costs nothing.
- **Go wide when the person is away.** Prepare several independent exams so one answer settles many things.
- **Give each subject its own exam letter.** Two exams named S1 cost a round of confusion.

## Ask what is missing, before the person does

In the 1958 cafe the pool table had its balls and no cues. The person found that, and most other gaps, by walking in
with a list of what was missing: the first one to ask "what is missing here?" was the person, not the agent that
built the room. Ask it yourself first, while designing and again before they go in, then ask them.

- **Ask yourself, object by object and person by person.** What goes with this thing in use (a pool table: cues, a
  rack, chalk, a cue rack on the wall; a stage: cables, stands, a set list taped down; a bar: what is on it at this
  hour)? What is each person holding, wearing, carrying? What does the room need to work (light sources, a way in,
  signage, air in a Mississippi summer)? What would someone who worked there notice first? What did the era have,
  and not have?
- **Then ask the story.** The notes, the treatment and the lineage (a remodel keeps what survived, loses what was
  cut down) say what must be there; read them again with the list in hand.
- **Write the list down and sort it**: build now (cheap and in frame), keep as a note (in `PROGRESS.md`, under the
  scene), or ask. Missing does not mean build everything: naming a gap is the job, the person decides the scope.
- **Then ask the person** with the list in hand: "here is what I think is missing; what else?", before they go in.
  Their walk-through should find what only they can know, not the cues.


## Bodies, props and contact

Rigged human bodies (export, wrists, start poses, hands, props on bones, following a person, the clip checks) have
their own reference: `rigged-characters.md`. Read it before exporting, posing or following anyone.

- **Fix locally from the state the person liked.** When a liked pose has one flaw, restore that pose and change only
  that, the way a body would (roll the shoulder, sink into the seat). Blind searches over many joints drift into
  contortion. Look at a render before the third search.
- **Score the look, not only the constraint.** Zero clipping bought with a winged elbow is a failure. Measure the
  cause first; measure contact on the skin, not on the bones.
- **Clip-check every contact** with a close-up render, at the keys and between them: chairs in walls, hands through
  partners, a mic floating off its stand, a bottle hovering over a table.
- **Measure references from photos** of real people doing the thing (a hand landmark model gives joint bends) rather
  than inventing a grip.
- **Quiet set dressing.** Surfaces in most shots must be dull to the eye; a busy floor or wallpaper steals every
  frame. Keep colour by a matte of the named objects, never by hue: a coloured key light makes everything of that hue
  keep its colour.
- **No real likenesses** of public people (`music-video.md`, "Avoid").

## When the person is inside the work

A person in a headset cannot see your terminal. Contact is part of the interface:

- **Answer at once, then briefly.** Play a short recorded acknowledgement the moment a message arrives (a generated
  one can arrive a minute late), then short spoken replies. Long silence reads as being left alone.
- **Capture what they point at when they speak.** The view and the selection at the moment a voice note ends belong
  to the note; reading them later shows a different moment.
- **Let them interrupt**, and keep what they did not hear for later.
- **Queue your lines; keep a caption up until its line has been spoken.** Generated speech can take a minute on a
  busy machine: lines that pile up play on top of each other, a card that vanishes first is never read, and talking
  over a recording ruins the take.
- **Every interaction is a sound too.** A countdown ticks, a shutter fires, a lock clicks, a drop lands. A timed
  action with no sound left the person guessing when the camera would fire.
- **One sound, one meaning, and a sound always comes with something to see.** One chime that meant two things left
  the person hearing beeps with nothing in front of them. Never start music or a stream in the room without saying
  so first.
- **Things carry the names the person uses.** Keep a names map (scene item to the words the person and you say),
  so "the jukebox" means one object to both of you.
- **Teach the stage the first time.** With no history of the person on the stage, or when the events show a
  struggle (selects with no action, a grab that moves nothing, "how do I"), offer a spoken tutorial one gesture at
  a time, each step waiting for the event that proves it worked, each with its sound: talk, point and select, move
  a thing, move yourself, take a picture, set a camera, record a take, housekeeping. Keep the gesture list in one
  place the page and you both read, so a new gesture joins the tutorial when it ships.
- **Show which version is running** (a stamp on the page). After a change, reload to the plain scene address: on
  one headset an address with a fresh cache stamp hung while the plain one loaded.
- **Nothing reloads, re-exports or hitches while they are in, unless they ask.** Lag makes a person in a headset
  sick. Changes to the world stream in; code waits for their update, and its note says what they will notice.
  Measure comfort, never assume it: read the frame times after every export and scene change.
- **Give every run a purpose.** When they go in, what to check is already waiting as pins or a card.
- **A card that only informs never blocks commands**, and a hand reading taken near a panel is not a placement.
- **Panels appear where they belong and stay there**; opening, closing or stepping through one never puts the
  person inside a wall or a performer.
- **Anything shown in the room can be touched by hand**, not only reached by the agent, and no gesture opens
  anything by accident.
- **When two sessions can hear the person, settle who answers**, so a note is answered once.
- **Address every note.** After a session, check every message against what was done and say what was not.
- **Measure a gesture from a take before binding it.** A pinch that the system already uses collides with yours
  (one pinch once had six jobs and opened a palette while the person's hands were busy);
  gestures are personal, so calibrate them per person, and keep their recordings local without consent.
- **The gesture map is data, read it before you name a gesture.** `stage_gestures(scene)` lists what each hand shape
  means in VR now (the intent, shape, hand, hold, where it is read, and whose row it is). `stage_gesture_set(scene,
  intent, shape=, hand=, hold_ms=, off=)` rebinds one in the person's own map (it follows them into every scene), or
  with `owner="scene"` in this scene's rows (world.json `gestures`; the person's rows win). The page takes it at
  once. A rebind that would share a shape with another row where both are read is refused, naming that row.
  Leaving VR, the performance stop and the voice note are locked (never rebound or off, so nobody is trapped);
  travel, the snapshot frame, select and hold are fixed to how the shape moves. The yes and no answers can be
  rebound but not switched off (`stage_ask` waits on them). `stage_gesture_reset` goes back.
- **Keep tooling in service of the work.** An editor feature is worth building when the scene waits on it; when the
  scene is waiting for layout, build the scene.
- **The scene's content is yours; how the stage works is the stage dev's.** Models, lights, the street, music, the
  environment: change them yourself. The page, gestures, panels, voice, the server: the stage dev.
- **Findings go to the handoff; during a live test, straight to the stage dev.** A gap in the engine goes in the
  project's HANDOFF.md. While the person is in the stage testing with you, send what they ask of the stage directly
  as a message to the stage dev's session, conversation or agent, not through a handoff. If no stage dev is
  running, start one yourself (a new session, conversation or agent with the ismail skill, asked to be the stage
  dev, with what the person asked), so the person in the headset never waits on "the dev needs to do it". The
  stage dev sends its change to the contributor dev as a local merge request (`development.md`, the roles). When
  the change touches
  the page's code, the stage dev messages you back that an update is ready; tell the person in the room, and they
  take it with the update gesture (a left-hand thumbs up on the updates card).
- **A message to the person rides with them; a note about a place stays there.** `stage_panel_show(anchor="body")`
  puts it just out of view beside where their body faces (not their head: looking left or right finds it, looking
  ahead does not), with `seconds` or until they close it, and never holds your command queue. Your panels and speech
  wait while they talk, and for 3 s after a note, because they often send the next one at once. Menus they open
  themselves never wait, and nothing times out while they talk. A voice note's `during` lists what they did while it
  recorded (selections, menus shown and pressed, presses on things), so "this one" can be read without the event log.
- **A sign-off in the headset is a right thumbs up** (the map's `answer_yes`; `stage_gestures` says if the person
  moved it). Before anything that hitches or changes the room around them (a reload, a re-export, a scene switch),
  ask with `stage_ask`: right thumbs up is yes, down is no. Never wait for them to say a word for it, and never use
  the left hand, which takes updates. An open ask holds no other command. An ask (or a yes/no panel) that a standing
  grant already answers is refused, and text naming a private term is refused on every op that shows it: phone.md,
  "Nothing personal on the page" and "Don't ask for what is granted".
- **Seat a person before they are followed.** `stage_follow_anchor(person, joint="hips", to="<their seat>")` keeps
  them seated while the person drives them sitting or standing; a Follow keeps nothing, so keep a good one with
  `stage_take_keep_last`. Leave everyone else unpinned: a Follow sets itself up from the body (seated stays on the
  seat, standing walks with the person, and a lean or a look aside bends the body without moving the feet), and the
  Follow panel says the setup in a sentence. Pin feet only when the person asks for it.
- **Test on a copy, or move on trial.** A plain `stage_object_set` saves into the scene's edits, which the build
  reads; `trial=True` moves it without saving. Camera moves keyed on the clock want `stage_key_interp(mode="smooth")`.
  A whole move goes in one call: `stage_keys_set(keys=[{name, t, location, look}, ...], interp={name: "smooth"})`
  keys from values (`look` aims a camera at a point) without moving anything to each spot first.
- **A camera move is seen and recorded in the room.** `stage_trail(object=<camera>)` draws its path on the clock
  (blue at the start to orange at the end, a small frustum at each key, a bright ball where the playhead puts it); in
  VR a key picked on the ray scrubs the clock there, and carried, it moves (its time stays; `stage_anim_save` keeps
  it). The camera's menu has the same: Path, and Record move. `stage_camera_record(camera, t0, t1)` records a move
  like a take: a countdown, then the clock plays and the camera is the person's eyes (`source="head"`, hidden while
  it records) or wherever it is carried (`source="hold"`: unlocked with Move first). The samples are smoothed and
  thinned to a few smooth keys (within 2 cm and 2 degrees) that replace the camera's keys between t0 and the last
  sample; keys outside that window stay. A Keep / Redo / Undo panel follows; nothing is saved before Keep. Watch
  the `cam_record` events, and do not talk over a take.
- **A Follow is a performance: be quick and quiet in it.** From the moment someone follows the person, the mic
  records in clips and their gestures do nothing. Watch the `perform_*` events; when a part is worth reading,
  `stage_perform(action="next_clip")` cuts the clip there (its words come back as `perform_clip`, on the Follow
  clock) while the next one records. Mark moments with `action="mark"`. Do not speak (a line is shown, not said,
  unless `aloud=True` because they asked you something). After the Follow it plays back on the person with the
  voice; read the whole thing with `stage_performance`.
- **A performance always has a way out.** Clips cut themselves at pauses, so their words reach `stage_listen` while
  it runs: listen during it, and when they ask to stop, `stage_perform(action="stop")` (it ends the Follow and a take
  with it). They can also stop it themselves: both thumbs down held, saying "stop the performance", or Stop on the
  Follow panel. A take borrowed onto another body plays silent unless `stage_actor_play(voice=True)`.
- **Stepping into a person** puts the person's eyes where the person's are: `stage_embody(person, when="next")` makes
  the next Follow or Take on them start inside (their menu's "Step in" does it with a take), `when="now"` counts down
  and steps in. Inside, the view is clean (no panels, beams, labels or their own hand models, so a headset recording
  can be filmed), and they stop it like any performance (both thumbs down held). They are put back where they stood
  and the take plays on the person for keep or redo. Tell them how to stop before they step in: there is no panel
  inside. `eyes="floor"` keeps their own height instead of the person's.
- **Give the person the control they need, and change it as you go.** Ask how they want to drive a part
  ("his feet with your hands?"), then `stage_control_set` it: `effector` for an arm or a leg (relative: their hand's
  motion becomes his foot's), `touch=True` so they start when ready, `pin` to keep a hand on the bar, `hold` to keep
  a part still, `mimic` for a turn (a fingertip into a tail). Seated is a hips pin plus the legs' drives. When a map
  works, save it to the actor (`stage_actor_map_save`); a "default" map is ready every Follow.
- **Find takes by what was said.** The person names takes and talks about them while recording; every take keeps
  those words. `stage_takes(query="glass")` finds them with the second each word was said; `stage_take_note` names
  a take or adds your own note.
- **Put dances on the beat by number.** `stage_take_sync(scene, takes, bpm)` reads each take's pulse, its own BPM,
  the rate that puts it on the beat, its seam, and the lag between takes; `loops=True` lists the best whole-bar
  windows of a long take (clear pulse on the beat, small seam, little travel). `stage_take_loop(take, name, bpm=)`
  cuts the best one into a seamless silent loop (or `start=`, `end=`), and `stage_take_warp(take, name, bpm, bars)`
  moves its hits onto the beats. Play it with `stage_actor_play(take=, loop=True, at_music=)` so it stays on the song.
- **Hand the take loop to a helper when you are busy.** `stage-takes.md` has the brief: the helper keeps the fast
  loop with the person, you keep the scene.

- **Start the person from their own pose.** `stage_actor_start(scene, person, pose)` gives them a start pose (a
  frame of a take, or a pose exported from Blender: the actor's rest is an A-pose standing at the origin, not the
  pose the scene shows); every Follow and playback then starts there, and the user's motion plays as changes from
  their pose at GO. Check it by number with `stage_actor_pose` (the hands on the bar top, the pelvis over the seat).
  With a start pose they also rest in it whenever nothing plays on them, instead of the statue baked into the scene.
  From GO their hands and fingers are the user's own, placed from the eyes and turned to the person's facing, and the
  shoulders follow the arms. For a grip that must hold (sticks, a guitar neck), `hands='pose'` keeps the start pose's
  hands and adds the user's motion to them.
- **Worn parts come off.** A piece of costume that is its own mesh in the body's file (glasses, a mask, a hat) can be
  hidden or slid off: `stage_actor_part(scene, person, part='glasses', show=False)`, then
  `stage_actor_part(scene, person, part='mask', move=[0, -0.25, 0.05], seconds=1.2, show=False)` slides the mask
  down and a little forward (metres: to the person's side, up, front) and hides it when it gets there. The piece rides
  the bone it is worn on while it moves, so it keeps nodding with the head. A wrong name lists the body's meshes.
  It holds while the page is open (a rest or a take shows it the same way), is not saved with the scene and is not
  in takes; `reset=True` puts everything back. Event `actor_part` when a slide ends.
- **Props in a person's hands.** Place the prop where it belongs on the person (in the hand, on the strap), then
  `stage_prop_pin(scene, object, person, bone='hand_r')`: it rides their body from then on, at rest, in a take and in
  a Follow, and is saved with the scene. `clear=True` unpins it.
- **Grab by touch (in VR, hand tracking).** `stage_grab_mode(scene, on=True, objects="unlocked", hold_s=1.5,
  release_s=1.0)`: a hand takes a thing by resting on it, the palm or the finger bases touching it and the hand nearly
  still, for `hold_s` (a ring fills; a touch or stillness lost for under 0.4 s pauses the ring instead of
  restarting it); it holds it until the hand opens past half its grip for `release_s` (a ring empties; closing again
  keeps it). The grip counts as at least half closed, so a thing taken with an open palm drops when the hand stays
  open flat: close the fingers around it to carry it. A hand takes the outermost group up to 2.2 m across (a mic
  stand, a chair, a pool ball under its table's group; never the table), unless a rule says `grab=True`. Let go, it falls onto what is under it with the hand's speed, so a toss flies.
  Fingertips alone stay the pinch for menus. Inside a person (`stage_embody`) the person's hand holds it, and the grab
  and the drop go into the take and play back with it. `objects` can be a list of names or globs (`["drum_stick_*"]`).
  `stage_grab_rule(scene, object, grab=True, fixed=False)` sets one thing: `fixed=True` for a bar or a rail the hand
  holds on to without moving it (pulling 6 cm off lets go), `grab=False` for one never taken. Grabbed things are never
  saved; the edits file keeps them where they stood. Events `grab_counting`, `grabbed`, `grab_releasing`,
  `grab_released`, `grab_landed`. Phase 1 limits: a box around each mesh (fine for sticks, bottles and bars), and a
  thing in flight only meets what is under it, not walls beside it.
- **A person who sings or speaks (lip sync).** Export their body with mouth shapes kept (mhx `export --morphs mouth`;
  a step that slims the body for the headset has to keep them too, with every weight at 0; a body without them is
  left alone and the page says so once, `face_unsupported`), then `stage_face(scene, person, at=<song second>, audio=<vocal stem>, start=, end=, words=[{word, start, end}])`
  puts that stretch of the voice on their mouth while the song plays (or `take=<id>` for a take's own mic and words;
  `transcribe=True` asks the speech server for the words). The reply shows the track as a mouth shape letter and a
  jaw digit per 0.1 s; read it against the words before you film. Without words the jaw only follows loudness, which
  reads as chewing on a mixed recording: use a vocal stem and words.
  With Rhubarb Lip Sync on the machine (free, MIT, about 87 MB for Windows: unpacked in `~/.ismail/tools/rhubarb/`
  or named by `ISMAIL_RHUBARB`; download it only on the person's yes), `stage_face` hears the mouth shapes in the
  voice itself: the timing follows the sounds and pauses, in any language and in singing, at about 4 times faster
  than real time. `engine='rhubarb_english'` uses its English recognizer guided by the words (slower than real
  time), `engine='words'` the words only. The reply says which one made the track, and why Rhubarb did not run.
- **Keep the headset light with load sets.** When the work moves to one group (the band), unload the others (the
  dancers): `stage_set_define(scene, 'dancers', items=['person_couple_*'], note='the six dancers')` once, then
  `stage_set_load(scene, 'dancers', loaded=False)`. They are not drawn, compiled or uploaded, their people neither
  play nor rest, and a ghost box per member with the set's name stands in their place, so the room still says what
  is in the film. The page replies with the meshes and triangles it took out; load them again before a take with them.
- **Set up a moment in one call.** `stage_batch(scene, ops=[...])` runs several stage ops in order (seat a person,
  mark the clip, show a card): the page commands land together, and a failure stops the rest.
- **Say who you are.** Pass `sender=` (your name as the person knows it, e.g. "crossroads film") on `stage_say` and
  `stage_panel_show`: the card shows it in your colour, on your side. Several agents can be talking to them at once.

## Things that do things: save the work as code

When the person presses something in the room (a light switch, a door, a radio), the room answers by itself, at once,
with no agent in the loop. You write what each thing does; the stage runs it. The person, 2026-10-08: a light switch
should flip the lights "directly because the film agent was able to put code into the switch", and every press that
needs a model to listen spends tokens on work that could have been saved as deterministic code.

- **Where it lives.** `scenes/<scene>/behaviours.js`, plain JavaScript with no imports: `export default {
  object_name: { sound, state, apply(s), press(s), menu: {label: (s) => ...}, inputs: {name: (s, value) => ...} } }`.
  It reloads with the scene; after writing it, call `stage_behaviours(scene, reload=True)` and read the errors and
  warnings.
- **Listen for `behaviour_error`.** Every failure, at load or when someone presses the thing, goes out as
  `behaviour_error {object, phase, action, error, line}` (line in behaviours.js), so the agent that wrote the thing can
  fix it while the person is still there. The person, after a touch did nothing: "you should have an event hook on
  object functionality errors". In VR a failed press buzzes and shows a short note at the object, so a broken switch
  never passes for one that is off.
- **State, then room.** Keep what a thing is in `state` (`{club: true}`, `{open: false}`) and put the state-to-room
  mapping in `apply(s)`: it runs at load and after every change, so a reload, a scene switch or an agent's
  `stage_behaviour_state` shows the right room. `press` and menu items only change state. State is saved with the
  scene.
- **What `s` can do.** `get`, `set`, `state` (another object's), `send` (wire to another object's input, like a
  switch to a lamp), `light` (watts and colour, with `seconds` to fade; `energy: null` puts a light back as built),
  `show`, `move` (`by`, `to`, `turn` in degrees, `seconds`), `sound`, `emit` (a message to agents: `object_message`),
  `do` (any page command, the same ones you send), `after`, `every`. Names take a list or a glob (`cf_floor_*`).
- **Every interaction sounds.** Give each thing a `sound`: a file under the scene folder, rendered with ismail (a
  switch should sound like a switch). Without one the page plays a plain click and warns you. The same goes for
  anything else you build that the person touches: no silent controls.
- **Turn hand work into functions.** When you have just lit, moved or dressed something by hand with stage ops and the
  person will want it again, write it into the thing that should do it. Test it the way they will use it:
  `stage_behaviour_run(scene, object, action)` runs the same function and plays its sound.
- **Run time is not an edit.** What a behaviour changes (lights, moves, visibility) is never saved into the scene's
  edits or the Blender build; the scene keeps how it was built and edited, and the behaviour state says what the
  switch is doing now.
- **Where the line is.** The stage (its runtime and the `s` calls) is the dev's; what each thing in a scene does is
  yours. If a behaviour needs a call `s` does not have, ask the stage owner for it instead of working around it.

## Shared editing: never lose what the person did

Every rule here cost the person work once.

- **Saves merge.** A save that overwrites the scene file drops what the other side placed. Keep a history copy of
  every save.
- **The event log is the recovery path.** Log every edit as it happens; a lost save can be rebuilt from it.
- **A page never saves before its scene has loaded.** Boot into a neutral space while the scene streams in, and
  refuse saves until it is whole: a half-loaded page that saves writes an empty scene over the real one. A load
  that stalls retries with a fresh address (a headset's pooled connection can die silently).
- **Never trust a save the page did not confirm.** Save on idle and when the page hides, and show the person that
  it saved.
- **A server running old code must say so.** After changing the server, restart it; until then every reply carries
  a stale flag.
- **Re-export after a build**, or the person edits yesterday's scene.
- **Never move the person's things.** A camera they placed that ends up inside a character gets reported, and a new
  one is added beside it.
- **A resting hand does nothing.** Selecting is free; a change needs a deliberate unlock; a limp or idle hand is
  ignored, and a selection lapses with time and distance.
- **One coordinate frame.** Convert between the editor's and Blender's axes in one place, and read world positions
  only after the scene has updated.
- **Draw on demand.** An idle page that redraws every frame keeps the GPU busy on a shared machine.
- **Silent failures to check for:** low memory renders flat pink materials and still reports success; a relative
  output folder writes renders somewhere else while stale ones look current; a syntax check can pass a page that will
  not start (check modules as modules).

## Agents first

Everything a hand can do on the stage is also a plain call an agent can make: select, grab, set a key, scrub, retime,
bind a finger to a joint, turn a knob. Every recorded take is data an agent can read, edit and replay. A knob that
must answer in milliseconds is mapped once (`live_map` in `live.md`) and applied by the engine, never routed through
a model.
