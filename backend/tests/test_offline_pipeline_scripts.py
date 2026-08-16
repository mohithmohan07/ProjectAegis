"""Guards for the offline aegis_pipeline CLI tools.

These scripts are operator tools, not part of the FastAPI app, so nothing else
in the suite imports them — which is exactly how ``bulk_upload_ultimate.py``
shipped with an IndentationError that made it unimportable.

They also depend on packages the backend does not install (pandas, requests).
The structural checks therefore parse the source rather than importing it; the
call-path checks stub those two packages and the OpenAI client so the real
request-building code still runs.
"""
from __future__ import annotations

import ast
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

from aegis_pipeline import openai_policy

PIPELINE_DIR = Path(openai_policy.__file__).resolve().parent

# The CLI entry points that talk to OpenAI directly.
MODEL_SCRIPTS = (
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


def test_prelearning_tool_carries_no_similarity_threshold_or_count_quota():
    """concept_mapping_to_prelearning must not re-grow deterministic judgment.

    The 0.95 SequenceMatcher similarity report and the 4-6 topics / 5-7
    concepts-per-topic validator were purged: near-duplicate detection and
    "how many concepts does this chapter warrant" are model decisions, not
    arithmetic. The prompts now carry the no-quota doctrine instead.
    """

    source = (PIPELINE_DIR / "concept_mapping_to_prelearning.py").read_text(
        encoding="utf-8"
    )

    # The similarity machinery (threshold, ratio, report) is gone.
    assert "SequenceMatcher" not in source
    assert "SIMILARITY_THRESHOLD" not in source
    assert "similarity" not in source.lower()

    # The topic/concept count quota (constants, validator, prompt text) is gone.
    for quota_token in (
        "MIN_TOPICS",
        "MAX_TOPICS",
        "MIN_CONCEPTS_PER_TOPIC",
        "MAX_CONCEPTS_PER_TOPIC",
        "topic count",
    ):
        assert quota_token not in source, quota_token

    # And the prompts carry the no-quota doctrine in its place.
    assert "there is no quota" in source


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


# --------------------------------------------------------------------------- #
# Runtime behaviour of the migrated OpenAI call path.
#
# These tools depend on packages the backend does not install, so the stubs
# below stand in for them. That is enough to execute call_gpt_json for real
# against a fake client and prove the request it builds is one a current
# reasoning model accepts.
# --------------------------------------------------------------------------- #

class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeCompletions:
    def __init__(self, recorder: list[dict]) -> None:
        self._recorder = recorder

    def create(self, **kwargs):
        self._recorder.append(kwargs)
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=_FakeMessage(json.dumps({"questions": []}))
                )
            ]
        )


def _load_script(monkeypatch, module_name: str, recorder: list[dict]):
    """Import an offline tool with its non-backend deps and client stubbed."""

    for name in ("pandas", "requests"):
        if name not in sys.modules:
            stub = types.ModuleType(name)
            stub.DataFrame = object
            stub.ExcelWriter = object
            monkeypatch.setitem(sys.modules, name, stub)

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    import openai

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda *args, **kwargs: types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=_FakeCompletions(recorder))
        ),
    )
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    return importlib.import_module(module_name)


@pytest.mark.parametrize(
    "module_name",
    [
        "aegis_pipeline.bulk_upload_ultimate",
    ],
)
def test_call_gpt_json_builds_a_request_a_reasoning_model_accepts(
    monkeypatch, module_name: str
):
    calls: list[dict] = []
    module = _load_script(monkeypatch, module_name, calls)

    data = module.call_gpt_json("gpt-5.6-luna", "system", "user")

    assert data == {"questions": []}
    assert len(calls) == 1
    request = calls[0]

    # temperature is rejected outright by reasoning models.
    assert "temperature" not in request
    assert request["model"] == "gpt-5.6-luna"
    assert request["response_format"] == {"type": "json_object"}
    # The modern parameter name, not the removed max_tokens.
    assert "max_completion_tokens" in request
    assert "max_tokens" not in request
    # Question parsing/enrichment is source-extraction work; the policy
    # right-sizes it to "high" rather than requesting maximum deliberation.
    assert request["reasoning_effort"] == "high"
    assert [message["role"] for message in request["messages"]] == [
        "system",
        "user",
    ]


def test_call_gpt_json_negotiates_effort_like_the_web_app(monkeypatch):
    """The offline tools get the same recovery as the FastAPI paths."""

    calls: list[dict] = []

    class _RejectingCompletions(_FakeCompletions):
        def create(self, **kwargs):
            if kwargs.get("reasoning_effort") == "high":
                error = Exception(
                    "Unsupported value: 'reasoning_effort' does not support 'high'"
                )
                error.status_code = 400
                error.param = "reasoning_effort"
                error.code = "unsupported_value"
                calls.append(kwargs)
                raise error
            return super().create(**kwargs)

    module = _load_script(
        monkeypatch, "aegis_pipeline.bulk_upload_ultimate", calls
    )
    module.client.chat.completions = _RejectingCompletions(calls)

    data = module.call_gpt_json("gpt-5.6-luna", "system", "user")

    assert data == {"questions": []}
    assert [call["reasoning_effort"] for call in calls] == ["high", "medium"]
