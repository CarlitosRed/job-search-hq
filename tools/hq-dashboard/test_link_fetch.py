"""Offline tests for link_fetch (no network: DNS and fetching are replaced)."""
import importlib.util
import json
from pathlib import Path
import socket
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hq_link_fetch_test', HERE / 'link_fetch.py')
lf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lf)


def dns(*addresses):
    return lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, 0)) for a in addresses]


class Addresses(unittest.TestCase):
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
        with mock.patch('socket.getaddrinfo', dns('127.0.0.1')), mock.patch('socket.create_connection') as opened:
            self.assertIsNone(lf.fetch_posting_text('https://localhost/job'))
            opened.assert_not_called()


class Parsing(unittest.TestCase):
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
        with mock.patch.object(lf, '_get', return_value=('https://x.example/j', 'text/html', page)):
            text = lf.fetch_posting_text('https://x.example/j')
        self.assertTrue(text.startswith('Analyst'))
        self.assertIn('Responsibilities and requirements.', text)
        self.assertNotIn('menu', text)

    def test_workday_json_is_used(self):
        info = {'jobPostingInfo': {'title': 'Analyst', 'location': 'Example City', 'jobDescription': '<p>%s</p>' % ('Duties and skills. ' * 30)}}
        calls = []

        def fake(url, accept, deadline=None):
            calls.append(url)
            return url, 'application/json', json.dumps(info)
        with mock.patch.object(lf, '_get', fake):
            text = lf.fetch_posting_text('https://acme.wd5.myworkdayjobs.com/en-US/External/job/EX/Analyst_R1')
        self.assertEqual(len(calls), 1)
        self.assertIn('/wday/cxs/acme/External/job/EX/Analyst_R1', calls[0])
        self.assertIn('Example City', text)
        self.assertIn('Duties and skills.', text)

    def test_empty_javascript_shell_gives_nothing(self):
        page = '<html><body><div id="root"></div><script>app()</script></body></html>'
        with mock.patch.object(lf, '_get', return_value=('https://x.example/j', 'text/html', page)):
            self.assertIsNone(lf.fetch_posting_text('https://x.example/j'))

    def test_non_links_are_ignored(self):
        for bad in (None, '', 'javascript:alert(1)', 'ftp://x.example/a', 'x' * 3000):
            self.assertIsNone(lf.fetch_posting_text(bad))


if __name__ == '__main__':
    unittest.main()
