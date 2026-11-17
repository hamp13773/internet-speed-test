"""Measure HTTP download speed over ten consecutive requests."""

import argparse
import sys
from http.client import HTTPException
from time import perf_counter
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

REQUEST_COUNT = 10
CHUNK_SIZE = 64 * 1024
TIMEOUT_SECONDS = 15


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


def download(url: str, opener: OpenerDirector) -> tuple[int, float]:
    request = Request(url, method="GET", headers={
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    })
    downloaded = 0

    started = perf_counter()
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        while chunk := response.read(CHUNK_SIZE):
            downloaded += len(chunk)
        elapsed = perf_counter() - started

        length = response.headers.get("Content-Length")
        if length is not None and response.headers.get("Transfer-Encoding") is None:
            # Reading in blocks may reach EOF early without raising IncompleteRead.
            expected = int(length)
            if downloaded != expected:
                raise ValueError(f"неполный ответ: получено {downloaded} из {expected} Б")

    return downloaded, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Скорость скачивания по 10 последовательным HTTP-запросам.")
    parser.add_argument("url", type=http_url, help="прямой HTTP(S)-адрес файла")
    args = parser.parse_args()

    opener = build_opener(NoRedirects())
    total_bytes = 0
    total_seconds = 0.0
    for number in range(1, REQUEST_COUNT + 1):
        try:
            size, seconds = download(args.url, opener)
        except (OSError, HTTPException, ValueError) as error:
            print(f"Запрос {number} из {REQUEST_COUNT}: {error}", file=sys.stderr)
            return 1
        total_bytes += size
        total_seconds += seconds

    average_ms = total_seconds * 1000 / REQUEST_COUNT
    megabits_per_second = total_bytes * 8 / total_seconds / 1_000_000
    print(f"Запросов: {REQUEST_COUNT}")
    print(f"Среднее время запроса: {average_ms:.2f} мс")
    print(f"Скачано: {total_bytes} Б")
    print(f"Скорость скачивания: {megabits_per_second:.2f} Мбит/с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
