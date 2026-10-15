// Scenes from inside the experience (the user, 2026-10-03: "you're making it so that you can easily control scene
// swaps just like how you do in-experience editing of the scene for me"). Going to another scene, or bringing in a
// re-export of this one, happens under the construct: the grey void closes around the user, the room is swapped,
// they are placed (where this headset last stood in that scene, its start camera, or where Claude says), and the void
// opens on the new room. The rig, the hands, voice and the live link stay; every module follows the scene by name.
//   live: scene_go {name, position?, target?, reload?}  (position/target in Blender xyz, as goto)
//         scene_list                                     -> the scenes the server has
//   events: scene_leaving {from, to} on the old scene's bus, scene_switched {from, to, objects, ms} on the new one.
// After scene_switched, send further commands to the new scene (live.py -s <name>): the page listens there now.

export function initScenes(ed, live, xr, construct, mods) {
  let busy = null;

  async function list() {
    const j = await fetch('scenes', { cache: 'no-store' }).then((r) => r.json());
    const names = Array.isArray(j) ? j : j.scenes || [];
    return { current: ed.sceneName, scenes: names.filter((n) => !String(n).startsWith('_')) };
  }
  // run fn with the void closed over the room; if it fails, the old room comes back
  async function under(title, line, fn) {
    await construct.cover(title, line);
    try { return await fn(); } catch (e) { if (ed.root) ed.root.visible = true; throw e; } finally { construct.dissolve(); }
  }
  async function go(c) {
    const name = c.name || c.scene;
    if (!name) throw new Error('no scene given (scene_list names them)');
    if (busy) throw new Error('a scene change is already running');
    const from = ed.sceneName, same = name === from;
    if (same && !c.reload) return { from, to: name, already: true };
    busy = (async () => {
      const t0 = performance.now();
      live.emit('scene_leaving', { from, to: name });
      try { if (ed.dirty && ed.dirty()) await ed.save(); } catch (_) { /* the autosave history has it */ }
      if (mods.actors) for (const p of [...mods.actors.playing.keys()]) mods.actors.stop({ person: p });
      if (mods.view) mods.view.clear();
      const info = await under(name.replace(/_/g, ' '), same ? 'updating the room' : `going to ${name}`, async () => {
        const r = same ? await ed.reload(Date.now()) : await ed.switchTo(name);
        const u = new URL(location.href);                 // a reload of the page lands here too
        u.searchParams.set('scene', name);
        history.replaceState(null, '', u.toString());
        xr.placeInScene();
        if (c.position) {                                   // placed where Claude says; never held up by an animation
          const g = Promise.resolve(live.handlers.goto({ position: c.position, target: c.target || c.position })).catch(() => null);
          await Promise.race([g, new Promise((res) => setTimeout(res, 2500))]);
        }
        return r;
      });
      if (live.rebased) await Promise.race([live.rebased(), new Promise((res) => setTimeout(res, 3000))]);
      const res = { from, to: name, objects: info.objects, ms: Math.round(performance.now() - t0) };
      live.emit('scene_switched', res);
      return res;
    })();
    try { return await busy; } finally { busy = null; }
  }
  // a re-export of this scene (live.js watches for it) comes in under the void too, never as a frozen frame
  live.setWrapSwap((fn) => under(ed.sceneName, 'updating the room', fn));
  live.handlers.scene_go = (c) => go(c);
  live.handlers.scene_list = () => list();
  return { go, list, under };
}
