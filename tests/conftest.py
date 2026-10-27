import os
import tempfile

# the suite never reads the person's own settings file (ledger:M368): it gets one of its own that does not exist
# yet, before anything reads a setting; a test that writes settings points ISMAIL_CONFIG at its own (test_settings.py)
os.environ['ISMAIL_CONFIG'] = os.path.join(tempfile.gettempdir(), f'ismail_suite_{os.getpid()}', 'config.json')

import pytest  # noqa: E402

from ismail import machine  # noqa: E402

# a bare song name is a folder in the songs setting (ledger:M375): the suite's land in a songs folder of its own,
# never the person's. The board stays the shared one (children inherit it by name), so the suite still waits its turn
os.environ.setdefault('ISMAIL_MACHINE_DIR', machine.board_dir())
os.environ['ISMAIL_SONGS'] = os.path.join(tempfile.gettempdir(), f'ismail_suite_{os.getpid()}', 'songs')

HEAVY_FILES = 4         # a run across this many test files (the full suite) is a heavy job
RENDER_HEAVY = {'test_live_parity.py', 'test_live_song_parity.py', 'test_roundtrip.py'}   # heavy on their own:
                        # they render whole windows; any other single file, however many tests, is light


def pytest_collection_modifyitems(config, items):
    files = {os.path.basename(str(it.fspath)) for it in items}
    config._ismail_heavy = len(files) >= HEAVY_FILES or bool(files & RENDER_HEAVY)


@pytest.fixture(scope='session', autouse=True)
def _the_suite_is_one_heavy_job(request):
    """A big test run is one CPU-heavy job on the shared machine: it waits its turn like a render (a busy or hot
    machine refuses it with the reason), and the renders inside it run in its slot. The tests of a few touched files
    are light and run without a slot, unless a file renders whole windows. CI machines are not shared."""
    if not getattr(request.config, '_ismail_heavy', False):
        yield
        return
    with machine.slot('cpu', 'pytest ismail', force=bool(os.environ.get('CI'))):
        yield


@pytest.fixture(scope='session', autouse=True)
def _no_local_guard_files(tmp_path_factory):
    """The checks in ismail.guards read local files (private terms, standing grants, the chat mic log) and the git
    email. The suite never reads or writes the person's own: an empty terms file (so no 'scan is off' line in
    replies), no grants file, a tmp mic log, and no git email. A test that wants them sets its own through the same
    env vars (tests/test_guards.py)."""
    from ismail import guards
    d = tmp_path_factory.mktemp('guards')
    (d / 'private_terms.txt').write_text('# none in the suite\n', encoding='utf8')
    old = {k: os.environ.get(k) for k in (guards.PRIVATE_TERMS_ENV, guards.GRANTS_ENV, guards.MIC_CONSENT_ENV)}
    os.environ[guards.PRIVATE_TERMS_ENV] = str(d / 'private_terms.txt')
    os.environ[guards.GRANTS_ENV] = str(d / 'no_grants' / 'standing_grants.json')
    os.environ[guards.MIC_CONSENT_ENV] = str(d / 'mic_consent.jsonl')
    email = guards._git_email
    guards._git_email = lambda: None
    yield
    guards._git_email = email
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _reclaim_in_tmp(tmp_path_factory, monkeypatch):
    """Nothing in the suite moves into the person's reclaim folder (D:\\_reclaim, ledger:M229): every test gets its
    own temp reclaim root. A short one: Windows paths stop at 260 characters and a moved file keeps its whole path."""
    from ismail import reclaim
    monkeypatch.setenv(reclaim.ENV, str(tmp_path_factory.mktemp('rc')))
