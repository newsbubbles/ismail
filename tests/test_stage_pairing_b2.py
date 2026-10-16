"""The security floor part B2 (research/multiplayer/security-floor.md, tests 10 to 12 and the B1 review's F3), server
side: temporary keys for headless pages on this PC, only guarded requests counted as unpaired (with the last few
listed), the content policy on the stage's pages (report-only, then enforced), and JSON endpoints that take
application/json only in enforce. The page side runs in a browser (the e2e in the PR)."""
import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.request

from ismail.api import OPS
from ismail.stage import pairing as PA
from ismail.stage import server as S
from test_stage import stage  # noqa: F401  (the fixture)
from test_stage_pairing import _agent, _mode, _paired, _req


def _raw(port, path, body=b'', ctype='text/plain', headers=None, method='POST'):
    r = urllib.request.Request(f'http://127.0.0.1:{port}/{path}', data=body if method == 'POST' else None, method=method,
                               headers={**({'Content-Type': ctype} if ctype else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=15) as f:
            return f.status, dict(f.headers), f.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_a_temporary_key_is_an_automated_page_and_lapses(stage, monkeypatch):
    _mode(stage, 'enforce')
    p = stage['port']
    got = _req(p, 'pair/start', {'name': 'preflight ab12', 'kind': 'desktop', 'temp_s': 300}, _agent(stage))[1]
    assert got['temp_s'] == 300
    assert _req(p, 'health')[1]['auth']['pending_codes'] == 0          # nobody is asked to type a headless page's code
    st, rec = _req(p, 'pair/claim', {'code': got['code'], 'name': None, 'kind': None})
    assert st == 200 and rec['device'] == 'preflight ab12' and rec['kind'] == 'desktop' and rec['temp']
    key = rec['key']
    assert not stage['srv'].auth.devices()                                # memory only, never the device file
    assert _req(p, 'live/event?scene=room', {'type': 'gesture'}, {'X-Stage-Key': key})[0] == 200
    ev = _req(p, 'live/events?scene=room&since=0&wait=0', headers={'X-Stage-Key': key})[1]['events'][-1]
    assert ev['principal'] == {'agent': 'preflight ab12', 'for': 'nate'}  # an automated page, never the person
    assert 'temporary until' in OPS['stage_devices']()
    assert _req(p, 'health')[1]['auth']['temp_keys'] == 1
    t = time.time()
    with monkeypatch.context() as m:
        m.setattr(PA.time, 'time', lambda: t + 301)
        assert _req(p, 'live/event?scene=room', {'type': 'gesture'}, {'X-Stage-Key': key})[0] == 401
    for bad in (0, -5, PA.TEMP_MAX_S + 1):
        assert _req(p, 'pair/start', {'kind': 'desktop', 'temp_s': bad}, _agent(stage))[0] == 400


def test_unpairing_a_temporary_key(stage):
    _mode(stage, 'enforce')
    p = stage['port']
    code = _req(p, 'pair/start', {'name': 'capture', 'kind': 'desktop', 'temp_s': 60}, _agent(stage))[1]['code']
    key = _req(p, 'pair/claim', {'code': code})[1]['key']
    assert _req(p, 'pair/unpair', {'device': 'capture'}, _agent(stage))[1]['unpaired'] == 1
    assert _req(p, 'live/event?scene=room', {'type': 'gesture'}, {'X-Stage-Key': key})[0] == 401


def test_only_guarded_requests_count_as_unpaired(stage):
    _mode(stage, 'report')
    p = stage['port']
    before = _req(p, 'health')[1]['auth']['unpaired']
    for path in ('index.html', 'health', 'main.js'):                      # the page's own files and health: open
        assert _raw(p, path, method='GET', ctype=None)[0] == 200
    assert _req(p, 'health')[1]['auth']['unpaired'] == before
    assert _req(p, 'live/event?scene=room', {'type': 'gesture'})[0] == 200
    assert _req(p, 'health')[1]['auth']['unpaired'] == before + 1
    recent = _req(p, 'pair/devices', headers=_agent(stage))[1]['unpaired_recent']
    assert recent[-1]['method'] == 'POST' and recent[-1]['path'] == '/live/event' and recent[-1]['addr'] == '127.0.0.1'
    assert 'POST /live/event' in OPS['stage_devices']()
    for _ in range(25):
        _req(p, 'world?scene=room')
    assert len(_req(p, 'pair/devices', headers=_agent(stage))[1]['unpaired_recent']) == 20          # the last 20 only


def _hashes(html):
    blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
    return {"'sha256-" + base64.b64encode(hashlib.sha256(b.replace('\r\n', '\n').encode()).digest()).decode() + "'"
            for b in blocks}


def test_the_pages_carry_a_content_policy(stage):
    p = stage['port']
    _mode(stage, 'report')
    for path in ('', 'index.html', 'upload.html'):
        st, h, body = _raw(p, path, method='GET', ctype=None)
        assert st == 200
        csp = h.get('Content-Security-Policy-Report-Only')
        assert csp and 'Content-Security-Policy' not in h, path
        assert "default-src 'self'" in csp and 'report-uri /csp' in csp and "'unsafe-eval'" not in csp
        hs = _hashes(body.decode('utf-8'))
        assert hs and all(x in csp for x in hs), path                      # every inline script by its hash, no more
    assert 'Content-Security-Policy' not in _raw(p, 'main.js', method='GET', ctype=None)[1]   # a module, not a page
    _mode(stage, 'enforce')
    h = _raw(p, 'index.html', method='GET', ctype=None)[1]
    assert h.get('Content-Security-Policy') and 'Content-Security-Policy-Report-Only' not in h


def test_a_policy_report_is_taken_from_anyone(stage):
    _mode(stage, 'enforce')
    p = stage['port']
    rep = json.dumps({'csp-report': {'blocked-uri': 'inline', 'violated-directive': 'script-src'}}).encode()
    assert _raw(p, 'csp', rep, 'application/csp-report')[0] == 200        # unpaired, not JSON: still taken
    assert _req(p, 'health')[1]['auth']['csp'] == 1
    assert _raw(p, 'csp', b'x' * 50000, 'application/csp-report')[0] == 200   # a big one is cut, not refused


def test_enforce_takes_json_only_on_json_endpoints(stage):
    key, _ = _paired(stage)
    p = stage['port']
    k = {'X-Stage-Key': key}
    _mode(stage, 'report')
    assert _raw(p, 'live/event?scene=room', b'{"type": "gesture"}', 'text/plain', k)[0] == 200   # report: as before
    _mode(stage, 'enforce')
    for path in ('live/event?scene=room', 'cues?scene=room', 'waypoints?scene=room', 'clientlog'):
        for ctype in ('text/plain', 'application/x-www-form-urlencoded', None):
            st, _h, body = _raw(p, path, b'{"type": "gesture"}', ctype, k)
            assert st == 415 and json.loads(body)['refused'] == 'not JSON', (path, ctype)
    assert _raw(p, 'live/event?scene=room', b'{"type": "gesture"}', 'application/json; charset=utf-8', k)[0] == 200
    assert S.JSON_POSTS and all(x.startswith('/') for x in S.JSON_POSTS)


def test_a_waiting_code_is_counted_for_the_page_to_ask(stage):
    p = stage['port']
    assert _req(p, 'health')[1]['auth']['pending_codes'] == 0
    out = OPS['stage_pair'](name='phone', kind='phone')
    assert '#pair=' in out
    assert _req(p, 'health')[1]['auth']['pending_codes'] == 1             # an unpaired page shows its code field now
    _req(p, 'pair/claim', {'code': out.split()[1]})
    assert _req(p, 'health')[1]['auth']['pending_codes'] == 0
