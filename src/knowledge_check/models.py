from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Contract):
    id: str
    title: str
    text: str
    objectives: list[str] = Field(min_length=1)


class Question(Contract):
    id: str
    objective: str
    prompt: str = Field(min_length=10)
    options: list[str] = Field(min_length=4, max_length=4)
    correct_index: int = Field(ge=0, le=3)
    explanations: list[str] = Field(min_length=4, max_length=4)
    source_id: str
    source_quote: str = Field(min_length=10)

    @model_validator(mode="after")
    def distinct_options(self) -> Question:
        if len({option.strip().casefold() for option in self.options}) != 4:
            raise ValueError("Answer options must be distinct.")
        if any(not text.strip() for text in [*self.options, *self.explanations, self.objective]):
            raise ValueError("Options, explanations, and objective must not be blank.")
        return self


class QuestionSet(Contract):
    questions: list[Question] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def unique_ids(self) -> QuestionSet:
        if len({q.id for q in self.questions}) != 5:
            raise ValueError("Question IDs must be unique.")
        return self

    def check_grounding(self, source: Source) -> None:
        for question in self.questions:
            if question.source_id != source.id or question.source_quote not in source.text:
                raise ValueError(f"{question.id}: citation is not an exact source passage.")
            if question.objective not in source.objectives:
                raise ValueError(f"{question.id}: unknown learning objective.")


class Finding(Contract):
    question_id: str
    category: Literal["ambiguity", "unsupported_answer", "multiple_correct", "wording"]
    explanation: str = Field(min_length=10)
    suggestion: str = Field(min_length=10)


class Review(Contract):
    findings: list[Finding]
    source_findings: list[str]
    summary: str

    def check_references(self, questions: QuestionSet) -> None:
        known_ids = {q.id for q in questions.questions}
        if any(f.question_id not in known_ids for f in self.findings):
            raise ValueError("Review references an unknown question.")


class Artifact(Contract):
    run_id: str
    provider: Literal["foundry"] = "foundry"
    source: Source
    questions: QuestionSet
    review: Review
    status: Literal["needs_review", "ready_for_human", "approved"]
    approved_by: str | None = None

    @model_validator(mode="after")
    def consistent_state(self) -> Artifact:
        self.questions.check_grounding(self.source)
        self.review.check_references(self.questions)
        if (self.review.findings or self.review.source_findings) and self.status != "needs_review":
            raise ValueError("Unresolved findings require needs_review status.")
        if self.status == "approved" and not (self.approved_by or "").strip():
            raise ValueError("Approval requires a human reviewer.")
        if self.status != "approved" and self.approved_by is not None:
            raise ValueError("Only approved artifacts can have approved_by.")
        return self
