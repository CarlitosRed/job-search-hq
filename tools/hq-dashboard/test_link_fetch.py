# Fictional fixtures and settings-aware expectations.
"""Offline link reader tests with fictional responses and a loopback proxy."""
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import time
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hq_link_fetch_test', HERE / 'link_fetch.py')
lf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lf)
spec = importlib.util.spec_from_file_location('hq_browser_read_test', HERE / 'browser_read.py')
browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser)
spec = importlib.util.spec_from_file_location('hq_recheck_test', HERE.parent / 'tracker' / 'recheck.py')
recheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recheck)


def dns(*addresses):
    return lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, 0)) for a in addresses]


class Addresses(unittest.TestCase):
    def test_listed_hosts_stop_before_direct_or_browser_read(self):
        for host in ('amazon.jobs', 'www.amazon.jobs', 'AMAZON.COM.', 'careers.amazon.com',
                     'smartrecruiters.com', 'jobs.smartrecruiters.com', 'api.smartrecruiters.com'):
            link = f'https://{host}/example/123'
            with self.subTest(host=host), mock.patch.object(lf, '_get', return_value=None) as get, \
                    mock.patch.object(lf, 'browser_reader', return_value=None) as render:
                self.assertEqual(lf.fetch_status_text(link), ('automatic_reading_disallowed', ''))
                self.assertEqual(lf.fetch_posting_text(link, with_status=True),
                                 ('automatic_reading_disallowed', None))
                self.assertIsNone(lf.fetch_posting_text(link))
                get.assert_not_called()
                render.assert_not_called()

    def test_host_lookalikes_and_names_in_paths_are_read(self):
        body = ('Review fictional reports. ' * 20).strip()
        for link in ('https://notamazon.jobs/example', 'https://amazon.jobs.example.org/example',
                     'https://example.org/smartrecruiters.com?next=amazon.com'):
            with self.subTest(link=link), mock.patch.object(lf, '_get', return_value=(200, 'text/plain', body)) as get, \
                    mock.patch.object(lf, 'browser_reader') as render:
                self.assertEqual(lf.fetch_posting_text(link), body)
                self.assertEqual(get.call_args.args[0], link)
                render.assert_not_called()

    def test_direct_redirect_to_listed_host_stops_before_dns_or_browser(self):
        link = 'http://jobs.example/example'
        response = mock.Mock(status=302)
        response.getheader.side_effect = {'Location': '//jobs.smartrecruiters.com/example/123',
                                          'Content-Type': 'text/html'}.get
        response.read.return_value = b''
        with mock.patch.object(lf, 'public_address', return_value='93.184.216.34') as resolve, \
                mock.patch('socket.create_connection') as dial, \
                mock.patch('http.client.HTTPConnection') as connection, \
                mock.patch.object(lf, 'browser_reader') as render:
            connection.return_value.getresponse.return_value = response
            self.assertEqual(lf.fetch_status_text(link), ('automatic_reading_disallowed', ''))
        self.assertEqual([call.args[0] for call in resolve.call_args_list], ['jobs.example'])
        dial.assert_called_once()
        connection.return_value.request.assert_called_once()
        render.assert_not_called()

    def test_private_and_local_addresses_are_refused(self):
        for bad in ('127.0.0.1', '10.0.0.5', '192.168.1.9', '172.16.0.1', '169.254.169.254', '100.64.0.1', '0.0.0.0'):
            with mock.patch('socket.getaddrinfo', dns(bad)):
                self.assertIsNone(lf.public_address('x.example'), bad)

    def test_one_private_answer_poisons_the_name(self):
        with mock.patch('socket.getaddrinfo', dns('93.184.216.34', '10.0.0.5')):
            self.assertIsNone(lf.public_address('x.example'))

    def test_public_address_is_accepted(self):
        with mock.patch('socket.getaddrinfo', dns('93.184.216.34')):
            self.assertEqual(lf.public_address('x.example'), '93.184.216.34')

    def test_unresolvable_name_is_refused(self):
        def boom(*a, **k):
            raise socket.gaierror('no')
        with mock.patch('socket.getaddrinfo', boom):
            self.assertIsNone(lf.public_address('x.example'))

    def test_extra_denied_ranges_and_ipv4_preference(self):
        for bad in ('64:ff9b::7f00:1', '::127.0.0.1', '224.0.0.1', 'ff02::1'):
            with mock.patch('socket.getaddrinfo', dns(bad)):
                self.assertIsNone(lf.public_address('x.example'), bad)
        with mock.patch('socket.getaddrinfo', dns('2606:2800:220:1:248:1893:25c8:1946', '93.184.216.34')):
            self.assertEqual(lf.public_address('x.example'), '93.184.216.34')

    def test_empty_userinfo_is_refused(self):
        with mock.patch('socket.getaddrinfo', dns('93.184.216.34')), mock.patch('socket.create_connection') as opened:
            self.assertIsNone(lf._get('http://:pw@x.example/a', 'text/html'))
            opened.assert_not_called()

    def test_slow_drip_server_is_cut_off_by_the_deadline(self):
        class Drip:
            status = 200

            def getheader(self, name):
                return 'text/html' if name == 'Content-Type' else None

            def read(self, n):
                time.sleep(0.05)
                return b'x'

        class Conn:
            def __init__(self, *a, **k):
                self.sock = None

            def request(self, *a, **k):
                pass

            def getresponse(self):
                return Drip()

            def close(self):
                pass
        started = time.monotonic()
        with mock.patch('socket.getaddrinfo', dns('93.184.216.34')), mock.patch('socket.create_connection'),                 mock.patch('ssl.SSLContext.wrap_socket'), mock.patch('http.client.HTTPSConnection', Conn):
            self.assertIsNone(lf._get('https://x.example/a', 'text/html', lf.Deadline(0.5)))
        self.assertLess(time.monotonic() - started, 3)

    def test_deadline_wakes_a_read_that_is_stuck_on_a_silent_server(self):
        import threading
        mine, theirs = socket.socketpair()  # theirs never sends: a read on mine would wait forever
        result = []
        deadline = lf.Deadline(0.3)
        deadline.socks.append(mine)

        def read():
            try:
                result.append(mine.recv(10))
            except OSError:
                result.append('closed')
        worker = threading.Thread(target=read, daemon=True)
        started = time.monotonic()
        worker.start()
        worker.join(3)
        deadline.stop()
        theirs.close()
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 2)

    def test_bad_schemes_ports_and_credentials_are_never_opened(self):
        with mock.patch('socket.getaddrinfo', dns('93.184.216.34')), mock.patch('socket.create_connection') as opened:
            for url in ('file:///etc/passwd', 'ftp://x.example/a', 'https://x.example:8080/a', 'https://user:pw@x.example/a', 'https://x.example:22/'):
                self.assertIsNone(lf._get(url, 'text/html'), url)
            opened.assert_not_called()

    def test_private_target_is_never_opened(self):
        with mock.patch('socket.getaddrinfo', dns('127.0.0.1')), mock.patch('socket.create_connection') as opened, \
                mock.patch.object(lf, 'browser_reader', return_value=None):
            self.assertIsNone(lf.fetch_posting_text('https://localhost/job'))
            opened.assert_not_called()


class Parsing(unittest.TestCase):
    def setUp(self):
        self.browser = self.enterContext(mock.patch.object(lf, 'browser_reader', return_value=None))

    def test_workday_page_maps_to_its_json_twin(self):
        url = 'https://acme.wd5.myworkdayjobs.com/en-US/External/job/Example-Country/Analyst_R123'
        self.assertEqual(lf.workday_api(url), 'https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/Example-Country/Analyst_R123')
        self.assertIsNone(lf.workday_api('https://example.com/en-US/External/job/x/y'))
        self.assertIsNone(lf.workday_api('https://evil.com/acme.wd5.myworkdayjobs.com/en-US/External/job/x/y'))

    def test_html_to_text_drops_scripts_and_keeps_lines(self):
        text = lf.html_to_text('<html><head><title>t</title></head><body><script>var x=1</script><h1>Role</h1><ul><li>One</li><li>Two</li></ul></body></html>')
        self.assertNotIn('var x', text)
        self.assertEqual(text.split('\n'), ['Role', '', 'One', '', 'Two'] if '' in text.split('\n') else ['Role', 'One', 'Two'])

    def test_json_ld_job_posting_is_preferred(self):
        body = 'Responsibilities and requirements. ' * 20
        page = '<html><script type="application/ld+json">%s</script><body>menu</body></html>' % json.dumps(
            {'@context': 'https://schema.org', '@type': 'JobPosting', 'title': 'Analyst', 'description': '<p>%s</p>' % body})
        with mock.patch.object(lf, '_get', return_value=(200, 'text/html', page)):
            text = lf.fetch_posting_text('https://x.example/j')
        self.assertTrue(text.startswith('Analyst'))
        self.assertIn('Responsibilities and requirements.', text)
        self.assertNotIn('menu', text)

    def test_workday_json_is_used(self):
        info = {'jobPostingInfo': {'title': 'Analyst', 'location': 'Example City', 'jobDescription': '<p>%s</p>' % ('Duties and skills. ' * 30)}}
        calls = []

        def fake(url, accept, deadline=None, with_status=False):
            calls.append(url)
            return 200, 'application/json', json.dumps(info)
        with mock.patch.object(lf, '_get', fake):
            text = lf.fetch_posting_text('https://acme.wd5.myworkdayjobs.com/en-US/External/job/EX/Analyst_R1')
        self.assertEqual(len(calls), 1)
        self.assertIn('/wday/cxs/acme/External/job/EX/Analyst_R1', calls[0])
        self.assertIn('Example City', text)
        self.assertIn('Duties and skills.', text)

    def test_empty_javascript_shell_gives_nothing(self):
        page = '<html><body><div id="root"></div><script>app()</script></body></html>'
        with mock.patch.object(lf, '_get', return_value=(200, 'text/html', page)):
            self.assertIsNone(lf.fetch_posting_text('https://x.example/j'))

    def test_non_links_are_ignored(self):
        for bad in (None, '', 'javascript:alert(1)', 'ftp://x.example/a', 'x' * 3000):
            self.assertIsNone(lf.fetch_posting_text(bad))

    def test_public_board_json_is_read_by_both_entry_points(self):
        description = 'Review fictional reports. ' * 20
        cases = [
            ('https://job-boards.greenhouse.io/example/jobs/123?source=example',
             'https://boards-api.greenhouse.io/v1/boards/example/jobs/123',
             {'title': 'Example Analyst', 'content': '&lt;p&gt;' + description + '&lt;/p&gt;'}, 'Review fictional reports.'),
            ('https://jobs.lever.co/example/fictional-id/apply',
             'https://api.lever.co/v0/postings/example/fictional-id',
             {'text': 'Example Analyst', 'descriptionPlain': description,
              'lists': [{'text': 'Qualifications', 'content': '<li>Fictional skills</li>'}]}, 'Fictional skills'),
        ]
        for link, api, body, expected in cases:
            with self.subTest(link=link), mock.patch.object(lf, '_get', return_value=(200, 'application/json', json.dumps(body))) as get:
                text = lf.fetch_posting_text(link)
                self.assertEqual(lf.fetch_status_text(link), (200, text))
                self.assertTrue(text.startswith('Example Analyst'))
                self.assertIn(expected, text)
                self.assertNotIn('<p>', text)
                self.assertEqual([call.args[0] for call in get.call_args_list], [api, api])
        self.browser.assert_not_called()

    def test_icims_uses_the_iframe_page(self):
        link = 'https://example.icims.com/jobs/123/example-analyst/job?mode=job&iFrame=0&in_iframe=0#top'
        body = '<main><p>' + 'Review fictional reports. ' * 20 + '</p></main>'
        with mock.patch.object(lf, '_get', return_value=(200, 'text/html', body)) as get:
            code, text = lf.fetch_status_text(link)
        self.assertEqual(code, 200)
        self.assertIn('Review fictional reports.', text)
        self.assertEqual(get.call_args.args[0],
                         'https://example.icims.com/jobs/123/example-analyst/job?mode=job&iFrame=0&in_iframe=1')
        self.assertIsNone(lf.icims_page('https://example.icims.com.example.org/jobs/123/example-analyst/job'))
        self.browser.assert_not_called()

    def test_missing_board_json_can_fall_back_to_the_original_page(self):
        link = 'https://jobs.lever.co/example/fictional-id'
        text = ('Fictional reports. ' * 30).strip()
        with mock.patch.object(lf, '_get', side_effect=[(404, '', ''), (200, 'text/plain', text)]) as get:
            self.assertEqual(lf.fetch_status_text(link), (200, text))
        self.assertEqual([call.args[0] for call in get.call_args_list],
                         ['https://api.lever.co/v0/postings/example/fictional-id', link])
        self.browser.assert_not_called()

    def test_browser_runs_for_a_shell_and_blocked_direct_reads(self):
        link = 'https://example.org/jobs/123'
        browser_text = ('Review fictional reports. ' * 20).strip()
        self.browser.return_value = (200, browser_text)
        cases = [(200, 'text/html', '<div id="app"></div>'), (403, '', ''), (429, '', ''),
                 (200, 'text/html', '<title>Access Denied</title><p>' + 'Blocked. ' * 50 + '</p>')]
        for result in cases:
            with self.subTest(result=result), mock.patch.object(lf, '_get', return_value=result):
                self.browser.reset_mock()
                self.assertEqual(lf.fetch_posting_text(link), browser_text)
                self.assertEqual(lf.fetch_status_text(link), (200, browser_text))
                self.assertEqual(self.browser.call_args_list, [mock.call(link, 30), mock.call(link, 30)])

    def test_unavailable_or_challenged_browser_preserves_the_blocked_status(self):
        link = 'https://example.org/jobs/123'
        for result in (None, (200, 'Verify you are human. ' * 30)):
            with self.subTest(result=result), mock.patch.object(lf, '_get', return_value=(403, '', '')):
                self.browser.return_value = result
                self.assertEqual(lf.fetch_posting_text(link, with_status=True), (403, None))
                self.assertEqual(lf.fetch_status_text(link), (403, ''))

    def test_detected_direct_bot_check_is_blocked_without_opening_browser(self):
        link = 'https://jobs.lever.co/example/fictional-id'
        challenges = ('<title>Just a moment...</title>', '<h1>Verify you are human</h1>',
                      '<p>Checking your browser</p>',
                      '<h1>Performing security verification</h1><p>This page verifies you are not a bot.</p>')
        for status in (200, 403, 404, 410, 429, 503):
            for body in challenges:
                with self.subTest(status=status, body=body), mock.patch.object(
                    lf, '_get', return_value=(status, 'text/html', body)
                ) as get:
                    self.assertEqual(lf.fetch_posting_text(link, with_status=True), (403, None))
                    get.assert_called_once()
        self.browser.assert_not_called()

    def test_challenge_header_on_503_or_redirect_ends_the_read_before_body_or_browser(self):
        link = 'http://jobs.example/jobs/123'
        for status in (503, 302):
            with self.subTest(status=status), mock.patch.object(lf, 'public_address', return_value='93.184.216.34'), \
                    mock.patch.object(lf.socket, 'create_connection'), \
                    mock.patch.object(lf.http.client, 'HTTPConnection') as http:
                response = http.return_value.getresponse.return_value
                response.status = status
                response.getheader.side_effect = {'cf-mitigated': 'challenge', 'Location': '/other'}.get
                self.assertEqual(lf.fetch_status_text(link), (403, ''))
                http.assert_called_once()
                response.read.assert_not_called()
                self.browser.assert_not_called()

    def test_challenge_body_on_503_ends_the_read_before_browser(self):
        link = 'http://jobs.example/jobs/123'
        with mock.patch.object(lf, 'public_address', return_value='93.184.216.34'), \
                mock.patch.object(lf.socket, 'create_connection'), \
                mock.patch.object(lf.http.client, 'HTTPConnection') as http:
            response = http.return_value.getresponse.return_value
            response.status = 503
            response.getheader.side_effect = {'Content-Type': 'text/html'}.get
            response.read.side_effect = [b'<title>Just a moment...</title>', b'']
            self.assertEqual(lf.fetch_status_text(link), (403, ''))
        self.browser.assert_not_called()

    def test_browser_challenge_is_blocked_after_a_direct_shell(self):
        link = 'https://example.org/jobs/123'
        self.browser.return_value = (200, 'This page verifies you are not a bot. ' + 'Fictional filler. ' * 30)
        with mock.patch.object(lf, '_get', return_value=(200, 'text/html', '<div id="app"></div>')):
            self.assertEqual(lf.fetch_posting_text(link, with_status=True), (403, None))
        self.browser.assert_called_once_with(link, 30)

    def test_gone_direct_read_does_not_open_the_browser(self):
        with mock.patch.object(lf, '_get', return_value=(410, '', '')):
            self.assertEqual(lf.fetch_status_text('https://example.org/jobs/123'), (410, ''))
        self.browser.assert_not_called()

    def test_short_expiry_is_kept_in_both_readers_and_recheck_without_a_browser(self):
        link = 'https://example.org/jobs/123'
        notice = 'This posting is expired.'
        self.browser.return_value = (200, 'Fictional open posting. ' * 20)
        with mock.patch.object(lf, '_get', return_value=(200, 'text/html', '<p>' + notice + '</p>')):
            self.assertEqual(lf.fetch_status_text(link), (200, notice))
            self.assertEqual(lf.fetch_posting_text(link, with_status=True), (200, notice))
            self.assertEqual(recheck.link_status(link, fetch=lf.fetch_status_text), ('closed_text', notice))
        self.browser.assert_not_called()

    def test_qualified_short_expiry_still_uses_the_browser(self):
        link = 'https://example.org/jobs/123'
        ad = ('Review fictional reports. ' * 20).strip()
        self.browser.return_value = (200, ad)
        notice = 'If this posting is expired, check other openings.'
        with mock.patch.object(lf, '_get', return_value=(200, 'text/plain', notice)):
            self.assertEqual(lf.fetch_status_text(link), (200, ad))
        self.browser.assert_called_once_with(link, 30)

    def test_browser_gone_status_after_direct_shell_reaches_recheck(self):
        link = 'https://example.org/jobs/123'
        for status in (404, 410):
            with self.subTest(status=status), mock.patch.object(lf, '_get', return_value=(200, 'text/plain', 'Loading')):
                self.browser.return_value = status, ''
                self.assertEqual(recheck.classify(*lf.fetch_status_text(link)),
                                 ('gone', f'The posting link returned {status}.'))
                self.assertEqual(lf.fetch_posting_text(link, with_status=True), (status, None))

    def test_browser_server_error_after_direct_shell_is_unclear(self):
        link = 'https://example.org/jobs/123'
        self.browser.return_value = (503, '')
        with mock.patch.object(lf, '_get', return_value=(200, 'text/plain', 'Loading')):
            self.assertEqual(lf.fetch_status_text(link), (503, ''))
            self.assertEqual(recheck.link_status(link, fetch=lf.fetch_status_text),
                             ('unclear', 'The posting link returned 503.'))

    def test_recheck_prefers_the_rendered_notice_to_a_short_shell(self):
        link = 'https://example.org/jobs/123'
        notice = 'This job has expired.'
        self.browser.return_value = (200, notice)
        with mock.patch.object(lf, '_get', return_value=(200, 'text/plain', 'Loading the Example Corp job posting.')):
            self.assertEqual(lf.fetch_status_text(link), (200, notice))
            self.assertEqual(lf.fetch_posting_text(link), notice)


class BrowserAddresses(unittest.TestCase):
    def test_browser_proxy_checks_scheme_port_and_all_resolved_addresses(self):
        def check(url):
            return browser.request_target(url, lf.public_address, lf.public_url, None)

        url = 'https://example.org/jobs/123'
        with mock.patch('socket.getaddrinfo', dns('93.184.216.34', '10.0.0.1')):
            self.assertIsNone(check(url))
        with mock.patch('socket.getaddrinfo', side_effect=dns('93.184.216.34')) as resolver:
            for blocked in ('ftp://example.org/job', 'wss://example.org/job', 'file:///private',
                            'https://example.org:8080/jobs/123', 'https://user@example.org/jobs/123'):
                self.assertIsNone(check(blocked))
            resolver.assert_not_called()
            self.assertEqual(check(url)[1:], (443, '93.184.216.34'))
            resolver.assert_called_once_with('example.org', None, type=socket.SOCK_STREAM)

    def test_missing_playwright_skips_browser_launch(self):
        with mock.patch.dict('sys.modules', {'playwright.async_api': None}), mock.patch.object(browser, '_render') as render:
            self.assertIsNone(browser.read('https://example.org/jobs/123', 1, lf.public_address,
                                           lf.public_url, lf.MAX_BYTES, lf.MAX_TEXT, lf.MIN_USEFUL, lf._bot_check,
                                           lf._closed))
            render.assert_not_called()


class BrowserProxy(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connect_client = asyncio.open_connection
        self.upstream = asyncio.StreamReader()
        self.peer = SimpleNamespace(write=mock.Mock(), drain=mock.AsyncMock(), close=mock.Mock(),
                                    transport=mock.Mock())
        self.dial = self.enterContext(mock.patch.object(
            browser.asyncio, 'open_connection', return_value=(self.upstream, self.peer)))
        self.resolver = self.enterContext(mock.patch.object(
            socket, 'getaddrinfo', side_effect=lambda host, port, **kwargs:
            dns('93.184.216.34' if host == 'jobs.example' else '127.0.0.1')(host, port, **kwargs)))

    def budget(self, seconds=5, max_bytes=4096):
        def check(url):
            return browser.request_target(url, lf.public_address, lf.public_url, deadline)

        deadline = SimpleNamespace(end=time.monotonic() + seconds)
        return browser._ProxyBudget(check, deadline.end, max_bytes)

    async def client(self, port, request):
        reader, writer = await self.connect_client('127.0.0.1', port)
        self.addAsyncCleanup(self.close_client, writer)
        writer.write(request)
        await writer.drain()
        return reader

    async def close_client(self, writer):
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass

    async def read_to_close(self, reader):
        return await asyncio.wait_for(reader.read(), 2)

    async def test_proxy_refuses_listed_http_and_tls_destinations_before_dns(self):
        self.resolver.side_effect = dns('93.184.216.34')
        async with self.budget().listen() as port:
            for request in (b'GET http://www.amazon.jobs/example HTTP/1.1\r\n\r\n',
                            b'CONNECT api.smartrecruiters.com:443 HTTP/1.1\r\n\r\n'):
                client = await self.client(port, request)
                self.assertEqual(await self.read_to_close(client), b'')
        self.resolver.assert_not_called()
        self.dial.assert_not_awaited()

    async def test_get_pins_the_address_and_denies_the_redirect_destination(self):
        reply = b'HTTP/1.1 302 Found\r\nLocation: http://private.example/job\r\nContent-Length: 0\r\n\r\n'
        self.upstream.feed_data(reply)
        self.upstream.feed_eof()
        async with self.budget().listen() as port:
            first = await self.client(port, b'GET http://jobs.example/job?q=1 HTTP/1.1\r\nHost: ignored.example\r\n\r\n')
            self.assertEqual(await self.read_to_close(first), reply)
            second = await self.client(port, b'GET http://private.example/job HTTP/1.1\r\n\r\n')
            self.assertEqual(await self.read_to_close(second), b'')
        self.assertEqual([call.args[0] for call in self.resolver.call_args_list], ['jobs.example', 'private.example'])
        self.dial.assert_awaited_once_with('93.184.216.34', 80)
        self.peer.write.assert_called_once_with(b'GET /job?q=1 HTTP/1.1\r\nHost: jobs.example\r\nConnection: close\r\n\r\n')

    async def test_connect_pins_the_address_and_relays_both_directions(self):
        connect = b'CONNECT jobs.example:443 HTTP/1.1\r\n\r\n'
        payload = b'fictional TLS client bytes'
        reply = b'fictional TLS server bytes'

        def respond():
            self.upstream.feed_data(reply)
            self.upstream.feed_eof()

        self.peer.drain.side_effect = respond
        budget = self.budget(max_bytes=len(connect) + len(payload) + len(reply))
        async with budget.listen() as port:
            client = await self.client(port, connect + payload)
            self.assertEqual(await self.read_to_close(client), b'HTTP/1.1 200 Connection Established\r\n\r\n' + reply)
        self.resolver.assert_called_once_with('jobs.example', None, type=socket.SOCK_STREAM)
        self.dial.assert_awaited_once_with('93.184.216.34', 443)
        self.peer.write.assert_called_once_with(payload)
        self.assertEqual(budget.remaining_bytes, 0)

    async def test_plain_http_websocket_upgrade_is_closed_by_the_proxy(self):
        async with self.budget().listen() as port:
            request = b'GET http://jobs.example/socket HTTP/1.1\r\nUpgrade: websocket\r\n\r\n'
            client = await self.client(port, request)
            self.assertEqual(await self.read_to_close(client), b'')
        self.dial.assert_not_awaited()

    async def test_stalled_peer_is_closed_at_deadline_and_extra_connections_are_refused(self):
        request = b'GET http://jobs.example/job HTTP/1.1\r\n\r\n'
        connected = asyncio.Event()
        self.peer.drain.side_effect = connected.set
        budget = self.budget(seconds=0.75)
        with mock.patch.object(browser, 'MAX_CONNECTIONS', 1):
            async with budget.listen() as port:
                first = await self.client(port, request)
                await asyncio.wait_for(connected.wait(), 2)
                # Leave the refused socket idle so there is no unread request to reset it.
                second = await self.client(port, b'')
                self.assertEqual(await self.read_to_close(second), b'')
                self.peer.transport.abort.assert_not_called()
                self.assertEqual(await self.read_to_close(first), b'')
        self.dial.assert_awaited_once()
        self.peer.transport.abort.assert_called_once()
        self.peer.close.assert_called_once()

    async def test_get_connections_share_one_byte_budget(self):
        request = b'GET http://jobs.example/job HTTP/1.1\r\n\r\n'
        forwarded = b'GET /job HTTP/1.1\r\nHost: jobs.example\r\nConnection: close\r\n\r\n'
        reply = b'HTTP/1.1 200 OK\r\n\r\nfictional'
        self.upstream.feed_data(reply)
        self.upstream.feed_eof()
        second_upstream = asyncio.StreamReader()
        second_upstream.feed_data(reply)
        second_upstream.feed_eof()
        self.dial.side_effect = [(self.upstream, self.peer), (second_upstream, self.peer)]
        budget = self.budget(max_bytes=2 * len(forwarded) + len(reply) + 6)
        async with budget.listen() as port:
            first = await self.client(port, request)
            self.assertEqual(await self.read_to_close(first), reply)
            second = await self.client(port, request)
            self.assertEqual(await self.read_to_close(second), reply[:6])
            third = await self.client(port, b'')
            self.assertEqual(await self.read_to_close(third), b'')
        self.assertEqual(self.dial.await_count, 2)
        self.assertEqual(budget.remaining_bytes, 0)


class BrowserRendering(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_browser_navigation_returns_the_disallowed_status(self):
        result = await self.render_terminal_page(200, {}, '', '', 0,
                                                 redirect='https://www.amazon.jobs/example')
        self.assertEqual(result, ('automatic_reading_disallowed', ''))

    async def test_launch_uses_the_first_available_channel_with_the_same_safety_settings(self):
        channels = ['chrome', 'msedge', None]
        for failures in range(4):
            with self.subTest(failures=failures):
                opened = object()
                launch = mock.AsyncMock(side_effect=[OSError('Fictional launch failure')] * failures + [opened])
                result = await browser._launch(SimpleNamespace(launch=launch), OSError, 12345, lambda: 1)
                self.assertIs(result, opened if failures < len(channels) else None)
                self.assertEqual([call.kwargs.get('channel') for call in launch.await_args_list],
                                 channels[:failures + 1])
                for call in launch.await_args_list:
                    self.assertTrue(call.kwargs['headless'])
                    self.assertTrue(call.kwargs['chromium_sandbox'])
                    self.assertEqual(call.kwargs['proxy'],
                                     {'server': 'http://127.0.0.1:12345', 'bypass': '<-loopback>'})
                    self.assertEqual(call.kwargs['args'], [
                        '--disable-quic', '--disable-http2',
                        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])

    async def test_browser_returns_terminal_results_without_waiting_and_closes_websockets(self):
        for status, headers, text, expected, samples in (
                (200, {}, 'Performing security verification', (403, ''), 1),
                (403, {}, '', (403, ''), 0),
                (429, {}, 'Performing security verification', (403, ''), 1),
                (503, {'cf-mitigated': 'challenge'}, '', (403, ''), 0),
                (503, {}, 'Checking your browser', (403, ''), 1),
                (200, {}, 'This posting is expired.', (200, 'This posting is expired.'), 1)):
            with self.subTest(status=status):
                result = await self.render_terminal_page(status, headers, 'Example page', text, samples)
                self.assertEqual(result, expected)

    async def test_browser_404_and_410_check_for_challenges_before_reporting_gone(self):
        for status in (404, 410):
            for title, text, expected_status, observation, samples in (
                    ('Checking your browser', '', 403, 'unclear', 0),
                    ('Example page', 'Checking your browser', 403, 'unclear', 1),
                    ('Posting unavailable', 'This fictional posting was removed.', status, 'gone', 1)):
                with self.subTest(status=status, title=title, text=text):
                    result = await self.render_terminal_page(status, {}, title, text, samples)
                    self.assertEqual(result, (expected_status, ''))
                    self.assertEqual(recheck.classify(*result)[0], observation)

    async def render_terminal_page(self, status, headers, title, text, samples, redirect=None):
        url = 'https://example.org/jobs/123'
        page = SimpleNamespace(goto=mock.AsyncMock(return_value=SimpleNamespace(status=status, headers=headers)),
                               title=mock.AsyncMock(return_value=title),
                               evaluate=mock.AsyncMock(return_value=text), on=mock.Mock(), main_frame=object())
        if redirect:
            def fail_navigation(*args, **kwargs):
                handler = page.on.call_args.args[1]
                handler(SimpleNamespace(url=redirect, is_navigation_request=lambda: True, frame=page.main_frame))
                raise OSError('Fictional navigation failure')

            page.goto.side_effect = fail_navigation
        context = SimpleNamespace(route_web_socket=mock.AsyncMock(), route=mock.AsyncMock(),
                                  new_page=mock.AsyncMock(return_value=page))
        opened = SimpleNamespace(new_context=mock.AsyncMock(return_value=context), close=mock.AsyncMock())
        playwright = mock.MagicMock()
        playwright.return_value.__aenter__.return_value = SimpleNamespace(chromium=object())
        proxy = mock.MagicMock()
        proxy.sockets = [SimpleNamespace(getsockname=lambda: ('127.0.0.1', 12345))]
        proxy.wait_closed = mock.AsyncMock()
        with mock.patch.object(browser.asyncio, 'start_server', return_value=proxy), \
                mock.patch.object(browser, '_launch', return_value=opened), \
                mock.patch.object(browser.asyncio, 'sleep') as sleep:
            options = {}
            if redirect:
                options['blocked_read'] = lambda url: (lf.READ_DISALLOWED, '') if lf.automatic_read_disallowed(url) else None
            result = await browser._render(playwright, OSError, url, lambda _: True, time.monotonic() + 2,
                                           lf.MAX_BYTES, lf.MAX_TEXT, lf.MIN_USEFUL, lf._bot_check, lf._closed,
                                           **options)
        page.goto.assert_awaited_once()
        self.assertEqual(page.evaluate.await_count, samples)
        sleep.assert_not_called()
        opened.close.assert_awaited_once()
        pattern, close_ws = context.route_web_socket.await_args.args
        self.assertEqual(pattern, '**/*')
        websocket = SimpleNamespace(close=mock.AsyncMock(), connect_to_server=mock.Mock())
        await close_ws(websocket)
        websocket.close.assert_awaited_once()
        websocket.connect_to_server.assert_not_called()
        return result


class DeadlineDns(unittest.TestCase):
    """Piece 3 re-check (Astra round 3): a slow name lookup must not outlast the caller's time budget."""

    def test_slow_lookup_returns_within_the_timeout_and_never_connects(self):
        def slow(*args, **kwargs):
            time.sleep(1.5)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 0))]
        connects = []
        with mock.patch.object(lf.socket, 'getaddrinfo', side_effect=slow), \
                mock.patch.object(lf.socket, 'create_connection', side_effect=lambda *a, **k: connects.append(a)), \
                mock.patch.object(lf, 'browser_reader', return_value=None):
            started = time.monotonic()
            result = lf.fetch_status_text('https://slow.example/jobs/1', timeout=0.3)
            elapsed = time.monotonic() - started
            time.sleep(1.6)
        self.assertEqual(result, (None, ''))
        self.assertLess(elapsed, 1.0)
        self.assertEqual(connects, [])


if __name__ == '__main__':
    unittest.main()
