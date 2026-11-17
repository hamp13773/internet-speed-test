import re
import subprocess
import sys
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from speed_test import main

SCRIPT = Path(__file__).with_name("speed_test.py")


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@contextmanager
def serve(handler):
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/file"
        finally:
            server.shutdown()
            worker.join()


def run_cli(url):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), url], capture_output=True, text=True, timeout=10,
    )


class SpeedTestCli(unittest.TestCase):
    def test_downloads_ten_complete_responses_sequentially(self):
        lengths = []
        lock = threading.Lock()
        active = 0
        max_active = 0

        class Handler(QuietHandler):
            def do_GET(self):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                    lengths.append(70_001 + len(lengths))
                    length = lengths[-1]
                self.send_response(200)
                self.send_header("Content-Length", str(length))
                self.end_headers()

                # Send the body in two parts to expose early returns and overlapping requests.
                self.wfile.write(b"x")
                time.sleep(0.015)
                with lock:
                    self.wfile.write(b"x" * (length - 1))
                    active -= 1

        with serve(Handler) as url:
            result = run_cli(url)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(lengths, list(range(70_001, 70_011)))
        self.assertEqual(max_active, 1)
        self.assertIn("Запросов: 10", result.stdout)
        self.assertIn("Скачано: 700055 Б", result.stdout)
        average_ms = float(re.search(r"Среднее время запроса: ([0-9.]+)", result.stdout)[1])
        speed = float(re.search(r"Скорость скачивания: ([0-9.]+)", result.stdout)[1])
        expected_speed = 700_055 * 8 / (average_ms * 10 * 1000)
        self.assertAlmostEqual(speed, expected_speed, delta=0.03)

    def test_terminal_uses_received_bytes_when_response_size_is_unknown(self):
        responses = 0

        class Handler(QuietHandler):
            def do_GET(self):
                nonlocal responses
                responses += 1
                self.send_response(200)
                self.end_headers()
                if responses == 1:
                    time.sleep(0.16)
                self.wfile.write(b"hello")

        class TerminalBuffer(StringIO):
            def isatty(self):
                return True

        stdout, stderr = TerminalBuffer(), TerminalBuffer()
        with serve(Handler) as url, patch("sys.argv", [str(SCRIPT), url]):
            with patch.dict("os.environ", {"COLUMNS": "80"}), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(), 0)

        self.assertIn("01/10 [>-----------] 5 Б | всего 5 Б", stderr.getvalue())
        self.assertIn("10/10 [############] 100%", stderr.getvalue())
        self.assertIn("Скачано: 50 Б", stdout.getvalue())
        self.assertNotIn("10/10", stdout.getvalue())

    def test_truncated_body_fails_without_printing_partial_results(self):
        requests = []

        class Handler(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "10")
                self.end_headers()
                self.wfile.write(b"short")

        with serve(Handler) as url:
            result = run_cli(url)

        self.assertEqual(requests, ["/file"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("неполный ответ: получено 5 из 10 Б", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_redirect_is_not_followed(self):
        requests = []

        class Handler(QuietHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(302 if self.path == "/file" else 200)
                self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

        with serve(Handler) as url:
            result = run_cli(url)

        self.assertEqual(requests, ["/file"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("302", result.stderr)
        self.assertEqual(result.stdout, "")
