"""Music videos for ismail songs (optional; needs opencv-python, pycollada, pillow and Blender 5.x).

A song's video lives in <song>/video/ next to <song>/proj/:

    video/config.json   fps, master wav, family names for special notes, Blender path
    video/plan.md       the treatment: story, look, bar-by-bar timeline
    video/shots/*.py    one Blender script per shot (ismail.video.blender kit)
    video/rigs/*.json   character rigs: bone names, poses, eye textures, mounts
    video/cut.py        the cut list (ismail.video.edit)
    video/assets/       ripped or downloaded models (private)
    video/build/        events, features, model extracts, looks, caches
    video/renders/      shots/<shot>_vN.mp4 and <Title> vN.mp4

CLI: python -m ismail.video <init|rip|sync|still|render|sheet|edit> --song <song dir> ...
Method and lessons: skills/ismail/references/music-video.md
"""
import json
import os

DEFAULTS = {
    "fps": 30,
    "master": None,            # path to the master wav; default: newest <song>/*.wav
    "title": None,             # default: the project name
    "families": {},            # {"track": {"code": "name"}}: names for notes by velocity code (talk words, gun parts ...)
    "blender": None,           # default: $ISMAIL_BLENDER, then the usual Windows install, then `blender` on PATH
    "pct": 50,                 # shot render size, percent of 1920x1080
    "samples": 16,
}


def video_dir(song):
    return os.path.join(os.path.abspath(song), 'video')


def config(song):
    c = dict(DEFAULTS)
    p = os.path.join(video_dir(song), 'config.json')
    if os.path.exists(p):
        c.update(json.load(open(p)))
    return c


def master_wav(song):
    c = config(song)
    if c['master']:
        return c['master'] if os.path.isabs(c['master']) else os.path.join(os.path.abspath(song), c['master'])
    wavs = [os.path.join(song, f) for f in os.listdir(song) if f.lower().endswith('.wav')]
    if not wavs:
        raise FileNotFoundError(f'no master wav in {song}; set "master" in video/config.json')
    return max(wavs, key=os.path.getmtime)


def blender_exe(song=None):
    c = config(song) if song else DEFAULTS
    for cand in (c.get('blender'), os.environ.get('ISMAIL_BLENDER'),
                 r'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe',
                 '/Applications/Blender.app/Contents/MacOS/Blender', 'blender'):
        if cand and (cand == 'blender' or os.path.exists(cand)):
            return cand
    return 'blender'
