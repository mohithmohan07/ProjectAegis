"""Syntax and model-policy guards for the offline aegis_pipeline CLI tools.

These scripts are operator tools, not part of the FastAPI app, so nothing else
in the suite imports them — which is exactly how ``bulk_upload_ultimate.py``
shipped with an IndentationError that made it unimportable. They also depend on
packages the backend does not install (pandas, requests), so these tests parse
the source instead of importing it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aegis_pipeline import openai_policy

PIPELINE_DIR = Path(openai_policy.__file__).resolve().parent

# The CLI entry points that talk to OpenAI directly.
MODEL_SCRIPTS = (
    "bulk_upload_mathpix.py",
    "bulk_upload_ultimate.py",
    "concept_mapping_to_prelearning.py",
    "excel_to_concepts_prelearning.py",
    "mmd_to_concepts_excel.py",
)


def _source_files() -> list[Path]:
    return sorted(
        path
        for path in PIPELINE_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )


@pytest.mark.parametrize(
    "path", _source_files(), ids=lambda path: path.name
)
def test_every_pipeline_module_parses(path: Path):
    """A tool nothing imports still has to be valid Python."""

    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # IndentationError is a SyntaxError subclass
        pytest.fail(f"{path.name} line {exc.lineno}: {exc.msg}")


@pytest.mark.parametrize("name", MODEL_SCRIPTS)
def test_cli_tools_take_their_model_from_policy_not_a_pinned_slug(name: str):
    """One setting moves the web app and the offline tools together."""

    tree = ast.parse(
        (PIPELINE_DIR / name).read_text(encoding="utf-8"), filename=name
    )
    assignments = {
        target.id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"MODEL", "MODEL_PARSE", "MODEL_ENRICH"}
    }
    assert assignments, f"{name} declares no model constant"
    for constant, value in assignments.items():
        assert isinstance(value, ast.Call) and isinstance(
            value.func, ast.Name
        ) and value.func.id == "configured_openai_model", (
            f"{name}:{constant} is not configured_openai_model()"
        )


def test_mcq_and_fill_blank_rows_are_siblings_of_one_branch():
    """Guards the exact defect: a branch dedented out of its enclosing loop.

    Both row builders must remain arms of a single if/elif over ``q_type``, so
    each question produces a row on exactly one sheet.
    """

    tree = ast.parse(
        (PIPELINE_DIR / "bulk_upload_ultimate.py").read_text(encoding="utf-8"),
        filename="bulk_upload_ultimate.py",
    )
    builder = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_excel_from_parsed"
    )
    loop = next(
        node
        for node in ast.walk(builder)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "questions"
    )
    dispatch = next(
        node for node in loop.body if isinstance(node, ast.If)
    )

    appended: list[str] = []
    branch: ast.If | None = dispatch
    while branch is not None:
        appended.extend(
            node.func.value.id
            for node in ast.walk(branch)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id.endswith("_rows")
        )
        # Walk to the elif/else arm, if there is one.
        branch = next(
            (node for node in branch.orelse if isinstance(node, ast.If)), None
        )

    assert {"objective_rows", "subjective_rows", "descriptive_rows"} <= set(
        appended
    )


def test_fill_blank_strips_katex_once_per_row_not_per_unused_column():
    """The strip belongs to the row, not to one arm of the column loop.

    It previously sat inside the ``else`` of ``for i in range(1, 11)``, so a
    question with ten blanks never reached it and its KaTeX survived.
    """

    tree = ast.parse(
        (PIPELINE_DIR / "bulk_upload_ultimate.py").read_text(encoding="utf-8"),
        filename="bulk_upload_ultimate.py",
    )
    builder = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_excel_from_parsed"
    )
    # The per-column branch is the one testing the blank index against
    # blanks_count. Nothing row-wide belongs inside either of its arms.
    per_column_branches = [
        node
        for node in ast.walk(builder)
        if isinstance(node, ast.If)
        and any(
            isinstance(name, ast.Name) and name.id == "blanks_count"
            for name in ast.walk(node.test)
        )
        and node.orelse
    ]
    assert per_column_branches, "per-column blanks_count branch not found"

    for branch in per_column_branches:
        for node in ast.walk(branch):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "strip_katex_delimiters"
            ):
                pytest.fail(
                    "strip_katex_delimiters runs inside the per-column "
                    "branch; it must apply once to the finished row"
                )
