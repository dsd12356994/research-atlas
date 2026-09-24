"""Bounded public GET requests. Never silently return a partially downloaded body."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
import time
import requests

SESSION = requests.Session()
SESSION.headers['User-Agent'] = 'ResearchHub/0.2 (personal academic literature monitor)'
LAST = {}

class DownloadLimit(ValueError):
    pass

class RetryDeferred(RuntimeError):
    pass

def retry_delay(value, attempt):
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    return min(30, 2 ** attempt)

def get(url, *, retries=3, max_bytes=64_000_000, **kwargs):
    host = urlparse(url).hostname or ''
    interval = 3.2 if host.endswith('arxiv.org') else 1.0
    timeout = kwargs.pop('timeout', (15, 45))
    for attempt in range(retries + 1):
        pause = interval - (time.monotonic() - LAST.get(host, 0))
        if pause > 0:
            time.sleep(pause)
        LAST[host] = time.monotonic()
        response = None
        try:
            response = SESSION.get(url, timeout=timeout, stream=True, **kwargs)
            if response.status_code == 429 or response.status_code in (500, 502, 503, 504):
                if attempt == retries:
                    response.raise_for_status()
                delay = retry_delay(response.headers.get('Retry-After'), attempt)
                if delay > 60:
                    raise RetryDeferred('Server requests a longer delay; resume in a later run')
                response.close()
                time.sleep(delay)
                continue
            response.raise_for_status()
            body = bytearray()
            for block in response.iter_content(65536):
                body.extend(block)
                if len(body) > max_bytes:
                    raise DownloadLimit('Body exceeds configured download limit')
            # iter_content decompresses; Content-Length is comparable only without encoding.
            declared = response.headers.get('Content-Length')
            if declared and not response.headers.get('Content-Encoding'):
                if len(body) != int(declared):
                    raise requests.exceptions.ChunkedEncodingError('Body length mismatch')
            response._content = bytes(body)
            response._content_consumed = True
            return response
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError):
            if attempt == retries:
                raise
            time.sleep(min(30, 2 ** attempt))
        finally:
            if response is not None:
                response.close()

