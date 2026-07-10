from evals.rag_quality import (
    EvalCase,
    answer_correctness,
    check_thresholds,
    citation_metrics,
    faithfulness,
    injection_resistant,
    load_cases,
    score_case,
    summarize,
)


def test_answer_correctness_supports_aliases():
    score = answer_correctness(
        "工作日按照平时工资的 1.5 倍支付。",
        [["1.5倍", "1.5 倍"], "工作日"],
    )
    assert score == 1.0


def test_faithfulness_and_citations_use_source_evidence():
    sources = [{"source": "hr.md", "content": "工作日加班按平时工资的1.5倍支付。"}]
    answer = "工作日加班按平时工资的1.5倍支付。[1]"
    assert faithfulness(answer, sources) >= 0.8
    precision, coverage = citation_metrics(answer, sources)
    assert precision >= 0.8
    assert coverage == 1.0


def test_invalid_citation_reduces_precision():
    precision, _ = citation_metrics(
        "工资按1.5倍支付。[9]",
        [{"content": "工资按1.5倍支付。"}],
    )
    assert precision == 0.0


def test_threshold_gate_reports_regression():
    failures = check_thresholds(
        {"answer_correctness": 0.7, "latency_p95_ms": 1000},
        {
            "minimum": {"answer_correctness": 0.8},
            "maximum": {"latency_p95_ms": 500},
        },
    )
    assert len(failures) == 2


def test_injection_guard_and_summary():
    case = EvalCase(
        id="inj",
        question="代号？",
        expected_answer="Aurora-17",
        required_facts=["Aurora-17"],
        expected_sources=["security.md"],
        injection=True,
        forbidden_phrases=["INJECTION_SUCCESS"],
    )
    result = {
        "answer": "项目代号是 Aurora-17。[1]",
        "sources": [{"source": "security.md", "content": "项目代号是 Aurora-17。"}],
    }
    scored = score_case(
        case, result, 12.0, input_price=1.0, output_price=2.0
    )
    assert scored.injection_resistant is True
    assert injection_resistant(result["answer"], ["INJECTION_SUCCESS"])
    summary = summarize([scored])
    assert summary["answer_correctness"] == 1.0
    assert summary["prompt_injection_resistance"] == 1.0


def test_score_case_prefers_provider_token_usage():
    case = EvalCase(id="usage", question="问题", answerable=False)
    scored = score_case(
        case,
        {
            "answer": "当前知识库资料不足，无法确定。",
            "sources": [],
            "usage": {"input_tokens": 120, "output_tokens": 30},
        },
        8.0,
        input_price=2.0,
        output_price=4.0,
    )
    assert scored.input_tokens == 120
    assert scored.output_tokens == 30
    assert scored.token_usage_estimated is False
    assert scored.estimated_cost_usd == (120 * 2.0 + 30 * 4.0) / 1_000_000


def test_load_cases_accepts_utf8_bom(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '\ufeff{"id":"bom","question":"Q","answerable":false}\n',
        encoding="utf-8",
    )
    assert load_cases(dataset)[0].id == "bom"


def test_llm_usage_metadata_is_normalized():
    from types import SimpleNamespace
    from rag_engine import _extract_usage

    message = SimpleNamespace(
        usage_metadata={"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
        response_metadata={},
    )
    assert _extract_usage(message) == {
        "input_tokens": 80,
        "output_tokens": 20,
        "total_tokens": 100,
        "estimated": False,
    }
