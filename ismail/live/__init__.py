"""ismail live: play a queue of clips in real time while an agent edits it.

engine.py   the player process (clock, scheduler, render workers, mixer, device, local HTTP control)
timeline.py the queue rules in beats (pure; tested without audio)
safety.py   master trim, level rider, lookahead limiter, hard ceiling (trim and cap from live_start or the
            environment, the ceiling from the environment)
worker.py   render one note/phrase with the offline instrument code (performer voices, baked studio effects)
ops.py      the live_* agent ops (registered into ismail.api.OPS)
fx_blocks.py, dsp_blocks.py
            the live twins of studio effects and DSP: processors that keep state between blocks

Studio and live: the studio code (ismail/*.py outside live/) is the source of truth and live never edits it.
Live imports studio voices, instruments and effect definitions read-only; where live needs a different shape
(block-by-block effects) it keeps its own twin here, held to the studio version by tests/test_live_parity.py. A
studio effect with no twin is baked: the render workers run the studio function on each note or phrase
(graph.split_chain). A studio voice module with perform() and no voice() is a performer: overlapping notes render
as one event with the clip's expr lanes. So studio work lands in main without touching live, and live work
without touching studio.
"""
