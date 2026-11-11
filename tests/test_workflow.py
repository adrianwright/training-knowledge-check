from unittest.mock import Mock

import pytest

from knowledge_check.models import Artifact, Finding, Review
from knowledge_check.workflow import approve, execute, poisoned_source


def ambiguity_review():
    return Review(
        findings=[
            Finding(
                question_id="q2",
                category="multiple_correct",
                explanation="Both named staff roles can answer storage questions.",
                suggestion="Ask who to notify about damaged equipment instead.",
            )
        ],
        source_findings=[],
        summary="The seeded question has two correct answers.",
    )


def test_ambiguous_draft_requires_review_and_cannot_be_approved(source, questions, tmp_path):
    agents = Mock()
    agents.invoke.side_effect = [questions, ambiguity_review()]
    artifact = execute(agents, source, "ambiguous", "unit-run", tmp_path)
    assert artifact.status == "needs_review"
    with pytest.raises(ValueError, match="ready_for_human"):
        approve(artifact, "human")


def test_missed_ambiguity_fails_quality_gate(source, questions, clean_review, tmp_path):
    agents = Mock()
    agents.invoke.side_effect = [questions, clean_review]
    with pytest.raises(ValueError, match="missed"):
        execute(agents, source, "ambiguous", "unit-run", tmp_path)


def test_repair_calls_both_foundry_agents_again(source, questions, clean_review, tmp_path):
    agents = Mock()
    agents.invoke.side_effect = [questions, ambiguity_review(), questions, clean_review]
    artifact = execute(agents, source, "repaired", "unit-run", tmp_path)
    assert [c.args[0] for c in agents.invoke.call_args_list] == [
        "author",
        "review",
        "author",
        "review",
    ]
    assert artifact.status == "ready_for_human"


def test_security_requires_source_finding_not_just_clean_questions(source, questions, tmp_path):
    agents = Mock()
    agents.invoke.side_effect = [
        questions,
        Review(findings=[], source_findings=[], summary="No issues."),
    ]
    with pytest.raises(ValueError, match="instruction override"):
        execute(agents, source, "security", "unit-run", tmp_path)
    assert "SYNTHETIC-DEMO-unit-run" in poisoned_source(source, "unit-run").text


def test_source_security_issue_also_blocks_human_approval(source, questions):
    review = Review(
        findings=[], source_findings=["Embedded instruction override."], summary="Unsafe."
    )
    with pytest.raises(ValueError, match="Unresolved findings"):
        Artifact(
            run_id="unit-run",
            source=source,
            questions=questions,
            review=review,
            status="approved",
            approved_by="human",
        )


def test_human_approval_is_explicit_and_requires_a_name(source, questions, clean_review):
    artifact = Artifact(
        run_id="unit-run",
        source=source,
        questions=questions,
        review=clean_review,
        status="ready_for_human",
    )
    with pytest.raises(ValueError, match="human reviewer"):
        approve(artifact, " ")
    assert approve(artifact, "Demo reviewer").status == "approved"


def test_fabricated_quote_fails_grounding(source, questions):
    questions.questions[0].source_quote = "This invented sentence is not in the guide."
    with pytest.raises(ValueError, match="exact source"):
        questions.check_grounding(source)
