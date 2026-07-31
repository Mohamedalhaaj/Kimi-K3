"""SSRF-safe outbound HTTP.

The audit (docs/AUDIT.md §5) recorded a TOCTOU hole in the prototype's guard:
``is_public_web_url`` called ``getaddrinfo``, checked the addresses, then
**threw them away**. httpx then resolved the name again at connect time, so a
short-TTL hostname could answer public during validation and ``127.0.0.1`` or
``169.254.169.254`` on connect.

The fix here is to *pin* the resolution: resolve once, validate every returned
address, then connect to a validated address literal while carrying the original
hostname in the ``Host`` header and the TLS SNI. Certificate verification still
happens against the hostname, so the connection is both pinned and authenticated.

Every redirect hop is re-validated the same way; nothing is followed blindly.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlparse, urlunparse

import httpx
import structlog

log = structlog.get_logger(__name__)

#: Hard ceiling on any single download. Applied while streaming, not after.
MAX_BYTES: Final = 4 * 1024 * 1024
MAX_REDIRECTS: Final = 5
DEFAULT_TIMEOUT: Final = 12.0

_ALLOWED_SCHEMES: Final = frozenset({"http", "https"})

_BLOCKED_HOSTNAMES: Final = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        # AWS/GCP/Azure instance metadata by name.
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)

USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class UnsafeUrlError(Exception):
    """The URL is not allowed to be fetched. The reason is safe to log."""


@dataclass(slots=True)
class FetchResult:
    url: str
    """The final URL after redirects."""
    status: int
    text: str
    content_type: str
    truncated: bool = False
    redirects: list[str] = field(default_factory=list)


def _address_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject anything that is not a routable public address.

    ``is_global`` alone is not enough: it permits some reserved ranges on older
    Python versions, so the explicit checks stay as a belt-and-braces layer.
    Cloud metadata endpoints are covered — 169.254.169.254 is link-local and
    fd00:ec2::254 is unique-local (``is_private``).
    """
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None)
    )


def resolve_public_addresses(hostname: str, port: int) -> list[str]:
    """Resolve ``hostname`` and return its addresses, or raise.

    Fails closed in two ways: any resolution error is a rejection, and if
    *any* returned address is non-public the whole hostname is rejected rather
    than filtering down to the public subset. A name that answers with a mix of
    public and private addresses is exactly the rebinding pattern we refuse.
    """
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise UnsafeUrlError(f"could not resolve {hostname!r}") from exc

    addresses: list[str] = []
    for info in infos:
        # typeshed types sockaddr loosely; the first element is the address text.
        raw = str(info[4][0])
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise UnsafeUrlError(f"unparseable address for {hostname!r}") from exc
        if not _address_is_public(ip):
            raise UnsafeUrlError(f"{hostname!r} resolves to a non-public address ({ip.version})")
        if raw not in addresses:
            addresses.append(raw)

    if not addresses:
        raise UnsafeUrlError(f"{hostname!r} resolved to nothing")
    return addresses


@dataclass(slots=True)
class PinnedTarget:
    """A validated destination: where to connect, and who to claim to be."""

    hostname: str
    port: int
    address: str
    scheme: str
    path_and_query: str

    @property
    def connect_url(self) -> str:
        literal = f"[{self.address}]" if ":" in self.address else self.address
        return f"{self.scheme}://{literal}:{self.port}{self.path_and_query}"

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        return self.hostname if self.port == default else f"{self.hostname}:{self.port}"


def validate_url(url: str) -> PinnedTarget:
    """Parse, policy-check, and resolve ``url`` into a pinned target."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise UnsafeUrlError("malformed URL") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"scheme {scheme!r} is not allowed")

    if not parsed.hostname:
        raise UnsafeUrlError("URL has no host")

    hostname = parsed.hostname.lower().strip(".")
    if not hostname or hostname in _BLOCKED_HOSTNAMES:
        raise UnsafeUrlError("host is not allowed")

    # A bare IP in the URL skips DNS entirely; check it directly.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _address_is_public(literal):
            raise UnsafeUrlError("URL points at a non-public address")
        addresses = [hostname]
    else:
        addresses = resolve_public_addresses(
            hostname, parsed.port or (443 if scheme == "https" else 80)
        )

    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    return PinnedTarget(
        hostname=hostname,
        port=port,
        address=addresses[0],
        scheme=scheme,
        path_and_query=path,
    )


def _decode(raw: bytes, content_type: str) -> str:
    """Decode using the declared charset, falling back sanely.

    The prototype decoded everything as UTF-8 with ``errors="replace"`` and
    never consulted the charset, which mangles Windows-1256 Arabic pages.
    """
    charset = ""
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";")[0].strip().strip('"')
    for encoding in (charset, "utf-8", "windows-1256", "latin-1"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


class SafeFetcher:
    """Fetches public web pages with pinned DNS and bounded reads."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = MAX_BYTES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=min(6.0, self._timeout)),
                follow_redirects=False,  # every hop is re-validated by hand
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str, *, accept: str = "text/html,*/*") -> FetchResult:
        """GET ``url``, following redirects manually and re-validating each hop."""
        client = await self._get_client()
        redirects: list[str] = []
        current = url

        for _hop in range(MAX_REDIRECTS + 1):
            target = validate_url(current)
            headers = {
                "Host": target.host_header,
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Accept-Language": "en,ar;q=0.8",
            }
            extensions = {"sni_hostname": target.hostname} if target.scheme == "https" else {}

            try:
                async with client.stream(
                    "GET",
                    target.connect_url,
                    headers=headers,
                    extensions=extensions,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise UnsafeUrlError("redirect without a location")
                        redirects.append(current)
                        current = _join(current, location)
                        continue

                    content_type = response.headers.get("content-type", "")
                    chunks: list[bytes] = []
                    total = 0
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        remaining = self._max_bytes - total
                        if len(chunk) >= remaining:
                            chunks.append(chunk[:remaining])
                            truncated = True
                            break
                        chunks.append(chunk)
                        total += len(chunk)

                    return FetchResult(
                        url=_rebuild(current),
                        status=response.status_code,
                        text=_decode(b"".join(chunks), content_type),
                        content_type=content_type,
                        truncated=truncated,
                        redirects=redirects,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Never surface the raw transport message: it embeds internal
                # hostnames and ports.
                raise UnsafeUrlError(f"could not fetch {target.hostname}") from exc

        raise UnsafeUrlError("too many redirects")


def _join(base: str, location: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, location)


def _rebuild(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))


async def gather_bounded[T](
    tasks: list[asyncio.Future[T] | asyncio.Task[T]], limit: int
) -> list[T | None]:
    """Run awaitables with bounded concurrency; failures become ``None``.

    One failing source must never invalidate the successful ones.
    """
    semaphore = asyncio.Semaphore(limit)

    async def guarded(task: asyncio.Future[T] | asyncio.Task[T]) -> T | None:
        async with semaphore:
            try:
                return await task
            except Exception:
                return None

    return list(await asyncio.gather(*(guarded(t) for t in tasks)))
