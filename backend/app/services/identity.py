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
reverse). Nothing here may import ``bulk_import.writer``.
"""
from __future__ import annotations

from .. import bulk_import as bi


def topic_identity(title: str) -> str:
    """The comparable identity of a topic title.

    Mechanical text normalisation only: it strips the workbook's ``Topic NN:``
    prefix and its trailing ``(machine_tag)``, collapses whitespace and
    casefolds. It decides nothing about what a topic means — two rows either
    name the same topic or they do not.
    """
    return bi.normalize_question_text(bi.strip_topic_title(title))
