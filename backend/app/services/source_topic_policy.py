"""Source-owned curriculum policy supplied to semantic authors and critics.

This is prompt material, never a heading classifier. New decision identities
include its version/text; sealed Phase 3 envelopes carry their recorded policy.
"""

from typing import Any, Mapping

SOURCE_TOPIC_POLICY_VERSION = "source-topic-ownership-2026-09-09-v1"


def enabled(metadata: Mapping[str, Any] | None) -> bool:
    """Read an explicit carried version; never infer curriculum from text."""
    return (metadata or {}).get("source_topic_policy_version") == (
        SOURCE_TOPIC_POLICY_VERSION
    )


def hierarchy_schema(schema: dict[str, Any], *, active: bool) -> dict[str, Any]:
    """Add the declared learner-facing name to a fresh hierarchy response."""
    if active:
        arrays = schema["schema"]["properties"]
        items = arrays.get("sections", arrays.get("repairs"))["items"]
        items["properties"]["topic_display_name"] = {"type": "string"}
        items["required"].append("topic_display_name")
    return schema


HIERARCHY_TOPIC_INSTRUCTION = """\
For each main_topic, set topic_display_name to a specific source-grounded
teaching name; preserve a meaningful source heading verbatim without its
section number, but replace a generic container title with the subject it
actually teaches. For other roles use topic_display_name="". Preserve every
source section ID, title, block and question: the display name does not edit
the source. A summary/introduction that only supports other teaching is a
content_heading with parent_section_id pointing to its semantic teaching home;
its source content remains present. Numbering is evidence, not authority to
force an editorial container back into a main_topic. Independent substantive
opening content can be a main_topic with a meaningful display name. A critic
repair must carry the resulting topic_display_name too.
"""

SOURCE_TOPIC_POLICY = """\
SOURCE TOPIC OWNERSHIP AND COMPLETE COVERAGE
Read every supplied source passage, heading, table, image, activity and learner
task. A Topic names a meaningful subject of teaching. Summary, Introduction,
Exercises, Review and similar editorial containers are not standalone teaching
Topics merely because they are headings. Judge the content, not a banned-word
list: preserve every teaching element and question inside those containers and
place it with the topic/concept whose meaning or capability it develops. When
an opening independently teaches substantive content, give that content a
specific source-grounded teaching name instead of an Introduction bucket.

Named examples in these instructions illustrate the ownership rule; they are
not source evidence. Never import their people, facts, titles, questions or
topology into another chapter. Use them only when the supplied source actually
contains that material; otherwise apply only the general semantic principle.

Printed position never determines Pre/Post or academic ownership. The opening
Frédéric Sorrieu print and vision in The Rise of Nationalism in Europe teach
current-chapter nationalism and require Post ownership; introduction-like
placement does not make them prerequisites or disposable framing. Apply the
same semantic test to other openings, including substantive astronaut/poem
material introducing a solar-system chapter. Pure advance organisers may be
support, but their substantive teaching, required context and learner asks
must remain reachable. Keep original block/task identities and provenance.

Respect meaningful source headings and teaching progression, including short
lower-grade sections. Atomise independently teachable capabilities and their
observable mastery; do not merge distinct capabilities because their text is
short, impose a minimum/maximum number, or split every fact/example into a
concept. A concise source-backed objective can stand alone. Supporting facts,
examples and representations stay whole with the capability they serve.

Every exercise question belongs to its assessed concept. There is no Exercises
topic or last-topic question dump. Decide ownership by the complete ask, givens,
representations and required methods, keeping each original question occurrence
once. Post questions come only from supplied source tasks: no generated extra
questions, replacement tasks, variants or quota filling. A box for a tick,
number or short response is part of the task's response form, not evidence that
it is optional, trivial, a new concept, or a missing question to invent.
A support/activity/info box can contain genuine assessable learner asks:
retain the whole support occurrence and each source-present task identity with
its question role. The wrapper's role must not suppress a task or exclude it
from question polishing; invent no question when the support contains no ask.

For English prose/poem chapters, the final topic is Detailed Analysis of '<work name>'.
Use distinct, source-supported, grade-appropriate whole-work concepts from
Theme / Central Idea; Plot / Development of Ideas; Characterisation / Speaker;
Setting & Atmosphere; Language & Literary Devices; followed by a substantive
chapter Culmination. These dimensions are not a quota: do not invent a setting,
cast, plot, device, absent ending or other content merely to fill a lens.
For factual English reading passages, use the supported Main Idea / Supporting
Evidence, Development of Ideas and Informative Language interpretations; do
not invent literary characters or setting for nonfiction prose.
Whole-work questions route first to the matching analytical concept: a
characterisation question belongs to Characterisation / Speaker even when its
evidence spans every episode. Apply the same rule to theme, development,
setting and language questions. Chapter-wide scope alone does not make a task
Culmination; reserve that home for genuine integration across distinct lenses.

Preserve coherent source-taught grammar/phonics mini-units and independent
reading passages with their questions, even when they follow a story or poem.
Do not force an unrelated comprehension passage into the story's characters,
theme or episode. If the upload includes an adjacent chapter/page, retain its
content and flag the source-boundary discrepancy with evidence; do not silently
discard it or pretend that it teaches the selected chapter. Use only the
supplied work: an abridgement's ending is its ending, never complete it from
world knowledge. All allocation, grouping and source-boundary judgments belong
to the API author and independent critic, with recorded evidence.
"""


# Pre authoring must not inherit the Post work's narrative/analysis topology.
PRE_SOURCE_POLICY = """PRE-LEARNING TOPIC OWNERSHIP
Name topics for the retained, independently teachable prior capabilities, not
Introduction, Summary or Exercises containers. Group by prerequisite meaning,
without a quota or padding. Descriptions and mastery stay within the retained
prior knowledge; do not import current-work characters, themes, episodes,
literary-analysis lenses or a final Detailed Analysis topic into the Pre map.
Source coverage is audited in the source/Post pipeline. It does not require
duplicating source content or source questions in this Pre output. Preserve
retained prerequisite identity and scope; never fill an empty concept with
current-chapter content or generate extra teaching to support a question count.
"""
