import json
from dataclasses import replace

import pytest

from jevagent.candidates import CANDIDATES
from jevagent.schema import REQUIRED, JevSchema, QuestionSpec, SchemaError, build_schema


@pytest.mark.parametrize("c", CANDIDATES, ids=lambda c: c.base)
def test_every_finalist_compiles_to_valid_schema(c):
    s = build_schema(c)
    assert set(s.questions) == set(REQUIRED)
    assert JevSchema.from_json(s.to_json()) == s
    qs = s.compile()  # real typesafe_sdk objects
    assert set(qs) == set(REQUIRED)
    assert list(s.questions["direction"].criteria) == ["long", "short", "neutral"]
    assert list(s.questions["regime"].criteria) == ["trending", "mean_reverting", "high_vol", "crisis"]
    assert len(s.questions["setup_quality"].criteria) == 4


def test_schema_cannot_change_option_keys():
    s = build_schema(CANDIDATES[0])
    bad = dict(s.questions)
    bad["direction"] = QuestionSpec("choice", "x", {"long": "a", "short": "b", "yolo": "c"})
    with pytest.raises(SchemaError):
        replace(s, questions=bad).validate()


def test_schema_cannot_add_questions():
    d = json.loads(build_schema(CANDIDATES[0]).to_json())
    d["questions"]["position_size"] = {"kind": "score", "instructions": "how big", "criteria": ["a", "b"]}
    with pytest.raises(SchemaError):
        JevSchema.from_json(json.dumps(d))


def test_shadow_prefix_compiles_side_by_side():
    s = build_schema(CANDIDATES[0])
    both = {**s.compile(), **s.compile(prefix="shadow__")}
    assert len(both) == 10
