"""What a record's facts do to its session's row."""

from agdash.adapters.store import SessionFacts, apply_facts
from agdash.db.models import Session


def used(session: Session, *models: str) -> list[str]:
    for model in models:
        apply_facts(session, SessionFacts(model=model), {})
    return session.models


def test_models_are_kept_once_each_with_the_current_one_last():
    session = Session(harness="claude_code", native_id="synthetic", models=[])
    assert used(session, "model-a", "model-a") == ["model-a"]
    assert used(session, "model-b") == ["model-a", "model-b"]
    # Going back to a model used before makes it the current one again.
    assert used(session, "model-a") == ["model-b", "model-a"]
    assert used(session, "model-c", "model-b", "model-b") == ["model-a", "model-c", "model-b"]


def test_a_record_that_names_no_model_leaves_them_alone():
    session = Session(harness="codex", native_id="synthetic", models=["model-a", "model-b"])
    before = session.models
    apply_facts(session, SessionFacts(effort="high"), {})
    assert session.models is before
