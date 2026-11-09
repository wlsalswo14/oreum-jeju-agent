"""반출 안전장치(Export Guard)·LLM 관문·수치 검증 테스트."""
import numpy as np
import pandas as pd
import pytest

from app.agent.narrator import unverified_numbers
from app.guard.export_guard import AggTable, Rollup, check
from app.guard.llm_gate import GateViolation, facts_from_results


def _tbl(vals, src="1-12", **kw):
    df = pd.DataFrame({"week": ["월", "월", "화", "화", "토", "토"], "age": ["20", "30"] * 3, "use_cnt": vals})
    return AggTable(key="t", title="t", df=df, dims=["week", "age"], values=["use_cnt"], kind={"use_cnt": "simple"},
                    sources=[src], **kw)


def test_exportable_source_passes_even_with_small_values():
    r = check(_tbl([1, 2, 3, 4, 5, 6], src="3-04"))
    assert r.status == "pass" and r.n_suppressed == 0


def test_all_cells_ge_10_passes():
    r = check(_tbl([10, 20, 30, 40, 50, 60]))
    assert r.status == "pass"


def test_small_cell_suppressed_with_complementary_suppression():
    r = check(_tbl([25, 4, 30, 12, 40, 55], margins=["age"]))
    assert r.status == "fixed"
    mask = r.suppressed_mask["use_cnt"].tolist()
    # 월요일 30대(4) 1차 비공개 + 같은 요일 합계로 역산 가능하므로 월요일 20대도 비공개
    assert mask[:2] == [True, True] and sum(mask) == 2
    assert np.isnan(r.table.df["use_cnt"].iloc[1])


def test_no_margin_means_no_secondary():
    r = check(_tbl([25, 4, 30, 12, 40, 55]))
    assert r.n_suppressed == 1


def test_rollup_preferred_over_suppression():
    t = _tbl([25, 4, 30, 12, 40, 55], margins=["age"],
             rollups=[Rollup("age", {"20": "20~30대", "30": "20~30대"}, "연령 통합")])
    r = check(t)
    assert r.status == "fixed" and r.n_suppressed == 0
    assert any("범주처리" in a for a in r.actions)
    assert len(r.table.df) == 3


def test_support_rule_hides_amount_with_few_transactions():
    df = pd.DataFrame({"bjd": ["A", "B"], "amt": [5_000_000, 9_000_000]})
    t = AggTable(key="a", title="a", df=df, dims=["bjd"], values=["amt"], kind={"amt": "simple"}, sources=["1-08"],
                 support=pd.DataFrame({"amt": [3, 50]}), support_only=["amt"])
    r = check(t)
    assert r.suppressed_mask["amt"].tolist() == [True, False]


def test_applied_with_small_support_hidden_but_applied_values_not_threshold_checked():
    df = pd.DataFrame({"bjd": ["A", "B", "C"], "growth": [3.2, -1.5, 8.0]})
    t = AggTable(key="g", title="g", df=df, dims=["bjd"], values=["growth"], kind={"growth": "applied"},
                 sources=["1-08"], support=pd.DataFrame({"growth": [100, 5, 200]}))
    r = check(t)
    assert r.suppressed_mask["growth"].tolist() == [False, True, False]  # 3.2%는 '10 미만'이어도 비율이므로 공개


def test_extract_blocked():
    t = _tbl([100] * 6)
    t.is_extract = True
    assert check(t).status == "blocked"


def test_mostly_suppressed_blocked():
    r = check(_tbl([1, 2, 3, 4, 50, 60], margins=["age"]))
    assert r.status == "blocked"


def test_public_column_not_checked_in_mixed_table():
    df = pd.DataFrame({"bjd": ["A", "B"], "store_cnt": [3, 40], "idx": [90.0, 110.0]})
    t = AggTable(key="m", title="m", df=df, dims=["bjd"], values=["store_cnt", "idx"],
                 kind={"store_cnt": "simple", "idx": "applied"}, sources=["hub:793", "1-08"],
                 col_sources={"store_cnt": ["hub:793"], "idx": ["1-08"]})
    r = check(t)
    assert r.status == "pass"


def test_llm_gate_excludes_blocked_and_rejects_raw():
    ok = check(_tbl([10, 20, 30, 40, 50, 60]))
    bad = check(_tbl([1, 2, 3, 4, 50, 60], margins=["age"]))
    g = facts_from_results([ok, bad])
    assert g.table_keys == ["t"] and g.blocked_keys == ["t"]
    with pytest.raises(GateViolation):
        facts_from_results([pd.DataFrame({"a": [1]})])


def test_suppressed_cells_never_reach_llm_text():
    r = check(_tbl([25, 4, 30, 12, 40, 55], margins=["age"]))
    text = facts_from_results([r]).text
    assert "비공개(<10)" in text and "| 4 |" not in text and "| 25 |" not in text


def test_number_grounding():
    ev = "| 123,456,789원 | 12.3% | 4,567명 |"
    assert unverified_numbers("매출 1.2억 원, 12.3% 증가, 4,567명", ev) == []
    assert unverified_numbers("매출 99.9점", ev) == ["99.9"]
