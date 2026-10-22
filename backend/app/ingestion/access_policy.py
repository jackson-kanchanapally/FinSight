"""Conservative robots policy for autonomous HTTP collection; no bypasses.

Policy failures remain retryable job gaps. Explicitly absent robots files allow
access; denied, redirected, unavailable, or malformed policies do not.
"""
import asyncio
from time import monotonic
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
import httpx
from app.ingestion.sources.base import SourceUnavailableError
from app.ingestion.retry_policy import retry_after_seconds


class SourceAccessPolicy:
    def __init__(self, client=None):
        self.client = client
        self.rules = {}
        self.last_request = {}

    async def check(self, url):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
            raise SourceUnavailableError('SOURCE_ACCESS_REQUIRES_APPROVED_HTTPS')
        origin = f'https://{parts.hostname}'
        if origin not in self.rules:
            own = self.client is None
            client = self.client or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False,
                headers={'User-Agent': 'almonds/0.83 (personal research)'})
            try:
                async with client.stream('GET', origin+'/robots.txt') as response:
                    if response.status_code in (404, 410):
                        lines = ['User-agent: *', 'Allow: /']
                    elif response.status_code == 200:
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 512_000:
                                raise SourceUnavailableError('ROBOTS_POLICY_TOO_LARGE')
                        content = data.decode('utf-8', errors='replace')
                        if content.strip() and 'user-agent:' not in content.lower():
                            raise SourceUnavailableError('ROBOTS_POLICY_MALFORMED')
                        lines = content.splitlines()
                    else:
                        raise SourceUnavailableError(f'ROBOTS_POLICY_UNAVAILABLE_HTTP_{response.status_code}', retry_after_seconds(response.headers))
                rule = RobotFileParser()
                rule.parse(lines)
                self.rules[origin] = rule
            except httpx.HTTPError as exc:
                raise SourceUnavailableError('ROBOTS_POLICY_UNAVAILABLE') from exc
            finally:
                if own:
                    await client.aclose()
        rule = self.rules[origin]
        if not rule.can_fetch('almonds', url):
            raise SourceUnavailableError('ROBOTS_ACCESS_DISALLOWED')
        rate = rule.request_rate('almonds')
        delay = max(1, rule.crawl_delay('almonds') or 0, rate.seconds/rate.requests if rate else 0)
        # Do not hold a worker lease sleeping through an unusually long policy.
        if delay > 60:
            raise SourceUnavailableError('ROBOTS_CRAWL_DELAY_REQUIRES_LONGER_SCHEDULE')
        wait = delay - (monotonic()-self.last_request.get(origin, 0))
        if wait > 0:
            await asyncio.sleep(wait)
        self.last_request[origin] = monotonic()
