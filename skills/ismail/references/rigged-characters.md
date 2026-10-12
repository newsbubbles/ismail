# Rigged characters on a VR stage

Lessons from putting rigged human bodies into a WebXR scene: a body comes out of a character generator, gets posed
in Blender, and is posed again by the page from a start pose, a recorded take, or a live person following it. The
worked example is MakeHuman (MPFB in Blender, the 53-bone `game_engine` rig, glTF into three.js), so bone names below
are that rig's. The lessons hold for any rig a generator or auto-rigger gives you (Mixamo, a VRM avatar, a rigged
scan), but the rig facts differ: run "Know your rig first" on yours before trusting any number here.

The numbers come from one long film build (a band with instruments, a bar crowd, six dancers) checked in a headset.
Anything not yet seen working is marked **unverified**. Building the character itself (body, clothes, export
format) belongs to your character tool's own guide; the stage ops are in `stage.md`.

Read this before exporting a body for the stage, posing someone in Blender for it, giving a person a start pose,
putting a prop in a hand, or judging a Follow.

Each lesson reads: what was seen, the cause, the fix, and the measured number where there is one.

## Know your rig first

Answer these from the file, not from memory, and write them down beside the body:

- **Bones.** List the skin's joints from the GLB. `ismail/stage/rigs.py` (`bones_of`, `rig_type`) does it; a body
  with the 17 core human bones reads as `human_game_engine`, and any other rig gets its unbranched bone chains as
  parts, so it can still be mapped.
- **Rest pose and facing.** T or A pose, standing at the origin, and which way it faces. MakeHuman rests in an
  A-pose and faces -Y in Blender.
- **Bone axes.** Along the bone is +Y on these rigs. The hand bone is rolled about 41 deg off the palm's plane, and
  the thumb's nail points along its local -Z, not out of the back of the hand.
- **Twist bones.** Is there anything between the forearm and the hand, or along the upper arm? The `game_engine`
  rig has none. This one fact decides how far a hand may turn (next sections).
- **How the runtime skins.** three.js uses linear blend skinning: no dual quaternions, no volume preservation, no
  corrective shape keys. The Blender file you pose in may deform better than the headset will.
- **Axes.** Blender is Z-up, three.js is Y-up: Blender (x, y, z) is three.js (x, z, -y). Convert in one place.

## Export and the rest pose

- **A posed body exports in its rest pose.** Seen: a seated guitarist arrived in the browser in a T-pose with his
  guitar floating in front of him; props parented to bones floated where the rest bones were. Cause: Blender's glTF
  exporter defaults `export_rest_position_armature=True`. Fix: decide what each file is for. A statue (a posed
  person that never moves) exports with `export_rest_position_armature=False`, or with the pose baked into the mesh.
  An actor (a skinned body the page poses) exports its rest on purpose, and the pose travels as data (a start pose,
  below). Measured after the fix: wrist positions in the page matched the Blender pose to the millimetre. Check by
  reading bone world positions in the page, not by eye.
- **Statue and actor of the same person.** Seen: a person drawn twice while followed; their selection box stood
  0.46 m from their body (it came from the hidden statue), and the glass and rag the statue held stayed behind.
  Fix: when an actor replaces a statue, hide the statue, take the box and the picks from the actor's body, and move
  what the statue held into the actor's hands (or pin it, below).
- **Actors with nothing to play stand in their rest pose.** Seen: dancers and a DJ standing arms-out in captures and
  in the headset. Fix: every actor gets a start pose and rests in it when nothing plays (a dancer rests on the first
  frame of their loop). A capture that starts before the bodies have loaded shows statues and placeholders: in one
  check every body was rested 6 s after the first frame, so wait for them.
- **"Model done" is not "on the stage".** Seen: three bodies cast in the scene but with no start pose, so only
  144-vertex placeholder boxes were drawn, and the person was sent to frame a character who was not there. Fix:
  check what each cast person actually draws (a body in a pose, a body playing, or a placeholder) before giving them
  any step that needs it.
- **Light enough for a standalone headset.** The actor export that worked: leave out hair, teeth, tongue, lashes
  and brows; apply the shape keys (keep the mix) first, since keys block applying modifiers; apply the clothing
  masks so no skin is left under clothes; decimate each mesh to a budget (body 5000 triangles, every other mesh
  2500) with the armature modifier kept last; textures at most 1024 px, WebP. Result: 53 bones, 0.6 to 1.7 MB and
  12 to 15 k triangles per actor (19 k as a full statue). A view of 1.2 M triangles and 1156 draw calls ran about
  27 fps on the headset; the proposed scene budget of under 400 k triangles and 300 draws is **unverified**.
- **Hair curves never export to glTF.** Use card or mesh hair if it must show.
- **Generated and auto-rigged bodies.** A generated rig has no standard rest pose: one bind pose leaned 29 deg
  forward, and human crouch angles stacked on it folded the body in half. Measure the rest lean (head bone against
  foot bones) and counter it at the spine, never the hips (the hips carry the legs too). Its mesh and armature may
  sit in different spaces (a 0.01 armature scale, skinning through bind matrices), so take the world box with
  `Box3.setFromObject`, never from raw vertex positions. An FBX round trip drops PBR image references: rebind the
  materials from the source file.

## Skin weights and twist: pose only what the skin can carry

- **Wrists that wring.** Seen: crushed wrists, collapsed shirt and jacket cuffs, a hand bending off a thin wrist;
  the pose had 164 and 126 deg of roll at the two wrists. Cause: no forearm twist bones, so the forearm's skin is
  weighted only between the lowerarm and the hand, and linear blend skinning collapses any turn about the forearm's
  axis into the wrist. Fix in the pose: split every arm by swing and twist (quaternion swing-twist about the bone's
  own axis) into elbow flex (about the lowerarm's local X), the elbow's off-hinge remainder, the forearm's own turn,
  wrist roll and wrist bend, and pose only what the rig carries:
  - the elbow is a pure hinge;
  - the elbow's swivel about the shoulder-wrist line is chosen so the hand's twist against the forearm comes out
    near zero, and the hand then only bends;
  - a few hand frames for the grip are searched, scored at 3 per degree of wrist twist, 2 per degree of bend past
    20 deg (0.3 below), with a heavy cost for the elbow above the shoulder and for upper-arm roll past 100 deg;
  - whatever twist is left goes to the forearm's own turn (a share of 0.6, at most 55 deg), where the elbow skin
    hides it better than the wrist does.

  Measured: wrist roll at most 35 deg everywhere, cuffs on the wrists in close-ups; fretting hands 30 to 35 deg at
  the wrist plus 40 to 48 deg of forearm turn; a plucking hand bent 47 deg (the weakest spot that remained).
- **The real fix is twist bones (unverified).** A fretting hand needs roughly 75 to 85 deg of forearm turn
  (an estimate), more than the pose workaround can hide. Add two twist bones between the lowerarm and the hand,
  split the forearm's weights along its length, and spread the hand's turn about the forearm over them (1/3 and
  2/3). The stage page already does the spreading for bones named `lowerarm_twist_01_[lr]` and
  `lowerarm_twist_02_[lr]`; no exported body has them yet, so the weights and the look are untested.
- **Reaching the target is not a pass.** A pose report with 0 mm wrist miss and 0 faces inside the instrument
  passed while the wrists were crushed. Put the rig's own limits (wrist roll, off-hinge elbow, wrist bend) in every
  pose report, with a threshold, beside the target misses.
- **Fix from the state that was liked.** One forearm clip turned into six global searches that drifted into a
  winged elbow and a contortionist's pose. The fix was a joint the solver never had: roll the shoulder forward and
  lift it. Give the solver the joints a body would use (shoulder girdle, chest turn) before adding constraints, and
  look at a render before a third search.
- **Measure real people for a grip.** A hand landmark model on photos of real guitarists gave the picking arm: upper
  arm 24 to 29 deg from vertical, wrist bend 22 to 35 deg, fingers curled 100 to 130 deg each. A posed hand
  matched to that read right; an invented one did not.

## Start poses: world frames, not local rotations

- **Store each bone's world frame, for rest and pose.** The format that worked: per bone, the head, tail and the
  bone's X axis in scene metres, once for the rest and once for the pose. The runtime turns each bone, parents
  first, by frame(pose) times frame(rest) inverse, applied to its own rest rotation in world space, and puts the
  pelvis at the pose's head. Why: local rotations depend on each tool's bone roll and axis conventions (Blender's
  roll, glTF node rotations, an A or T rest), and a world turn on top of each rig's own rest cancels them. The same
  idea retargets between different rigs. Measured: the stage matched the pose file to 0.0 deg on every finger bone.
- **Pose in the scene's frame.** Put the posed armature where the scene has that person (their group's location
  and rotation) before writing the file, or the pose lands at the origin.
- **Facing.** Know which way the rig faces at rest. A statue baked into a mesh carries its facing in the mesh, not
  in its object's rotation, so read it from the body.
- **Read the pose back by number.** After loading, read the joints from the stage (`stage_actor_pose`): hands at
  1.177 m over a bar top at 1.142 m, the pelvis over the seat. Measure contact on the skin, not on bones.
- **Seats and counters, by skin.** Seen: a bar guest 2.5 cm above the cushion with forearms 6 cm into the bar top.
  Fix: turned 11 deg about the seat, back 4 cm and down 3.4 cm, he sat 4 mm into the cushion with forearms 2 mm over
  the bar. A booth couple needed the hips up 1 cm and the wrists up 4 cm. A drummer's seat skin sits 8 to 9 mm into
  the padding on purpose. Tolerances that served as a clip scan: pelvis 5 to 12 cm above the seat top and within
  12 cm of its centre; soles 0 to 3 cm above the floor or a rung; a resting palm 1 to 5 cm above a counter; "through"
  means deeper than 2 cm for 3 frames or more.
- **Pick start poses from the person's own takes, by measurement.** Seen: six dancers who "looked shy": their poses
  held the wrists 28 to 37 cm under the head and 15 to 25 cm apart. Fix: frames from the person's own whole-song
  dance, picked by measured wrist height and spread, one per dancer; hands now 18 to 63 cm off the torso line.

## Hands, fingers and grips

- **Map tracked joints straight onto bones, with a calibration from the rig's rest.** A WebXR hand joint has -Z
  along the bone toward the tip and +Y out of the back of the hand (the nail). Per bone, compute the turn that
  carries those axes into the bone's own frame at rest, then bone = joint times that calibration. Take "back of the
  hand" for the hand and the four fingers from the rig's own palm (the hand bone is rolled about 41 deg off it), and
  for the thumbs from their nail axis. Seen before this: "his thumbs are completely the wrong way", because the old
  map assumed every joint lay along the arm at rest and this rig rests in an A-pose.
- **Fingers from a posed start.** Seen: a drummer posed with fingers wrapped round his sticks; in a Follow, the
  person's open hand at the start already read as a deep curl, and the fingers went wrong. Cause: relative mode added
  the person's finger motion on top of the start pose's curl. Fix: at GO a posed body takes the person's own hands
  and fingers (they matched the tracked joints within 1 deg); for a grip that must hold (sticks, a guitar neck), keep
  the start pose's hands and add the person's motion to them (`hands='pose'` on the stage).
- **A fist that looks open.** Seen: a picking fist, closed in the Blender render, looked open and bent back in the
  headset. Measured: the stage matched the file exactly; the view hid it. The render looked down on the back of the
  hand, the person looked from the thumb side, where a loose curl shows gaps. Fix: render from the viewer's eye pose
  before blaming the transfer, then tighten the curl: end joints from 33 to 39 deg up to 62, the index knuckle from
  57 to 79. Bend a finger by a world rotation about its own bend axis (the cross of its two segments) so the curl
  stays in its plane, and check the angle grew (undo if it opened).
- **Grips on held things.** Place the held thing from the posed hand bones (a glass from the hand that holds it),
  then measure skin against it: the deepest skin point inside it and the nearest gap. Thumbs on guitar necks about
  1 cm off still read as playing; a take that moves the body leaves its props behind unless they are pinned (next
  section).
- **Two hands on one point pass through each other.** Partners' joined hands: 9 cm apart along the line between
  them, one under, one over, and they read as clasped.
- **The person's own hands in VR.** In three.js, calling `renderer.xr.getHand()` stops the controller grip pose from
  updating while hands are tracked, with no error: anything parented to the grip freezes. Parent wrist-worn things to
  the wrist joint and fall back to the grip when the joints are not visible. A hand holding a real object never reads
  as a pinch (the fingers wrap it), and a resting hand (wrist about 50 cm under the eyes, fingers hanging) should do
  nothing at all. A custom hand mesh for the person: rigged to the 25 WebXR joint names, under about 8 k triangles,
  one material, one 1024 px texture (**unverified** on the headset).

## Props held by or pinned to bones

- **Pin a held prop to its bone.** Place it where it belongs on the body first (in the hand, on the strap), then pin
  it (`stage_prop_pin`, bone `hand_r`): it rides the body at rest, in a take and in a Follow, keeping its offset (a
  drumstick sat about 3 cm from the hand bone).
- **One object per pin.** Seen: a pin refused with "no object named band_stick_r". Cause: the room export grouped
  the two sticks, and the merge step for the headset joined the group into one mesh. Fix: export each held prop as
  its own object, and check the names in the exported scene before pinning.
- **Fit an instrument to the posed skin, not the bones.** Measure skin and clothes against the instrument: an upper
  arm pressed about 15 mm into a guitar's edge after the forearm had cleared it, so check every arm segment, not
  only the hand. Keep clearances to other things (a guitar with 0 vertices within 15 mm of a mic stand), check the
  mic against the lips (one had to come down 40 mm to a singer's lips), and check the feet against stand bases and
  cables.

## Following a live person

- **Take offsets at GO, after a countdown.** Seen: the person pressed Follow standing, then sat at the bar; the
  character's hands stayed "up and to the right" while the person's were on the bar. Cause: offsets were taken at
  the button press. Fix: a countdown into the start pose, and relative mode takes the person's pose at GO as zero:
  their head turn turns the character's head and spine, their wrists move and turn its hands.
- **Shoulders follow the arms.** Seen: reaches went through the body, and turning a hand swung the shoulder. Fix
  that worked: the clavicle takes 30% of the arm's swing away from the start pose, at most 25 deg. The hand-turn part
  has the same cause as the wrung wrists (no twist bones) and waits on them (**unverified**).
- **A standing body from a headset.** Moving the whole body with the head looked wrong ("sometimes the rest of my
  body moves, but that's not what's going on"). Values in use: the hips stay put while the head moves within 12 cm
  of above them (the spine bends instead); the body turns only when the head is more than 35 deg off it (a look
  aside turns neck and spine, not feet); a foot steps when it lags 22 cm, in 0.28 s, lifted 7 cm.
- **Seated: pin the hips, and keep the pin in playback.** A pinned pelvis stays on the seat; the person's head bends
  the spine by direction only, so sitting lower or higher in the real room does not sink or lift the character.
  Seen: a seated take stood up at the bar in playback, anchored by the feet. Cause: the pin did not reach playback.
  Fix: a take recorded with a pin keeps it on playback.
- **Hands from the eyes.** The person's eyes sit about 8 cm above and 9 cm in front of the head bone; place their
  hands relative to the eyes.
- **Scale by the median head height.** A take is scaled by the character's height over the person's standing head
  height, clamped to 0.6 to 1.4. Use the median: a high percentile took moments raised on the thumbstick for standing
  height. A scale near the limit (1.39 once) means the take was recorded sitting or crouching, and the reach may look
  too big (**unverified** look).
- **Interpolate playback.** Seen: hands that stuck and jumped. Cause: a take recorded at 19.8 samples a second and
  played at 72 fps held each sample; the hand sat still on 73% of frames. Fix: blend the two samples around each
  frame: 5%, at about 0.03 ms per person per frame. A loop's seam is a 0.4 s cross-fade with quaternions sign-aligned
  before blending. Any smoothing over rotation vectors needs each frame unwrapped to the equivalent nearest the last
  one, or a correct take flips the long way round.
- **Mirror travels with the take.** A Follow done mirrored must play back mirrored: store the flag in the take.
- **Processed takes clip more than raw ones.** Seen: beat-warped dance takes on six dancers looked "erratic", arms
  through bodies. Measured: 3 to 4 of about 50 samples had a hand inside the torso (closest 10 cm); the raw loops had
  none (closest 16 to 21 cm). Run the self-clip check on every processed take before it plays on anyone.

## Checks that catch it

- **Joint self-clip over a take.** Sample the joints every 0.1 s through the take; a hand or forearm closer than
  13 cm (a torso radius) to the pelvis to spine to head line counts as inside; report the count and the closest. For
  posed players with instruments the bar used was 24 cm. Limit: joint centres only, so an upper arm sunk into the
  torso is not measured.
- **Mesh self-clip on a posed body.** Give every vertex (body and clothes) its dominant bone; build a tree of the
  faces whose vertices all belong to the torso and thighs; count forearm, hand and finger vertices more than 3 mm
  inside those surfaces. After the band's fix: 0 for all three players.
- **Review on the body the headset draws.** Import the exported GLB and pose it the way the page does (world frames,
  linear blend skinning), not the file you posed in. In Blender, clear its stored custom normals and unlink normal
  maps, or the posed body shatters under EEVEE, and dither its alpha. The first band review on the authoring file
  passed; the person saw crushed cuffs in the headset.
- **Close-up renders of every wrist and every contact**, at the keys and between them: hands on a bar, a seat, a
  partner, a held thing.
- **Render from the viewer's eye pose.** Build the camera from a headset snapshot's head pose (camera Z is minus
  forward, X is up cross Z, vertical field of view). It settles "looks wrong from here" against "is wrong" in one
  picture (the open-looking fist above).
- **Contact sheets of stage captures** for motion: frame-locked captures read as a sheet found a camera passing
  through a dancer, actors still arms-out, and a stage with no band.
- **Read numbers back from the stage**, not from the export script: joints from `stage_actor_pose`, names from the
  exported scene.

## Materials and skin on export

- **Black shards that look like inverted normals.** Cause: MakeHuman wires texture alpha into every material, so
  clothes, shoes, hats and eyes export as alpha BLEND, and three.js does not sort triangles inside one blended mesh:
  the inside of a jacket draws over its outside. A winding check found 0 to 1% of faces disagreeing, and turning
  normal maps off changed nothing. Fix: every textured material at full opacity becomes opaque with `alphaTest`
  0.5 and depth writes on; glass and bottles under full opacity keep blending. Apply it by material, never by "is
  skinned": posed people merged into static meshes are not skinned, and an older skinned-only rule missed them.
- **Skin comes through dark.** MakeHuman's node-group skin materials do not survive the glTF exporter. A flat skin
  tone served the headset; bake the skin if it must show detail.
- **Garment through garment.** Hiding the body under clothes stops skin poking through, but a shirt can still show
  through a waistband (visible only close). A looser garment fixes it; otherwise say so.

## What was MakeHuman, and what holds everywhere

- **This rig only:** the 53 `game_engine` bone names, no twist bones, an A-pose rest facing -Y, a hand bone rolled
  about 41 deg off the palm, thumb nails on local -Z, alpha BLEND on every textured material, dark node-group skin.
  Check each of these on your rig.
- **Any rig:** the exporter's rest-pose default, linear blend skinning wringing a twist across one joint, start poses
  as world frames, offsets taken at GO, one object per pinned prop, the self-clip checks, review on the runtime
  body, and renders from the viewer's own eyes.
