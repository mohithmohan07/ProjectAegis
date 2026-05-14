def test_concepts_seeded(client):
    r = client.get("/concepts")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 5
    codes = {c["chapter_code"] for c in items}
    assert "09ICMA_CH01" in codes


def test_filter_pre_learning(client):
    native = client.get("/concepts", params={"pre_learning": False}).json()
    pl = client.get("/concepts", params={"pre_learning": True}).json()
    assert all(c["is_pre_learning"] == 0 for c in native)
    assert all(c["is_pre_learning"] == 1 for c in pl)
    assert len(pl) >= 1


def test_filter_by_chapter(client):
    items = client.get("/concepts", params={"chapter_code": "09ICPH_CH03"}).json()
    assert items
    assert all(c["chapter_code"] == "09ICPH_CH03" for c in items)


def test_chapters_endpoint(client):
    chapters = client.get("/concepts/chapters").json()
    assert any(c["chapter_code"] == "09ICMA_CH01" for c in chapters)
