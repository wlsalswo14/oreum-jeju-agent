"""요일·공휴일 달력 보조 (요일별 '하루 평균' 지수 계산용)."""
from __future__ import annotations

import calendar
from datetime import date

# 관공서 공휴일(대체·임시공휴일 포함). 요일 정규화용 근사치이며 분석 결과의 핵심 수치에는 영향이 작다.
HOLIDAYS = {
    2023: ["0101", "0121", "0122", "0123", "0124", "0301", "0505", "0527", "0529", "0606", "0815",
           "0928", "0929", "0930", "1002", "1003", "1009", "1225"],
    2024: ["0101", "0209", "0210", "0211", "0212", "0301", "0410", "0505", "0506", "0515", "0606",
           "0815", "0916", "0917", "0918", "1001", "1003", "1009", "1225"],
    2025: ["0101", "0127", "0128", "0129", "0130", "0301", "0303", "0505", "0506", "0603", "0606",
           "0815", "1003", "1005", "1006", "1007", "1008", "1009", "1225"],
    2026: ["0101", "0216", "0217", "0218", "0301", "0302", "0505", "0524", "0525", "0603", "0606",
           "0815", "0817", "0924", "0925", "0926", "1003", "1005", "1009", "1225"],
}
WEEK_NAMES = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def holiday_dates(year: int) -> set[date]:
    return {date(year, int(s[:2]), int(s[2:])) for s in HOLIDAYS.get(year, [])}


def day_counts(months: list[int]) -> dict[str, int]:
    """주어진 연월(YYYYMM) 목록에 포함된 요일별 날짜 수 + 공휴일 수."""
    out = {w: 0 for w in WEEK_NAMES}
    out["공휴일"] = 0
    for ym in months:
        y, m = divmod(int(ym), 100)
        hol = holiday_dates(y)
        for d in range(1, calendar.monthrange(y, m)[1] + 1):
            dt = date(y, m, d)
            out[WEEK_NAMES[dt.weekday()]] += 1
            if dt in hol:
                out["공휴일"] += 1
    return out


def is_weekend_or_holiday(dt: date) -> bool:
    return dt.weekday() >= 5 or dt in holiday_dates(dt.year)
