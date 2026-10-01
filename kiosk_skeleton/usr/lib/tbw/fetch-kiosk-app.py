#!/usr/bin/env python3
"""Download the published TBW kiosk UI into the nginx document root.

The UI is the production build of apps/tbw-root-kiosk-app. Hashed bundles
live under /assets, and the quiz illustrations live under /images. Those
illustration URLs are often built at runtime (`/images/kiosk/` + `gender-f.png`),
so this crawl keeps directory prefixes and filenames that appear in the same
file, not only complete URLs.
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

# Stop at quotes, backticks, and template placeholders so a runtime
# concatenation (`/images/kiosk/${file}`) is not treated as one URL.
_URL_ATOM = r"""[^"'`\\\s>),${}]+"""
ASSET_RE = re.compile(
    rf"""(?:(?:src|href)=|url\()?["'`]?(?P<url>(?:https?:)?//{_URL_ATOM}|/(?:assets|icons|images)/{_URL_ATOM}|/(?:favicon\.ico|manifest\.json)|\./{_URL_ATOM}|(?:assets|icons|images)/{_URL_ATOM})""",
    re.IGNORECASE,
)
PREFIX_RE = re.compile(
    r"""(?P<prefix>/?(?:images|assets|icons)/[A-Za-z0-9_.-]*/)"""
)
FILENAME_RE = re.compile(
    r"""[`'"](?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:png|jpe?g|gif|webp|svg|ico|avif))[`'"]""",
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
    raw = raw.strip().strip("\"'`")
    if not raw or raw.startswith(("data:", "mailto:", "javascript:", "${")):
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
    elif raw.startswith(("assets/", "icons/", "images/")):
        path = "/" + raw
    else:
        return None
    path = path.split("?", 1)[0].split("#", 1)[0]
    if path != "/" and not any(path.lower().endswith(suffix) for suffix in KEEP_SUFFIXES):
        return None
    if ".." in path.split("/"):
        return None
    return path


def asset_paths_in(page_url: str, text: str) -> list[str]:
    """Paths this document asks the browser to load.

    Complete URLs are taken as written. The published kiosk bundle also
    builds quiz art as a directory prefix plus a filename literal, so those
    two pieces from the same file are joined.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        path = _normalize(page_url, raw)
        if path and path not in seen:
            seen.add(path)
            found.append(path)

    for match in ASSET_RE.finditer(text):
        add(match.group("url"))

    prefixes = list(dict.fromkeys(PREFIX_RE.findall(text)))
    names = list(dict.fromkeys(FILENAME_RE.findall(text)))
    if len(prefixes) <= 32 and len(names) <= 200:
        for prefix in prefixes:
            for name in names:
                add(f"{prefix}{name}")
    return found


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
        for normalized in asset_paths_in(page, text):
            if reserve(normalized):
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
    referenced_images = [path for path in seen if path.startswith("/images/")]
    saved_images = [path for path in written if path.startswith("/images/")]
    if referenced_images and not saved_images:
        raise RuntimeError(
            "kiosk UI referenced /images/ files but none were downloaded: "
            + ", ".join(referenced_images[:8])
        )
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
