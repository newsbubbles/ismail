"""A Quest diet for any room before its VR export (the user, 2026-10-03, in the bedroom at 21 to 42 fps: "we need a
function to help us with this"). The bar's lessons (bf_detail.vr_prep / vr_merge), made generic:

- every grabbable group joined into one mesh under its root's name (the bedroom drew ~650 pieces per eye; draw calls,
  not triangles, were the cost), groups holding an armature left alone (posed people stay people);
- a triangle budget per mesh (decimate over the VR_TRI_MAX switch, default 6000; names in KEEP_PREFIX get twice that);
- textures capped at 1024 px;
- MakeHuman skin (a node group the exporter cannot read) rebuilt as a plain material: its diffuse picture when there
  is one, else the bar's flat tone; and no mask picture used as a normal or roughness map (the bedroom's Cyrus got
  his lip mask as both: a black face and flat pink arms).

Call quest_diet() just before the bridge, with the VR_DETAIL switch set so the joined parts' sources are left out.
"""
import json
import os
import sys

import bpy

S = bpy.context.scene
KEEP_PREFIX = ('es335',)                     # the hero guitar: held close, so a looser budget


def _link(ob):
    S.collection.objects.link(ob)
    return ob


def _mask_upstream(node, seen=None):
    """True when a picture named *mask* feeds this node, however many nodes back."""
    seen = seen if seen is not None else set()
    if node is None or node.name in seen:
        return False
    seen.add(node.name)
    if node.type == 'TEX_IMAGE' and node.image and 'mask' in node.image.name.lower():
        return True
    return any(_mask_upstream(l_.from_node, seen) for i in node.inputs for l_ in i.links)


def skin_and_masks():
    fixed = []
    for m in bpy.data.materials:
        if not m.use_nodes:
            continue
        nt = m.node_tree
        for n in nt.nodes:                   # no mask picture drives a normal or roughness map
            if n.type == 'NORMAL_MAP' and n.inputs['Color'].links:
                src = n.inputs['Color'].links[0].from_node
                if src.type == 'TEX_IMAGE' and src.image and 'mask' in src.image.name.lower():
                    for l_ in list(n.outputs['Normal'].links):
                        nt.links.remove(l_)
                    fixed.append(m.name + ' normal')
            if n.type == 'BSDF_PRINCIPLED':
                for sock in ('Roughness', 'Metallic', 'Normal'):
                    for l_ in list(n.inputs[sock].links):
                        if _mask_upstream(l_.from_node):  # through bumps, mixes and maths too (the 1958 suit)
                            nt.links.remove(l_)
                            fixed.append(f'{m.name} {sock.lower()}')
                for sock in ('Coat Weight', 'Sheen Weight'):
                    if sock in n.inputs:
                        n.inputs[sock].default_value = 0.0
        if not m.name.endswith('.body'):
            continue
        diffuse = next((n.image for n in nt.nodes if n.type == 'TEX_IMAGE' and n.image
                        and 'diffuse' in n.image.name.lower()), None)
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        out = nt.nodes.new('ShaderNodeOutputMaterial')
        b = nt.nodes.new('ShaderNodeBsdfPrincipled')
        nt.links.new(b.outputs['BSDF'], out.inputs['Surface'])
        b.inputs['Roughness'].default_value = 0.55
        if diffuse is not None:
            t = nt.nodes.new('ShaderNodeTexImage')
            t.image = diffuse
            nt.links.new(t.outputs['Color'], b.inputs['Base Color'])
        else:
            b.inputs['Base Color'].default_value = (0.22, 0.12, 0.075, 1)
        if hasattr(m, 'blend_method'):
            m.blend_method = 'OPAQUE'
        fixed.append(m.name + (' skin from ' + diffuse.name if diffuse else ' flat skin'))
    print('VR quest: materials', fixed)


def budget():
    sw = (getattr(sys, 'vr_job', None) or {}).get('switches') or {}       # the export's job (stage_scene_export)
    tri_max = int(sw.get('VR_TRI_MAX') or os.environ.get('VR_TRI_MAX') or 6000)
    cut = []
    for o in list(S.objects):
        if o.type != 'MESH' or o.hide_render or o.get('vr_keep_tris'):
            continue
        if any(md.type == 'ARMATURE' for md in o.modifiers):
            continue                         # skinned people: decimating a skin tears it at the joints
        lim = tri_max * (2 if o.name.startswith(KEEP_PREFIX) else 1)
        tris = sum(len(p.vertices) - 2 for p in o.data.polygons)
        if tris > lim:
            md = o.modifiers.new('vr_budget', 'DECIMATE')
            md.ratio = max(0.08, lim / tris)
            cut.append(f'{o.name} {tris}->{lim}')
    for img in bpy.data.images:
        w, h = img.size
        if max(w, h) > 1024:
            k = 1024 / max(w, h)
            img.scale(max(1, int(w * k)), max(1, int(h * k)))
    print('VR quest: decimated', len(cut), cut[:12])


def merge_groups():
    n0 = len([o for o in S.objects if o.type == 'MESH' and not o.hide_render])
    dg = bpy.context.evaluated_depsgraph_get()
    for root in [o for o in S.objects if o.parent is None and o.type in ('MESH', 'EMPTY')]:
        if any(o.type == 'ARMATURE' for o in root.children_recursive):
            continue
        kids = [o for o in root.children_recursive if o.type in ('MESH', 'CURVE') and not o.hide_render]
        if len(kids) + (root.type == 'MESH') < 2:
            continue
        srcs = ([root] if root.type == 'MESH' and not root.hide_render else []) + kids
        copies, parts = [], {}
        for k, o in enumerate(srcs, 1):
            me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
            # each piece's vertices carry its number (_part, exported: the name starts with _), so the page can split
            # a piece back out of the joined mesh by name and move it live (the user, 2026-10-13: the heater could not
            # be turned because it no longer existed on its own)
            me.attributes.new('_part', 'FLOAT', 'POINT').data.foreach_set('value', [float(k)] * len(me.vertices))
            c = _link(bpy.data.objects.new(o.name + '_m', me))
            c.matrix_world = o.matrix_world
            copies.append(c)
            if o is not root:
                loc, q, sc = o.matrix_world.decompose()
                parts[o.name] = {'part': k, 'parent': o.parent.name if o.parent else None,
                                 'location': list(loc), 'quaternion': list(q), 'scale': list(sc)}
            o.hide_render = True
        with bpy.context.temp_override(active_object=copies[0], selected_editable_objects=copies, selected_objects=copies):
            bpy.ops.object.join()
        j = copies[0]
        if root.type == 'MESH':              # the joined mesh takes the root's name, so a grab and an edit still land
            nm = root.name
            root.name = nm + '_src'
            j.name = nm
        else:
            M = j.matrix_world.copy()
            j.parent = root
            j.matrix_world = M
            j.name = root.name + '_mesh'
        merged = json.loads(S.get('vr_merged') or '{}')     # the manifest's merged: piece name -> {into, part, ...}
        merged.update({n: {'into': j.name, **e} for n, e in parts.items()})
        S['vr_merged'] = json.dumps(merged)
    print('VR quest: merged', n0, '->', len([o for o in S.objects if o.type == 'MESH' and not o.hide_render]), 'meshes')


def quest_diet():
    skin_and_masks()
    budget()
    merge_groups()
