"""One pool of kept-alive connections for every call the agent makes.

`urllib.request.urlopen` opens a socket, negotiates TLS and throws the whole
thing away on every call. Against a remote endpoint that handshake is 150-400ms
— which is fine when a request takes half a minute, and absurd here: a System
One round trip is often faster than the handshake in front of it.

So the transport is a small pool instead. Connections are kept per host and
handed out to whoever asks, which matters twice over: the decision model is
called once per step in sequence, and the writer fires its candidates in
parallel, so the pool has to be both reusable and safe to share.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import queue
import ssl
import threading
import time
import urllib.parse
import urllib.request

TIMEOUT = 120
POOL_PER_HOST = 12
IDLE_SECONDS = 55          # most gateways close an idle keep-alive at 60


class HTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__("HTTP %d: %s" % (status, body[:300]))
        self.status = status
        self.body = body


def _proxy_for(scheme: str, host: str) -> tuple:
    """The proxy this request has to go through, as (host, port, auth header).

    Corporate networks, CI runners and sandboxes all put a proxy in front of
    the outside world and announce it the same way — `HTTPS_PROXY` and friends.
    `urllib` reads those for you; `http.client` does not, so a transport that
    swaps one for the other has to carry the proxy across or it stops working
    on exactly the machines that need it most.
    """
    proxies = urllib.request.getproxies()
    url = proxies.get(scheme) or proxies.get("all")
    if not url or urllib.request.proxy_bypass(host):
        return None, None, ""
    parts = urllib.parse.urlsplit(url if "//" in url else "//" + url)
    auth = ""
    if parts.username:
        raw = "%s:%s" % (urllib.parse.unquote(parts.username),
                         urllib.parse.unquote(parts.password or ""))
        auth = "Basic " + base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return parts.hostname, parts.port or 8080, auth


class _Host:
    """Connections to one host, checked out and returned."""

    def __init__(self, scheme: str, host: str, port: int | None):
        self.scheme = scheme
        self.host = host
        self.port = port or (443 if scheme == "https" else 80)
        self.free: queue.LifoQueue = queue.LifoQueue()
        self.context = ssl.create_default_context() if scheme == "https" else None
        self.proxy_host, self.proxy_port, self.proxy_auth = _proxy_for(scheme, host)

    @property
    def via_proxy(self) -> bool:
        return bool(self.proxy_host)

    def absolute(self, path: str) -> str:
        """http through a proxy asks for the whole URL, not just the path."""
        if self.via_proxy and self.scheme == "http":
            return "%s://%s:%d%s" % (self.scheme, self.host, self.port, path)
        return path

    def _new(self):
        if not self.via_proxy:
            if self.scheme == "https":
                conn = http.client.HTTPSConnection(self.host, self.port, timeout=TIMEOUT,
                                                   context=self.context)
            else:
                conn = http.client.HTTPConnection(self.host, self.port, timeout=TIMEOUT)
            conn.connect()
            return conn
        if self.scheme == "https":
            # CONNECT once, then TLS to the real host inside the tunnel, so the
            # certificate is checked against it and not against the proxy.
            conn = http.client.HTTPSConnection(self.proxy_host, self.proxy_port,
                                               timeout=TIMEOUT, context=self.context)
            conn.set_tunnel(self.host, self.port,
                            headers={"Proxy-Authorization": self.proxy_auth}
                            if self.proxy_auth else {})
            conn.connect()
            return conn
        conn = http.client.HTTPConnection(self.proxy_host, self.proxy_port, timeout=TIMEOUT)
        conn.connect()
        return conn

    def take(self):
        while True:
            try:
                conn, parked = self.free.get_nowait()
            except queue.Empty:
                return self._new(), True
            if time.time() - parked > IDLE_SECONDS:
                _shut(conn)
                continue
            return conn, False

    def give(self, conn) -> None:
        if self.free.qsize() >= POOL_PER_HOST:
            _shut(conn)
            return
        self.free.put((conn, time.time()))


def _shut(conn) -> None:
    try:
        conn.close()
    except Exception:                                    # noqa: BLE001
        pass


_hosts: dict = {}
_lock = threading.Lock()


def _host_for(url: str) -> tuple:
    parts = urllib.parse.urlsplit(url)
    key = (parts.scheme, parts.hostname, parts.port)
    with _lock:
        host = _hosts.get(key)
        if host is None:
            host = _Host(parts.scheme, parts.hostname, parts.port)
            _hosts[key] = host
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, path


def post_json(url: str, payload: dict, headers: dict, timeout: int = TIMEOUT) -> tuple:
    """POST JSON, read JSON back. Returns (parsed body, seconds spent).

    A connection taken from the pool may have been closed by the other side
    while it sat there; that is invisible until the write fails, so a reused
    connection gets exactly one retry on a fresh socket. A brand new connection
    gets none — if that fails, the failure is real.
    """
    host, path = _host_for(url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sent = dict(headers)
    sent.setdefault("Content-Type", "application/json")
    sent["Content-Length"] = str(len(body))
    sent["Connection"] = "keep-alive"
    sent.setdefault("Accept-Encoding", "identity")
    if host.via_proxy and host.scheme == "http" and host.proxy_auth:
        sent["Proxy-Authorization"] = host.proxy_auth
    path = host.absolute(path)

    started = time.time()
    for attempt in (0, 1):
        conn, fresh = host.take()
        try:
            conn.request("POST", path, body=body, headers=sent)
            response = conn.getresponse()
            raw = response.read()
        except Exception:                                # noqa: BLE001
            _shut(conn)
            if fresh or attempt:
                raise
            continue
        status = response.status
        keep = response.getheader("Connection", "").lower() != "close"
        if keep:
            host.give(conn)
        else:
            _shut(conn)
        text = raw.decode("utf-8", "replace")
        if status >= 400:
            raise HTTPError(status, text)
        return json.loads(text), time.time() - started
    raise HTTPError(0, "connection could not be reused")


def close_all() -> None:
    with _lock:
        hosts = list(_hosts.values())
        _hosts.clear()
    for host in hosts:
        while True:
            try:
                conn, _ = host.free.get_nowait()
            except queue.Empty:
                break
            _shut(conn)


def warm(*urls: str) -> None:
    """Open the sockets before they are needed.

    The first call of a run pays for DNS and TLS; nothing else in the agent
    does. Doing it in the background while the repository is being scanned
    takes that cost off the first step entirely.
    """
    def one(url: str) -> None:
        if not url:
            return
        host, _ = _host_for(url)
        try:
            conn, _fresh = host.take()
        except Exception:                                # noqa: BLE001
            return
        host.give(conn)

    for url in urls:
        threading.Thread(target=one, args=(url,), daemon=True).start()


DISABLED = os.environ.get("JEVCODE_NO_KEEPALIVE") == "1"
