// One object menu at a time, and a pick that lands while the last menu is still closing is kept, not dropped (the user,
// 2026-10-11, #25726: "when I click on Dell I don't get his pop-up menu"). Picking Dell straight from the drum kit's
// open menu closed the kit's menu, but its loop ends a tick later, so Dell's menu found it busy and was lost; picking
// him again did nothing, since he was already selected. Here the last pick made while busy opens once the menu before
// it has gone, if it is still the selection.
//   run(it): the menu's own loop (a promise that ends when the menu is closed for good)
//   current(): what is selected now
export function menuGate(run, current) {
  let busy = false, want = null;
  async function show(it) {
    if (busy) { want = it; return false; }
    busy = true; want = null;
    try { await run(it); } finally {
      busy = false;
      const w = want; want = null;
      if (w && current() === w) show(w);
    }
    return true;
  }
  // the menu for `it` is up now: a pick of it made while it was opening is answered already
  const shown = (it) => { if (want === it) want = null; };
  return { show, shown, busy: () => busy };
}
