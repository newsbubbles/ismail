"""exam_check: the pre-flight every exam runs before it reaches the person (ismail/exam_check.py has the checks)."""
from .api import OpError, op
from . import exam_check as EC


@op()
def exam_check(clips: list = None, page: str = None, key: dict = None, secrets: list = None,
               answers_path: str = None, submit_url: str = None, submit_body: dict = None,
               lufs_tol: float = 1.0) -> str:
    """Run before any exam page goes to the person; show it only on READY. Checks that every clip exists and decodes,
    that the clips sit within lufs_tol of one loudness, and that a blind exam cannot be told by anything but the
    sound: given key={clip label, file name or path: its class} and secrets=[source names, words that must not show],
    no class or secret in file names, URLs, metadata tags or the page source (or the scripts and styles it loads), no
    key file the page loads, no format, length, leading-silence or order that separates the classes. submit_url +
    answers_path: posts a test answer marked preflight and checks it lands where you read answers. clips: paths or
    [{label, path}]; page: the exam's HTML file or its URL (its clips are found when clips is none)."""
    try:
        ready, lines = EC.run(clips=clips, page=page, key=key, secrets=secrets, answers_path=answers_path,
                              submit_url=submit_url, submit_body=submit_body, lufs_tol=lufs_tol)
    except (OSError, ValueError) as e:
        raise OpError(f"exam_check: {e}")
    return '\n'.join(lines)


@op()
def exam_eye_crops(pairs: list, out: str, windows: list = None, n_windows: int = 3, seed: int = 0) -> str:
    """The blind crop check, step 1 (a gate before a real-vs-made exam): the same time window cut from the real and
    the made clip of each pair, as spectrogram images side by side ("1" and "2" in a random order), with the key
    hidden. pairs: [[real_path, made_path], ...]; windows: per pair, [[t0, t1], ...] in seconds (a word +-60 ms),
    else n_windows through each clip. Look at every out/q*.png and pick the side that looks real, WITHOUT opening
    out/key.json, then exam_eye_score(out, {1: '2', 2: '1', ...}). If you can tell from the picture, so can they."""
    from . import exam_check as EC
    try:
        n = EC.eye_crops(pairs, out, windows, n_windows, seed=seed)
    except (OSError, ValueError, RuntimeError) as e:
        raise OpError(f"exam_eye_crops: {e}")
    return (f"{n} crop pairs in {out} (q01.png ... q{n:02d}.png; questions.json says each one's pair and window). "
            f"Do not open key.json. Look at each and pick the side that looks real, then exam_eye_score(out, "
            f"{{1: '1' or '2', ...}}).")


@op()
def exam_eye_score(out: str, answers: dict) -> str:
    """The blind crop check, step 2: score your picks from exam_eye_crops against the hidden key. NOT READY when
    you beat chance (p < 0.05): the picture gives the real side away, so the person will likely hear it too."""
    from . import exam_check as EC
    try:
        ok, lines = EC.eye_score(out, answers)
    except (OSError, ValueError) as e:
        raise OpError(f"exam_eye_score: {e}")
    return '\n'.join(lines)


@op()
def exam_picture_round(out: str, items: list, title: str, intro: str = '', seed: int = 0, f_lo: float = 0.0,
                       f_hi: float = 16000.0, labels: dict = None, host: bool = True, port: int = 8871) -> str:
    """The eye exam page for the person (ledger:M165 step 2, vox's word-picture round): one sound per card, the real
    picture and ours of the same window stacked so one shows at a time (F flips, Shift peeks, B blinks: the eye reads
    a difference as movement), M / S / 0 answers about the picture showing, and comments pinned at a time and
    frequency (a click, or a dragged box). The key is served only after they answer. items: [{word, real: path,
    other: path, window: [t0, t1] s, other_window (default the same), words: [[t0, t1, text]] in the real file,
    other_label: what ours is, said after the answer, version}]; real-first and real-second are balanced and seeded.
    labels: the page's words {real, ours, play} (default "This one is the real one", "This one is ours", "Play the
    real sound"). out: the round's folder; its parent is what the page server serves. host=True starts it on
    127.0.0.1:port unless it runs. Read the answers with exam_picture_score(out)."""
    import os
    from . import exampage as X
    try:
        man = X.build(out, items, title, intro, seed=seed, f_lo=f_lo, f_hi=f_hi, labels=labels)
    except (OSError, KeyError, ValueError) as e:
        raise OpError(f"exam_picture_round: {e} (each item needs word, real, other and window)")
    rnd = man['round']
    if not X.SAFE.match(rnd):
        raise OpError(f"the round folder's name {rnd!r} must be letters, digits, '_', '-' or '.'")
    url = (X.host(os.path.dirname(os.path.abspath(out)), port) + rnd) if host else None
    return (f"round {rnd}: {len(man['items'])} cards in {out}" + (f"; open {url}" if url else
            f"; serve with python -m ismail.exampage serve {os.path.dirname(os.path.abspath(out))}") +
            ". The key is served only after a submit; answers land in answers.jsonl; exam_picture_score(out) reads "
            "the last one.")


@op()
def exam_picture_score(out: str) -> str:
    """Score the last answers of an exam_picture_round against its key: per card right, WRONG (ours fooled them),
    cant or none, with flips, blinks and plays, their note, and each pinned comment on the REAL picture or OURS,
    then the tally per version."""
    from . import exampage as X
    try:
        return X.score(out)
    except (OSError, KeyError, ValueError) as e:
        raise OpError(f"exam_picture_score: {e}")
