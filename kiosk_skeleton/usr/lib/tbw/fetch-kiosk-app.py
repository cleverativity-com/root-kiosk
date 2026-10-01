#!/usr/bin/env python3
"""Download the published TBW kiosk UI into the nginx document root.

The UI is the production build of apps/tbw-root-kiosk-app. Asset names are
content-hashed, so this crawls index.html and each script/style it references.
"""

from __future__ import annotations

import argparse
import re
import ssl
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

ASSET_RE = re.compile(
    r"""(?:(?:src|href)=)?["']?(?P<url>(?:https?:)?//[^"'\\\s>),]+|/(?:assets|icons)/[^"'\\\s>),]+|/(?:favicon\.ico|manifest\.json)|\./[^"'\\\s>),]+|(?:assets|icons)/[^"'\\\s>),]+)""",
    re.IGNORECASE,
)
SCAN_SUFFIXES = {".html", ".js", ".css", ".json", ".svg"}
KEEP_SUFFIXES = {
    ".html",
    ".js",
    ".css",
    ".json",
    ".svg",
    ".png",
    ".ico",
    ".webp",
    ".jpg",
    ".jpeg",
    ".gif",
    ".woff",
    ".woff2",
    ".webmanifest",
    ".txt",
}
MAX_FILES = 5000
MAX_BYTES = 200 * 1024 * 1024
WORKERS = 24


def _normalize(page_url: str, raw: str) -> str | None:
    raw = raw.strip().strip("\"'")
    if not raw or raw.startswith(("data:", "mailto:", "javascript:")):
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    if raw.startswith("http://") or raw.startswith("https://"):
        try:
            parts = urlsplit(raw)
        except ValueError:
            return None
        base = urlsplit(page_url)
        if parts.netloc != base.netloc:
            return None
        path = parts.path or "/"
    elif raw.startswith("/"):
        path = raw
    elif raw.startswith("./"):
        parent = urlsplit(page_url).path.rsplit("/", 1)[0]
        path = f"{parent}/{raw[2:]}"
    elif raw.startswith("assets/") or raw.startswith("icons/"):
        path = "/" + raw
    else:
        return None
    path = path.split("?", 1)[0].split("#", 1)[0]
    if path != "/" and not any(path.lower().endswith(suffix) for suffix in KEEP_SUFFIXES):
        return None
    if ".." in path.split("/"):
        return None
    return path


def _dest_for(root: Path, path: str) -> Path:
    relative = "index.html" if path == "/" else path.lstrip("/")
    target = (root / relative).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError(f"refusing to write outside {root}: {path}")
    return target


def fetch(url: str, dest: Path) -> list[str]:
    base = url if url.endswith("/") else url + "/"
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(base))
    context = ssl.create_default_context()
    seen: set[str] = set()
    written: list[str] = []
    total = 0
    lock = threading.Lock()

    def reserve(path: str) -> bool:
        with lock:
            if path in seen:
                return False
            if len(seen) >= MAX_FILES:
                raise RuntimeError(f"kiosk app has more than {MAX_FILES} files")
            seen.add(path)
            return True

    def download(path: str) -> list[str]:
        nonlocal total
        target_url = origin + path
        request = urllib.request.Request(target_url, headers={"User-Agent": "root-kiosk-image-build"})
        try:
            with urllib.request.urlopen(request, context=context, timeout=60) as response:
                payload = response.read()
                final_path = urlsplit(response.geturl()).path or path
        except urllib.error.HTTPError as exc:
            if path == "/":
                raise
            print(f"skip {path}: HTTP {exc.code}", file=sys.stderr)
            return []
        with lock:
            total += len(payload)
            if total > MAX_BYTES:
                raise RuntimeError("kiosk app download exceeded size limit")
        target = _dest_for(dest, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        with lock:
            written.append(path)
        suffix = Path(final_path).suffix.lower()
        if path != "/" and suffix not in SCAN_SUFFIXES:
            return []
        discovered: list[str] = []
        text = payload.decode("utf-8", errors="ignore")
        page = origin + ("/" if path == "/" else path)
        for match in ASSET_RE.finditer(text):
            normalized = _normalize(page, match.group("url"))
            if normalized and reserve(normalized):
                discovered.append(normalized)
        return discovered

    wave = [path for path in ("/", "/manifest.json", "/favicon.ico") if reserve(path)]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        while wave:
            discovered: list[str] = []
            for batch in pool.map(download, wave):
                discovered.extend(batch)
            wave = discovered
    if not (dest / "index.html").is_file():
        raise RuntimeError("kiosk download did not include index.html")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args()
    args.dest.mkdir(parents=True, exist_ok=True)
    files = fetch(args.url, args.dest)
    print(f"installed {len(files)} kiosk files into {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
