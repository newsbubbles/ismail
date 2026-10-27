"""example shot: a lit, foggy empty stage with a slow push. Replace with a room: S.room('<stem from rip>')."""
import kit                          # `python -m ismail.video` puts the kit on Blender's path

N = 96
S = kit.Shot('s01_example', N, start_bar=1)
S.fog((0, 0, 20), (200, 200, 60), density=0.01)
S.light('SPOT', (0, -30, 40), 900, (1.0, 0.9, 0.7), target=(0, 0, 0), spot_deg=40, name='key')
for e in S.events('kick'):          # the song's own kicks, as frames of this shot
    pass
S.camera(1, (0, -80, 20), (0, 0, 10), lens=35)
S.camera(N, (0, -60, 18), (0, 0, 10), lens=35)
S.handheld(0.3)
S.go()
