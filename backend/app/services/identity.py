"""One definition of the identities the pipeline keys entities on.

Created in slice S2 with **one** function. Four different topic identities are
live in this codebase (spec-step8 T4-6): the release file builder casefolds,
the publication normalises, ``build_concepts._find_or_create_topic``
strip-then-normalises per lane, and ``bulk_import.reader`` used the raw title
with no normalisation and no lane at all. They converge here rather than
acquiring a fifth copy.

S4 EXTENDS this module with the minted, persisted machine ids
(``machine_id_for_topic`` / ``machine_id_for_concept``), the shared cell
composers and ``source_order_key`` (moved out of ``bulk_import.writer`` so the
import direction stays ``bulk_import -> services.identity`` and never the
reverse). Nothing here may import ``bulk_import.writer`` —
``test_identity_module_imports_no_bulk_import_writer`` pins that in a fresh
subprocess.

EVERYTHING in this module is mechanics, never judgment (CLAUDE.md Rule 1): it
parses, orders, hashes and composes. Nothing here inspects a title to decide
what a concept *is*; the positions it counts come from stored ``source_order``
and creation ids, and the tag alphabet comes from the workbook's own
round-trip regex.

THE SHAPE (spec-step8 T4, D1/Q14)::

    chapter_key        = f"{code_prefix}_{slug12(chapter_title) or 'X'}_{h8}"
    topic.machine_id   = f"{chapter_key}_{lane}_T{n:02d}"    lane in {PL, PrL}
    concept.machine_id = f"{topic.machine_id}_C{m:02d}"
    question_label     = f"{concept.machine_id} Q{k:02d}"    (space, D2/Q15)

The readable name is NOT inside the id — it rides beside it in the cell as
§6:522's "name + (machine ID)" pair — because ``_underscore_slug`` collapses
to ``"X"`` for any non-Latin script (two different Marathi chapters minted an
identical concept tag) and because a name-bearing label overruns
``models.Question.question_label``'s ``String(128)``. ``h8`` carries the
uniqueness when the slug collapses.
"""
from __future__ import annotations

import hashlib
import re

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import object_session

from .. import bulk_import as bi
from . import directory

# The id's own alphabet. ``bi._TITLE_TAG_RE`` (bulk_import/__init__.py:233)
# accepts ``[A-Za-z0-9]+(_[A-Za-z0-9]+)+`` and is consumed by
# ``strip_title_tag`` at every import/deposit/read-back boundary, so a minted
# tag that leaves this alphabet does not round-trip and the importer cannot
# match it. A hyphen alone breaks the trip.
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]+")

_CHAPTER_SLUG_LENGTH = 12
_HASH_LENGTH = 8

LANE_POST_TOKEN = "PL"
LANE_PRE_TOKEN = "PrL"


def topic_identity(title: str) -> str:
    """The comparable identity of a topic title.

    Mechanical text normalisation only: it strips the workbook's ``Topic NN:``
    prefix and its trailing ``(machine_tag)``, collapses whitespace and
    casefolds. It decides nothing about what a topic means — two rows either
    name the same topic or they do not.
    """
    return bi.normalize_question_text(bi.strip_topic_title(title))


# --------------------------------------------------------------------------- #
# Ordering (moved here from ``bulk_import.writer._source_order_key`` in S4)
# --------------------------------------------------------------------------- #

def source_order_key(value) -> tuple[int, int]:
    """Canonical position first; creation id only breaks legacy/tied rows.

    Body unchanged from ``writer._source_order_key``; it lives here because
    the writer now CALLS this module, and a module that both calls the writer
    and is called by it is the cycle T4-9(b) names. ``writer`` keeps no alias:
    an alias is a second name for one identity, which is exactly how four
    topic identities happened.

    A row with ``source_order == 0`` is a legacy row that no accepted topology
    has positioned yet; it sorts AFTER every positioned sibling, in creation-id
    order. Deterministic, total, and read off storage rather than off content.
    """
    source_order = int(getattr(value, "source_order", 0) or 0)
    return (
        source_order if source_order > 0 else 10**9,
        int(getattr(value, "id", 0) or 0),
    )


# --------------------------------------------------------------------------- #
# The tag alphabet
# --------------------------------------------------------------------------- #

def alnum_token(text: str) -> str:
    """Every ASCII alphanumeric run of ``text``, concatenated."""
    return "".join(_ALNUM_RUN.findall(str(text or "")))


def chapter_slug(chapter_title: str) -> str:
    """The readable-but-lossy chapter segment. ``h8`` carries the uniqueness."""
    return alnum_token(chapter_title)[:_CHAPTER_SLUG_LENGTH] or "X"


def lane_token(pre_post_learning: str) -> str:
    """``PL`` for the Post lane, ``PrL`` for the Pre lane.

    The lane is IN the id, which is what retires ``generation._topic_index``:
    that function existed only because ``question_label`` carried a literal
    ``_PL_`` and no lane discriminator, so a lane-scoped topic number made a
    Pre label byte-identical to a Post one.
    """
    return (
        LANE_PRE_TOKEN
        if str(pre_post_learning or "").strip().casefold().startswith("pre")
        else LANE_POST_TOKEN
    )


def minted_topic_ordinal(tag: str) -> int | None:
    """The ``T##`` this module would have minted, or ``None``.

    A PARSER of this module's own id grammar — it reads the tail it composed
    (``…_<lane>_T##``) back off a string and decides nothing about content.
    It exists because ``reader`` restores a tag out of a workbook cell: the
    pre-S4 writer stamped ``directory.topic_tag``, which is CHAPTER-level, so
    every topic of a chapter carried ONE tag; restoring that unparsed gave
    [measured] six duplicate topic identities in the owner's own eight-topic
    reference workbook, and a duplicate id is a duplicate ``question_label``,
    which ``assessment_release_service`` skips silently (R4).
    """
    parts = str(tag or "").strip().split("_")
    if len(parts) < 2:
        return None
    lane, tail = parts[-2], parts[-1]
    if lane not in (LANE_POST_TOKEN, LANE_PRE_TOKEN):
        return None
    if not (tail.startswith("T") and tail[1:].isdigit() and len(tail) > 1):
        return None
    return int(tail[1:])


def minted_concept_ordinal(tag: str) -> int | None:
    """The ``C##`` of ``…_<lane>_T##_C##``, or ``None``. See above."""
    parts = str(tag or "").strip().split("_")
    if len(parts) < 1:
        return None
    tail = parts[-1]
    if not (tail.startswith("C") and tail[1:].isdigit() and len(tail) > 1):
        return None
    if minted_topic_ordinal("_".join(parts[:-1])) is None:
        return None
    return int(tail[1:])


def title_tag(text: str) -> str:
    """The trailing ``(tag)`` a writer composed, or ``""``.

    Defined by SUBTRACTION against ``bi.strip_title_tag`` rather than by a
    second copy of ``bi._TITLE_TAG_RE``: whatever the one regex removes is the
    tag, so a tag returned here is by construction one that round-trips. A
    title carrying a hyphenated or otherwise off-alphabet parenthetical yields
    ``""`` — the importer then treats the row as tagless instead of restoring
    an id the writer could never have written.
    """
    raw = str(text or "").strip()
    stripped = bi.strip_title_tag(raw)
    if not raw or stripped == raw:
        return ""
    tail = raw[len(stripped):].strip()
    if tail.startswith("(") and tail.endswith(")"):
        return tail[1:-1]
    return ""


# --------------------------------------------------------------------------- #
# The chapter key
# --------------------------------------------------------------------------- #

def chapter_key(chapter, *, chapter_id: int | None = None) -> str:
    """``<code_prefix>_<slug12>_<h8>`` for one chapter row.

    ``chapter_id`` overrides the object's own id for the one caller that needs
    it: ``build_concepts_release_files.transient_release_hierarchy`` builds a
    NEVER-persisted ``models.Chapter`` with ``id = -1`` standing in for the
    real target chapter, and a staged id that did not match the published one
    would re-key every concept at publication.
    """
    board = str(getattr(chapter, "board", "") or "")
    subject = str(getattr(chapter, "subject", "") or "")
    title = str(getattr(chapter, "chapter_title", "") or "")
    grade_token, _fallback = directory.normalized_grade(
        getattr(chapter, "grade", "")
    )
    resolved_id = (
        int(chapter_id)
        if chapter_id is not None
        else int(getattr(chapter, "id", 0) or 0)
    )
    prefix = directory.code_prefix(board, getattr(chapter, "grade", ""), subject)
    digest = hashlib.sha256(
        "\x1f".join([board, grade_token, subject, title, str(resolved_id)])
        .encode("utf-8")
    ).hexdigest()[:_HASH_LENGTH]
    return f"{alnum_token(prefix) or 'X'}_{chapter_slug(title)}_{digest}"


def grade_review_flag(chapter) -> str:
    """A note when the chapter's grade did not parse, or ``""``.

    T4-2: normalise when the parse succeeds, retain the stripped raw token when
    it does not, and RECORD the fallback. ``KG`` and ``Nursery`` must never
    collapse into one ``00`` family — that is CLAUDE.md:48-50's exact failure
    mode, and lower grades are where it bites hardest.
    """
    raw = str(getattr(chapter, "grade", "") or "")
    token, fallback = directory.normalized_grade(raw)
    if not fallback:
        return ""
    return (
        f"grade {raw!r} did not parse to a two-digit class; the identity "
        f"family uses the raw token {token!r} (grade_token_not_normalised)"
    )


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #

def _union(loaded, queried) -> list:
    """Every distinct row in either collection, identity-compared.

    A pending row lives only in the relationship collection (the session does
    not autoflush), and a row created by another unit of work lives only in the
    table. Counting a position — or checking which ids are already taken —
    against half the siblings is how a second row lands on an id the first
    already holds.
    """
    out = list(loaded)
    seen = {id(row) for row in out}
    for row in queried:
        if id(row) not in seen:
            seen.add(id(row))
            out.append(row)
    return out


def sibling_topics(topic) -> list:
    """Every topic of ``topic``'s chapter in the SAME LANE, ``topic`` included.

    Lane membership is decided by ``lane_token`` — the very token that goes
    INTO the id — not by the raw stored string. [measured] grouping on the raw
    string put a ``"Post"`` topic and a ``"post"`` topic in two groups that
    both number from 1 while both resolving to ``PL``, so both minted
    ``…_PL_T01``; ``reader`` takes that string verbatim from a workbook cell
    and already records a ``topic_lane_conflict`` for exactly this state.
    """
    chapter = getattr(topic, "chapter", None)
    loaded = list(getattr(chapter, "topics", None) or []) or [topic]
    queried: list = []
    session = _session_of(topic)
    chapter_id = int(getattr(topic, "chapter_id", 0) or 0)
    if session is not None and chapter_id:
        queried = (
            session.query(type(topic))
            .filter_by(chapter_id=chapter_id)
            .all()
        )
    lane = lane_token(getattr(topic, "pre_post_learning", ""))
    rows = [
        row for row in _union(loaded, queried)
        if lane_token(getattr(row, "pre_post_learning", "")) == lane
    ]
    return rows or [topic]


def sibling_concepts(concept) -> list:
    """Every concept of ``concept``'s topic, ``concept`` included."""
    topic = getattr(concept, "topic", None)
    loaded = list(getattr(topic, "concepts", None) or []) or [concept]
    queried: list = []
    session = _session_of(concept)
    topic_id = int(getattr(concept, "topic_id", 0) or 0)
    if session is not None and topic_id:
        queried = (
            session.query(type(concept)).filter_by(topic_id=topic_id).all()
        )
    return _union(loaded, queried) or [concept]


def topic_position(topic) -> int:
    """1-based position among the chapter's topics IN THE SAME LANE."""
    siblings = sorted(sibling_topics(topic), key=source_order_key)
    try:
        return siblings.index(topic) + 1
    except ValueError:
        return 1


def concept_position(concept) -> int:
    """1-based position among its topic's concepts."""
    siblings = sorted(sibling_concepts(concept), key=source_order_key)
    try:
        return siblings.index(concept) + 1
    except ValueError:
        return 1


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #

def compose_topic_machine_id(chapter_key_value: str, lane: str, n: int) -> str:
    return f"{chapter_key_value}_{lane}_T{int(n):02d}"


def compose_concept_machine_id(topic_machine_id: str, m: int) -> str:
    return f"{topic_machine_id}_C{int(m):02d}"


# SOP §3.2's difficulty prefixes inside a Group ID. The alphabet lives here
# with the other id grammar; ``assessment_grouping.TIER_CODES`` aliases it.
GROUP_TIER_CODES = {"Basic": "BG", "Intermediate": "IG", "Advanced": "AG"}


def compose_group_key(
    concept_machine_id: str, tier: str, sequence: int,
) -> str:
    """SOP §3.2's Group ID: ``(<ConceptID>) <BG|IG|AG>##``.

    Since the owner's 2026-08-21 nomenclature ruling (Q16, SOP §6.1) this
    one composition also supplies the visible ``group_name`` and
    ``group_display_name`` — both carry the group ID itself. Pure
    composition; the caller owns tier validation and any minting.
    """
    code = GROUP_TIER_CODES[tier]
    return f"({concept_machine_id}) {code}{int(sequence):02d}"


def free_slot(compose, start: int, taken) -> str:
    """The first ordinal at or after ``start`` no sibling already holds.

    A position is where a row sits TODAY; an id is what it was minted as ONCE.
    Composing straight off the position silently reuses an id whenever a row
    is inserted ahead of a minted sibling — [measured] an ordinary second
    publication that prepends one topic gave two topics, two concepts and two
    ``question_label``s the identical string, and
    ``assessment_release_service:612`` then skips the second concept's
    questions with no flag (R4). Refusing a slot a sibling holds and advancing
    is mechanics: the position still decides where the search STARTS, so the
    ordinary case is unchanged and only a genuine clash moves.
    """
    ordinal = max(int(start), 1)
    candidate = compose(ordinal)
    while candidate in taken:
        ordinal += 1
        candidate = compose(ordinal)
    return candidate


# --------------------------------------------------------------------------- #
# Lookup-then-mint (T4-3). This is the ONLY producer of a machine id.
# --------------------------------------------------------------------------- #

def _session_of(row):
    """The row's session, or ``None`` when it has none to persist to.

    ``object_session`` raises ``UnmappedInstanceError`` for anything that is
    not a mapped ORM instance — a lightweight stand-in concept, a duck-typed
    row from another lane. Such a row is transient by definition: there is
    nowhere to persist an id to, so it is treated exactly like a
    never-added ORM object rather than crashing a renderer.
    """
    try:
        return object_session(row)
    except SQLAlchemyError:
        return None


def machine_id_for_topic(
    topic, *, chapter_id: int | None = None, position: int | None = None,
) -> str:
    """The topic's persisted id, minting and persisting one only if blank.

    P-C1: identity is minted ONCE and PERSISTED; it is never re-derived. A row
    that already carries an id keeps it verbatim, whatever its title now says,
    because no formula survives the rename/merge/split/re-tag §7:577 permits.

    A row with no session is TRANSIENT (the never-persisted copies
    ``build_concepts_release_files`` builds). It returns whatever id is already
    stamped on the copy and mints nothing: there is nowhere to persist to, and
    a transient row must never invent an identity a persisted row will later
    contradict.

    ``position`` (S10 repair, Round 7): the caller's OWN 1-based ordinal for
    where the ``free_slot`` search starts, overriding the sibling-sort guess
    of ``topic_position``. The release publication passes its per-release
    counter here so the minted id equals the id the transient export stamped
    for the same row — [measured] the sibling-sort guess counts blank-id
    legacy rows and unstable ``source_order`` ties, so the two drifted and a
    republication minted a fresh id every time. Still ONE minter, still
    ``free_slot``, still persisted.
    """
    if topic is None:
        return ""
    existing = str(getattr(topic, "machine_id", "") or "").strip()
    if existing:
        return existing
    if _session_of(topic) is None:
        return existing
    chapter = getattr(topic, "chapter", None)
    if chapter is None:
        # Genuine impossibility: no chapter, so no identity tuple to key on.
        # Left blank and surfaced by the caller, never guessed.
        return ""
    key = chapter_key(chapter, chapter_id=chapter_id)
    lane = lane_token(topic.pre_post_learning)
    taken = {
        str(getattr(sibling, "machine_id", "") or "").strip()
        for sibling in sibling_topics(topic)
        if sibling is not topic
    }
    minted = free_slot(
        lambda n: compose_topic_machine_id(key, lane, n),
        topic_position(topic) if position is None else position,
        taken,
    )
    topic.machine_id = minted
    return minted


def machine_id_for_concept(
    concept, *, chapter_id: int | None = None, position: int | None = None,
) -> str:
    """The concept's persisted id, minting and persisting one only if blank.

    The single source of the ``<TopicID>_C##`` pattern, and — through
    ``generation.question_label`` — of ``<ConceptID> Q##`` as well. Two live
    minters meant the same question could carry two label shapes, so S5's
    ``_next_label_index`` max-scan and T5-2's ``UploadRefused`` comparison both
    read the wrong family.

    ``position`` overrides the sibling-sort start ordinal exactly as on
    ``machine_id_for_topic`` above, and exists for the same measured reason.
    """
    if concept is None:
        return ""
    existing = str(getattr(concept, "machine_id", "") or "").strip()
    if existing:
        return existing
    if _session_of(concept) is None:
        return existing
    topic = getattr(concept, "topic", None)
    base = machine_id_for_topic(topic, chapter_id=chapter_id)
    if not base:
        return ""
    taken = {
        str(getattr(sibling, "machine_id", "") or "").strip()
        for sibling in sibling_concepts(concept)
        if sibling is not concept
    }
    minted = free_slot(
        lambda m: compose_concept_machine_id(base, m),
        concept_position(concept) if position is None else position,
        taken,
    )
    concept.machine_id = minted
    return minted


# --------------------------------------------------------------------------- #
# Label numbering (spec-step8 T5-3, R5) — ONE max-scan, both lanes
# --------------------------------------------------------------------------- #

# The separator ``generation.question_label`` composes with: ``<base> Q##``
# (D2/Q15). Named once so the scan and the minter cannot drift apart.
LABEL_INFIX = " Q"

LEGACY_LABEL_FAMILY = "legacy_label_family"


def label_family_prefix(base: str) -> str:
    """The prefix every label of one concept's family starts with."""
    return f"{base}{LABEL_INFIX}"


def label_family_base(question_label: str) -> str:
    """The family a committed label belongs to, or ``""`` when it has none.

    Mechanics: it splits on the separator this module composes with and
    requires a numeric tail. It never reads the base to decide what the
    concept *is*.
    """
    raw = str(question_label or "")
    head, separator, tail = raw.rpartition(LABEL_INFIX)
    if not separator or not head or not tail.isdigit():
        return ""
    return head


def next_label_index(db, base: str) -> int:
    """Continue numbering after every label already committed for ``base``.

    A MAX-SCAN, never a count. A count breaks numbering the moment anything is
    deleted: ``len(questions) + 1`` hands the next mint the number of EVERY
    question ever removed from the family, including numbers already published
    on rows a learner has seen. The scan reads the highest number actually
    present and continues past it, so a deletion leaves a gap.

    **The guarantee, stated exactly.** A count reissues every deleted number;
    the max-scan reissues only the TOP of the range — delete the highest label
    of a family and the next mint takes it back. That residual is a property of
    scanning live rows, and closing it needs a durable record of retired labels
    (the uploaded release snapshots are the obvious one), which no slice up to
    here builds. So R5 — *a ``question_label``, once uploaded, is never
    reassigned* — is held here for every interior deletion and NOT for the top
    of a range; ``test_deleting_the_highest_label_of_a_family_still_reissues_
    its_number`` pins the open case rather than letting the name of the
    interior test imply the general guarantee.

    Promoted out of ``assessment_release_run`` (T5-3) so the Build Assessments
    lane and the release lane continue the SAME family. That is only correct
    because S4 left one minter: ``base`` is the concept's PERSISTED
    ``machine_id``, not a re-derived slug that collides across chapters.

    A grandfathered legacy label is not a counterexample — it carries a
    different prefix, so it falls outside this family and is neither scanned
    nor reassigned. ``legacy_label_family`` says so out loud.

    **A blank ``base`` is scanned, not short-circuited to 1.**
    ``machine_id_for_concept`` legitimately returns ``""`` for a concept whose
    topic has no resolvable chapter — genuine impossibility, left blank and
    surfaced by the caller, never guessed — and ``generation.question_label``
    then composes ``" Q##"``. Returning 1 for that base restarts the family on
    every run, so the second run re-mints ``" Q01"`` and the partial unique
    index turns it into an ``IntegrityError`` escaping mid-generation: the
    whole batch of generated questions is lost with no record, which is both a
    mid-run halt Q13 forbids and the R4 loss this slice exists to close. The
    blank family is a family like any other; scanning it keeps numbering
    monotonic, and the missing identity is caught where it belongs — at the
    publication act, as T9-1 B1's "a staged row whose concept still has no
    ``machine_id``".
    """
    from .. import models

    prefix = label_family_prefix(base)
    highest = 0
    rows = db.query(models.Question.question_label).filter(
        # ``autoescape`` matters: a machine id is full of ``_``, which LIKE
        # reads as "any single character" — without it the scan pulls in
        # sibling concepts' families.
        models.Question.question_label.startswith(prefix, autoescape=True)
    )
    for (label,) in rows:
        text = str(label or "")
        if not text.startswith(prefix):
            continue
        tail = text[len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


def legacy_label_family(labels, base: str) -> dict | None:
    """Name BOTH prefixes when one concept carries two label conventions.

    R5 grandfathers a published label instead of migrating it, so a concept
    whose old questions were minted under a different id keeps them while the
    new family restarts at 1. That is correct, and it is also confusing to
    read, so it is said out loud instead of left for a reviewer to discover.
    Returns ``None`` when the concept has only the minted family.
    """
    minted = str(base or "").strip()
    legacy = sorted({
        family
        for family in (label_family_base(label) for label in labels or [])
        if family and family != minted
    })
    if not legacy or not minted:
        return None
    return {
        "code": LEGACY_LABEL_FAMILY,
        "concept_machine_id": minted,
        "minted_prefix": label_family_prefix(minted),
        "legacy_prefixes": [label_family_prefix(f) for f in legacy],
        "prefixes": [
            label_family_prefix(f) for f in (*legacy, minted)
        ],
    }


def legacy_label_family_for_concept(concept, base: str) -> dict | None:
    """``legacy_label_family`` over every label already committed for one
    concept, read through its own groups."""
    if concept is None:
        return None
    labels = [
        question.question_label
        for group in (getattr(concept, "groups", None) or [])
        for question in (getattr(group, "questions", None) or [])
    ]
    return legacy_label_family(labels, base)


# --------------------------------------------------------------------------- #
# The shared cell composers (T14) — both renderers call these, neither owns
# them, and neither imports the other.
# --------------------------------------------------------------------------- #

def titled(name: str, machine_id: str) -> str:
    """§6:522's cell: ``"<readable name> (<machine id>)"``.

    The name is stripped of the tag it already carries ONLY when that tag is
    one an identity minted — this module's ``…_<lane>_T##[_C##]`` grammar, or
    the very id being applied — so an already-composed value never acquires a
    second one. A parenthetical that is part of the NAME is kept: [measured]
    stripping unconditionally turned ``'Ohm Law (V_I_R)'`` into
    ``'Ohm Law (<id>)'``, which re-imports as ``'Ohm Law'`` — a learner-visible
    name silently lost on a round trip (R4), and a loss the pre-S4 writer did
    not have, because it composed ``f"{title} ({tag})"`` without stripping.

    With no id the cell is the bare name — an empty ``()`` would not
    round-trip ``strip_title_tag`` and would read as an identity that exists
    and is blank.
    """
    raw = str(name or "")
    tag = str(machine_id or "").strip()
    carried = title_tag(raw)
    replaceable = bool(carried) and (
        carried == tag
        or minted_topic_ordinal(carried) is not None
        or minted_concept_ordinal(carried) is not None
    )
    clean = (bi.strip_title_tag(raw) or raw) if replaceable else raw
    return f"{clean} ({tag})" if tag else clean


def topic_concept_roster(topic, export_scope=None) -> str:
    """Column J: every concept of ``topic`` exactly as its own title cell reads.

    ``export_scope`` is taken as a PARAMETER and never imported: that is what
    lets one roster serve both renderers without ``services.identity``
    importing ``bulk_import.writer`` (T4-9(b)). It only has to answer
    ``concepts_for(topic)``.
    """
    if topic is None:
        return ""
    concepts = (
        list(export_scope.concepts_for(topic))
        if export_scope is not None
        else sorted(list(topic.concepts or []), key=source_order_key)
    )
    return ", ".join(
        titled(concept.concept_title, machine_id_for_concept(concept))
        for concept in concepts
    )
