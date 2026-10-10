"""The page's commands as ismail ops, one per command the stage page runs (generated from the Crossroads stubs,
songs/crossroads/video/vr/ops_draft.py, 2026-10-04; the docstrings are theirs, checked against the page code).

Each op queues the command on the stage server's live link for `scene`, waits for the page's cmd_done, and replies
with what the page answered (positions in Blender metres, z up). A page that is not showing the scene, or a command
the page refuses, raises OpError with the next step. Coordinates on the wire are Blender world space.
"""
from ..api import OpError, op
from .link import page_cmd


@op(mutates=True)
def stage_object_set(scene: str, object: str, location: list = None, offset: list = None, quaternion: list = None,
               scale: list = None, trial: bool = False) -> str:
    """Move, turn or scale one object as one undoable edit (page command: set). location is the new Blender world
    position; offset is added to location (or to where the object is now when location is not given); quaternion is
    [w, x, y, z]; scale is ignored for aimed objects (cameras and lights that track a target). At least one of
    location, offset, quaternion, scale is required. Errors: no object of that name (matched by exact name, then case
    insensitive). Page replies {object, location, quaternion, scale} (the transform after the edit). Emits:
    transform_end (via claude). trial=True: a test move that never saves (edits.json, which the build reads, keeps
    where it was before the first trial move) until the thing is edited for real (by hand, or a set without
    trial); the reply then says trial: true."""
    return page_cmd(scene, 'set', {'object': object, 'location': location, 'offset': offset, 'quaternion': quaternion, 'scale': scale,
                                   'trial': trial or None}, timeout=30)


@op(mutates=True)
def stage_object_select(scene: str, object: str = None) -> str:
    """Select an object, or clear the selection when object is None (page command: select). In VR, selecting a movable
    thing opens its action menu beside the person (Move, Take, Key, Pin, Drop, and so on: actions.js), which is a panel.
    Page replies the object's description {object, object_type, group, is_group, children, path}, or {selected: null}.
    Emits: select (or deselect), and in VR panel_shown for the menu."""
    return page_cmd(scene, 'select', {'object': object}, timeout=30)


@op(mutates=True)
def stage_object_deselect(scene: str) -> str:
    """Clear the selection (page command: deselect). In VR this also closes the open action menu and locks again a
    thing unlocked with Move. Page replies {selected: null}. Emits: deselect (only when something was selected),
    panel_closed (in VR, when its menu was open)."""
    return page_cmd(scene, 'deselect', {}, timeout=30)


@op(mutates=True)
def stage_object_highlight(scene: str, objects: list, color: str = '#38bdf8', seconds: float = 6) -> str:
    """Put a pulsing box outline, drawn over everything, around each named object or group (page command: highlight;
    the page also takes a single `object`). seconds 0 keeps it until markers_clear. A highlight already on an object is
    replaced. Errors: any name not found. Page replies {objects, seconds} (seconds null when it stays). Emits:
    nothing."""
    return page_cmd(scene, 'highlight', {'objects': objects, 'color': color, 'seconds': seconds}, timeout=30)


@op(mutates=True)
def stage_object_drop(scene: str, object: str) -> str:
    """Stand the object back up (its tilt as Blender had it, heading kept) and let it fall under gravity onto the
    highest surface under its footprint, as one undoable edit (page command: drop). The reply comes before the fall
    animation ends. Lights, aimed objects and big building parts do not drop, and nothing under it means no drop: in
    both cases onto_y_up is null and no error is raised. Page replies {object, onto_y_up} (the landing height, three Y
    = Blender Z, metres). Emits: transform_end once it lands (via claude)."""
    return page_cmd(scene, 'drop', {'object': object}, timeout=30)


@op(mutates=True)
def stage_marker_set(scene: str, marker_id: str, position: list, label: str = '', color: str = '#d97757') -> str:
    """Put a pin (ball and stem, drawn on top) with an HTML label at a Blender position (page command: marker; the
    server renames the command's `id` field to marker_id). The same marker_id replaces the old pin. The label is only
    drawn on the desktop: it is hidden in look-through and in VR. Page replies {id}. Emits: nothing."""
    return page_cmd(scene, 'marker', {'marker_id': marker_id, 'position': position, 'label': label, 'color': color}, timeout=30)


@op(mutates=True)
def stage_markers_clear(scene: str, ids: list = None) -> str:
    """Remove markers (page command: clear_markers). With ids, only those markers; without, every marker AND every
    highlight. Page replies {cleared} (the number of markers removed). Emits: nothing."""
    return page_cmd(scene, 'clear_markers', {'ids': ids}, timeout=30)


@op(mutates=True)
def stage_edit_undo(scene: str) -> str:
    """Undo the last edit on the page (move, light, material), whoever made it (page command: undo). Page replies
    {undone, undo_depth}. Emits: undo {objects, lights, materials, depth} when there was something to undo."""
    return page_cmd(scene, 'undo', {}, timeout=30)


@op(mutates=True)
def stage_person_goto(scene: str, position: list, target: list, seconds: float = 1.2) -> str:
    """Put the person at position facing target, both Blender xyz (page command: goto). Desktop: the view camera
    flies there in `seconds` (walk mode is turned off first). VR: the person is placed at once, standing on the floor
    below position (a floor found from 30 cm above it), turned so they face target (yaw only), with a whoosh; seconds
    is ignored. Errors: position or target missing. Page replies {arrived} on the desktop, {arrived, xr: true, floor}
    in VR (floor is three Y = Blender Z, null when no floor was found). Emits: mode (desktop, when walk was on)."""
    return page_cmd(scene, 'goto', {'position': position, 'target': target, 'seconds': seconds}, timeout=((60 if seconds is None else seconds) + 30))


@op(mutates=True)
def stage_camera_goto(scene: str, camera: str, seconds: float = 1.2) -> str:
    """Fly the desktop view to a Blender camera and look through it (page command: goto_camera; the page reads `name`
    or `camera`). Desktop only. Errors: in VR ("not in XR"), no such object, or the object is not a CAMERA. Page
    replies {arrived, camera}. Emits: mode (look-through), and mode first when walk was on."""
    return page_cmd(scene, 'goto_camera', {'camera': camera, 'seconds': seconds}, timeout=((60 if seconds is None else seconds) + 30))


@op(mutates=True)
def stage_view_focus(scene: str, object: str, seconds: float = 1.0) -> str:
    """Frame an object on the desktop: fly to fit its bounds, keeping the viewing direction, never from below the floor
    (page command: focus). Desktop only. Errors: in VR, no such object. Page replies {arrived, object, object_type,
    group, is_group, children, path}. Emits: focus."""
    return page_cmd(scene, 'focus', {'object': object, 'seconds': seconds}, timeout=30)


@op(mutates=True)
def stage_view_walk(scene: str, on: bool) -> str:
    """Turn desktop walk mode (eye height over the floor, WASD, mouse look) on or off (page command: walk). Desktop
    only. Errors: in VR. Page replies {mode} (orbit, walk or look-through). Emits: mode."""
    return page_cmd(scene, 'walk', {'on': on}, timeout=30)


@op(mutates=True)
def stage_view_look_through(scene: str, camera: str = None) -> str:
    """Look through a Blender camera on the desktop, or back to the editor view when camera is None (page command:
    look_through; the page reads `name`). A name that is not a camera silently goes back to the editor view. Errors: no
    object of that name. Page replies {mode, camera}. Emits: mode."""
    return page_cmd(scene, 'look_through', {'camera': camera}, timeout=30)


@op(mutates=False)
def stage_view_snapshot(scene: str) -> str:
    """Save a PNG of what the person sees to scenes/<scene>/snapshots/ (page command: snapshot). Desktop: the screen
    (the 16:9 frame when looking through a camera), tag = camera name or 'editor'. VR: what the left eye sees, 1024 px
    square, tag 'eye'. Errors: desktop view with no size (hidden or minimised tab). Page replies {path} on the desktop
    (the server's reply) and {path, eye, frame, canvas, pose} in VR (canvas is a browser object and does not survive
    JSON; pose is unset for an eye shot). Emits: nothing."""
    return page_cmd(scene, 'snapshot', {}, timeout=30)


@op(mutates=False)
def stage_view_eyecam(scene: str, fps: float = 1, seconds: float = 10) -> str:
    """Record a timelapse of the left eye in VR, one PNG per frame, up to 600 frames (page command: eyecam). Blocks the
    command queue while it runs. Outside VR it records nothing and replies frames 0 (no error). fps 0 is read as 1.
    Page replies {frames, first, last} (paths). Emits: nothing."""
    return page_cmd(scene, 'eyecam', {'fps': fps, 'seconds': seconds}, timeout=((60 if seconds is None else seconds) + 30))


@op(mutates=True)
def stage_say(scene: str, text: str, seconds: float = 8, voice: bool = False, voice_name: str = None,
              sender: str = None, aloud: bool = False) -> str:
    """Tell the person something (page command: say). Desktop: a caption, one at a time, queued, shown only while the
    tab is visible. VR (or voice=True on the desktop): also spoken by Kokoro (voice_name picks the voice; the server
    default is af_heart), and in VR shown for min(120, 25 + 0.8 x words) seconds as a caption panel. A line that waited
    20 s or more in the queue is captioned, not spoken; 60 s or more, no VR panel either. Page replies
    {captions_ahead}, plus {spoken: false, stale_s} for a stale line. Emits: voice_spoken, voice_hushed (the person
    started a voice note mid-line), voice_dropped, voice_error, and in VR panel_shown and panel_closed. sender= your
    name as the person should see it (e.g. "crossroads film"): the caption and its VR card say it, in that name's
    colour, on that name's side of their body. While the person performs (someone follows them) the line is shown,
    not spoken, since it would be in the recording (page replies held: ...); aloud=True speaks it anyway (an answer
    they asked for)."""
    return page_cmd(scene, 'say', {'text': text, 'seconds': seconds, 'voice': voice, 'voice_name': voice_name, 'from': sender,
                                   'aloud': aloud or None}, timeout=30)


@op(mutates=True)
def stage_ask(scene: str, text: str, seconds: float = 60, sender: str = None) -> str:
    """Ask a yes/no question out loud and wait for a RIGHT thumbs up (yes) or down (no) held 400 ms (page command:
    ask). Blocks the command queue until answered or `seconds` pass. Only answerable in VR (the thumbs are hand
    gestures). A second ask while one is open does not raise: the page replies {error: 'already asking: ...'} (the op
    should turn that into OpError). Page replies {question, answer: yes | no | 'no answer', seconds}. Emits: answer,
    voice_spoken, voice_error. While it waits, the question shows over the person's right hand (the hand that
    answers) with what up and down mean, under sender= (your name as they know it)."""
    return page_cmd(scene, 'ask', {'text': text, 'seconds': seconds, 'from': sender}, timeout=((60 if seconds is None else seconds) + 30))


@op(mutates=True)
def stage_voice_ack(scene: str) -> str:
    """Play a recorded "got it" in the headset at once, so the person knows a voice note arrived (page command: ack).
    Plays only if the command is under 10 s old (by its server `ts`), and not over a line already playing. Page replies
    {played}. Emits: nothing."""
    return page_cmd(scene, 'ack', {}, timeout=30)


@op(mutates=True)
def stage_voice_note(scene: str, action: str = 'start') -> str:
    """Open or close the headset mic for a voice note, as the phone gesture does (page command: voice_rec). action:
    start | stop (anything but 'stop' starts). A note records up to 90 s; on stop it is uploaded and transcribed on the
    laptop. With no mic permission start does not raise: it replies {error: 'no mic'} (the op should raise). Page replies
    {recording: true} | {already: true} on start, {sent, seconds} | {dropped: true} | {recording: false} on stop.
    Emits: voice_note_start, voice_note_dropped, voice_error; the server then emits voice_in and voice_message (the
    transcript)."""
    return page_cmd(scene, 'voice_rec', {'action': action}, timeout=30)


@op(mutates=True)
def stage_panel_show(scene: str, panel_id: str = None, title: str = '', text: str = '', image: str = None,
               buttons: list = None, width: float = None, seconds: float = None, quiet: bool = False,
               wait: bool = None, near: list = None, anchor: str = 'world', side: str = None,
               sender: str = None) -> str:
    """Open a panel in VR with a title, text, an image (URL) and buttons (page command: panel). anchor='world'
    (default) fixes it 0.7 m in front of the person: for a note about a place. anchor='body' makes it ride with them,
    just out of view to the `side` ('right' or 'left') of where their body faces, so they turn their head to read it
    and it follows them as they move: for a message to them. seconds closes it by itself; the X closes it. wait defaults
    to True for a world panel (a question) and False for a body panel (a message never holds the command queue).
    sender= your name as the person should see it (e.g. "crossroads film"): a chip and border in that name's colour,
    and a body panel goes on that name's side unless side= says otherwise.
    It never opens while they are talking (a note recording, the phone gesture, 3 s after a note): it waits, and
    emits panel_held. The person answers by poking a button, pointing and pinching, or a right thumbs
    up / down held 450 ms (first / last button). seconds closes it by itself. quiet skips the chime. wait=True BLOCKS the
    command queue until the answer; wait=False replies at once. The same panel_id replaces an open panel. near is used
    by the page's own modules as a three.js vector; from a command it is unclear (a JSON list has no x/y/z) and should
    not be relied on. Page replies {id, answer, via, seconds}, {id, answer: null, why} when closed unanswered, or
    {id, shown: true} with wait=False. Emits: panel_shown, panel_answer, panel_closed."""
    if anchor not in ('world', 'body') or side not in (None, 'right', 'left'):
        raise OpError(f"anchor is 'world' or 'body' and side 'right' or 'left' (got {anchor!r}, {side!r})")
    if wait is None:
        wait = anchor == 'world'
    return page_cmd(scene, 'panel', {'panel_id': panel_id, 'title': title, 'text': text, 'image': image, 'buttons': buttons, 'width': width, 'seconds': seconds, 'quiet': quiet, 'wait': wait, 'near': near, 'anchor': anchor, 'side': side, 'from': sender}, timeout=((60 if seconds is None else seconds) + 120))


@op(mutates=True)
def stage_panel_close(scene: str, panel_id: str) -> str:
    """Close an open panel (page command: panel_close). A waiting panel_show then resolves with answer null. Page
    replies {closed}. Emits: panel_closed (why 'closed by Claude')."""
    return page_cmd(scene, 'panel_close', {'panel_id': panel_id}, timeout=30)


@op(mutates=True)
def stage_gallery_add(scene: str, url: str, open: bool = False) -> str:
    """Add a picture (a URL the page can load, e.g. scenes/<scene>/snapshots/render_<time>_<what>.png) to the
    person's in-VR gallery and set it as the wrist thumbnail (page command: gallery_add). open=True also opens the
    gallery panel on it after 600 ms. Page replies {n} (the number of pictures). Emits: panel_shown when opened."""
    return page_cmd(scene, 'gallery_add', {'url': url, 'open': open}, timeout=30)


@op(mutates=True)
def stage_cue_set(scene: str, on: str, title: str = None, cue: str = None, text: str = '', buttons: list = None,
            match: dict = None, near: dict = None, then: dict = None, once: bool = True, ttl_s: float = None,
            width: float = 0.4) -> str:
    """Attach a menu ahead of time to something the person will do (page command: cue). on is a page event type
    (touch, menu, drop, teleport, waypoint_done, gesture, ...) or 'near'; match {field: value} must hold on that event
    (strings match by substring); a 'near' cue needs near {position (Blender xyz), radius (default 1.5)}. When it fires
    the panel opens beside the thing the event names, or at its point, else in front of the person; buttons default to
    ['OK']. then {button: command} runs that page command on the page directly (no cmd_done for it). once=False keeps
    the cue after it fires; ttl_s expires it. Kept per scene in cues.json. The id is `cue` (the page also reads `name`),
    else a new one. One cue fires at a time. Page replies {id, waiting}. Emits (later, when it fires): cue_fired,
    panel_shown, panel_answer, panel_closed, cue_answer, voice_error."""
    return page_cmd(scene, 'cue', {'on': on, 'title': title, 'cue': cue, 'text': text, 'buttons': buttons, 'match': match, 'near': near, 'then': then, 'once': once, 'ttl_s': ttl_s, 'width': width}, timeout=30)


@op(mutates=True)
def stage_cue_remove(scene: str, cue: str) -> str:
    """Remove a waiting cue by id (page command: cue_remove; the page also reads `name`). Page replies {removed} (a
    count, 0 when there was no such cue: no error). Emits: nothing."""
    return page_cmd(scene, 'cue_remove', {'cue': cue}, timeout=30)


@op(mutates=True)
def stage_cues_clear(scene: str) -> str:
    """Remove every waiting cue of the scene (page command: cues_clear). Page replies {cleared}. Emits: nothing."""
    return page_cmd(scene, 'cues_clear', {}, timeout=30)


@op(mutates=False)
def stage_cues_list(scene: str) -> str:
    """List the waiting cues (page command: cues_list). Page replies [{id, on, title, match}]. Emits: nothing."""
    return page_cmd(scene, 'cues_list', {}, timeout=30)


@op(mutates=True)
def stage_waypoint_set(scene: str, position: list, wp: str = None, label: str = None, note: str = None,
                 kind: str = 'note', target: list = None, stand: list = None) -> str:
    """Pin a note at a place in the scene (Blender xyz, metres) (page command: waypoint). kind: note | todo | done |
    look (the pin's colour). target: what the person faces when sent there (waypoint_go); stand: where they stand then
    (else 1.6 m from the pin on their side). wp is the pin's id: an existing id updates that pin (fields merge), none
    makes a new one (wp_<time>). Extra fields are kept on the pin (the page itself writes by, at, object, was). Kept
    per scene in waypoints.json. Page replies {id, kind}. Emits: waypoint_set."""
    return page_cmd(scene, 'waypoint', {'position': position, 'wp': wp, 'label': label, 'note': note, 'kind': kind, 'target': target, 'stand': stand}, timeout=30)


@op(mutates=True)
def stage_waypoint_remove(scene: str, wp: str) -> str:
    """Remove one pin (page command: waypoint_remove). Page replies {removed} (false when there was no such pin: no
    error). Emits: nothing."""
    return page_cmd(scene, 'waypoint_remove', {'wp': wp}, timeout=30)


@op(mutates=True)
def stage_waypoints_clear(scene: str, kind: str = None) -> str:
    """Remove every pin, or only those of one kind (note | todo | done | look) (page command: waypoints_clear). Page
    replies {cleared}. Emits: nothing."""
    return page_cmd(scene, 'waypoints_clear', {'kind': kind}, timeout=30)


@op(mutates=False)
def stage_waypoints_list(scene: str) -> str:
    """List the scene's pins (page command: waypoints_list). Page replies the full pin records [{id, position, label,
    note, kind, target, stand, by, at, ...}]. Emits: nothing."""
    return page_cmd(scene, 'waypoints_list', {}, timeout=30)


@op(mutates=True)
def stage_waypoint_go(scene: str, wp: str = None, next: bool = False, distance: float = 1.6) -> str:
    """Send the person to a pin: stand at its `stand`, else `distance` metres from it on the side they are on, facing
    its target (else the pin) (page command: waypoint_go). next=True with no wp picks the first todo, else the first
    note. Runs person_goto, so it flies on the desktop and places at once in VR. Errors: no such pin, no open pin. Page
    replies what goto replies ({arrived} or {arrived, xr, floor}). Emits: waypoint_go."""
    return page_cmd(scene, 'waypoint_go', {'wp': wp, 'next': next, 'distance': distance}, timeout=600)


@op(mutates=True)
def stage_take_start(scene: str, name: str = '') -> str:
    """Start recording a take: head and both hands (25 joints each, three.js space) and the WebXR body when granted,
    at 30 Hz, plus the mic, to scenes/<scene>/takes/<id>/ (page command: take_start). The id is the start time plus the
    name. Frames are only sampled while the page is in VR. Page replies {id}, or {id, already: true} when one is
    running. Emits: take_start, body_tracking (first body frame), voice_error (no mic: the take has no audio)."""
    return page_cmd(scene, 'take_start', {'name': name}, timeout=30)


@op(mutates=True)
def stage_take_stop(scene: str) -> str:
    """Stop the take being recorded and flush its frames (page command: take_stop). Page replies {id, frames,
    seconds}, or {stopped: false} when none was running. Emits: take_stop; the server then emits take_audio."""
    return page_cmd(scene, 'take_stop', {}, timeout=600)


@op(mutates=True)
def stage_take_view(scene: str, take: str, n: int = 8, layout: str = 'strip') -> str:
    """Show a take in 4D: the paths of the head and both wrists and n ghost poses (2..24) coloured blue to orange
    (page command: take_view). layout: strip (a filmstrip in front of the person, 40 cm apart) | place (where it
    happened). BUG today: the page reads the take id from the command's `id`, but server.py drops a sent `id` and
    puts its own command number there (only `marker` gets a rename to marker_id), so from the live link this looks
    for take "<command number>" and fails; it works only from the page's own review menu. The op should send the take
    under its own field and the page read it. Errors: no take, no frames. Page replies {id, poses, seconds, frames}.
    Emits: take_view."""
    return page_cmd(scene, 'take_view', {'take': take, 'n': n, 'layout': layout}, timeout=30)


@op(mutates=True)
def stage_take_view_clear(scene: str) -> str:
    """Remove the 4D take view (page command: take_view_clear). Page replies {cleared: true}. Emits: nothing."""
    return page_cmd(scene, 'take_view_clear', {}, timeout=30)


@op(mutates=True)
def stage_actor_play(scene: str, person: str, take: str, actor: str = None, loop: bool = True, rate: float = 1,
               trim: list = None, in_place: bool = False, assets: str = None, takes: str = None, voice: bool = None,
               mirror: bool = None, at_music: float = None) -> str:
    """Play a take on a person: their skinned body replaces the statue and is driven by the take's head and hands,
    scaled to their height (page command: actor_play). actor defaults from the person (a fixed table in actors.js);
    trim [t0, t1] in the take's seconds defaults to the take's saved trim (the page treats an explicit null as "no
    trim", which a None default here cannot express); in_place plays it where it was recorded instead of where the
    person stands; assets / takes read the body / take from another scene. voice: a take made in a performance plays
    its recorded voice only on the person it was recorded for (None, the default); True plays it on this body too,
    False keeps it silent. One clip sounds once however many bodies play the take. mirror: a take plays mirrored as it
    was recorded (meta.mirror, from the Follow's mirror; None, the default); True or False overrides. at_music: play
    on the music clock: the take's first frame (after trim) sits at this song second and every frame reads the song's
    time as heard, so a beat-warped loop stays on the beat however late it started (stage_music plays the song; with
    no music playing it runs on the wall clock). Errors: no actor for that person,
    no take, no frames. Page replies {person, actor, frames, scale}. Emits: actor_stop (a take already on them),
    actor_play."""
    return page_cmd(scene, 'actor_play', {'person': person, 'take': take, 'actor': actor, 'loop': loop, 'rate': rate, 'trim': trim, 'in_place': in_place, 'assets': assets, 'takes': takes, 'voice': voice, 'mirror': mirror, 'at_music': at_music}, timeout=30)


@op(mutates=True)
def stage_actor_stop(scene: str, person: str, why: str = 'stopped') -> str:
    """Stop a played take or a live follow on a person; the statue comes back (page command: actor_stop). Page
    replies {stopped} (false when nothing was playing: no error). Emits: actor_stop {person, why, live}."""
    return page_cmd(scene, 'actor_stop', {'person': person, 'why': why}, timeout=30)


@op(mutates=True)
def stage_actor_follow(scene: str, person: str, actor: str = None, mode: str = 'place', mirror: bool = False,
                 assets: str = None, countdown: int = 0) -> str:
    """Make a person move with the person in VR, live, from where they stand (page command: actor_follow). mode:
    place (dances on the spot) | walk (walks as the user walks); mirror reflects left and right. It stops by itself
    when the user goes more than 6 m away. Meant for VR (it reads the live head and hands). Errors: no actor for that
    person, no live body source. Page replies {person, actor, following: true, scale}. Emits: actor_stop (anything
    already on them), actor_follow; later actor_stop {why: walked_away}. countdown= seconds counted down in front of
    the user first (3, 2, 1, GO, with ticks) so they can take the person's pose; the Follow starts from their pose at
    GO (the menu's Follow and Take count 3). Emits follow_countdown when it begins."""
    return page_cmd(scene, 'actor_follow', {'person': person, 'actor': actor, 'mode': mode, 'mirror': mirror, 'assets': assets,
                                            'countdown': countdown or None}, timeout=30 + (countdown or 0))


@op(mutates=True)
def stage_anchor_set(scene: str, object: str, hand: str = 'left', joint: str = 'thumb-metacarpal', at: str = 'keep') -> str:
    """Pin an object to a joint of a tracked hand for this session (page command: anchor). hand: left | right; joint:
    a WebXR hand joint name; at: keep (holds the pose it has now, relative to the joint) | joint (its origin onto the
    joint). Never saved: the edits file keeps the object where it was, and leaving VR or the scene releases it. Errors:
    no such object, the hand is not tracked or has no such joint. Page replies {anchored, hand, joint}. Emits:
    anchor_released (when it was already pinned), anchored."""
    return page_cmd(scene, 'anchor', {'object': object, 'hand': hand, 'joint': joint, 'at': at}, timeout=30)


@op(mutates=True)
def stage_anchor_release(scene: str, object: str = None) -> str:
    """Release a pinned object back to where it was, or every pinned object when none is named (page command:
    anchor_release). Page replies {released} (the names). Emits: anchor_released, one per object."""
    return page_cmd(scene, 'anchor_release', {'object': object}, timeout=30)


@op(mutates=True)
def stage_clock_set(scene: str, action: str = None, t: float = None, rate: float = None, span: list = None) -> str:
    """Drive the scene's stage clock (page command: clock). action: play | pause (only these two act; the clock.js
    header also names seek, rate and span, which work as the fields below instead). t seeks (clamped to the span),
    rate sets the speed, span [t0, t1] sets the timeline span in seconds. Playing loops at the end. Page replies
    {t, playing, rate, span}. Emits: clock (when t is given)."""
    return page_cmd(scene, 'clock', {'action': action, 't': t, 'rate': rate, 'span': span}, timeout=30)


@op(mutates=True)
def stage_key_set(scene: str, name: str, t: float = None) -> str:
    """Key an object's current Blender world transform at time t (default: the playhead) (page command: key). A key
    at the same time is replaced. Not saved until anim_save. Errors: no object. Page replies {name, t, keys}. Emits:
    keyed."""
    return page_cmd(scene, 'key', {'name': name, 't': t}, timeout=30)


@op(mutates=True)
def stage_keys_set(scene: str, keys: list, replace: list = None, interp: dict = None, save: bool = False) -> str:
    """Many keys in one call, from values, on any objects (page command: key with keys): a camera move or an object's
    path without moving the thing to each spot first. keys = [{"name": "cam_a", "t": 0, "location": [x, y, z],
    "look": [x, y, z]}, {"name": "cam_a", "t": 4, ...}, {"name": "door", "t": 2, "quaternion": [w, x, y, z]}]: Blender
    world values, t in stage clock seconds; what a key leaves out (location, quaternion, scale) is the object's
    transform now; look aims the key's -Z at a point, world Z up (cameras and lights; stored as a quaternion, so
    renders read it as any key). A key at the same t replaces the old one. replace: objects whose old keys go first;
    interp: {"cam_a": "smooth"} (stage_key_interp). Every key is checked before any is written; the clock's span grows
    to take keys past its end. save=True also saves anim.json (stage_anim_save). Page replies {keyed, keys (per
    object, its key count), span}. Emits: keyed."""
    if not isinstance(keys, list) or not keys:
        raise OpError('keys is a list of {"name", "t", "location"?, "quaternion"?, "scale"?, "look"?}')
    for i, k in enumerate(keys):
        if not isinstance(k, dict) or not k.get('name') or not isinstance(k.get('t'), (int, float)):
            raise OpError(f'key {i}: needs "name" and "t" (seconds)')
        extra = set(k) - {'name', 't', 'location', 'quaternion', 'scale', 'look'}
        if extra:
            raise OpError(f'key {i} ({k["name"]}): unknown fields {sorted(extra)}')
    if interp and any(m not in ('stop', 'smooth') for m in interp.values()):
        raise OpError("interp values are 'stop' or 'smooth'")
    r = page_cmd(scene, 'key', {'keys': keys, 'replace': replace or [], 'interp': interp or {}}, timeout=60)
    if save:
        r += '; ' + page_cmd(scene, 'anim_save', {}, timeout=30)
    return r


@op(mutates=True)
def stage_key_delete(scene: str, name: str, t: float = None) -> str:
    """Delete an object's key at time t, or all its keys when t is None (page command: key_delete). Not saved until
    anim_save. Page replies {deleted} (a count, 0 when it had none). Emits: nothing."""
    return page_cmd(scene, 'key_delete', {'name': name, 't': t}, timeout=30)


@op(mutates=True)
def stage_anim_save(scene: str) -> str:
    """Save the keys and growth spans to scenes/<scene>/anim.json (the old one goes to history/), read by the Blender
    renders (page command: anim_save). Errors: the server refused it. Page replies the server's {ok, path}. Emits:
    anim_saved (from the page with {objects, path}, and from the server with {objects})."""
    return page_cmd(scene, 'anim_save', {}, timeout=30)


@op(mutates=True)
def stage_anim_clear(scene: str, name: str = None) -> str:
    """Drop one object's keys, or every object's keys when name is None (growth spans stay) (page command:
    anim_clear). Not saved until anim_save. Page replies {cleared} (the name or 'all'). Emits: nothing."""
    return page_cmd(scene, 'anim_clear', {'name': name}, timeout=30)


@op(mutates=True)
def stage_timeline_show(scene: str, show: bool = True) -> str:
    """Show the timeline bar in front of the person (0.6 m ahead, 30 cm below the eyes), or hide it (page command:
    timeline). Page replies {shown}. Emits: nothing."""
    return page_cmd(scene, 'timeline', {'show': show}, timeout=30)


@op(mutates=True)
def stage_growth_set(scene: str, name: str, t0: float, t1: float) -> str:
    """Map the clock onto one tree's growth: it grows from nothing at t0 to full at t1 (page command: growth). name is
    the tree's top-level item. Not saved until anim_save. Page replies {name, t0, t1, growers} (every tree that can
    grow). Emits: nothing."""
    return page_cmd(scene, 'growth', {'name': name, 't0': t0, 't1': t1}, timeout=30)


@op(mutates=True)
def stage_trees_reload(scene: str) -> str:
    """Load (again) the grown trees, scenes/<scene>/trees/tree_<k>.glb, under their tree_<k> items, paced like the room
    (page command: trees_reload). A load already running is joined, not restarted. The page also runs this by itself
    after a scene reload. Page replies {trees, growers}. Emits: trees_loaded, voice_error (per tree that failed)."""
    return page_cmd(scene, 'trees_reload', {}, timeout=30)


@op(mutates=True)
def stage_music(scene: str, action: str = 'play', url: str = None, at: str = None, lift: float = 1.0,
                ref: float = 3, rolloff: float = 1, loop: bool = True, volume: float = 0.6, start: float = 0) -> str:
    """Play one audio file from a place in the room, positional (louder as the person walks up), or stop it, or set
    its volume (page command: music). action: play | stop | volume (any value other than stop or volume plays). at is
    an object to play from (its position, lifted by lift metres; none: the room origin). One music source at a time:
    play replaces it. volume with nothing playing falls through to play and needs url. start: begin this many seconds
    into the song. The song's time as heard is in the page state (`music`: {playing, url, t, duration, loop}) and from
    stage_music_time; takes can play on it (stage_actor_play at_music). Page replies {playing, at, seconds, from} |
    {stopped: true} | {volume}. Emits: music_start {url, from, seconds, at}, music_stop {url, at_s}."""
    return page_cmd(scene, 'music', {'action': action, 'url': url, 'at': at, 'lift': lift, 'ref': ref, 'rolloff': rolloff, 'loop': loop, 'volume': volume, 'from': start}, timeout=30)


@op()
def stage_music_time(scene: str) -> str:
    """The song's time as the person hears it (page command: music_time): {playing, url, t (song seconds), duration,
    loop}, or {playing: false}. Read it to place a take on the beat (stage_actor_play at_music)."""
    return page_cmd(scene, 'music_time', {}, timeout=15)


@op(mutates=True)
def stage_stream(scene: str, action: str = 'play', name: str = 'master', stream_id: str = None, at: str = None,
                 lift: float = 0.5, port: int = None, stereo: bool = False, lead: float = 0.3, ref: float = 1.5,
                 rolloff: float = 1, volume: float = 0.7) -> str:
    """Stream an ismail live bus from the PC into the room, played from an object through an HRTF panner (page
    command: stream). action: play | stop | volume | move | status. name: master | bus:<name> | deck:<name>. The stream
    is known by stream_id, else at, else name. port picks the ismail live engine (default: the newest running one,
    chosen by server.py). Mono unless stereo; lead is the jitter buffer in seconds. stop without an id stops every
    stream; move re-places it at `at`. Errors: bad name, no engine running, unknown stream for volume or move, no
    AudioWorklet. Page replies {stream, name, at, rate, mono, lead} on play, {stopped} (bool or count), {volume},
    {at}, or a status list [{stream, name, at, seconds, under, skips, lead_s}]. Emits: stream_play, stream_end,
    stream_underrun."""
    return page_cmd(scene, 'stream', {'action': action, 'name': name, 'stream_id': stream_id, 'at': at, 'lift': lift, 'port': port, 'stereo': stereo, 'lead': lead, 'ref': ref, 'rolloff': rolloff, 'volume': volume}, timeout=30)


@op(mutates=True)
def stage_sky_set(scene: str, mode: str = 'scene') -> str:
    """Set the sky: day | night | scene (the scene's own, the default) (page command: sky). Remembered on that
    headset for 20 minutes. Page replies {sky} (the sky now in use). Emits: nothing."""
    return page_cmd(scene, 'sky', {'mode': mode}, timeout=30)


@op(mutates=False)
def stage_behaviours(scene: str, reload: bool = False) -> str:
    """What the things in a scene do on their own: the objects with a behaviour in scenes/<scene>/behaviours.js, what
    each can do (press, its menu items, its inputs), its state now, its sound, and every load error and warning
    (page command: behaviours). reload=True reads the file again first: after writing or changing it, call this with
    reload and read the errors (each {object, phase, action, error, line}; line is in behaviours.js).
    The file is plain JavaScript, no imports: `export default { object_name: { sound, state, apply(s), press(s),
    menu: {label: (s) => ...}, inputs: {name: (s, value) => ...} } }`. The page runs these itself when the person
    pinches or triggers the object in VR (or picks a menu item), so the room answers with no agent in the loop: turn
    anything you did by hand (lights, moves, visibility, music, any page command through s.do) into such a function.
    `s` is the object's handle: get, set, state, send (wire to another object's input), light, show, move, sound,
    emit (a message to agents: object_message), do, after, every. apply(s) runs at load and after every state change:
    put the state-to-room mapping there, so a reload or a scene switch shows the right room. Every interaction needs
    its own sound (`sound`: a file under the scene folder, rendered with ismail); without one the page clicks and
    warns. Every error, at load or at run time, also goes out as behaviour_error {object, phase (load, apply, press,
    menu, input, after, every), action, error, line}; listen for it. In VR a press that fails buzzes and shows a short
    note at the object, and an object whose code did not load (`broken`) does the same instead of opening the edit
    menu. Page replies {file, objects: [{object, actions, state, sound}], errors, warnings, broken: {object: why}}.
    Emits: behaviours_loaded, behaviour_error, behaviour_warning."""
    return page_cmd(scene, 'behaviours', {'reload': reload or None}, timeout=60)


@op(mutates=True)
def stage_behaviour_run(scene: str, object: str, action: str = 'press', value=None, sound: bool = True) -> str:
    """Do what the person does in VR: press an object with a behaviour, pick one of its menu items (action = the label),
    or send one of its inputs a value (page command: behaviour_run). The same function runs as for the person, and
    its sound plays (sound=False for a silent test). Errors: no behaviour on that object, no such action (the reply
    lists them). A function that fails answers with `error` and emits behaviour_error. Page replies {object, action,
    via, state, error?}. Emits: behaviour_run, behaviour_state when the state changed."""
    return page_cmd(scene, 'behaviour_run', {'object': object, 'action': action, 'value': value,
                                             'sound': None if sound else False}, timeout=30)


@op(mutates=True)
def stage_behaviour_state(scene: str, object: str, state: dict = None) -> str:
    """Read an object's behaviour state, or merge `state` into it (page command: behaviour_state). A change runs the
    object's apply(s), so the room follows, and is saved with the scene (behaviour_state.json), so it is the same
    after a reload. Page replies the state. Emits: behaviour_state."""
    return page_cmd(scene, 'behaviour_state', {'object': object, 'state': state}, timeout=30)


@op(mutates=True)
def stage_light_set(scene: str, light: str, energy: float = None, color: list = None) -> str:
    """Set a light's energy (Blender watts) and / or colour (linear RGB) as one undoable edit (page command: light;
    the page reads `name` or `light`). Errors: no such object, not a light. Page replies {light, energy, color}. Emits:
    light_change."""
    return page_cmd(scene, 'light', {'light': light, 'energy': energy, 'color': color}, timeout=30)


@op(mutates=True)
def stage_scene_go(scene: str, name: str, position: list = None, target: list = None, reload: bool = False) -> str:
    """Go to another scene, under the construct (the grey void closes, the room is swapped, the void opens) (page
    command: scene_go; the page reads `name` or `scene` for the destination). Unsaved edits are saved first, playing
    actors stop, anchors release. The person is placed where this headset last stood there, else at its start camera,
    else at position facing target (Blender xyz). reload=True with the current scene re-reads it. After this the page
    listens on the NEW scene's bus: later ops must pass scene=name. Errors: no scene given, a change already running,
    load failure (the old room comes back). Page replies {from, to, objects, ms}, or {from, to, already: true}. Emits:
    scene_leaving (old scene's bus), scene_switched (new scene's bus), and on the way save, actor_stop,
    anchor_released."""
    return page_cmd(scene, 'scene_go', {'name': name, 'position': position, 'target': target, 'reload': reload}, timeout=600)


@op(mutates=False)
def stage_scene_list(scene: str) -> str:
    """List the scenes the server has, without the ones starting with '_' (page command: scene_list). Page replies
    {current, scenes}. Emits: nothing."""
    return page_cmd(scene, 'scene_list', {}, timeout=30)


@op(mutates=True)
def stage_scene_reload(scene: str) -> str:
    """Swap in a fresh export of this scene now, keeping the view and the unsaved edits it can (page command: reload).
    The page also does this by itself when scene.glb and manifest.json change (not mid drag, after VR edits end). Errors:
    a reload already running, the files cannot be read. Page replies {objects, kept_edits, dropped, selection,
    lost_selection, why, mode, selftest, checked, selftest_fails, version}. Emits: scene_reload or scene_reload_failed,
    then trees_loaded (the page reloads the trees 0.5 s later)."""
    return page_cmd(scene, 'reload', {}, timeout=30)


@op(mutates=True)
def stage_take_keep_last(scene: str, name: str = None) -> str:
    """Keep the person's last Follow as a take (page command: take_keep_last). Every Follow is recorded into the
    page's memory (the newest 180 s) whether or not it was a take; this saves the last one under name (default: the
    person followed) in scenes/<scene>/takes/, where actor_play and the review can use it. Only the last Follow is
    kept, until the next Follow or a page reload. Page replies {id, person, frames, seconds}. Emits: follow_kept."""
    return page_cmd(scene, 'take_keep_last', {'name': name}, timeout=60)


@op(mutates=True)
def stage_perform(scene: str, action: str = 'state', label: str = None) -> str:
    """Your hand on a performance (page command: perform). A Follow is a performance: while someone follows the person
    no gesture acts (pokes still press panels), your speech is shown and not spoken (stage_say aloud=True speaks), and
    the mic records from the first moment in clips on the Follow's clock (seconds since it began). action:
    'state' (default): {performing, perf, person, seconds, mic, clip, clips, markers, take};
    'stop': end the whole performance: the Follow, and a take recording with it (the person can too: both thumbs down
    held 1.5 s, saying "stop the performance", or Stop on the Follow panel, which opens however the Follow began);
    'stop_clip': end the clip now so it is transcribed (the person goes on; the mic waits for start_clip);
    'start_clip': record the next clip (turns the mic back on);
    'next_clip': stop_clip then start_clip in one step (read one part while recording the next);
    'mic_off': end the clip and leave the mic off (start_clip turns it on);
    'mark': a marker with label= at this moment on the Follow clock.
    Clips also cut themselves at the first pause after 6 s, or at 25 s, so the words arrive while they go on: each
    perform_clip with words also reaches stage_listen. A stopped clip arrives as event perform_clip {perf, clip, at, seconds, text, words: [[word, start, end]]} with
    word times on the Follow clock, snapped onto the measured voice; stage_performance reads a whole performance. The
    Follow panel has Mic off / Mic on for the person. Errors: nobody follows the person; the mic is not allowed.
    Emits: perform_clip_start, perform_clip_stop, perform_mic, perform_mark, perform_stop_asked, perform_stop (and
    from the server perform_clip_in, perform_clip)."""
    if action not in ('state', 'stop', 'stop_clip', 'start_clip', 'next_clip', 'mic_off', 'mark'):
        raise OpError("action is 'state', 'stop', 'stop_clip', 'start_clip', 'next_clip', 'mic_off' or 'mark'")
    if action == 'mark' and not label:
        raise OpError("mark needs label= (what happens at this moment)")
    return page_cmd(scene, 'perform', {'action': action, 'label': label}, timeout=30)


@op(mutates=True)
def stage_follow_anchor(scene: str, person: str, joint: str = 'hips', to: str | list = None, legs: str = 'keep_pose',
                        clear: bool = False) -> str:
    """Pin a person's joint for when the user follows (animates) them (page command: follow_anchor): joint 'hips'
    (default), 'foot_l', 'foot_r' or 'feet'; to= an object (a seat: the hips sit just above its top) or [x, y, z] in
    Blender metres, or 'here' (where that joint is now, during a Follow). Pinned hips stay on the seat facing the
    person's own way, the user's head only bends the spine and the hands drive the arms, and the feet plant in front of
    the seat (legs='keep_pose') unless they have pins of their own (a rung, the floor). The user walking clearly away
    from the seat lets go for that Follow. clear=True removes the pin. Pins last for the page's session and apply to
    every Follow of that person; the Follow panel shows them and has Pin / Unpin hips. Page replies
    {person, pinned: {joint: [x, y, z]}, legs, following}. Emits: anchored_joint, pin_released."""
    if joint not in ('hips', 'foot_l', 'foot_r', 'feet'):
        raise OpError("joint is 'hips', 'foot_l', 'foot_r' or 'feet'")
    if not clear and to is None:
        raise OpError("to= an object name (a seat), [x, y, z] in Blender metres, or 'here' during a Follow")
    return page_cmd(scene, 'follow_anchor', {'person': person, 'joint': joint, 'to': to, 'legs': legs, 'clear': clear}, timeout=30)


# page command type -> its typed op (stage_cmd refuses these and names the op)
@op(mutates=True)
def stage_key_interp(scene: str, object: str, mode: str) -> str:
    """How a keyed object moves between its keys (page command: key_interp): mode 'stop' (it eases into and out of
    every key, so it stops at each; what an object with no mode set does) or 'smooth' (it glides through them: a
    camera move). Saved in anim.json "interp" with stage_anim_save. The same curves are in ismail/stage/page/interp.js
    (the page) and ismail/stage/interp.py (sample_anim, for a render), held equal by a test; a render that turns
    anim.json into keyframes must sample through interp.py or it drifts from the stage again. Errors: the object has
    no keys. Page replies {name, mode, keys}."""
    if mode not in ('stop', 'smooth'):
        raise OpError("mode is 'stop' or 'smooth'")
    return page_cmd(scene, 'key_interp', {'name': object, 'mode': mode}, timeout=30)


@op(mutates=True)
def stage_control_set(scene: str, person: str, part: str, mode: str = 'default', joint: str | list = None,
                      at: str | list = None, scale: float = None, touch: bool = False) -> str:
    """Change what drives one part of a person, now, during a Follow too (page command: control_set). The built-in
    map stays the base (the user's head drives head and spine, the wrists the arms, the fingers the fingers, legs
    step or sit); a drive takes one part over. part: a named part of the actor's rig (stage_actor_profile(person) lists
    them; on people: head, spine, arm_l, arm_r, leg_l, leg_r, fingers_l, fingers_r, thumb_l, index_l, ...). mode:
    'hold' keeps the pose it has now (riding with the body); 'effector' (arms and legs) reaches for a target that
    moves as joint= moves, relative to where both were when it bound (his feet acted by the user's hands: part
    'leg_l', joint 'hand_l'), scale= multiplies the motion, touch=True shows a ball in front of the user and binds
    when that hand reaches it; 'pin' (arms and legs) reaches for at= (an object, [x, y, z] in Blender metres, or
    'here') and stays; 'mimic' copies the turn of joint= since it bound, spread down the chain, or 1:1 with a list
    of joints, one per bone; 'default' gives the part back to the built-in map. joint: 'head', 'hand_l', 'hand_r',
    or 'hand_r:<webxr joint>' (e.g. hand_r:index-finger-tip). A hand that drives another part lets go of its own
    arm and fingers (they hold). Drives last for the page's session; stage_control_map applies saved ones. Page
    replies {person, part, mode, joint, waiting, drives}. Emits: control_set, control_bound."""
    from .rigs import MODES
    if mode not in MODES:
        raise OpError(f"mode is one of {MODES}")
    if mode in ('effector', 'mimic') and not joint:
        raise OpError(f"{mode} needs joint= 'head', 'hand_l', 'hand_r' or 'hand_r:<webxr joint>'")
    if mode == 'pin' and at is None:
        raise OpError("pin needs at= an object, [x, y, z] in Blender metres, or 'here'")
    return page_cmd(scene, 'control_set', {'person': person, 'part': part, 'mode': mode, 'joint': joint, 'at': at,
                                           'scale': scale, 'touch': touch or None}, timeout=30)


@op(mutates=True)
def stage_control_map(scene: str, person: str, preset: str = None, drives: list = None, clear: bool = False) -> str:
    """Read or set a person's whole control map (page command: control_map). With nothing else: the drives now, the
    presets saved in the actor's profile, and their pins. preset= applies a saved map from the profile (its pins and
    drives; a map named "default" applies by itself when a Follow starts and nothing was set); drives= a list of
    {part, mode, joint, at, scale, touch} applied in order; clear=True first gives every part back to the built-in
    map. Save a map to the actor with stage_actor_map_save. Page replies {person, drives, presets,
    pins}. Emits: control_map, control_set, control_bound."""
    return page_cmd(scene, 'control_map', {'person': person, 'preset': preset, 'drives': drives, 'clear': clear or None}, timeout=30)


@op()
def stage_actor_pose(scene: str, person: str, t: float = None) -> str:
    """Where a person's joints are, in Blender metres (page command: actor_pose): pelvis, spine_03, head, lowerarm_l/r,
    hand_l/r, calf_l/r, foot_l/r. While the user follows them: now. While a take plays on them: at t seconds of the
    take (default: the frame playing). Otherwise: their start pose (stage_actor_start; 'rest' when none), i.e. frame 0
    of the next Follow. Check contact numerically: the pelvis over the seat top, the hands on the bar top. `short`
    names the limb ends that could not reach their target in that pose and by how many metres (a hand driven past
    arm's length stops at full reach): {"hand_l": 0.04} means the left hand is 4 cm short of where it was sent. Page
    replies {person, joints, short, start, take, t}."""
    return page_cmd(scene, 'actor_pose', {'person': person, 't': t}, timeout=30)


TYPED = {'behaviours': 'stage_behaviours', 'behaviour_run': 'stage_behaviour_run', 'behaviour_state': 'stage_behaviour_state', 'music_time': 'stage_music_time', 'load_set': 'stage_set_load', 'actor_pose': 'stage_actor_pose', 'key_interp': 'stage_key_interp', 'control_set': 'stage_control_set', 'control_map': 'stage_control_map', 'perform': 'stage_perform', 'follow_anchor': 'stage_follow_anchor', 'take_keep_last': 'stage_take_keep_last', 'ack': 'stage_voice_ack', 'actor_follow': 'stage_actor_follow', 'actor_play': 'stage_actor_play', 'actor_stop': 'stage_actor_stop', 'anchor': 'stage_anchor_set', 'anchor_release': 'stage_anchor_release', 'anim_clear': 'stage_anim_clear', 'anim_save': 'stage_anim_save', 'ask': 'stage_ask', 'clear_markers': 'stage_markers_clear', 'clock': 'stage_clock_set', 'cue': 'stage_cue_set', 'cue_remove': 'stage_cue_remove', 'cues_clear': 'stage_cues_clear', 'cues_list': 'stage_cues_list', 'deselect': 'stage_object_deselect', 'drop': 'stage_object_drop', 'eyecam': 'stage_view_eyecam', 'focus': 'stage_view_focus', 'gallery_add': 'stage_gallery_add', 'goto': 'stage_person_goto', 'goto_camera': 'stage_camera_goto', 'growth': 'stage_growth_set', 'highlight': 'stage_object_highlight', 'key': 'stage_key_set', 'key_delete': 'stage_key_delete', 'light': 'stage_light_set', 'look_through': 'stage_view_look_through', 'marker': 'stage_marker_set', 'music': 'stage_music', 'panel': 'stage_panel_show', 'panel_close': 'stage_panel_close', 'reload': 'stage_scene_reload', 'say': 'stage_say', 'scene_go': 'stage_scene_go', 'scene_list': 'stage_scene_list', 'select': 'stage_object_select', 'set': 'stage_object_set', 'sky': 'stage_sky_set', 'snapshot': 'stage_view_snapshot', 'stream': 'stage_stream', 'take_start': 'stage_take_start', 'take_stop': 'stage_take_stop', 'take_view': 'stage_take_view', 'take_view_clear': 'stage_take_view_clear', 'timeline': 'stage_timeline_show', 'trees_reload': 'stage_trees_reload', 'undo': 'stage_edit_undo', 'voice_rec': 'stage_voice_note', 'walk': 'stage_view_walk', 'waypoint': 'stage_waypoint_set', 'waypoint_go': 'stage_waypoint_go', 'waypoint_remove': 'stage_waypoint_remove', 'waypoints_clear': 'stage_waypoints_clear', 'waypoints_list': 'stage_waypoints_list'}
