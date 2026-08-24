"""Self-check for fetch_window's auth handling. Run: python scripts/test_reportes_auth.py

Verifies (no network) that a 401/403 aborts via AuthError instead of being
retried and swallowed, and that a normal 200 still returns its reportes.
"""
import requests
from fetch_reportes_api import fetch_window, AuthError


class FakeResp:
    def __init__(self, status, text="", data=None):
        self.status_code = status
        self.text = text
        self._data = data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        return self._resp


# 401 -> AuthError on the FIRST call (no 3x retry, no swallow).
s = FakeSession(FakeResp(401, '{"error":"Correo o contrasena incorrectos."}'))
try:
    fetch_window(s, ("u", "p"), 0, 86_400_000)
    raise SystemExit("FAIL: 401 did not raise AuthError")
except AuthError:
    pass
assert s.calls == 1, f"expected fail-fast (1 call), got {s.calls}"

# 403 also aborts.
try:
    fetch_window(FakeSession(FakeResp(403, "")), ("u", "p"), 0, 86_400_000)
    raise SystemExit("FAIL: 403 did not raise AuthError")
except AuthError:
    pass

# 200 still returns the reportes list.
ok = FakeSession(FakeResp(200, data={"reportes": [{"id": "a"}, {"id": "b"}]}))
assert fetch_window(ok, ("u", "p"), 0, 86_400_000) == [{"id": "a"}, {"id": "b"}]

print("scripts/test_reportes_auth.py OK")
