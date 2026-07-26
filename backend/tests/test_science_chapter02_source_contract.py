"""Focused source-contract regressions from Grade VIII Science Chapter 02."""

from app.services import generation as g


def _anchors(source: str) -> tuple[list[dict], list[dict]]:
    sections = g.parse_mmd_sections(source)
    return sections, g._attach_explicit_figure_images(
        g._source_task_anchors(sections), sections)


def test_discussion_keeps_compound_prompts_and_distinct_speech_bubble():
    source = r"""
\subsection*{2.4 How Are We Connected to Microbes?}
Can we find microorganisms in other places, too? Let us have a discussion:
Have you ever seen a lemon, tomato, orange, or any other food item rot after being left outside for some time? If yes, you may have noticed a powdery growth on it (Fig. 2.9). This happens because it has been infected by microbes. But where did these microbes come from? How did they come in contact with the food?
But why do microorganisms not infect the pickles and murabbas?
How does the diversity of microorganisms play a role in our daily life? How do they help clean the environment?
"""

    _sections, anchors = _anchors(source)

    assert [item["raw_task"] for item in anchors] == [
        "Can we find microorganisms in other places, too?",
        (
            "Have you ever seen a lemon, tomato, orange, or any other food item "
            "rot after being left outside for some time? If yes, you may have "
            "noticed a powdery growth on it (Fig. 2.9). This happens because it "
            "has been infected by microbes. But where did these microbes come "
            "from? How did they come in contact with the food?"
        ),
        "But why do microorganisms not infect the pickles and murabbas?",
        (
            "How does the diversity of microorganisms play a role in our daily "
            "life? How do they help clean the environment?"
        ),
    ]


def test_activity_result_context_keeps_followup_questions_but_not_answer():
    source = r"""
\subsection*{2.4 Microorganisms and food}
\section*{Activity 2.8: Let us perform}
\begin{itemize}
\item[-] Prepare equal bowls of dough, adding yeast only to bowl A.
\item[-] Observe both bowls after 4-5 hours.
\end{itemize}
Did you find any change in the volume, smell, or texture of the dough? If not,
leave the dough for some more time.

After some time, you may notice that dough A has risen and smells different.
Why does this happen? What is the role of yeast? Why did we add sugar and warm
water to the flour?

Yeast is a type of microorganism. It releases carbon dioxide while respiring.
"""

    _sections, anchors = _anchors(source)
    activity = next(item for item in anchors if item["_activity_origin"])

    assert "Did you find any change" in activity["raw_task"]
    assert "Why does this happen?" in activity["raw_task"]
    assert "What is the role of yeast?" in activity["raw_task"]
    assert "Why did we add sugar and warm water to the flour?" in activity["raw_task"]
    assert "Yeast is a type of microorganism" not in activity["raw_task"]


def test_activity_merge_preserves_separate_followup_and_dedupes_full_activity():
    source = r"""
\subsection*{2.3 What Are Microorganisms?}
\section*{Activity 2.6: Let us study}
Compare the observations in Table 2.1 and Table 2.2 and record the categories
of microorganisms that you find.

\begin{table}
\caption{Table 2.1: Organisms present in pond water}
\begin{tabular}{|l|l|}
\hline Amoeba & Protozoa \\
\end{tabular}
\end{table}

Did you also observe any of these microorganisms or something different?
Record in your notebook and discuss in your class.

In the tables you identified a variety of microorganisms. They are everywhere.
"""
    sections = g.parse_mmd_sections(source)
    anchors = g._source_task_anchors(sections)
    activity = next(item for item in anchors if item["_activity_origin"])
    semantic_items = [
        {
            "qid": "QINV-0001",
            "source_kind": "activity",
            "source_label": "Activity 2.6",
            "topic_hint": "What Are Microorganisms?",
            "raw_task": (
                "Compare the observations in Table 2.1 and Table 2.2 and record "
                "the categories of microorganisms that you find."
            ),
            "normalized_task": "",
            "shared_context": "",
            "image_urls": [],
        },
        {
            "qid": "QINV-0002",
            "source_kind": "intext_question",
            "source_label": "Activity 2.6: Let us study",
            "topic_hint": "What Are Microorganisms?",
            "raw_task": (
                "Did you also observe any of these microorganisms or something "
                "different? Record in your notebook and discuss in your class."
            ),
            "normalized_task": "",
            "shared_context": "Table 2.1 and Table 2.2 observations.",
            "image_urls": [],
        },
    ]

    merged = g._merge_source_task_anchors(semantic_items, anchors)

    assert len(merged) == 2
    assert sum(bool(item.get("_activity_origin")) for item in merged) == 1
    assert any(
        item.get("qid") == "QINV-0002"
        and "Did you also observe" in item["raw_task"]
        for item in merged
    )
    assert "In the tables you identified" not in activity["raw_task"]


def test_panel_reference_selects_the_matching_combined_figure():
    first = "https://example.test/fig-2-3-a.png"
    combined = "https://example.test/fig-2-3-bcd.png"
    source = rf"""
\subsection*{{2.1 What Is a Cell?}}
\section*{{Activity 2.2: Let us study a cell}}
\begin{{itemize}}
\item[-] Compare Fig. 2.3c and Fig. 2.3d.
\end{{itemize}}
\begin{{figure}}
\includegraphics{{{first}}}
\caption{{Fig. 2.3: (a) Removing onion peel}}
\end{{figure}}
\begin{{figure}}
\includegraphics{{{combined}}}
\caption{{Fig. 2.3: (b) Mounting the peel; (c) Onion cells; (d) Brick wall}}
\end{{figure}}
"""

    _sections, anchors = _anchors(source)
    activity = next(item for item in anchors if item["_activity_origin"])

    assert activity["image_urls"] == [combined]


def test_explicit_figure_can_use_the_only_embedded_uncaptioned_image():
    figure = "https://example.test/fig-2-8.png"
    source = rf"""
\subsection*{{2.3 What Are Microorganisms?}}
\section*{{Activity 2.5: Let us observe soil suspension}}
\begin{{itemize}}
\item[-] Prepare a soil suspension.
\end{{itemize}}
![]({figure})
\begin{{itemize}}
\item[-] Observe it under the microscope (Fig. 2.8).
\end{{itemize}}
"""

    _sections, anchors = _anchors(source)
    activity = next(item for item in anchors if item["_activity_origin"])

    assert activity["image_urls"] == [figure]


def test_unnumbered_diagram_does_not_borrow_a_figure_from_another_section():
    bacterial_cell = "https://example.test/fig-2-13.png"
    source = rf"""
\subsection*{{2.5 Why Is Cell Considered to Be a Basic Unit of Life?}}
\begin{{figure}}
\includegraphics{{{bacterial_cell}}}
\caption{{Fig. 2.13: A bacterial cell showing the nucleoid region}}
\end{{figure}}
\section*{{Keep the curiosity alive}}
\begin{{itemize}}
\item[1.] Write the supplied labels in the appropriate places in the following
diagram. Nucleus, Cytoplasm, Chloroplast, Cell wall, Cell membrane, Nucleoid.
\end{{itemize}}
"""

    _sections, anchors = _anchors(source)
    question = next(item for item in anchors if item["source_label"].endswith("Q1"))

    assert question["requires_visual"] is True
    assert question["image_urls"] == []


def test_unreferenced_embedded_figure_requires_semantic_ownership():
    root_nodule = "https://example.test/fig-2-12.png"
    source = rf"""
\subsection*{{2.4 Microorganisms and food}}
\section*{{Activity 2.9: Let us prepare}}
\begin{{itemize}}
\item[-] Compare curd formation in warm milk and cold milk.
\item[-] Record your predictions and observations in Table 2.4.
\end{{itemize}}
\begin{{figure}}
\includegraphics{{{root_nodule}}}
\caption{{Fig. 2.12: Root nodules containing Rhizobium}}
\end{{figure}}
"""

    _sections, anchors = _anchors(source)
    activity = next(item for item in anchors if item["_activity_origin"])

    assert activity["image_urls"] == []
    assert activity["requires_visual"] is False


def test_public_inventory_task_removes_latex_layout_scaffolding():
    item = {
        "source_kind": "activity",
        "raw_task": (
            r"\begin{itemize}\item[-] Compare both bowls.\end{itemize} "
            r"\begin{table}\captionsetup{labelformat=empty}"
            r"\caption{Table 2.4: Observations}"
            r"\begin{tabular}{|l|l|}\hline Bowl A & Bowl B \\ "
            r"Warm & Cold \\\hline\end{tabular}\end{table}"
        ),
        "normalized_task": "",
        "shared_context": "",
        "image_urls": [],
    }

    rendered = g._inventory_task_text(item)

    assert r"\begin{itemize}" not in rendered
    assert r"\end{itemize}" not in rendered
    assert r"\begin{table}" not in rendered
    assert r"\end{table}" not in rendered
    assert r"\caption" not in rendered
    assert r"\captionsetup" not in rendered
    assert "Compare both bowls." in rendered
    assert "Table 2.4: Observations" in rendered
    assert "Bowl A" in rendered
    assert "Bowl B" in rendered


def test_recovered_callout_does_not_overwrite_a_stale_checkpoint_ordinal():
    old_source = r"""
\subsection*{2.4 How Are We Connected to Microbes?}
Can we find microorganisms in other places, too?
How does the diversity of microorganisms help clean the environment?
"""
    new_source = r"""
\subsection*{2.4 How Are We Connected to Microbes?}
Can we find microorganisms in other places, too?
But why do microorganisms not infect the pickles and murabbas?
How does the diversity of microorganisms help clean the environment?
"""
    _old_sections, old_anchors = _anchors(old_source)
    semantic_items = [
        {
            **anchor,
            "qid": f"QINV-{index:04d}",
        }
        for index, anchor in enumerate(old_anchors, start=1)
    ]
    new_sections, new_anchors = _anchors(new_source)

    merged = g._merge_source_task_anchors(semantic_items, new_anchors)
    merged = g._attach_explicit_figure_images(merged, new_sections)
    tasks = [g._inventory_task_text(item) for item in merged]

    assert len(tasks) == 3
    assert "But why do microorganisms not infect the pickles and murabbas?" in tasks
    assert (
        "How does the diversity of microorganisms help clean the environment?"
        in tasks
    )


def test_activity_refresh_ignores_shifted_section_index_and_removes_answer_tail():
    source = r"""
\subsection*{2.4 Microorganisms and food}
\section*{Activity 2.8: Let us perform}
\begin{itemize}
\item[-] Prepare equal bowls of dough, adding yeast only to bowl A.
\item[-] Observe both bowls after 4-5 hours.
\end{itemize}
Did you find any change in the volume, smell, or texture of the dough?
Why does this happen? What is the role of yeast?
"""
    sections, anchors = _anchors(source)
    anchor = next(item for item in anchors if item["_activity_origin"])
    semantic = {
        **anchor,
        "qid": "QINV-0001",
        "_source_section_index": anchor["_source_section_index"] - 1,
        "raw_task": (
            anchor["raw_task"]
            + " Yeast is a type of microorganism. It releases carbon dioxide "
            "while respiring."
        ),
    }

    merged = g._merge_source_task_anchors([semantic], anchors)
    merged = g._attach_explicit_figure_images(merged, sections)

    assert len(merged) == 1
    assert "What is the role of yeast?" in merged[0]["raw_task"]
    assert "Yeast is a type of microorganism" not in merged[0]["raw_task"]
