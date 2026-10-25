"""The harness store packages (python -m ismail.release, hq:D-213, D-214): each target builds, validates against its
store's documented rules, matches the committed repo files, and its declared MCP server answers an initialize and a
tools/list the way the client would start it. Offline: uvx's download step is replaced by this interpreter running
the same `ismail` entry point with the declared arguments (ismail.release.local_command)."""
import json
import os
import queue
import subprocess
import sys
import threading

import pytest

from ismail import release

ROOT = release.ROOT


def handshake(server, cwd, timeout=180):
    """Start a declared stdio server as its client would and return the tools/list result."""
    cmd = release.local_command(server)
    env = {**os.environ, **server.get('env', {}), 'PYTHONPATH': str(ROOT)}
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(l) for l in p.stdout], daemon=True).start()

    def send(msg):
        p.stdin.write((json.dumps(msg) + '\n').encode())
        p.stdin.flush()

    def reply(want):
        while True:
            msg = json.loads(lines.get(timeout=timeout))
            if msg.get('id') == want:
                assert 'error' not in msg, msg
                return msg['result']

    try:
        send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
              'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                         'clientInfo': {'name': 'store-plugin-test', 'version': '0'}}})
        init = reply(1)
        send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        return init, reply(2)
    finally:
        p.stdin.close()
        p.kill()
        p.wait()


def assert_serves_ismail(server, tmp_path):
    init, tools = handshake(server, tmp_path)
    assert init['serverInfo']['name']
    names = {t['name'] for t in tools['tools']}
    assert {'guide', 'ops_find'} <= names, sorted(names)[:20]


@pytest.fixture(scope='module')
def meta():
    return release.load_meta()


# ---------------------------------------------------------------- every target

@pytest.mark.parametrize('name', sorted(release.TARGETS))
def test_target_builds_clean_and_deterministic(name, meta):
    out, errors = release.build(name, meta=meta)
    assert errors == []
    again, _ = release.build(name, meta=meta)
    assert out.dist == again.dist and out.repo == again.repo        # same bytes, build after build
    assert release.repo_drift(out) == [], 'run: python -m ismail.release --all --sync'
    for rel in out.repo:
        assert json.loads(out.repo[rel])                              # valid JSON


def test_version_and_shared_text_come_from_one_source(meta):
    assert meta['version'] == release.project_version()
    for name in release.TARGETS:
        out, _ = release.build(name, meta=meta)
        for rel, data in out.repo.items():
            doc = json.loads(data)
            if 'version' in doc:
                assert doc['version'] == meta['version'], rel


def test_validators_catch_broken_listings(meta):
    bad = json.loads(json.dumps(meta))
    bad['openai']['listing']['shortDescription'] = 'x' * 31
    bad['openai']['listing']['defaultPrompt'] = ['a', 'b', 'c', 'd']
    bad['name'] = 'Bad--Name'
    _, errors = release.build('openai', meta=bad)
    text = '\n'.join(errors)
    assert 'shortDescription is 31' in text and 'defaultPrompt' in text and 'name' in text
    _, errors = release.build('claude-code', meta={**meta, 'name': 'claude-ismail'})
    assert any('reserved' in e for e in errors)


# ---------------------------------------------------------------- openai (ChatGPT and Codex)

def test_openai_directory_zip(meta):
    out, _ = release.build('openai', meta=meta)
    files = release.read_zip(out.dist[release.openai_zip_name(meta)])
    assert {'plugin.json', 'assets/logo.png', 'LICENSE', 'skills/ismail/SKILL.md'} <= set(files)
    assert not any(n.split('/')[-1] in ('mcp.json', '.mcp.json', '.app.json') or 'hooks' in n.split('/')
                   for n in files)
    assert not any(n.startswith('.codex-plugin/') for n in files)          # one manifest, never both
    m = json.loads(files['plugin.json'])
    I = m['extensions']['com.openai']['interface']
    listing = meta['openai']['listing']
    assert m['description'] == listing['description']                    # Marketing's copy, as approved
    for k in ('displayName', 'shortDescription', 'longDescription', 'defaultPrompt', 'category'):
        assert I[k] == listing[k]
    assert release.image_size(files['assets/logo.png'])[:2] == (180, 180)


def test_codex_marketplace_server_answers(meta, tmp_path):
    out, _ = release.build('openai', meta=meta)
    servers = json.loads(out.repo['.codex-plugin/mcp.json'])['mcpServers']
    (server,) = servers.values()
    assert server['type'] == 'stdio' and server['startup_timeout_sec'] >= 60
    assert_serves_ismail(server, tmp_path)


# ---------------------------------------------------------------- claude-code

def test_claude_code_plugin_server_answers(meta, tmp_path):
    out, _ = release.build('claude-code', meta=meta)
    plugin = json.loads(out.repo['.claude-plugin/plugin.json'])
    (server,) = plugin['mcpServers'].values()
    files = release.read_zip(out.dist[release.claude_zip_name(meta)])
    assert json.loads(files['.claude-plugin/plugin.json']) == plugin
    assert 'skills/ismail/SKILL.md' in files
    assert_serves_ismail(server, tmp_path)

