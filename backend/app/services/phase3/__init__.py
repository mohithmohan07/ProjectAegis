"""The rewritten Phase 3: four passes from the sealed 81% envelope.

See docs/phase3-rewrite-spec.md. Everything in this package consumes the
envelope explicitly — no ambient session state, no monkeypatch installs —
and talks to the model exclusively through the decision kernel.
"""
