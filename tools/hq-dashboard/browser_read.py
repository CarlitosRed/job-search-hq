"""Optional, isolated Chromium rendering for a public posting URL."""
import asyncio
from contextlib import asynccontextmanager
import threading
import time
from types import SimpleNamespace

MAX_CONNECTIONS = 8


def request_target(url, public_address, public_url, deadline):
    """Check a proxy destination and return the IP the connection must use."""
    return public_url(url, public_address, deadline)


class _ProxyBudget:
    def __init__(self, check, expires, max_bytes):
        self.check = check
        self.expires = expires
        self.remaining_bytes = max_bytes
        self.tasks = set()

    def accept(self, reader, writer):
        seconds = self.expires - time.monotonic()
        if seconds <= 0 or not self.remaining_bytes or len(self.tasks) >= MAX_CONNECTIONS:
            writer.close()
            return
        task = asyncio.create_task(_proxy(reader, writer, self))
        self.tasks.add(task)

        def finished(task):
            self.tasks.discard(task)
            writer.close()

        task.add_done_callback(finished)

    def take(self, size):
        # Every connection and tunnel direction shares one event loop and byte allowance.
        size = min(size, self.remaining_bytes)
        self.remaining_bytes -= size
        return size

    @asynccontextmanager
    async def listen(self):
        server = await asyncio.start_server(self.accept, '127.0.0.1', 0)
        try:
            yield server.sockets[0].getsockname()[1]
        finally:
            server.close()
            tasks = tuple(self.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await server.wait_closed()


async def _relay(reader, writer, budget):
    while budget.remaining_bytes:
        chunk = await reader.read(min(budget.remaining_bytes, 16384))
        if not chunk:
            break
        size = budget.take(len(chunk))
        if not size:
            break
        writer.write(chunk[:size])
        await writer.drain()


async def _proxy(reader, writer, budget):
    """Proxy one HTTP GET or TLS tunnel using a checked IP, including on redirect hops."""
    upstream = None
    try:
        async with asyncio.timeout(max(0, budget.expires - time.monotonic())):
            raw = await reader.readuntil(b'\r\n\r\n')
            if budget.take(len(raw)) != len(raw):
                return
            request, *headers, _, _ = raw.split(b'\r\n')
            method, target, version = request.decode('ascii').split()
            tunnel = method == 'CONNECT'
            if version not in ('HTTP/1.0', 'HTTP/1.1') or method not in ('GET', 'HEAD', 'CONNECT'):
                return
            if tunnel and any(char in target for char in '/?#'):
                return
            if any(header.lower().startswith(b'upgrade:') for header in headers):
                return
            checked = await asyncio.to_thread(budget.check, 'https://' + target if tunnel else target)
            if not checked:
                return
            parts, port, ip = checked
            if not tunnel and parts.scheme != 'http':
                return
            if not tunnel:
                path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
                omitted = {b'host', b'connection', b'content-length', b'transfer-encoding', b'upgrade'}
                forwarded = [header for header in headers if b':' in header
                             and not header.lower().startswith(b'proxy-')
                             and header.split(b':', 1)[0].lower() not in omitted]
                request = b'\r\n'.join([f'{method} {path} HTTP/1.1'.encode('ascii'),
                                        f'Host: {parts.netloc}'.encode('ascii'),
                                        b'Connection: close', *forwarded, b'', b''])
                extra = max(0, len(request) - len(raw))
                if budget.take(extra) != extra:
                    return
            if not budget.remaining_bytes:
                return
            remote, upstream = await asyncio.open_connection(ip, port)
            if not tunnel:
                upstream.write(request)
                await upstream.drain()
                await _relay(remote, writer, budget)
                return
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()
            transfers = [asyncio.create_task(_relay(source, destination, budget))
                         for source, destination in ((reader, upstream), (remote, writer))]
            try:
                await asyncio.wait(transfers, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for transfer in transfers:
                    transfer.cancel()
                await asyncio.gather(*transfers, return_exceptions=True)
    except (TimeoutError, asyncio.CancelledError) as error:
        writer.transport.abort()
        if upstream:
            upstream.transport.abort()
        if isinstance(error, asyncio.CancelledError):
            raise
    except (OSError, ValueError, EOFError, asyncio.LimitOverrunError):
        pass
    finally:
        if upstream:
            upstream.close()
        writer.close()


_VISIBLE_TEXT = """maxText => {
    const chunks = [];
    let remaining = maxText;
    const visit = doc => {
        if (!doc || !remaining || !['http:', 'https:'].includes(doc.location.protocol)) return;
        const main = doc.querySelector('main, article, [role=main]');
        const text = ((main && main.innerText.trim()) || (doc.body && doc.body.innerText.trim()) || '');
        if (text) {
            const chunk = ((chunks.length ? '\\n\\n' : '') + text).slice(0, remaining);
            chunks.push(chunk);
            remaining -= chunk.length;
        }
        for (const frame of doc.querySelectorAll('iframe')) {
            if (!remaining) break;
            // contentDocument is null for cross-origin and sandboxed frames.
            visit(frame.contentDocument);
        }
    };
    visit(document);
    return chunks.join('');
}"""


async def _launch(chromium, launch_error, port, remaining):
    for options in ({'channel': 'chrome'}, {'channel': 'msedge'}, {}):
        try:
            return await chromium.launch(
                **options, headless=True, chromium_sandbox=True, timeout=max(1, remaining() * 1000),
                proxy={'server': f'http://127.0.0.1:{port}', 'bypass': '<-loopback>'},
                args=['--disable-quic', '--disable-http2',
                      '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                      '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        except launch_error:
            continue
    return None


async def _render(playwright, launch_error, link, check, expires, max_bytes, max_text, min_useful, bot_check, closed):
    def remaining():
        return max(0, expires - time.monotonic())

    if not await asyncio.to_thread(check, link):
        return None
    budget = _ProxyBudget(check, expires, max_bytes)
    async with budget.listen() as port:
        async with playwright() as driver:
            browser = await _launch(driver.chromium, launch_error, port, remaining)
            if browser is None:
                return None
            try:
                context = await browser.new_context(accept_downloads=False, service_workers='block')

                async def block_websocket(route):
                    await route.close()

                await context.route_web_socket('**/*', block_websocket)

                async def filter_request(route):
                    request = route.request
                    if not request.url.startswith(('http://', 'https://')) or request.resource_type in ('image', 'font', 'media'):
                        await route.abort()
                    else:
                        await route.continue_()

                await context.route('**/*', filter_request)
                page = await context.new_page()
                response = await page.goto(link, wait_until='domcontentloaded', timeout=max(1, remaining() * 1000))
                if not response:
                    return None
                if response.status == 403 or response.headers.get('cf-mitigated', '').strip().lower() == 'challenge':
                    return 403, ''
                # Check the first DOM and each sample while waiting for posting text.
                settle = min(expires, time.monotonic() + 4)
                while True:
                    if bot_check(await page.title()):
                        return 403, ''
                    text = await page.evaluate(_VISIBLE_TEXT, max_text)
                    if bot_check(text):
                        return 403, ''
                    if response.status != 200:
                        return response.status, ''
                    if len(text) >= min_useful or closed(text) or time.monotonic() >= settle:
                        return 200, text[:max_text]
                    await asyncio.sleep(0.25)
            finally:
                await browser.close()


def read(link, timeout, public_address, public_url, max_bytes, max_text, min_useful, bot_check, closed):
    """Return (status, text) or None within the caller's browser budget."""
    seconds = min(timeout, 30)
    if seconds <= 0:
        return None
    expires = time.monotonic() + seconds
    deadline = SimpleNamespace(end=expires)
    result = []

    def check(url):
        return request_target(url, public_address, public_url, deadline)

    async def run(playwright, launch_error):
        async with asyncio.timeout(max(0, expires - time.monotonic())):
            return await _render(playwright, launch_error, link, check, expires, max_bytes, max_text, min_useful,
                                 bot_check, closed)

    def worker():
        try:
            from playwright.async_api import Error, async_playwright

            result.append(asyncio.run(run(async_playwright, Error)))
        except Exception:
            # Playwright or its browser channels can be unavailable, or the page can fail to load.
            pass

    thread = threading.Thread(target=worker, name='hq-posting-browser', daemon=True)
    thread.start()
    thread.join(max(0, expires - time.monotonic()))
    return result[0] if result else None
