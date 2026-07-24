from app.main import _safe_frontend_file


def test_spa_file_lookup_stays_inside_frontend_root(tmp_path):
    root = tmp_path / "frontend"
    root.mkdir()
    asset = root / "asset.js"
    asset.write_text("safe", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")

    assert _safe_frontend_file(root, "asset.js") == asset.resolve()
    assert _safe_frontend_file(root, "../private.txt") is None
    assert _safe_frontend_file(root, str(outside.resolve())) is None
