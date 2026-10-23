"""Live processor for the spatial effect (ismail/spatial.py): the studio's Spatializer run block by block, so the
two are one kernel and agree to the sample (tests/test_fx_blocks.py, tests/test_live_parity.py).

Trajectory time: live, a trajectory starts when the effect is inserted or its trajectory is set, whichever is
later (a changed trajectory rebuilds the processor, so both cases are the processor's first block). Offline (the
studio twin) the anchor is the window's first block, and Env.offset_samples puts song time 0 where the studio has
it, so a studio trajectory runs on song time."""
from .. import spatial as S
from . import fx_blocks
from .fx_blocks import Proc


class _Spatial(Proc):
    hold_s = S.MAX_DELAY_S + 0.25     # every read lies within MAX_DELAY_S + 0.1 s (delay, ground, early reflections):
                                      # the path stays awake until a far source's sound has arrived

    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.sp = S.Spatializer(fx, env.sr)
        self.anchor = None
        self.next_pos = None

    def process(self, x, blk):
        if self.anchor is None:
            self.anchor = blk.pos
        elif blk.pos != self.next_pos:
            self.sp.snap()          # the engine skipped blocks (dormant path): take the position as it is now
        self.next_pos = blk.pos + x.shape[1]
        t0 = blk.pos - self.anchor - self.env.offset_samples
        return self.sp.process(x, lambda name: blk.param(name, self.fx[name]), t0)


PROCS = {'spatial': _Spatial}
fx_blocks.PROCS.update(PROCS)
