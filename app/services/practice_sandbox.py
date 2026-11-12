"""Proxy from the site to the ATL-101 practice sandbox service (sandbox/).

The sandbox is a separate container on an internal-only network. It never sees the
internet and never sees a raw cookie: the /practice blueprint checks the password, and
this module forwards the request with an X-Sandbox-Session header naming the visitor's
own sandbox (a random id kept in the signed Flask session).
"""
import re
import secrets

import requests

SESSION_KEY = "practice_sandbox_id"
SANDBOX_ID = re.compile(r"^[a-f0-9]{32}$")
# Request headers worth passing on; everything else (cookies, auth) stays here.
FORWARD_HEADERS = ("Content-Type", "Accept", "X-Sandbox")
# Response headers worth passing back.
RETURN_HEADERS = ("Content-Type",)
MAX_BODY = 512 * 1024

_http = requests.Session()


class SandboxUnavailable(Exception):
    pass


def new_sandbox_id() -> str:
    return secrets.token_hex(16)


def valid_sandbox_id(value) -> bool:
    return isinstance(value, str) and bool(SANDBOX_ID.match(value))


def forward(base_url, method, path, query, headers, body, sandbox_id, timeout):
    """Send one request to the sandbox; return (status, headers, content)."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    out_headers = {k: headers[k] for k in FORWARD_HEADERS if k in headers}
    out_headers["X-Sandbox-Session"] = sandbox_id
    try:
        resp = _http.request(method, url, params=query, data=body, headers=out_headers,
                             timeout=(5, timeout), allow_redirects=False)
    except requests.RequestException as err:
        raise SandboxUnavailable(str(err)) from err
    return resp.status_code, {k: resp.headers[k] for k in RETURN_HEADERS if k in resp.headers}, resp.content
