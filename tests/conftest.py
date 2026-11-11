import json
from pathlib import Path

import pytest

from knowledge_check.models import QuestionSet, Review, Source


@pytest.fixture
def source():
    return Source.model_validate_json(
        (Path(__file__).parents[1] / "data" / "guide.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def questions(source):
    quote = "Before moving equipment, inspect the case for visible damage."
    return QuestionSet.model_validate(
        {
            "questions": [
                {
                    "id": f"q{index + 1}",
                    "objective": objective,
                    "prompt": "What should staff do before moving the equipment?",
                    "options": [
                        "Inspect the case",
                        "Skip inspection",
                        "Give it away",
                        "Discard the log",
                    ],
                    "correct_index": 0,
                    "explanations": [
                        "Source requires inspection.",
                        "Not supported.",
                        "Not supported.",
                        "Not supported.",
                    ],
                    "source_id": source.id,
                    "source_quote": quote,
                }
                for index, objective in enumerate(source.objectives)
            ]
        }
    )


@pytest.fixture
def clean_review():
    return Review(findings=[], source_findings=[], summary="No quality issues found.")


def response_json(text):
    return {
        "id": "resp-unit-test",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "gpt-5.6-luna",
        "output": [
            {
                "id": "msg-unit-test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tools": [],
        "tool_choice": "auto",
    }


def parse_body(request):
    return json.loads(request.content)
