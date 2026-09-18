"""Offline tests: publishing must not need or inspect any live credentials."""
import base64
import importlib.util
from pathlib import Path
import tempfile
import unittest

import fitz

SCRIPT = Path(__file__).parents[1] / "scripts" / "publish_source_assets.py"
spec = importlib.util.spec_from_file_location("manual_asset_publisher", SCRIPT)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def bundle():
    document = fitz.open()
    page = document.new_page(width=24, height=24)
    page.draw_rect(fitz.Rect(4, 4, 20, 20), color=(0, 0, 0))
    raw = page.get_pixmap().tobytes("jpeg")
    sha = publisher.digest(raw)
    return {"schema_version": 1, "assets": [{
        "sha256": sha, "filename": sha + ".jpg", "bytes": len(raw),
        "media_type": "image/jpeg", "provenance": {"test": True},
        "data_base64": base64.b64encode(raw).decode("ascii"),
    }]}, raw


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aegis-asset-test-")
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_idempotent_without_overwriting_provenance(self):
        package, raw = bundle()
        first = publisher.install_package(package, self.root)
        target = self.root / package["assets"][0]["filename"]
        original_manifest = target.with_suffix(".json").read_bytes()
        second = publisher.install_package(package, self.root)
        self.assertEqual(first["assets"][0]["status"], "created")
        self.assertEqual(second["assets"][0]["status"], "reused")
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(target.with_suffix(".json").read_bytes(), original_manifest)
        self.assertIn("/source-assets/0/", first["assets"][0]["url"])

    def test_corrupt_existing_asset_is_not_replaced(self):
        package, _ = bundle()
        target = self.root / package["assets"][0]["filename"]
        target.write_bytes(b"prior bytes")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            publisher.install_package(package, self.root)
        self.assertEqual(target.read_bytes(), b"prior bytes")

    def test_invalid_bundle_never_writes(self):
        for field, value in [("filename", "../escape.jpg"), ("sha256", "x" * 64),
                             ("bytes", 0), ("bytes", True), ("media_type", "image/png"),
                             ("provenance", {}), ("data_base64", "not base64")]:
            with self.subTest(field=field, value=value):
                package, _ = bundle()
                package["assets"][0][field] = value
                with self.assertRaises((ValueError, TypeError)):
                    publisher.install_package(package, self.root)
                self.assertFalse(list(self.root.iterdir()))

    def test_decoding_rejects_fake_jpeg(self):
        package, _ = bundle()
        raw = b"\xff\xd8\xffnot-a-picture\xff\xd9"
        sha = publisher.digest(raw)
        package["assets"][0].update(sha256=sha, filename=sha + ".jpg", bytes=len(raw),
                                    data_base64=base64.b64encode(raw).decode("ascii"))
        with self.assertRaises(Exception):
            publisher.install_package(package, self.root)
        self.assertFalse(list(self.root.iterdir()))

    def test_symlink_is_refused(self):
        package, _ = bundle()
        unrelated = self.root / "unrelated"
        unrelated.write_bytes(b"keep")
        (self.root / package["assets"][0]["filename"]).symlink_to(unrelated)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            publisher.install_package(package, self.root)
        self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_duplicate_identity_is_refused(self):
        package, _ = bundle()
        package["assets"].append(dict(package["assets"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            publisher.install_package(package, self.root)
        self.assertFalse(list(self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
