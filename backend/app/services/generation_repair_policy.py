"""Job 129 repair contract for fresh source work and explicit revisions.

Absence on a saved upload remains historical. Corrected Pre work can adopt
this policy in its own sealed revision without changing the Post/source run.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Mapping

KEY = "generation_repair_policy"
VERSION = "owner-generation-repair-2026-09-11-v1"
REVIEWED_PRE_SCOPE_FIELD = "_aegis_reviewed_pre_scope"
_UNBOUND = object()
_run_policy: ContextVar = ContextVar(KEY, default=_UNBOUND)


def active(value: Mapping[str, Any] | None) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get(KEY) == VERSION:
        return True
    for name in ("metadata", "meta", "_resolved_metadata", "profile", "assessment_profile"):
        nested = value.get(name)
        if isinstance(nested, Mapping) and nested.get(KEY) == VERSION:
            return True
    return False


def fields(value: Mapping[str, Any] | None) -> dict[str, str]:
    return {KEY: VERSION} if active(value) else {}


def suffix(value: Mapping[str, Any] | None) -> str:
    return ";" + VERSION if active(value) else ""


@contextmanager
def bind_run(version: str | None):
    if version not in (None, VERSION):
        raise ValueError("Unknown saved generation repair policy")
    token = _run_policy.set(version)
    try:
        yield
    finally:
        _run_policy.reset(token)


def run_fields() -> dict[str, str]:
    value = _run_policy.get()
    if value is _UNBOUND:
        from . import model_provider
        profile = model_provider.bound_profile()
        value = VERSION if profile and profile.get("version") in {model_provider.PROFILE_VERSION, model_provider.MINI_PROFILE_VERSION} else None
    return {KEY: VERSION} if value == VERSION else {}


PRE_ASSESSMENT_INSTRUCTION = """ACCEPTED PRE CONCEPTS MUST RECEIVE DIAGNOSTIC QUESTIONS
The supplied Pre map is the accepted assessment scope. Prerequisite eligibility
was decided upstream or explicitly accepted in the corrected Concept workbook;
this coverage/authoring pass does not reopen that decision. For every supplied
pre_concept_id plan at least one meaningful diagnostic question, with an
adaptive total and tier split that assesses its concrete accepted knowledge and
mastery. Zero is not a valid plan or a request to drop an accepted concept here.
Do not replace a prerequisite's scope with awareness that a learner once studied
its name. Use the actual general knowledge or transferable skill described.

For reviewer-edited concepts, the current title, Description and Achieving
Mastery are authoritative. Superseded prerequisite statements, links, analysis,
old questions and audits are provenance only; they cannot override the edited
scope or require missing curriculum documentation. A supplied grade/subject and
source task assumptions can support a clearly identified inference of prior
learning, without inventing a specific curriculum record or year of acquisition.
Keep questions at the accepted foundational scope and grade. Do not import the
current chapter's exercises, unsupported details or inflated difficulty. The
independent reviewer checks coverage and actual asks/answers against that scope;
eligibility concerns remain explicit advisory findings, not missing questions.
"""

POST_GROUP_INSTRUCTION = """REVIEWED POST QUESTION GROUP
The reviewed_source_qids and reviewed_source_dependencies describe the original
members of ONE reviewer-accepted question. Their source answers, options, media,
tables and subparts are supporting evidence for that single frozen task, not
additional candidate questions. Preserve the exact accepted question and its
reviewed target; do not restore original separate questions or route a member
back to its former concept. Resolve answers and scoring against what the merged
task actually asks, preserving each essential member dependency. Do not blindly
concatenate independent answer sets or restore raw chapter exposition. Report
any incompatible evidence through the existing advisory review and repair path;
the Master Refiner's immutable fields and prose whitelist remain unchanged.
"""


def grouped_source_instruction(payload: Mapping[str, Any] | None) -> str:
    """Read explicit grouped-source identities, never infer question grouping."""
    if not active(payload):
        return ""
    values = [
        payload, payload.get("source_atom"), payload.get("atom"),
        payload.get("item"), payload.get("candidate"),
    ]
    for context in (payload, payload.get("context")):
        if isinstance(context, Mapping):
            values.extend(context.get("source_atoms") or [])
    packets = [payload.get("chapter_evidence")]
    packets.extend(
        row.get("source_evidence")
        for row in payload.get("rows") or [] if isinstance(row, Mapping)
    )
    for packet in packets:
        if not isinstance(packet, Mapping):
            continue
        inventory = packet.get("question_task_inventory") or []
        if isinstance(inventory, Mapping):
            inventory = inventory.get("items") or []
        if isinstance(inventory, list):
            values.extend(inventory)
    for value in values:
        if not isinstance(value, Mapping):
            continue
        context = value.get("source_context")
        if not isinstance(context, Mapping):
            continue
        ids = context.get("reviewed_source_qids")
        if isinstance(ids, list) and len(ids) > 1:
            return "\n" + POST_GROUP_INSTRUCTION
    return ""
