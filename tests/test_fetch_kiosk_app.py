#!/usr/bin/env python3
"""The image build must download quiz illustrations, not only hashed bundles."""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "kiosk_skeleton" / "usr" / "lib" / "tbw" / "fetch-kiosk-app.py"

spec = importlib.util.spec_from_file_location("fetch_kiosk_app", SCRIPT)
fetch_kiosk_app = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(fetch_kiosk_app)


PUBLISHED_BUNDLE = (
    "image:`gender-f.png`,image:`gender-m.png`,"
    "a=C(()=>`images/kiosk/${n.image}`)"
)


class AssetDiscoveryTests(unittest.TestCase):
    def test_runtime_concatenation_used_by_the_published_ui(self) -> None:
        paths = fetch_kiosk_app.asset_paths_in(
            "https://kiosk.example/assets/DispenserKioskPage.js",
            PUBLISHED_BUNDLE,
        )
        self.assertIn("/images/kiosk/gender-f.png", paths)
        self.assertIn("/images/kiosk/gender-m.png", paths)

    def test_root_absolute_image_urls(self) -> None:
        paths = fetch_kiosk_app.asset_paths_in(
            "https://kiosk.example/assets/DispenserKioskPage.js",
            'image:"/images/kiosk/gender-f.png",src:"/assets/app.js"',
        )
        self.assertIn("/images/kiosk/gender-f.png", paths)
        self.assertIn("/assets/app.js", paths)

    def test_hashed_bundle_without_leading_slash(self) -> None:
        paths = fetch_kiosk_app.asset_paths_in(
            "https://kiosk.example/",
            'm.f=["assets/KioskLayout-Dy8JYJvx.js","assets/index.css"]',
        )
        self.assertIn("/assets/KioskLayout-Dy8JYJvx.js", paths)
        self.assertIn("/assets/index.css", paths)


class FetchTests(unittest.TestCase):
    def test_download_writes_quiz_image(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"kiosk-quiz-image"

        origin_dir = Path(tempfile.mkdtemp(prefix="kiosk-origin-"))
        dest = Path(tempfile.mkdtemp(prefix="kiosk-dest-"))
        self.addCleanup(shutil.rmtree, origin_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, dest, ignore_errors=True)
        (origin_dir / "assets").mkdir()
        (origin_dir / "images" / "kiosk").mkdir(parents=True)
        (origin_dir / "index.html").write_text(
            '<!doctype html><script src="/assets/app.js"></script>',
            encoding="utf-8",
        )
        (origin_dir / "assets" / "app.js").write_text(PUBLISHED_BUNDLE, encoding="utf-8")
        (origin_dir / "images" / "kiosk" / "gender-f.png").write_bytes(png)
        (origin_dir / "images" / "kiosk" / "gender-m.png").write_bytes(png)

        handler = _handler_for(origin_dir)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        port = server.server_address[1]

        written = fetch_kiosk_app.fetch(f"http://127.0.0.1:{port}/", dest)
        saved = dest / "images" / "kiosk" / "gender-f.png"
        self.assertIn("/images/kiosk/gender-f.png", written)
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_bytes()[:8], png[:8])
        self.assertEqual(saved.read_bytes(), png)


def _handler_for(directory: Path) -> type[SimpleHTTPRequestHandler]:
    root = str(directory)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=root, **kwargs)

        def log_message(self, fmt: str, *args) -> None:
            return

    return Handler


class MissingImageTests(unittest.TestCase):
    def test_referenced_images_that_404_fail_the_install(self) -> None:
        origin_dir = Path(tempfile.mkdtemp(prefix="kiosk-origin-"))
        dest = Path(tempfile.mkdtemp(prefix="kiosk-dest-"))
        self.addCleanup(shutil.rmtree, origin_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, dest, ignore_errors=True)
        (origin_dir / "assets").mkdir()
        (origin_dir / "index.html").write_text(
            '<!doctype html><script src="/assets/app.js"></script>',
            encoding="utf-8",
        )
        (origin_dir / "assets" / "app.js").write_text(PUBLISHED_BUNDLE, encoding="utf-8")

        handler = _handler_for(origin_dir)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        port = server.server_address[1]

        with self.assertRaises(RuntimeError):
            fetch_kiosk_app.fetch(f"http://127.0.0.1:{port}/", dest)
        self.assertFalse((dest / "images").exists())


if __name__ == "__main__":
    unittest.main()
