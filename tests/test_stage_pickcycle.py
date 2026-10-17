"""A pinch again at the same point takes the next thing under it (page/pickcycle.js, run in node)."""
from pathlib import Path

import nodejs

CYCLE = (Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page' / 'pickcycle.js').as_uri()

# a point with the two methods pickcycle.js uses
V = """const V = (x, y, z) => ({ x, y, z, clone() { return V(this.x, this.y, this.z); },
  distanceTo(o) { return Math.hypot(this.x - o.x, this.y - o.y, this.z - o.z); } });"""


def _node(body):
    js = f"{V}\nimport('{CYCLE}').then((m) => {{ const seen = []; m.setPickEmit((t, e) => seen.push(e)); {body} }});"
    return nodejs.run(js)


def test_a_second_pinch_at_the_same_point_takes_the_next_thing():
    got = _node("""
      const pete = { name: 'person_bartender' }, cup = { name: 'cup' }, rag = { name: 'bar_rag' };
      const items = [pete, cup, rag], at = V(1, 1, 1), out = [];
      for (let i = 0; i < 4; i++) out.push(m.cyclePick(at, items, 'ray', 1).name);
      out.push(m.cyclePick(V(1.03, 1, 1), items, 'ray', 1).name);   // 3 cm off: the same point
      out.push(m.cyclePick(V(1.5, 1, 1), items, 'ray', 1).name);    // elsewhere: the usual pick again
      out.push(m.cyclePick(V(1.5, 1, 1), [cup], 'touch').name);       // one thing: nothing to cycle
      console.log(JSON.stringify({ out, seen }));""")
    assert got['out'] == ['person_bartender', 'cup', 'bar_rag', 'person_bartender', 'cup', 'person_bartender', 'cup']
    assert got['seen'][1] == {'how': 'ray', 'item': 'cup', 'n': 2, 'of': ['person_bartender', 'cup', 'bar_rag']}
    assert len(got['seen']) == 6                                        # no event for a single thing


def test_a_long_ray_allows_a_wider_same_point_and_the_list_can_change():
    got = _node("""
      const a = { name: 'a' }, b = { name: 'b' }, c = { name: 'c' }, out = [];
      out.push(m.cyclePick(V(0, 0, 0), [a, b], 'ray', 4).name);
      out.push(m.cyclePick(V(0.12, 0, 0), [a, b], 'ray', 4).name);      // 12 cm at 4 m is the same point
      out.push(m.cyclePick(V(0.12, 0, 0), [c, a], 'ray', 4).name);      // b no longer under it: the usual pick
      console.log(JSON.stringify({ out }));""")
    assert got['out'] == ['a', 'b', 'c']
