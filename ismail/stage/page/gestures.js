// The gesture map in force (server gestures.py, /gestures?scene=): which hand shape, on which hand, means which
// intent. hands.js goes on naming shapes; the modules ask here instead of naming a shape themselves, so an agent's
// rebind (stage_gesture_set, page command gestures_reload) takes effect at once. Until the map arrives, the engine's
// defaults below hold (the same rows the server ships).
const DEFAULTS = {
  vr_exit: { shape: 'thumbs_up', hand: 'both', hold_ms: 1500 },
  stop: { shape: 'thumbs_down', hand: 'both', hold_ms: 1500 },
  voice_note: { shape: 'phone', hand: 'either', hold_ms: 350 },
  travel: { shape: 'gun', hand: 'either', hold_ms: 0 },
  frame_shot: { shape: 'gun', hand: 'both', hold_ms: 300 },
  select: { shape: 'pinch', hand: 'either', hold_ms: 0 },
  hold: { shape: 'pinch', also: ['fist'], hand: 'either', hold_ms: 0 },
  reload: { shape: 'thumbs_up', hand: 'left', hold_ms: 700 },
  answer_yes: { shape: 'thumbs_up', hand: 'right', hold_ms: null },
  answer_no: { shape: 'thumbs_down', hand: 'right', hold_ms: null },
  drop: { shape: 'thumbs_down', hand: 'left', hold_ms: 500 },
};

export function initGestures(hands, sceneName) {
  let rows = JSON.parse(JSON.stringify(DEFAULTS));
  const row = (intent) => rows[intent] || DEFAULTS[intent];
  const sidesOf = (r) => (r.hand === 'either' || r.hand === 'both' ? ['left', 'right'] : [r.hand]);
  // does this hand make the intent's shape. o.cand: also while it is still forming; o.lost: also while its tracking
  // has dropped out (the shape last seen stands, as the performance stop always read it)
  function makes(intent, h, o = {}) {
    const r = row(intent);
    if (!r || r.off || !h || (!h.f && !o.lost) || h.resting) return false;
    const shapes = [r.shape, ...(r.also || [])];
    return shapes.includes(h.g) || (!!o.cand && shapes.includes(h.cand));
  }
  const G = {
    get rows() { return rows; },
    row,
    // the hand an intent's card or label sits over: its own hand, the left for either or both
    handOf(intent) { const r = row(intent); return r.hand === 'right' ? 'right' : 'left'; },
    // which side makes the intent now ('left', 'right', 'both'), or null; hands that are not its own never count
    on(intent, o = {}) {
      const r = row(intent), H = hands.state || {};
      if (!r || r.off) return null;
      if (r.hand === 'both') return makes(intent, H.left, o) && makes(intent, H.right, o) ? 'both' : null;
      for (const s of sidesOf(r)) if (makes(intent, H[s], o)) return s;
      return null;
    },
    hold(intent, fallback) { const v = row(intent).hold_ms; return v === null || v === undefined ? fallback : v; },
    // 'left thumbs up', 'either hand ok', 'both thumbs down': what a card or a label says to make
    words(intent) {
      const r = row(intent), shape = r.shape.replace('_', ' ');
      if (r.hand === 'either') return 'either hand ' + shape;
      return r.hand === 'both' && !shape.startsWith('thumbs') ? 'both hands ' + shape : `${r.hand} ${shape}`;
    },
    async reload() {
      const r = await fetch('gestures?scene=' + encodeURIComponent(sceneName())).then((x) => x.json());
      rows = r.rows || rows;
      return { intents: Object.keys(rows).length, collisions: r.collisions || [] };
    },
  };
  G.reload().catch(() => {});        // the defaults stand until it comes
  return G;
}
