"""A cited picture cannot waive quotation fidelity for unrelated text."""
import copy

import pytest

from app.services import reviewed_file_input as reviewed
from app.services.phase3 import kernel
from tests.test_independent_reviewed_files import critic, result, setup_job


def document():
    return {
        "version": reviewed.VERSION, "sha256": "f" * 64, "filename": "reviewed.pdf",
        reviewed.QUOTE_POLICY_KEY: reviewed.QUOTE_POLICY_VERSION,
        "blocks": [{"ref": "B1", "text": "Embedded image I1", "image_refs": ["I1"]}],
        "images": [{"ref": "I1", "url": "https://images.example.test/page.png", "page": 1}],
    }


def extraction(text="What is DNA, and what are its uses?", *, declared=True):
    value = result()
    question = value["questions"][0]
    question.update(question_spans=[text], image_refs=["I1"], image_transcriptions=[])
    if declared:
        question["image_transcriptions"] = [{
            "field": "question_spans", "span_index": 0, "image_refs": ["I1"],
            "location": "Page 1, the question beneath the DNA diagram",
        }]
    return value


def test_an_image_does_not_disable_exact_quote_checking():
    defects = reviewed._checker(document())(extraction("Entirely invented demand", declared=False))
    assert any("quoted from its cited" in defect for defect in defects)


def test_authors_visual_assertion_requires_independent_receipt():
    assert any("independent source verification receipt" in defect
               for defect in reviewed._checker(document())(extraction()))


@pytest.mark.parametrize("faithful", [True, False])
def test_only_positive_independent_image_verdict_accepts_transcription(faithful):
    claims_seen = []

    def verify(claims):
        claims_seen.extend(copy.deepcopy(claims))
        return [{"claim_id": claim["claim_id"], "faithful": faithful,
                 "evidence": "The visible question matches." if faithful else "That demand is not on the page."}
                for claim in claims]

    defects = reviewed._checker(document(), visual_verifier=verify)(extraction())
    assert bool(defects) is not faithful
    assert claims_seen[0]["image_refs"] == ["I1"]
    assert claims_seen[0]["text"] == "What is DNA, and what are its uses?"


def test_exact_text_beside_image_needs_no_model_verification():
    doc = document()
    doc["blocks"][0]["text"] = "What is DNA, and what are its uses?"
    assert reviewed._checker(doc, visual_verifier=lambda _: pytest.fail("Exact text must be free"))(
        extraction(declared=False)) == []


def test_cross_cell_frankenquote_is_rejected_but_separate_quoted_spans_work():
    doc = document()
    doc["blocks"][0].update(text="What is DNA?\nList its uses.", cells=[
        {"cell": "A3", "text": "What is DNA?"}, {"cell": "B3", "text": "List its uses."}])
    value = extraction("What is DNA?\nList its uses.", declared=False)
    assert reviewed._checker(doc)(value)
    value["questions"][0]["question_spans"] = ["What is DNA?", "List its uses."]
    assert reviewed._checker(doc)(value) == []


def test_negative_visual_verdict_is_recorded_without_pressuring_verifier():
    claims = [{"claim_id": "Q1:options:0", "text": "Invented", "image_refs": ["I1"], "location": "Page 1"}]
    assert reviewed._visual_verification_checker(claims)({"checks": [{
        "claim_id": "Q1:options:0", "faithful": False, "evidence": "There is no such option."}]}) == []


def test_step_two_repairs_rejected_transcription_and_replays_paid_proof(db, tmp_path):
    job = setup_job(db)
    stored = copy.deepcopy(job.question_inventory)
    stored[reviewed.INPUTS] = {"post": document()}
    job.question_inventory = stored
    db.commit()
    authors, verifications = [], []

    def author(payload):
        authors.append(payload)
        return extraction("Invented question" if len(authors) == 1 else "What is DNA, and what are its uses?")

    def verify(payload):
        verifications.append(copy.deepcopy(payload))
        return {"checks": [{"claim_id": claim["claim_id"],
                            "faithful": claim["text"] == "What is DNA, and what are its uses?",
                            "evidence": "Compared against the question below the page's DNA diagram."}
                           for claim in payload["claims"]]}

    store = kernel.DecisionStore(tmp_path / "decisions")
    current = reviewed.prepare(db, job, lane="post", provider=author, critic=critic,
                               visual_verifier=verify, store=store)
    assert len(authors) == len(verifications) == 2
    assert "not independently verified" in str(authors[1]["response_contract_feedback"])
    assert current["question_task_inventory"]["items"][0]["raw_task"] == "What is DNA, and what are its uses?"
    assert len(current["reviewed_file_receipt"]["image_quote_verifications"]) == 2
    assert reviewed.prepare(db, job, lane="post", provider=lambda _: pytest.fail("Already extracted"),
                            visual_verifier=lambda _: pytest.fail("Already verified"), store=store) == current


def test_historical_unstamped_input_keeps_its_original_schema():
    doc = document()
    doc.pop(reviewed.QUOTE_POLICY_KEY)
    value = extraction()
    value["questions"][0].pop("image_transcriptions")
    assert reviewed._checker(doc)(value) == []


def test_suspending_image_verification_does_not_rebuy_the_extraction_author(db, tmp_path):
    from app.services import run_control
    job = setup_job(db)
    durable = copy.deepcopy(job.question_inventory)
    durable[reviewed.INPUTS] = {"post": document()}
    job.question_inventory = durable
    db.commit()
    store = kernel.DecisionStore(tmp_path / "decisions")
    calls = []

    def author(_):
        calls.append("authored")
        return extraction()

    def waiting(_):
        raise run_control.RunDeferred("The submitted verification batch is pending.")

    with pytest.raises(run_control.RunDeferred):
        reviewed.prepare(db, job, lane="post", provider=author, critic=critic,
                         visual_verifier=waiting, store=store)

    def verified(payload):
        return {"checks": [{"claim_id": claim["claim_id"], "faithful": True,
                            "evidence": "The text is visible below the diagram."}
                           for claim in payload["claims"]]}

    reviewed.prepare(db, job, lane="post", provider=author, critic=critic,
                     visual_verifier=verified, store=store)
    assert calls == ["authored"]
