from dataclasses import FrozenInstanceError

import pytest

from rootweft.decide.models import (
    ChoiceQuestion,
    DecisionPolicy,
    EgressPreview,
    NoulQuestion,
    ProviderAnswer,
    ProviderConfigurationError,
    ProviderSchemaError,
    ScoreQuestion,
    validate_answer,
    validate_answers,
)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True, "0.5"])
def test_noul_rejects_invalid_probability(value):
    with pytest.raises(ProviderSchemaError):
        ProviderAnswer.noul(probability=value, model="jev-1.13.0")


def test_question_and_distribution_are_immutable():
    question = ChoiceQuestion("q", "Choose", ("a", "b"))
    with pytest.raises(FrozenInstanceError):
        question.id = "changed"
    source = {"a": 0.7, "b": 0.3}
    answer = ProviderAnswer.choice("q", "a", source, 0.6, "jev-1.13.0")
    source["a"] = 0
    assert answer.probabilities["a"] == 0.7
    with pytest.raises(TypeError):
        answer.probabilities["a"] = 0


@pytest.mark.parametrize("options", [(), ("a", "a"), tuple(map(str, range(256)))])
def test_choice_rejects_empty_duplicate_or_excessive_options(options):
    with pytest.raises(ProviderSchemaError):
        ChoiceQuestion("q", "Choose", options)


@pytest.mark.parametrize("levels", [("one",), tuple(map(str, range(11)))])
def test_score_requires_two_to_ten_levels(levels):
    with pytest.raises(ProviderSchemaError):
        ScoreQuestion("q", "Rate", levels)


@pytest.mark.parametrize(
    "distribution",
    [
        {"a": 0.2, "b": 0.2},
        {"a": float("nan"), "b": 0},
        {"a": -0.1, "b": 1.1},
        {"a": True, "b": 0},
    ],
)
def test_choice_rejects_invalid_distribution(distribution):
    with pytest.raises(ProviderSchemaError):
        ProviderAnswer.choice("q", "a", distribution, 0.5, "jev-1.13.0")


def test_choice_cannot_introduce_option_or_omit_distribution_member():
    question = ChoiceQuestion("q", "Choose", ("a", "b"))
    for probabilities, choice in [({"c": 1}, "c"), ({"a": 1}, "a")]:
        with pytest.raises(ProviderSchemaError):
            validate_answer(
                question,
                ProviderAnswer.choice("q", choice, probabilities, 1, "jev-1.13.0"),
            )


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -1, 2, True])
def test_score_rejects_invalid_value_for_rubric(score):
    with pytest.raises(ProviderSchemaError):
        validate_answer(
            ScoreQuestion("q", "Rate", ("low", "high")),
            ProviderAnswer.score("q", score, {"0": 0, "1": 1}, 1, "jev-1.13.0"),
        )


@pytest.mark.parametrize("confidence", [float("nan"), -1, 2, True])
def test_confidence_is_finite_probability(confidence):
    with pytest.raises(ProviderSchemaError):
        ProviderAnswer.choice("q", "a", {"a": 1}, confidence, "jev-1.13.0")


def test_answers_require_exact_id_bijection_type_and_model():
    questions = (NoulQuestion("q", "True?"),)
    good = ProviderAnswer.noul(0.5, "jev-1.13.0", "q")
    bad_sets = [
        (),
        (good, good),
        (ProviderAnswer.noul(0.5, "jev-1.13.0", "x"),),
        (ProviderAnswer.noul(0.5, "typesafe/jev-1.13", "q"),),
        (ProviderAnswer.choice("q", "a", {"a": 1}, 1, "jev-1.13.0"),),
    ]
    for answers in bad_sets:
        with pytest.raises(ProviderSchemaError):
            validate_answers(questions, answers, "jev-1.13.0")
    assert validate_answers(questions, (good,), "jev-1.13.0") == (good,)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_options": 256},
        {"max_questions": 0},
        {"max_payload_bytes": True},
        {"max_cost_usd": float("nan")},
        {"max_retries": 4},
        {"timeout_seconds": 0},
        {"max_retry_delay_seconds": 61},
    ],
)
def test_policy_rejects_unbounded_or_invalid_configuration(kwargs):
    with pytest.raises(ProviderConfigurationError):
        DecisionPolicy(**kwargs)


def test_preview_copies_mutable_paths():
    paths = ["src/app.py"]
    preview = EgressPreview(1, 1, paths, 100)
    paths.append("private.py")
    assert preview.paths == ("src/app.py",)


@pytest.mark.parametrize("model", ["typesafe/jev-1.13-20269999", "typesafe/jev-1.14-20260917", "typesafe/jev-latest"])
def test_openrouter_rejects_invalid_snapshot_identifiers(model):
    with pytest.raises(ProviderSchemaError):
        ProviderAnswer.noul(0.5, model)
