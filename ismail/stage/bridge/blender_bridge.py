"""The Blender side of the VR scene editor. Any build script execs this at its end (after the scene is built, before
it renders):
    exec(open('.../vr/blender_bridge.py').read())
VR_EXPORT=<dir>   writes <dir>/scene.glb (Y-up glTF, modifiers applied, curves as meshes, volumes left out) and
                  <dir>/manifest.json (every object's Blender name, type, world location and quaternion, light energy
                  and colour, material base colours), then quits without rendering.
VR_PASS=<f>[;<f>] a derived scene's passes (world.json derives_from + pass), run in order on the built parent
                  scene before the edits (each file is exec'd with S = the scene); paths joined by os.pathsep
VR_DIET=1         the Quest diet (quest.py next to this file, found through VR_BRIDGE) after the passes and
                  edits, before the export: merged groups, triangle and texture budgets, MakeHuman skin.
                  A room script that defines stage_diet() before exec'ing this file gets its own diet instead
                  (it must skip that diet in its build when VR_DIET is set, so the passes see whole objects)
VR_EDITS=<file>   applies an edits.json written by the editor (Blender world space, Z-up):
                  {"objects": {name: {"location": [x,y,z], "quaternion": [w,x,y,z], "scale": [x,y,z]}},
                   "lights": {name: {"energy": W, "color": [r,g,b]}},
                   "materials": {name: {"base_color": [r,g,b]}}}"""
import json
import os

import bpy


def _base_color(m):
    b = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) if m.use_nodes else None
    return b, (list(b.inputs['Base Color'].default_value)[:3] if b else None)


def _upstream_image(node, depth=0):
    if node.type == 'TEX_IMAGE':
        return node
    if depth > 12:
        return None
    for inp in node.inputs:
        for l_ in inp.links:
            t = _upstream_image(l_.from_node, depth + 1)
            if t is not None:
                return t
    return None


def vr_export(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    S_ = bpy.context.scene
    man = {'objects': {}, 'lights': {}, 'materials': {}}
    for ob in list(S_.objects):
        if any(s.material and s.material.node_tree and any(n.type == 'OUTPUT_MATERIAL' and n.inputs['Volume'].links
                                                            for n in s.material.node_tree.nodes)
               for s in ob.material_slots):
            bpy.data.objects.remove(ob)                           # volumes do not exist in glTF
            continue
    for ob in list(S_.objects):
        if ob.type in ('CURVE', 'FONT'):
            bpy.ops.object.select_all(action='DESELECT')
            ob.select_set(True)
            bpy.context.view_layer.objects.active = ob
            bpy.ops.object.convert(target='MESH')
    for m in bpy.data.materials:
        b, col = _base_color(m)
        if b is None:
            continue
        for sock in ('Base Color', 'Emission Color'):              # procedural colour: the exporter gets the base value
            for l_ in list(b.inputs[sock].links):
                if l_.from_node.type != 'TEX_IMAGE':
                    tex = _upstream_image(l_.from_node)             # a picture under the wear (posters, labels, clothes)
                    m.node_tree.links.remove(l_)
                    if tex is not None:
                        m.node_tree.links.new(tex.outputs['Color'], b.inputs[sock])
        man['materials'][m.name] = {'base_color': col}
    bpy.context.view_layer.update()
    for ob in S_.objects:
        if os.environ.get('VR_DETAIL') and ob.hide_render:
            continue                                               # stand-ins a built part replaced (not exported)
        loc, q, sc = ob.matrix_world.decompose()
        man['objects'][ob.name] = {'type': ob.type, 'location': list(loc), 'quaternion': list(q), 'scale': list(sc)}
        p_ = ob
        while p_ is not None and not p_.get('vr_lock'):
            p_ = p_.parent
        if p_ is not None:                     # the building's fixed parts: hands in VR cannot grab them (doors got knocked)
            man['objects'][ob.name]['locked'] = True
        if ob.type == 'LIGHT':
            ld = ob.data
            man['lights'][ob.name] = {'kind': ld.type, 'energy': ld.energy, 'color': list(ld.color),
                                      'size': getattr(ld, 'size', None), 'size_y': getattr(ld, 'size_y', None),
                                      'spot_size': getattr(ld, 'spot_size', None)}
    if S_.camera is not None:
        man['start'] = S_.camera.name                             # the editor opens here
    # the world light (glTF does not carry it): the page adds it as fill, or every surface facing away from the lights
    # renders black (a club ceiling read as night sky); and the sky the scene asks for (scene['vr_sky'], e.g. 'night')
    wd = S_.world
    if wd is not None and wd.use_nodes:
        bg = next((n for n in wd.node_tree.nodes if n.type == 'BACKGROUND'), None)
        if bg is not None:
            man['world'] = {'color': list(bg.inputs['Color'].default_value)[:3], 'strength': bg.inputs['Strength'].default_value}
    if S_.get('vr_sky'):
        man['sky'] = S_['vr_sky']
    # pieces the Quest diet joined into one mesh (quest.py merge_groups): the page splits one back out by name
    mg = {n: e for n, e in json.loads(S_.get('vr_merged') or '{}').items() if e['into'] in man['objects'] and n not in man['objects']}
    if mg:
        man['merged'] = mg
    bpy.ops.export_scene.gltf(filepath=os.path.join(out_dir, 'scene.glb'), export_format='GLB', export_apply=True,
                              export_lights=True, export_cameras=True, export_yup=True,
                              export_rest_position_armature=False,   # skinned characters in their posed state, not the T-pose
                              use_renderable=bool(os.environ.get('VR_DETAIL')),
                              # the built club's pictures as WebP, alpha kept (the PNGs were 30 of 43 MB; the Quest stalled)
                              export_image_format='WEBP' if os.environ.get('VR_DETAIL') else 'AUTO',
                              export_image_quality=80,
                              export_attributes=True)                # only names starting with _ (clock.js growth: _born ...)
    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(man, f, indent=1)
    print('VR EXPORT', out_dir, len(man['objects']), 'objects')


def vr_apply(path):
    with open(path, encoding='utf-8') as f:
        ed = json.load(f)
    for name, t in ed.get('objects', {}).items():
        ob = bpy.data.objects.get(name)
        if ob is None:
            print('VR EDITS: no object', name)
            continue
        if ob.parent is not None:                                  # edits are world space: go through matrix_world
            from mathutils import Matrix, Quaternion, Vector
            loc, q, sc = ob.matrix_world.decompose()
            loc = Vector(t.get('location', loc))
            q = Quaternion(t.get('quaternion', q))
            sc = Vector(t.get('scale', sc))
            ob.matrix_world = Matrix.LocRotScale(loc, q, sc)
            continue
        if 'location' in t:
            ob.location = t['location']
        if 'quaternion' in t:
            ob.rotation_mode = 'QUATERNION'
            ob.rotation_quaternion = t['quaternion']
        if 'scale' in t:
            ob.scale = t['scale']
    for name, t in ed.get('lights', {}).items():
        ob = bpy.data.objects.get(name)
        if ob is not None and ob.type == 'LIGHT':
            if 'energy' in t:
                ob.data.energy = t['energy']
            if 'color' in t:
                ob.data.color = t['color']
    for name, t in ed.get('materials', {}).items():
        m = bpy.data.materials.get(name)
        b, _ = _base_color(m) if m else (None, None)
        if b is not None and 'base_color' in t:
            src = b.inputs['Base Color']
            if src.links:                                          # procedural colour: tint it instead
                mixn = m.node_tree.nodes.new('ShaderNodeMix')
                mixn.data_type, mixn.blend_type = 'RGBA', 'MULTIPLY'
                mixn.inputs[0].default_value = 1.0
                base = src.default_value
                tint = [c / max(bc, 1e-4) for c, bc in zip(t['base_color'], base[:3])]
                m.node_tree.links.new(src.links[0].from_socket, mixn.inputs[6])
                mixn.inputs[7].default_value = (*tint, 1)
                m.node_tree.links.new(mixn.outputs[2], src)
            else:
                src.default_value = (*t['base_color'], 1)
    bpy.context.view_layer.update()
    print('VR EDITS applied from', path)


for _pass in [p for p in os.environ.get('VR_PASS', '').split(os.pathsep) if p]:
    print('VR PASS', _pass)
    exec(compile(open(_pass, encoding='utf-8').read(), _pass, 'exec'),
         {'__name__': '__vr_pass__', '__file__': _pass, 'bpy': bpy, 'S': bpy.context.scene, 'os': os})
    bpy.context.view_layer.update()
if os.environ.get('VR_EDITS'):
    vr_apply(os.environ['VR_EDITS'])
if os.environ.get('VR_DIET') and os.environ.get('VR_EXPORT'):
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.environ.get('VR_BRIDGE') or os.path.abspath('.')))
    if callable(globals().get('stage_diet')):                      # the room's own diet (merges it built for)
        stage_diet()                                                # noqa: F821
    else:
        import quest as _quest                                      # noqa: E402
        _quest.quest_diet()
if os.environ.get('VR_EXPORT'):
    vr_export(os.environ['VR_EXPORT'])
    import sys
    sys.exit(0)
