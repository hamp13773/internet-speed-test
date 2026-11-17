"""Measure HTTP download speed over ten consecutive requests."""

import argparse
import sys
from collections.abc import Callable
from functools import partial
from http.client import HTTPException
from shutil import get_terminal_size
from time import perf_counter
from typing import TextIO
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

REQUEST_COUNT = 10
CHUNK_SIZE = 64 * 1024
TIMEOUT_SECONDS = 15
PROGRESS_INTERVAL = 0.125
BAR_WIDTH = 12


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme in {"http", "https"} and parts.hostname:
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("укажите абсолютный URL с http:// или https://")


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} Б"
    amount = size / 1024
    for unit in ("КиБ", "МиБ"):
        if amount < 1024:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} ГиБ"


class Progress:
    def __init__(self, stream: TextIO):
        self.stream = stream
        self.width = max(1, get_terminal_size().columns - 1)
        self.line_width = 0

    def show(self, number: int, previous_bytes: int, received: int, expected: int | None) -> None:
        if expected is None:
            position = (received // CHUNK_SIZE) % BAR_WIDTH
            bar = "-" * position + ">" + "-" * (BAR_WIDTH - position - 1)
            detail = format_size(received)
        else:
            percent = min(100, received * 100 // expected) if expected else 100
            filled = percent * BAR_WIDTH // 100
            bar = "#" * filled + "-" * (BAR_WIDTH - filled)
            detail = f"{percent:3d}%  {format_size(received)} / {format_size(expected)}"
        line = f"{number:02d}/{REQUEST_COUNT} [{bar}] {detail} | всего {format_size(previous_bytes + received)}"
        line = line[:self.width]
        self.stream.write("\r" + line.ljust(self.line_width))
        self.stream.flush()
        self.line_width = len(line)

    def finish(self, *, clear: bool = False) -> None:
        self.stream.write("\r" + " " * self.line_width + "\r" if clear else "\n")
        self.stream.flush()


def download(
    url: str,
    opener: OpenerDirector,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> tuple[int, float]:
    request = Request(url, method="GET", headers={
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    })
    downloaded = 0
    display_seconds = 0.0

    started = perf_counter()
    next_update = started + PROGRESS_INTERVAL
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        length = response.headers.get("Content-Length")
        expected = None
        if length is not None and response.headers.get("Transfer-Encoding") is None:
            expected = int(length)
            if expected < 0:
                raise ValueError("Content-Length не может быть отрицательным")

        while chunk := response.read(CHUNK_SIZE):
            downloaded += len(chunk)
            if on_progress is not None and (now := perf_counter()) >= next_update:
                on_progress(downloaded, expected)
                rendered_at = perf_counter()
                display_seconds += rendered_at - now
                next_update = rendered_at + PROGRESS_INTERVAL
        elapsed = perf_counter() - started - display_seconds

        # Reading in blocks may reach EOF early without raising IncompleteRead.
        if expected is not None and downloaded != expected:
            raise ValueError(f"неполный ответ: получено {downloaded} из {expected} Б")

    return downloaded, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Скорость скачивания по 10 последовательным HTTP-запросам.")
    parser.add_argument("url", type=http_url, help="прямой HTTP(S)-адрес файла")
    args = parser.parse_args()

    opener = build_opener(NoRedirects())
    progress = Progress(sys.stderr) if sys.stdout.isatty() and sys.stderr.isatty() else None
    total_bytes = 0
    total_seconds = 0.0
    try:
        for number in range(1, REQUEST_COUNT + 1):
            show = partial(progress.show, number, total_bytes) if progress is not None else None
            if show is not None:
                show(0, None)
            size, seconds = download(args.url, opener, show)
            total_bytes += size
            total_seconds += seconds
            if show is not None:
                show(size, size)
    except KeyboardInterrupt:
        if progress is not None:
            progress.finish(clear=True)
        print("Замер прерван.", file=sys.stderr)
        return 130
    except (OSError, HTTPException, ValueError) as error:
        if progress is not None:
            progress.finish(clear=True)
        print(f"Запрос {number} из {REQUEST_COUNT}: {error}", file=sys.stderr)
        return 1

    if progress is not None:
        progress.finish()

    average_ms = total_seconds * 1000 / REQUEST_COUNT
    megabits_per_second = total_bytes * 8 / total_seconds / 1_000_000
    print(f"Запросов: {REQUEST_COUNT}")
    print(f"Среднее время запроса: {average_ms:.2f} мс")
    print(f"Скачано: {total_bytes} Б")
    print(f"Скорость скачивания: {megabits_per_second:.2f} Мбит/с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
