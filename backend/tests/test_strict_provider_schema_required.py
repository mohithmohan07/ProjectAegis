"""A strict provider schema must require every property it declares.

OpenAI Structured Outputs refuses the WHOLE request with 400 ``invalid_schema``
when a ``"strict": True`` schema declares a property it leaves out of
``required``. The contradiction is in the REQUEST, so it is worse than the
Fixer's (Q58): no retry, no Fixer and no model change can succeed — the
provider never generates a token.

The repo has already paid for this once. Commit d857f8a fixed it in
``concept_question_review``, whose comment still records the rule:

    Strict provider schemas require every property, including empty lists. A
    Python default makes this optional in model_json_schema() and causes the
    provider to reject the entire request before reading the workbook.

Two hand-written schemas carried the same defect from the day they were
written — ``release_review._instruction_schema`` (every reviewer instruction
round, HTTP 502) and ``concept_revisions._edit_schema`` (every revision round,
recorded as ``status="failed"`` so it read as a flaky provider rather than a
permanent refusal). Both consumers already read the affected fields with
``or ""``, so requiring them changed nothing except that the request is now
accepted.

This test is the sweep, kept: it walks the AST rather than evaluating the
literal, so a schema built with names or calls is still judged.
"""
import ast
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[1] / "app"


def _string_keys(node: ast.Dict) -> list[str]:
    return [key.value for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)]


def _value_for(node: ast.Dict, name: str):
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == name:
            return value
    return None


def _required_names(node) -> tuple[list[str], bool]:
    """The literal entries of a ``required`` list, and whether all were literal."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return [], False
    names, exact = [], True
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            names.append(element.value)
        else:
            exact = False
    return names, exact


def _object_schemas(node, found: list) -> None:
    if isinstance(node, ast.Dict):
        properties = _value_for(node, "properties")
        if isinstance(properties, ast.Dict):
            required, exact = _required_names(_value_for(node, "required"))
            found.append((node.lineno, _string_keys(properties), required, exact,
                          _value_for(node, "required") is not None))
    for child in ast.iter_child_nodes(node):
        _object_schemas(child, found)


def _strict_object_schemas():
    for path in sorted(SERVICES.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - not our file to fix
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict) or "strict" not in _string_keys(node):
                continue
            strict = _value_for(node, "strict")
            if not (isinstance(strict, ast.Constant) and strict.value is True):
                continue
            schema = _value_for(node, "schema")
            if schema is None:
                continue
            found: list = []
            _object_schemas(schema, found)
            for line, properties, required, exact, has_required in found:
                yield path, line, properties, required, exact, has_required


def test_every_strict_schema_requires_every_property_it_declares():
    judged = 0
    defects = []
    for path, line, properties, required, exact, has_required in _strict_object_schemas():
        if not has_required or not exact:
            # A computed ``required`` cannot be judged from the AST. Naming it
            # is the honest answer: a silent skip would let this class back in.
            defects.append(
                f"{path.name}:{line} builds `required` dynamically; check it by hand")
            continue
        judged += 1
        missing = [name for name in properties if name not in required]
        if missing:
            defects.append(
                f"{path.name}:{line} declares {missing} but does not require them; "
                "the provider refuses the whole request with 400 invalid_schema")
    assert judged, "the sweep found no strict schemas — it has stopped working"
    assert not defects, "\n".join(defects)


@pytest.mark.parametrize("module, name, argument", [
    ("app.services.release_review", "_instruction_schema", ["REC-1", "REC-2"]),
    ("app.services.concept_revisions", "_edit_schema", [11, 12]),
])
def test_the_two_repaired_schemas_are_sendable(module, name, argument):
    """The regression itself, on the real schemas, not a reconstruction."""
    import importlib

    schema = getattr(importlib.import_module(module), name)(argument)
    assert schema["strict"] is True
    additions = schema["schema"]["properties"]["additions"]["items"]
    assert set(additions["properties"]) == set(additions["required"])
    assert additions["additionalProperties"] is False
