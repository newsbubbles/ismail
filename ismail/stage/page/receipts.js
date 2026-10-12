// What the VR card says about the newest voice note and the sessions' busy lines (presence.py receipt() and
// busy_now(), through /live/version): [top line, second line, warn] or null for nothing to say.
//   receipt: {scene, note_id, by: [who, in the order they were handed it], answered, stuck: [who]} or null
//   busy:    {who: {text, age_s, left_s}}
// A busy line from a session that took the note goes under it; with none, any running busy line while the note is
// unanswered (the one who will answer may be another session).
const nm = (w) => (w === 'unnamed' ? 'an unnamed agent' : w);

export function noteText(receipt, busy) {
  const r = receipt, by = (r && r.by) || [];
  const b = by.find((w) => busy[w]) || (!r || !r.answered ? Object.keys(busy)[0] : null) || null;
  const busyLine = b ? nm(b) + ': ' + busy[b].text : '';
  if (r && r.stuck && r.stuck.length) return [r.stuck.map(nm).join(', ') + ' looks stuck', busyLine || 'no answer for 2 minutes', true];
  if (r && by.length) return ['heard by ' + by.map(nm).join(', '), busyLine, false];
  return b ? [nm(b) + ' is busy', busy[b].text, false] : null;
}
