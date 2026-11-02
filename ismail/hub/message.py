"""One outgoing message (text, files, buttons, a form), checked once, then shown the way each place can.

Buttons and forms use the phone panel's schema, so an agent writes them the same way for every place: buttons are
labels or {id, label}; a form is {title, inputs: [{id, kind: choice | check | toggle | text, label, options,
optional}]}. A place that cannot show them natively gets typed replies instead ("reply 1 or 2", "!f tempo=slower"),
and the answer says which, so the agent knows what the person saw."""
import os
import re

# who may press a button or answer a form: the person it went to, anyone, the owner
PRESS = ('to', 'anyone', 'owner')


def _inputs(inputs):
    from ..phone.server import panel_inputs        # the phone's own form schema and its checks (ledger:M167)
    return panel_inputs(inputs)


def check(text='', files=None, buttons=None, form=None, press='to'):
    """The message, normalised; raises ValueError saying what to write instead."""
    text = str(text or '').strip()
    out = {'text': text, 'files': [], 'buttons': [], 'form': None, 'press': press}
    if press not in PRESS:
        raise ValueError(f"press: one of {', '.join(PRESS)} (who may press its buttons or answer its form)")
    for f in files or []:
        if not os.path.isfile(f):
            raise ValueError(f"file not found: {f}")
        out['files'].append({'path': os.path.abspath(f), 'name': os.path.basename(f),
                             'mb': round(os.path.getsize(f) / 2 ** 20, 2)})
    seen = set()
    for i, b in enumerate(buttons or []):
        if not isinstance(b, dict):
            b = {'id': b, 'label': b}
        b = {'id': str(b.get('id') or b.get('label')), 'label': str(b.get('label') or b.get('id'))}
        if not b['id'] or b['id'] == 'None':
            raise ValueError(f"buttons[{i}]: a label, or {{'id': 'keep', 'label': 'Keep'}}")
        if b['id'] in seen:
            raise ValueError(f"buttons[{i}]: id {b['id']!r} twice; each button needs its own id")
        seen.add(b['id'])
        out['buttons'].append({'id': b['id'][:80], 'label': b['label'][:80]})
    if form:
        if isinstance(form, list):
            form = {'inputs': form}
        if not isinstance(form, dict) or not form.get('inputs'):
            raise ValueError("form: {'title': 'Change it', 'inputs': [{'id': 'tempo', 'kind': 'choice', 'label': "
                             "'Tempo', 'options': ['slower', 'same', 'faster']}]}")
        out['form'] = {'title': str(form.get('title') or 'Your answer')[:45], 'inputs': _inputs(form['inputs'])}
    if not (text or out['files'] or out['buttons'] or out['form']):
        raise ValueError('nothing to send: give text, files, buttons or a form')
    return out


def render(out, caps, place):
    """What the place will show: {text, files, buttons (native or []), form (native or None), typed (the typed
    fallbacks in force), shown (plain words for the agent)}. Raises ValueError when it cannot be shown at all."""
    text, shown, typed = out['text'], [], {}
    files = out['files']
    if files:
        if not caps.get('files_out'):
            raise ValueError(f"{place} cannot carry files; a link would mean hosting them in public, which needs "
                             f"the owner's OK")
        big = [f for f in files if f['mb'] > caps.get('file_max_mb', 8)]
        if big:
            raise ValueError(f"{', '.join(f['name'] + ' (' + str(f['mb']) + ' MB)' for f in big)}: {place} takes "
                             f"{caps.get('file_max_mb')} MB a file; render mp3s at 192 kbps or cut them shorter")
        shown.append(f"{len(files)} file(s)" + (', playable in the chat' if caps.get('audio_inline') else ''))
    buttons = out['buttons']
    if buttons and len(buttons) > caps.get('buttons', 0):
        typed['buttons'] = buttons
        text += '\n\nReply with a number: ' + ', '.join(f"{i + 1} {b['label']}" for i, b in enumerate(buttons))
        shown.append('buttons as typed numbers')
        buttons = []
    elif buttons:
        shown.append(f"{len(buttons)} button(s)")
    form = out['form']
    if form and (len(form['inputs']) > caps.get('form_max', 5) or
                 not all(x['kind'] in caps.get('forms', []) for x in form['inputs'])):
        typed['form'] = form
        text += '\n\n' + typed_form_help(form)
        shown.append('the form as one typed reply (!f ...)')
        form = None
    elif form:
        shown.append(f"a form ({len(form['inputs'])} field(s))")
    mx = caps.get('text_max', 2000)
    if len(text) > mx:
        raise ValueError(f"{len(text)} characters: {place} takes {mx}. Say it shorter or send two messages")
    return {'text': text, 'files': files, 'buttons': buttons, 'form': form, 'typed': typed, 'shown': shown}


def typed_form_help(form):
    parts = []
    for x in form['inputs']:
        v = '|'.join(x.get('options', [])) if x['kind'] in ('choice', 'check') else (
            'yes|no' if x['kind'] == 'toggle' else 'your words')
        parts.append(f"{x['id']}=<{v}>")
    return f"{form['title']}: reply !f " + ' '.join(parts) + ' (words with spaces in "quotes")'


TYPED_RE = re.compile(r'''(\w+)=("([^"]*)"|\S+)''')


def parse_typed(text, typed):
    """A typed reply to a message's fallbacks: ('press', button) / ('form', values) / None."""
    t = (text or '').strip()
    if typed.get('buttons') and re.fullmatch(r'!?\d{1,2}', t):
        i = int(t.lstrip('!')) - 1
        if 0 <= i < len(typed['buttons']):
            return 'press', typed['buttons'][i]
    if typed.get('form') and t.lower().startswith('!f'):
        ids = {x['id']: x for x in typed['form']['inputs']}
        vals = {}
        for k, raw, quoted in TYPED_RE.findall(t[2:]):
            if k in ids:
                v = quoted if raw.startswith('"') else raw
                x = ids[k]
                vals[k] = (v.lower() in ('yes', 'y', 'on', 'true', '1')) if x['kind'] == 'toggle' else (
                    [s for s in v.split(',') if s] if x['kind'] == 'check' else v)
        return 'form', vals
    return None
