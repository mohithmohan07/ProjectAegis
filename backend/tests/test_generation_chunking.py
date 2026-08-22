"""MMD is chunked (never trimmed) so no content is lost on long chapters."""
import inspect

import pytest

from app.services import generation as g


def _pass_completed_phase3(records, **kwargs):
    kwargs["phase3_carry"].update({
        "pre_map": {"rows": [], "topics": []},
        "pre_questions": {
            "plans": {}, "questions": {}, "blocked": {},
        },
        "pre_snapshot_writes": {},
    })
    return records


def _big_doc(sections: int, body_words: int = 60) -> str:
    out = []
    for i in range(sections):
        out.append(f"# Section {i:02d} Heading\n")
        out.append(("alpha beta gamma delta " * body_words) + "\n\n")
    return "".join(out)


def test_split_preserves_all_content():
    doc = _big_doc(30)
    chunks = g._split_mmd_into_chunks(doc, max_chars=8000)
    assert len(chunks) > 1
    # Concatenation preserves every non-whitespace character (nothing trimmed).
    joined = "".join(chunks)
    assert joined.replace("\n", "").replace(" ", "") == \
        doc.replace("\n", "").replace(" ", "")


def test_split_small_doc_single_chunk():
    assert g._split_mmd_into_chunks("# Only\n\nshort body", max_chars=9000) == \
        ["# Only\n\nshort body"]
    assert g._split_mmd_into_chunks("   ", max_chars=10) == []


def test_dry_concepts_include_parent_and_never_synthesize_culminations():
    records = g.concepts_from_mmd(
        "## Ratios\nEquivalent ratios\nRatio word problems",
        subject="Mathematics",
        board="CBSE",
        grade="07",
        unit="Numbers",
        chapter_title="Ratios",
    )
    assert records
    assert all("parent_concept" in r for r in records)
    # A culmination is model-authored content; the dry path never invents
    # one from code.
    assert not any(
        r["concept_title"].startswith("Culmination -") for r in records
    )


def test_skeleton_pass_strips_types_and_culminations(monkeypatch):
    def fake_openai(system, user, **kw):
        return {"rows": [
            {"topic": "T", "parent_concept": "P", "concept": "C",
             "concept_description": "Description: d // Types: Type 01: X Case 01: y",
             "keywords": "k"},
            {"topic": "T", "parent_concept": "P", "concept": "Culmination - T",
             "concept_description": "Description: Recap", "keywords": "k"},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = g._extract_skeleton_via_api(
        [{"text": "HEADING PATH: T\nSECTION TEXT:\nBody", "sections": []}],
        meta=g._metadata(subject="Math"),
    )
    assert [r["concept_title"] for r in records] == ["C"]
    assert "Types:" not in records[0]["concept_details"]


def _rows(n: int, topic: str = "T") -> list[dict]:
    return [
        {"topic": topic, "parent_concept": "P", "concept_title": f"Concept {i:02d}",
         "concept_details": f"Description: about concept {i}", "keywords": ""}
        for i in range(1, n + 1)
    ]


def _rows_with_families(
    n: int, family_count: int, topic: str = "T",
) -> list[dict]:
    return [
        {
            "topic": topic,
            "parent_concept": f"Source Family {((i - 1) % family_count) + 1:02d}",
            "concept_title": f"Concept {i:02d}",
            "concept_details": f"Description: about concept {i}",
            "keywords": "",
        }
        for i in range(1, n + 1)
    ]


def _to_api_rows(records: list[dict]) -> list[dict]:
    return [
        {"topic": r["topic"], "parent_concept": r["parent_concept"],
         "concept": r["concept_title"], "concept_description": r["concept_details"],
         "keywords": r["keywords"]}
        for r in records
    ]


_FAMILIES = ("Rational Numbers", "Integers", "Decimal Representation")


def _family_skeleton() -> list[dict]:
    """A headingless-chapter skeleton whose branches live in ``parent_concept``.

    Deliberately digit-free: the "no target count" regressions below assert
    that the correction prompt contains no digits at all, so a family label
    must not smuggle one in.
    """
    return [
        {
            "topic": "Number Systems",
            "parent_concept": family,
            "concept_title": f"{family} idea {'one' if i == 0 else 'two'}",
            "concept_details": f"Description: about {family}.",
            "keywords": "",
        }
        for family in _FAMILIES
        for i in (0, 1)
    ]


def _family_row(family: str, title: str) -> dict:
    return {
        "topic": "Number Systems",
        "parent_concept": family,
        "concept_title": title,
        "concept_details": f"Description: canonical {family}.",
        "keywords": "",
    }


def test_missing_skeleton_families_accounts_identities_never_counts():
    """"Family X is gone" is accounting; "fewer families" is a count."""
    records = _family_skeleton()

    # Every family still named as a parent: nothing is missing, even though
    # the map is far smaller than the skeleton.
    kept_as_parents = [_family_row(f, f"{f} core") for f in _FAMILIES]
    assert g._missing_skeleton_families(records, kept_as_parents) == []

    # A family merged UP into a concept of its own name is still present --
    # the identity survived, only the row shape changed.
    merged_up = [
        {
            "topic": "Number Systems",
            "parent_concept": "Number Systems",
            "concept_title": family,
            "concept_details": "Description: merged.",
            "keywords": "",
        }
        for family in _FAMILIES
    ]
    assert g._missing_skeleton_families(records, merged_up) == []

    # One identity actually absent is named -- and named, not counted.
    dropped = [_family_row(f, f"{f} core") for f in _FAMILIES if f != "Integers"]
    assert g._missing_skeleton_families(records, dropped) == ["Integers"]

    # A single row that keeps every family identity is accepted. There is no
    # floor left for a small map to fall under.
    assert g._missing_skeleton_families(records, kept_as_parents[:1]) == [
        "Integers", "Decimal Representation",
    ]


def test_canonicalize_reasks_by_naming_the_lost_family_not_a_row_count(
    monkeypatch,
):
    """The bounded correction is evidence: it names the family, never a count."""
    records = _family_skeleton()
    seen: list[str] = []

    def fake_openai(system, user, **kw):
        seen.append(user)
        if len(seen) == 1:
            return {"rows": _to_api_rows(
                [_family_row(f, f"{f} core") for f in _FAMILIES
                 if f != "Integers"])}
        return {"rows": _to_api_rows(
            [_family_row(f, f"{f} core") for f in _FAMILIES])}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)

    out = g._consolidate_concepts_via_api(records, subject="Math")

    assert len(seen) == 2
    correction = seen[1][len(seen[0]):]
    # The missing family is named in the correction ...
    assert "Integers" in correction
    assert "LOST THESE SOURCE-BACKED PARENT FAMILIES" in correction
    # ... and no number is asked for. Not "roughly N-M rows", not any digit.
    assert "Return roughly" not in correction
    assert not any(character.isdigit() for character in correction), correction
    assert "There is no target number of rows" in correction
    # The correction was accepted on identity: every family is back.
    assert g._missing_skeleton_families(records, out) == []
    assert len(out) == 3


def test_canonicalize_records_a_flagged_decision_when_a_family_stays_lost(
    monkeypatch,
):
    """Q13: one recorded, flagged decision, nothing lost, the run completes.

    The old behaviour threw the whole canonicalization away and reverted to
    the raw skeleton because the row count came in under ``topics x 2``. Now
    only the lost family's own rows come back, flagged by name, and everything
    the model actually decided stands.
    """
    records = _family_skeleton()
    kept = [_family_row(f, f"{f} core") for f in _FAMILIES if f != "Integers"]
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        return {"rows": _to_api_rows(kept)}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)

    out = g._consolidate_concepts_via_api(records, subject="Math")

    assert calls["n"] == 2  # first pass + one bounded, family-named re-ask
    titles = [row["concept_title"] for row in out]
    # The model's canonicalization of the surviving families stands: two rows,
    # not the four skeleton rows they were merged from.
    assert "Rational Numbers core" in titles
    assert "Decimal Representation core" in titles
    assert "Rational Numbers idea one" not in titles
    # The lost family is carried back from the skeleton, not invented ...
    restored = [row for row in out if row["parent_concept"] == "Integers"]
    assert [row["concept_title"] for row in restored] == [
        "Integers idea one", "Integers idea two",
    ]
    # ... and every restored row is flagged for review, naming the family.
    for row in restored:
        flags = row.get("review_flags") or []
        assert any("Integers" in flag for flag in flags), flags
    # Never a revert to the raw skeleton.
    assert len(out) == len(kept) + len(restored)
    assert len(out) != len(records)
    assert g._missing_skeleton_families(records, out) == []


def test_a_q13_flag_survives_the_repair_pass_that_runs_after_it(monkeypatch):
    """The Fixer is never silent — repair rewrites the row, not the decision.

    ``_restore_lost_skeleton_families`` is the only ``_fixer_flag_row`` caller
    upstream of ``_repair_records_via_api``, and the rows it carries back are
    raw draft-skeleton rows that never passed the canonicalize-stage
    validator, so tripping a repair is the expected case rather than the edge
    one. The repair pass replaces a failing row wholesale with the repair
    response; it must carry ``review_flags`` across or the reviewer is told
    nothing (docs/aegis-restructure.md §8.2).
    """
    records = [
        _family_row("Rational Numbers", "RN idea"),
        # No "Description:" prefix: the canonicalize-stage validator will
        # fail this row once it is carried back, and repair will rewrite it.
        {
            "topic": "Number Systems",
            "parent_concept": "Integers",
            "concept_title": "Walking the number line",
            "concept_details": "no house prefix here",
            "keywords": "",
        },
    ]
    kept = [_family_row("Rational Numbers", "Rational Numbers core")]

    def fake_openai(system, user, **kw):
        if "Validation errors" in user:
            return {"rows": _to_api_rows([{
                "topic": "Number Systems",
                "parent_concept": "Integers",
                "concept_title": "Walking the number line",
                "concept_details": "Description: adding on the number line.",
                "keywords": "",
            }])}
        return {"rows": _to_api_rows(kept)}

    monkeypatch.setattr(g, "_openai_json", fake_openai)

    out = g._consolidate_concepts_via_api(records, subject="Math")

    restored = [r for r in out if r["concept_title"] == "Walking the number line"]
    assert len(restored) == 1
    # The repair did run — the row was rewritten ...
    assert restored[0]["concept_details"].startswith("Description:")
    # ... and the recorded decision survived it.
    assert any(
        "Integers" in flag for flag in (restored[0].get("review_flags") or [])
    ), restored[0].get("review_flags")


def test_a_q13_flag_survives_the_title_dedupe_that_runs_after_it(monkeypatch):
    """A carried-back row that restates a kept concept still tells the reviewer.

    Audit F4 re-authored this contract: a carried-back Q13 row that shares a
    topic+title with a kept row but carries DIFFERENT content is a second
    claim on one identity — a mechanical pass must not adjudicate which
    teaching survives, so BOTH rows ship, both flagged for review (the
    topic-scoped duplicate validator surfaces them). Only a byte-equivalent
    restatement may be dropped. The Q13 decision therefore no longer has to
    hop rows: it ships on the row it was made on.
    """
    records = [
        _family_row("Rational Numbers", "Number line"),
        _family_row("Integers", "Number line"),
        _family_row("Integers", "Negative numbers"),
    ]
    # Every title survives; the "Integers" branch does not.
    reparented = [
        _family_row("Rational Numbers", "Number line"),
        _family_row("Rational Numbers", "Negative numbers"),
    ]

    monkeypatch.setattr(
        g, "_openai_json", lambda s, u, **kw: {"rows": _to_api_rows(reparented)})
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kw: records)

    out = g._consolidate_concepts_via_api(records, subject="Math")

    # Both claimants of each shared identity ship, flagged — nothing is
    # silently adjudicated away by a mechanical pass (R4).
    from collections import Counter

    title_counts = Counter(row["concept_title"] for row in out)
    duplicated_titles = {t for t, n in title_counts.items() if n > 1}
    assert duplicated_titles, "the carried-back claimants must survive"
    for row in out:
        if row["concept_title"] in duplicated_titles:
            assert any(
                "duplicate_identity_distinct_content" in flag
                for flag in row.get("review_flags") or []
            ), row
    # ... and the lost family is named to the reviewer on a shipped row.
    assert any(
        "Integers" in flag
        for row in out
        for flag in (row.get("review_flags") or [])
    ), out


def test_every_lost_family_ships_placed_or_flagged(monkeypatch):
    """R4 over the returned map, not over an intermediate.

    ``_missing_skeleton_families`` on the shipped rows may still name a family
    — that is honest, the model genuinely re-parented its concepts — but no
    family may be both absent and unmentioned.
    """
    records = [
        _family_row("Rational Numbers", "Number line"),
        _family_row("Integers", "Number line"),
        _family_row("Decimal Representation", "Place value"),
    ]
    reparented = [
        _family_row("Rational Numbers", "Number line"),
        _family_row("Rational Numbers", "Place value"),
    ]

    monkeypatch.setattr(
        g, "_openai_json", lambda s, u, **kw: {"rows": _to_api_rows(reparented)})
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kw: records)

    out = g._consolidate_concepts_via_api(records, subject="Math")

    flags = [
        flag for row in out for flag in (row.get("review_flags") or [])
    ]
    for family in ("Integers", "Decimal Representation"):
        placed = g._topic_comparison_key(family) in g._concept_identity_keys(out)
        flagged = any(family in flag for flag in flags)
        assert placed or flagged, f"{family} shipped neither placed nor flagged"


def test_account_shipped_families_flags_a_family_nothing_else_recorded():
    """The ledger records rather than halts, and invents nothing (Q13)."""
    records = [
        _family_row("Integers", "Negative numbers"),
        _family_row("Rational Numbers", "Number line"),
    ]
    shipped = [_family_row("Rational Numbers", "Number line")]

    out = g._account_shipped_families(records, shipped, ["Integers"])

    # No row invented, no row dropped ...
    assert len(out) == 1
    assert out[0]["concept_title"] == "Number line"
    # ... one recorded decision the reviewer will see, naming the family.
    assert any("Integers" in flag for flag in out[0]["review_flags"])

    # A family that IS present is left alone.
    present = [_family_row("Integers", "Negative numbers")]
    assert g._account_shipped_families(
        records, present, ["Integers"]) == present
    assert not present[0].get("review_flags")


def test_the_must_preserve_block_asks_for_exactly_what_is_checked(monkeypatch):
    """The ledger may only hold the model to what the prompt asked of it.

    ``_missing_skeleton_families`` scores survival by the family's own name
    appearing as a ``parent_concept`` or a ``concept_title``. If the prompt
    only said "keep a coherent concept for each", a model that complied
    exactly — one well-named concept per branch, renamed in teaching words —
    would be scored as having lost every family, re-asked, and then have its
    thin chapter re-inflated with the raw fragments it had just merged. So the
    instruction names the check.
    """
    records = _family_skeleton()
    seen: list[str] = []

    def fake_openai(system, user, **kw):
        seen.append(user)
        return {"rows": _to_api_rows(
            [_family_row(f, f"{f} core") for f in _FAMILIES])}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kw: records)

    g._consolidate_concepts_via_api(records, subject="Math")

    assert len(seen) == 1
    block = seen[0]
    assert "MUST-PRESERVE SOURCE-BACKED PARENT FAMILIES" in block
    assert "NAMEABLE" in block
    assert "parent_concept of a row" in block
    # The payload is evidence, not a target: the model is told what the rows
    # are, never how many of them there are or should be.
    assert "Draft skeleton map:" in block


def test_a_family_teaching_under_two_topics_is_named_once():
    """One branch is one identity to preserve, so it is listed once.

    Keying the label list on (topic, parent) printed the same family twice in
    the MUST-PRESERVE block while the accounting deduplicated it, so the model
    was handed a list that said less than it appeared to.
    """
    records = [
        {
            "topic": topic,
            "parent_concept": "Forces",
            "concept_title": f"{topic} forces",
            "concept_details": "Description: forces.",
            "keywords": "",
        }
        for topic in ("Motion", "Gravitation")
    ]

    assert g._skeleton_family_labels(records) == ["Forces"]


def test_canonicalize_accepts_an_over_merge_that_loses_no_family(monkeypatch):
    """An aggressive merge that keeps every identity ships as decided."""
    records = _family_skeleton()
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        return {"rows": _to_api_rows(
            [_family_row(family, family) for family in _FAMILIES])}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)

    out = g._consolidate_concepts_via_api(records, subject="Math")

    assert calls["n"] == 1  # six rows to three, and nothing objects
    assert len(out) == 3


def test_a_thin_low_grade_chapter_is_never_padded_to_a_row_count(monkeypatch):
    """The exact case CLAUDE.md names as a past mis-read.

    ``topic_count * MIN_PER_TOPIC`` pressured thin lower-grade chapters into
    inventing concepts to satisfy arithmetic. Five thin grade-3 headings put
    the old floor at five rows (``max(4, 5 x 2, 0)`` clamped to the skeleton
    size), so a two-row canonicalization was retried and then discarded back
    to five. Now it ships.
    """
    headings = [
        "Counting to Hundred", "Tens and Ones", "Adding Up",
        "Taking Away", "Shapes Around Us",
    ]
    records = [
        {
            "topic": heading,
            "parent_concept": heading,
            "concept_title": f"{heading} — the idea",
            "concept_details": f"Description: {heading}, in one short page.",
            "keywords": "",
        }
        for heading in headings
    ]
    # The model reads the chapter as teaching two durable ideas.
    canonical = [
        {
            "topic": "Counting to Hundred",
            "parent_concept": "Counting to Hundred",
            "concept_title": "Reading and writing numbers to hundred",
            "concept_details": "Description: place value to hundred.",
            "keywords": "",
        },
        {
            "topic": "Adding Up",
            "parent_concept": "Adding Up",
            "concept_title": "Adding and taking away within hundred",
            "concept_details": "Description: addition and subtraction.",
            "keywords": "",
        },
    ]
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        return {"rows": _to_api_rows(canonical)}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)

    out = g._consolidate_concepts_via_api(records, subject="Mathematics")

    assert calls["n"] == 1, "a floor retried a thin chapter for more rows"
    assert len(out) == 2, "a thin chapter was padded back up"
    assert [row["concept_title"] for row in out] == [
        row["concept_title"] for row in canonical
    ]


def test_canonicalization_keeps_no_row_count_bound_anywhere():
    """No numeric bound on row counts in the bounds, the prompt, or the gate."""
    assert not hasattr(g, "_canonicalize_target_bounds")
    for name in (
        "_CANONICALIZE_MIN_CHAPTER_ROWS",
        "_CANONICALIZE_MIN_PER_TOPIC",
        "_CANONICALIZE_MAX_PER_TOPIC",
        "_CANONICALIZE_MAX_CHAPTER_ROWS",
    ):
        assert not hasattr(g, name), f"{name} survived the purge"

    # Comments narrate what was purged; the executable body must not.
    source = "\n".join(
        line for line in
        inspect.getsource(g._consolidate_concepts_via_api).splitlines()
        if not line.lstrip().startswith("#")
    )
    for token in ("min_keep", "max_keep", "Return roughly", "over-merging"):
        assert token not in source, f"{token} survived in canonicalization"

    # The accounting itself never measures size: no len(), no comparison.
    for function in (g._missing_skeleton_families, g._concept_identity_keys):
        body = inspect.getsource(function)
        assert "len(" not in body, function.__name__


def test_canonicalize_accepts_reasonable_compaction(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        return {"rows": _to_api_rows(_rows(10))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    out = g._consolidate_concepts_via_api(_rows(20), subject="Math")
    assert calls["n"] == 1  # no retry needed
    assert len(out) == 10


def test_canonicalize_names_every_source_backed_family_to_the_model():
    records = _rows_with_families(18, family_count=9)

    labels = g._skeleton_family_labels(records)

    assert labels == [f"Source Family {i:02d}" for i in range(1, 10)]
    # Every one of them is accounted for afterwards, by identity.
    assert g._missing_skeleton_families(records, []) == labels


def test_canonicalize_carries_back_only_the_families_that_stayed_lost(
    monkeypatch,
):
    """Eight of nine families survive; the ninth is restored and flagged."""
    calls = {"n": 0}
    records = _rows_with_families(18, family_count=9)
    survivors = [
        {
            "topic": "T",
            "parent_concept": f"Source Family {i:02d}",
            "concept_title": f"Family {i:02d} canonical",
            "concept_details": "Description: canonical.",
            "keywords": "",
        }
        for i in range(1, 9)
    ]

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        assert "MUST-PRESERVE SOURCE-BACKED PARENT FAMILIES" in user
        if calls["n"] == 2:
            assert "Source Family 09" in user
        return {"rows": _to_api_rows(survivors)}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kw: records)

    out = g._consolidate_concepts_via_api(records, subject="Science")

    assert calls["n"] == 2
    # Eight canonical rows stand; only family 09's own skeleton rows return.
    assert len(out) == len(survivors) + 2
    restored = [r for r in out if r["parent_concept"] == "Source Family 09"]
    assert [r["concept_title"] for r in restored] == ["Concept 09", "Concept 18"]
    for row in restored:
        assert any(
            "Source Family 09" in flag
            for flag in (row.get("review_flags") or [])
        )
    assert g._missing_skeleton_families(records, out) == []


def test_canonicalize_accepts_a_granular_map_without_compacting(monkeypatch):
    """A high row count is no longer retried down to a target.

    This asserted the opposite until a live run showed the cost: a 44-row
    skeleton whose canonicalization kept 43 rows was compacted to 30 purely to
    satisfy topics x _CANONICALIZE_MAX_PER_TOPIC. The system prompt already
    asks for a compact teacher-facing map and carries no numeric target, so the
    retry merged on arithmetic rather than on meaning -- and a distinction
    collapsed here cannot be recovered by any later stage.
    """

    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        assert "TOO GRANULAR" not in user
        return {"rows": _to_api_rows(_rows(80))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    out = g._consolidate_concepts_via_api(_rows(80), subject="Math")
    assert calls["n"] == 1
    assert len(out) == 80


def _audit_aware_openai(script):
    """Stub ``_openai_json`` splitting audit calls from extraction calls.

    ``script`` maps call kinds to a list of responses consumed in order:
    ``{"extract": [...], "audit": [...]}`` — an Exception instance in the
    audit list is raised.
    """
    calls = {"extract": [], "audit": []}

    def fake_openai(system, user, **kw):
        if "audit a concept-skeleton extraction" in system:
            calls["audit"].append(user)
            step = script["audit"].pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        calls["extract"].append(user)
        return script["extract"].pop(0)

    return fake_openai, calls


def test_sound_audit_verdict_accepts_the_extraction_without_retry(monkeypatch):
    """(a) A complete/sound audit: exactly one extraction call plus one audit
    call — the model's extraction is the answer; no count-driven retry."""
    fake_openai, calls = _audit_aware_openai({
        "extract": [{"rows": _to_api_rows(_rows(2))}],
        "audit": [{"coverage": "complete", "grain": "sound", "reason": ""}],
    })
    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 2000)
    records = g._extract_skeleton_via_api(
        [{"text": chunk_text, "sections": []}], meta=g._metadata(subject="Math"))
    assert len(calls["extract"]) == 1
    assert len(calls["audit"]) == 1
    # However small the extraction, the sound verdict stands (no char floor).
    assert len(records) == 2


def test_under_extracted_audit_verdict_retries_with_named_objectives(
    monkeypatch,
):
    """(b) An under-extracted verdict triggers ONE retry whose user text names
    the missed objectives, and the retry is adopted."""
    fake_openai, calls = _audit_aware_openai({
        "extract": [
            {"rows": _to_api_rows(_rows(1))},
            {"rows": _to_api_rows(_rows(8))},
        ],
        "audit": [{
            "coverage": "under_extracted",
            "missing_objectives": [
                "how gradation reshapes landforms",
                "the chapter-opening framing of slow change",
            ],
            "grain": "sound",
            "reason": "whole objectives are missing",
        }],
    })
    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 2000)
    records = g._extract_skeleton_via_api(
        [{"text": chunk_text, "sections": []}], meta=g._metadata(subject="Math"))
    assert len(calls["extract"]) == 2
    assert len(calls["audit"]) == 1
    retry_user = calls["extract"][1]
    assert "how gradation reshapes landforms" in retry_user
    assert "chapter-opening framing of slow change" in retry_user
    assert "COVER every main teaching objective" in retry_user
    assert len(records) == 8


def test_micro_split_audit_verdict_retries_without_a_numeric_target(
    monkeypatch,
):
    """(c) A micro-split verdict triggers ONE consolidation retry that is
    adopted — and the retry prompt names NO numeric concept target."""
    fake_openai, calls = _audit_aware_openai({
        "extract": [
            {"rows": _to_api_rows(_rows_with_families(27, family_count=9))},
            {"rows": _to_api_rows(_rows(9))},
        ],
        "audit": [{
            "coverage": "complete",
            "grain": "micro_split",
            "reason": "terms and cases stand alone as rows",
        }],
    })
    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 1500)
    records = g._extract_skeleton_via_api(
        [{"text": chunk_text, "sections": []}], meta=g._metadata(subject="Math"))
    assert len(calls["extract"]) == 2
    assert len(calls["audit"]) == 1
    retry_user = calls["extract"][1]
    assert "micro-concepts" in retry_user
    assert "no target count" in retry_user
    # The must-preserve families are named, but never a number of concepts.
    assert "MUST-PRESERVE SOURCE-BACKED PARENT FAMILIES" in retry_user
    import re as _re
    assert not _re.search(
        r"(?i)\breturn\s+(?:between\s+)?\d+", retry_user)
    assert len(records) == 9


def test_unavailable_audit_keeps_the_extraction_unchanged(monkeypatch):
    """(d) An audit exception is advisory-only: the extraction is kept exactly
    as returned and no retry is spent."""
    fake_openai, calls = _audit_aware_openai({
        "extract": [{"rows": _to_api_rows(_rows(3))}],
        "audit": [RuntimeError("audit transport failure")],
    })
    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 1500)
    records = g._extract_skeleton_via_api(
        [{"text": chunk_text, "sections": []}], meta=g._metadata(subject="Math"))
    assert len(calls["extract"]) == 1
    assert len(calls["audit"]) == 1
    assert len(records) == 3


def test_culmination_pass_cannot_drop_normal_rows(monkeypatch):
    """The culmination model authors ONLY culmination rows; normal rows are
    merged back programmatically, so a bad response can't lose chapter content."""

    def fake_openai(system, user, **kw):
        # Model misbehaves: returns one culmination for topic A only, plus a
        # rewritten fragment of the map (which must be ignored).
        return {"rows": [
            {"topic": "Topic A", "parent_concept": "Culmination",
             "concept": "Culmination - Topic A Ideas",
             "concept_description": "Description: Recap", "keywords": ""},
            {"topic": "Topic A", "parent_concept": "P", "concept": "Concept 01",
             "concept_description": "Description: rewritten fragment", "keywords": ""},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    records = _rows(4, topic="Topic A") + _rows(3, topic="Topic B")
    out = g._build_culminations_via_api(records, meta=g._metadata(subject="Math"))
    normal = [r for r in out if not r["concept_title"].startswith("Culmination -")]
    culms = [r for r in out if r["concept_title"].startswith("Culmination -")]
    assert len(normal) == 7  # every normal row survives
    assert {r["concept_details"] for r in normal if r["topic"] == "Topic A"} == {
        f"Description: about concept {i}" for i in range(1, 5)
    }  # the rewritten fragment was ignored
    # Only the authored culmination ships; Topic B (which the model left
    # without one) ships without a culmination — nothing is synthesized —
    # and the validator report flags it for review.
    assert len(culms) == 1
    assert culms[0]["topic"] == "Topic A"
    # The authored title survives; it is never rebuilt from code.
    assert culms[0]["concept_title"] == "Culmination - Topic A Ideas"


def test_inventory_extraction_retries_underextracted_chunks(monkeypatch):
    """The completeness reviewer, not a count formula, triggers the retry."""
    calls = {"extract": 0, "review": 0}

    def fake_openai(system, user, **kw):
        if "inventory-completeness reviewer" in system:
            calls["review"] += 1
            if calls["review"] == 1:
                return {
                    "verdict": "under_extracted",
                    "reason": "the numbered exercise list was summarized",
                    "missed": ["numbered exercise items"],
                }
            return {"verdict": "complete", "reason": ""}
        calls["extract"] += 1
        if calls["extract"] == 1:
            return {"items": [
                {
                    "raw_task": (
                        f"Solve equation problem number {i} with every "
                        "supplied condition."
                    ),
                }
                for i in range(1, 3)
            ]}
        assert "under-extraction" in user
        assert "numbered exercise items" in user
        return {"items": [
            {
                "raw_task": (
                    f"Solve equation problem number {i} with every supplied "
                    "condition."
                ),
            }
            for i in range(1, 13)
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    # This test isolates the retry behavior; deterministic source anchors
    # have their own coverage regressions.
    monkeypatch.setattr(g, "_source_task_anchors", lambda sections: [])
    body = "1. Solve for x in the equation shown below. " * 400  # ~18k chars
    sections = g.parse_mmd_sections("# Exercises\n\n" + body)
    inventory = g._extract_question_task_inventory_via_api(
        meta=g._metadata(subject="Math"), sections=sections)
    assert calls["extract"] == 2
    assert calls["review"] == 2
    assert len(inventory["items"]) == 12
    assert inventory["items"][0]["qid"] == "QINV-0001"


def test_latex_headings_are_normalized_to_markdown():
    """Real Mathpix PDF output uses \\section*{...}, not Markdown '#'."""
    mmd = (
        "\\section*{NUMBER SYSTEMS}\n\nintro text\n\n"
        "\\section*{1．1 Introduction}\n\nbody one\n\n"
        "\\subsection*{1.2 Irrational Numbers}\n\nbody two\n\n"
        "\\section*{EXERCISE 1.1}\n\n1. Is zero rational?\n"
    )
    out = g.normalize_mmd_headings(mmd)
    assert "## NUMBER SYSTEMS" in out
    assert "## 1．1 Introduction" in out
    assert "### 1.2 Irrational Numbers" in out
    assert "\\section" not in out
    # Idempotent for already-Markdown documents.
    md = "# Title\n\n## Section\n\nbody\n"
    assert g.normalize_mmd_headings(md) == md


def test_parse_mmd_sections_handles_latex_headings():
    mmd = (
        "\\section*{NUMBER SYSTEMS}\n\n"
        "\\section*{1．1 Introduction}\n\nbody one\n\n"
        "\\subsection*{1.2 Irrational Numbers}\n\nbody two\n"
    )
    sections = g.parse_mmd_sections(mmd)
    headings = [s["heading"] for s in sections]
    # Fullwidth "1．1" section number is stripped like a normal one.
    assert "Introduction" in headings
    assert "Irrational Numbers" in headings
    assert len(sections) >= 3


def test_section_aware_chunks_split_headingless_documents():
    """A document with no (parseable) headings must still be chunked, not sent
    as one giant chunk that causes chapter-scale under-extraction."""
    body = ("A paragraph about numbers and operations. " * 40 + "\n\n") * 60
    chunks = g._section_aware_chunks(body, max_chars=20_000)
    assert len(chunks) > 1
    assert all(len(c["text"]) <= 26_000 for c in chunks)
    # No content is lost across the split.
    joined = "".join(s["body"] for c in chunks for s in c["sections"])
    assert joined.replace("\n", "").replace(" ", "") == \
        body.replace("\n", "").replace(" ", "")


def test_description_prefix_is_normalized_deterministically():
    f = g._normalize_description_prefix
    assert f("Description: fine as is") == "Description: fine as is"
    assert f("description: lowercase") == "Description: lowercase"
    assert f("Description： fullwidth colon") == "Description: fullwidth colon"
    assert f("  Description :  spaced colon") == "Description: spaced colon"
    assert f("Plain text with no prefix") == "Description: Plain text with no prefix"
    assert f("") == ""
    rows = g._concept_rows_to_records({"rows": [{
        "topic": "T", "concept": "C", "concept_description": "no prefix here",
    }]})
    assert rows[0]["concept_details"] == "Description: no prefix here"


def test_duplicate_concepts_are_merged_by_topic_and_title():
    records = [
        {"topic": "T", "parent_concept": "P", "concept_title": "Same",
         "concept_details": "Description: first", "keywords": ""},
        {"topic": "T", "parent_concept": "P", "concept_title": "Same",
         "concept_details": "Description: second", "keywords": ""},
    ]
    assert len(g._merge_concept_records(records)) == 1


def test_concepts_live_processes_every_chunk(monkeypatch):
    """Live concept extraction must call GPT for each chunk and merge results."""
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(g, "_MMD_CHUNK_CHARS", 4000)

    calls = {"n": 0, "skeleton": 0}

    def fake_openai_json(system, user, **kw):
        if "inventory-completeness reviewer" in system:
            return {"verdict": "complete", "reason": ""}
        if "audit a concept-skeleton extraction" in system:
            return {"coverage": "complete", "grain": "sound", "reason": ""}
        if "mined assessment-Type taxonomy" in system:
            return {"verdict": "healthy", "rationale": "reusable methods"}
        if "another auditor's verdict" in system:
            return {"verdict": "confirmed", "rationale": ""}
        calls["n"] += 1
        if "clean teachable concept skeleton" in system:
            calls["skeleton"] += 1
        n = calls["skeleton"] or calls["n"]
        # Each chunk yields two unique concepts.
        return {"rows": [
            {"topic": "Topic A", "concept": f"Concept {n}a",
             "concept_description": "Description: x // Types: // Misconception:",
             "keywords": "k"},
            {"topic": "Topic A", "concept": f"Concept {n}b",
             "concept_description": "Description: y // Types: // Misconception:",
             "keywords": "k"},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai_json)
    monkeypatch.setattr(
        g, "_consolidate_concepts_via_api",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_topic_segregation_verdict_via_api",
        lambda records, **kw: {"restructure": False, "reason": ""})

    # Everything after the 81% boundary runs through the rewritten Phase 3;
    # this test is about chunked extraction, so pass its output through.
    monkeypatch.setattr(
        g, "_prepare_final_concept_content", _pass_completed_phase3)
    monkeypatch.setattr(
        g, "_build_culminations_via_api",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_repair_records_via_api",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_validate_final_or_raise",
        lambda records, **kw: {"ok": True, "errors": [], "summary": {}})
    # This test's fake model deliberately emits one invented topic ("Topic A")
    # for every chunk. Source-topic coverage is validated in dedicated tests.
    monkeypatch.setattr(
        g, "_recover_missing_topic_concepts_via_api",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_missing_source_topic_excerpts",
        lambda records, source_topic_excerpts: [])
    doc = _big_doc(20)  # forces several chunks at 4000 chars
    records = g.concepts_from_mmd(doc, subject="Mathematics")
    assert calls["skeleton"] >= 3, "expected multiple chunks to be processed"
    # Every chunk's concepts survive the merge (2 per chunk, all unique).
    titles = {r["concept_title"] for r in records}
    assert len(titles) >= calls["skeleton"] * 2


def test_identify_questions_live_merges_and_dedupes(monkeypatch):
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(g, "_MMD_CHUNK_CHARS", 4000)

    calls = {"n": 0}

    def fake_openai_json(system, user, **kw):
        calls["n"] += 1
        n = calls["n"]
        def row(question):
            return {
                "question": question,
                "sheet_kind": "objective",
                "question_category": "Multiple Choice Question",
                "cognitive_skills": "Understand",
                "level_of_difficulty": "Moderate",
                "marks": 1,
                "question_duration": 2,
                "math_keyboard": "",
            }

        return {"questions": [
            row(f"Unique question {n}?"),
            row("Shared duplicate question?"),
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai_json)
    doc = _big_doc(20)
    records = g._live_identify_questions_from_mmd(
        doc, upload_type="questions", question_type="auto")
    assert calls["n"] >= 3
    questions = [r["question"] for r in records]
    # The duplicate that appears in every chunk is kept exactly once.
    assert questions.count("Shared duplicate question?") == 1
    # Every chunk's unique question is present (no trimming/cap loss).
    assert sum(1 for q in questions if q.startswith("Unique question")) == calls["n"]


def test_identify_questions_safety_cap_fails_closed(monkeypatch):
    monkeypatch.setattr(g, "_IDENTIFY_SAFETY_CAP", 2)
    monkeypatch.setattr(g, "_split_mmd_into_chunks", lambda _text: ["section"])

    def row(question):
        return {
            "question": question,
            "sheet_kind": "objective",
            "question_category": "Multiple Choice Question",
            "cognitive_skills": "Understand",
            "level_of_difficulty": "Moderate",
            "marks": 1,
            "question_duration": 2,
            "math_keyboard": "",
        }

    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: {
            "questions": [row("Question one?"), row("Question two?")]
        },
    )
    with pytest.raises(RuntimeError, match="refusing a truncated success"):
        g._live_identify_questions_from_mmd(
            "source", upload_type="questions", question_type="auto")
