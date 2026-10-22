"""Small HTTP helpers for the site APIs (stdlib only), and the websocket import with a forward-pointing error."""
import json
import urllib.error
import urllib.parse
import urllib.request


class HttpError(RuntimeError):
    def __init__(self, status, body, url):
        self.status, self.body = status, body
        msg = body.get('message') or body.get('error_description') or body.get('error') if isinstance(body, dict) \
            else str(body)[:300]
        if isinstance(msg, dict):                               # Google: {"error": {"message": ...}}
            msg = msg.get('message') or json.dumps(msg)[:300]
        super().__init__(f'{status} from {urllib.parse.urlparse(url).netloc}: {msg}')


def http(method, url, headers=None, params=None, json_body=None, form=None, timeout=20):
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(
            [(k, x) for k, v in params.items() if v is not None for x in (v if isinstance(v, (list, tuple)) else [v])])
    data, h = None, dict(headers or {})
    if json_body is not None:
        data, h['Content-Type'] = json.dumps(json_body).encode(), 'application/json'
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        h['Content-Type'] = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw.decode('utf8', 'replace')
        raise HttpError(e.code, body, url) from None


def ws_connect(url, **kw):
    try:
        from websockets.sync.client import connect
    except ImportError:
        raise ImportError("the websockets package is missing (site chat needs it)") from None
    return connect(url, open_timeout=15, close_timeout=3, max_size=2 ** 22, **kw)
