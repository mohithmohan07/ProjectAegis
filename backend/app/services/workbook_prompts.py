"""Register Create Workbooks (vendored) prompts into the editable registry.

The Create Workbooks pipeline lives in ``aegis_pipeline/create_workbooks`` and
reads overrides directly from ``prompt_overrides.json`` (see ``subject_prompts.
_ov``). Here we register each editable key with its built-in default so the
Admin tab can list and edit them. Registration is lazy/idempotent because the
vendored module is only importable after the sys.path vendor shim runs.
"""
from __future__ import annotations

from . import prompts

_registered = False
_CAT = "Create Workbooks · GPT pipeline"


def ensure_registered() -> bool:
    """Best-effort registration of workbook prompts. Returns True on success."""
    global _registered
    if _registered:
        return True
    try:
        from . import workbooks
        workbooks._vendor()
        import subject_prompts as sp  # vendored (create_workbooks/src)
    except Exception:
        return False

    prompts.register("workbook.planner", category=_CAT,
                     label="Planner (pass 1) system prompt",
                     description="Reads the full chapter MMD and inventories "
                                 "everything that must be covered.",
                     default=sp.PLANNER_SYSTEM)
    prompts.register("workbook.builder_base", category=_CAT,
                     label="Builder (pass 2) base system prompt",
                     description="Turns the plan + MMD into the workbook JSON.",
                     default=sp.BASE_BUILDER_SYSTEM)
    prompts.register("workbook.guide.science", category=_CAT,
                     label="Subject guide · Science", default=sp.SCIENCE_GUIDE)
    prompts.register("workbook.guide.mathematics", category=_CAT,
                     label="Subject guide · Mathematics", default=sp.MATH_GUIDE)
    prompts.register("workbook.guide.social", category=_CAT,
                     label="Subject guide · Social Science", default=sp.SOCIAL_GUIDE)
    prompts.register("workbook.guide.english", category=_CAT,
                     label="Subject guide · English", default=sp.ENGLISH_GUIDE)
    _registered = True
    return True
