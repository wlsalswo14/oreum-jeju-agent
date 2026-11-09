"""LLM 전송 관문(LLM Gate).

외부 LLM API로 나가는 데이터는 오직 이 모듈이 만든 문자열뿐이다.
- 허용: 이용자 질문, 공개 명세 정보(컬럼명·설명), 반출 검사를 통과한 집계표(GuardResult.ok)
- 금지: 원본 행, 발췌, 반출 검사 미통과/차단 표
게시판 공식 답변(2026.10.29): "반출 불가 데이터의 원본 또는 원본 조회 결과를 외부 LLM API로 전달하는 것은 어렵다.
집계·가공된 결과값은 각 데이터별 반출 기준을 충족하는 범위에서 외부 전송이 가능."
"""
from __future__ import annotations

from dataclasses import dataclass

from .export_guard import GuardResult

MAX_ROWS_PER_TABLE = 40


class GateViolation(RuntimeError):
    pass


@dataclass
class GatedFacts:
    text: str
    table_keys: list[str]
    blocked_keys: list[str]


def facts_from_results(results: list[GuardResult], max_rows: int = MAX_ROWS_PER_TABLE) -> GatedFacts:
    """반출 기준을 통과한 표만 마크다운으로 직렬화."""
    parts: list[str] = []
    keys: list[str] = []
    blocked: list[str] = []
    for r in results:
        if not isinstance(r, GuardResult):
            raise GateViolation("GuardResult가 아닌 객체는 LLM에 보낼 수 없습니다.")
        if not r.ok:
            blocked.append(r.table.key)
            continue
        df = r.display_df(max_rows=max_rows)
        more = len(r.table.df) - len(df)
        parts.append(
            f"### [{r.table.key}] {r.table.title}\n"
            f"- 가공 방식: {r.method} / 반출 검사: {r.status}\n"
            + (f"- 비고: {r.table.note}\n" if r.table.note else "")
            + _md_table(df)
            + (f"\n(이하 {more}행 생략)" if more > 0 else "")
        )
        keys.append(r.table.key)
    return GatedFacts(text="\n\n".join(parts), table_keys=keys, blocked_keys=blocked)


def _md_table(df) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row.values) + " |")
    return "\n".join(lines)
