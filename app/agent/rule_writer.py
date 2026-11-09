"""규칙 기반(오프라인) 보고서 서술 — API 키가 없거나 AI 호출이 실패해도 보고서를 완성한다.

값이 있는 사실만 문장으로 만들고(빈칸·'-' 출력 없음), 모든 수치는 반출 검사를 통과한 KPI에서만 가져온다.
"""
from __future__ import annotations

import re


def _num(v) -> float:
    m = re.search(r"-?[\d,]+(?:\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else float("nan")


def write_rule(mode: str, title: str, kpis: list[dict], facts: dict, notes: list[str], synthetic: bool,
               params: dict, tables_present: set[str]) -> dict:
    k = {x["label"]: x["value"] for x in kpis}
    if "12개월 매출" in k and "최근 12개월 매출" not in k:   # 허브 자료(과거 기간)일 때의 표시명
        k["최근 12개월 매출"] = k["12개월 매출"]
    f = {a: b for a, b in facts.items() if not str(a).startswith("_") and b not in (None, "", "-")}

    def has(*names):
        return all((n in k) or (n in f) for n in names)

    def j(*parts):
        return " ".join(p for p in parts if p).replace(" ,", ",").replace(" .", ".")

    syn = "(시연용 가상 데이터 기반 결과)" if synthetic else ""
    findings: list[dict] = []
    recs: list[dict] = []

    if mode == "trade_area":
        region, ind = params.get("region", ""), params.get("industry", "")
        per = params.get("period") or ""
        per_txt = f"({per})" if per else ""
        head = j(f"{region} {ind}:",
                 f"12개월 매출{per_txt} {k['최근 12개월 매출']}" if has("최근 12개월 매출") else "",
                 f"(전년 대비 {k['전년 대비']})" if has("전년 대비") else "",
                 f"· 업종 내 {k['업종 내 매출 순위']}" if has("업종 내 매출 순위") else "")
        src_txt = ("제주데이터허브 공개 실데이터(카드 매출·유동인구 매쉬업, 상가정보)" if f.get("card_source")
                   else "카드 결제·경쟁 점포·유동인구·대중교통 데이터")
        summary = j(f"{region}의 {ind} 업종을 {src_txt}로 분석했습니다.",
                    f"분석 기간은 {per}입니다." if per else "",
                    f"하루 평균 매출이 가장 높은 요일은 {f['peak_week']}입니다." if has("peak_week") else "",
                    f"시간당 매출이 가장 높은 시간대는 {f['peak_time']}입니다." if has("peak_time") else "",
                    f"결제금액 기준 주 고객층은 {f['top_customer']}입니다." if has("top_customer") else "", syn)
        if "trade.monthly" in tables_present and has("최근 12개월 매출"):
            findings.append({"title": "매출 규모와 추세", "evidence": ["trade.monthly", "trade.kpi_s"], "detail": j(
                f"{per or '최근 12개월'} 카드 이용금액은 {k['최근 12개월 매출']}입니다.",
                f"전년 같은 기간 대비 증감률은 {k['전년 대비']}입니다." if has("전년 대비") else "",
                f"건당 결제액은 {k['건당 결제액']}입니다." if has("건당 결제액") else "")})
        if "trade.compare" in tables_present and has("업종 내 매출 순위"):
            findings.append({"title": "지역 간 비교", "evidence": ["trade.compare"], "detail":
                             f"같은 업종 매출이 발생한 {f.get('n_regions', '')}개 지역 중 {region}의 매출 순위는 "
                             f"{k['업종 내 매출 순위']}입니다. 지역별 매출지수·증감률·관광객 비중은 비교표를 참고하세요."})
        if "trade.week" in tables_present and has("peak_week"):
            findings.append({"title": "요일 패턴", "evidence": ["trade.week"], "detail": j(
                f"하루 평균 매출 지수가 가장 높은 요일은 {f['peak_week']}입니다.",
                f"주말(토·일) 매출 비중은 {k['주말 매출 비중']}입니다." if has("주말 매출 비중") else "")})
        if "trade.time" in tables_present and has("peak_time"):
            findings.append({"title": "시간대 패턴", "evidence": ["trade.time"], "detail":
                             f"시간당 매출이 가장 높은 시간대는 {f['peak_time']}입니다. 영업시간과 인력 배치를 이 시간대에 맞추는 것이 유리합니다."})
        if "trade.customer" in tables_present and has("top_customer"):
            findings.append({"title": "고객 구성", "evidence": ["trade.customer", "trade.demo"], "detail": j(
                f"결제금액 기준 주 고객은 {f['top_customer']}입니다.",
                f"관광객 결제 비중은 {k['관광객 결제 비중']}입니다." if has("관광객 결제 비중") else "",
                f"연령·성별로는 {f['top_demo']}의 비중이 가장 큽니다." if has("top_demo") else "")})
        if "trade.population" in tables_present and has("peak_visit_hour"):
            findings.append({"title": "유동인구", "evidence": ["trade.population"], "detail":
                             f"주말 방문인구가 가장 많은 시각은 {f['peak_visit_hour']}시 무렵입니다. 영업시간을 정할 때 참고하세요."})
        if "trade.pop583" in tables_present:
            findings.append({"title": "최근 방문인구", "evidence": ["trade.pop583", "trade.pop583_kpi", "trade.pop_age"], "detail": j(
                f"허브 Open API 일별 유동인구({f['pop583_period']}) 기준" if has("pop583_period") else "",
                f"{region} 방문인구는 전년 대비 {k['최근 방문인구 증감']} 변했고," if has("최근 방문인구 증감") else "",
                f"주말 방문인구는 평일의 {k['주말/평일 방문인구']}입니다." if has("주말/평일 방문인구") else "",
                f"방문객 중 가장 많은 연령대는 {f['pop583_top_age']}입니다." if has("pop583_top_age") else "")})
        if "trade.pop748" in tables_present or "trade.per_visit" in tables_present:
            v = f.get("visit748_yoy")
            pv = _num(k.get("방문인구 대비 매출 지수", ""))
            trend = ""
            if isinstance(v, (int, float)):
                trend = "최근 12개월 방문인구는 그 전 12개월보다 " + ("늘었습니다." if v > 0 else "줄었습니다.")
            conv = ""
            if has("방문인구 대비 매출 지수", "per_visit_rank"):
                conv = (f"방문인구 대비 {ind} 매출 지수는 {k['방문인구 대비 매출 지수']}(행정동 평균=100, {f['per_visit_rank']})로, "
                        + ("방문객 1명이 이 업종에 쓰는 돈이 평균보다 많습니다." if pv >= 100
                           else "방문객은 많지만 이 업종 지출로 이어지는 비율은 평균보다 낮습니다."))
            findings.append({"title": "방문객 1명당 소비", "evidence": ["trade.per_visit", "trade.pop748"],
                             "detail": j(trend if "trade.pop748" in tables_present else "", conv)})
        if "trade.competition" in tables_present and has("경쟁 점포 수"):
            findings.append({"title": "경쟁 환경", "evidence": ["trade.competition"], "detail":
                             f"제주데이터허브 상가정보 기준 {region}의 동종 점포는 {k['경쟁 점포 수']}입니다. "
                             "점포당 매출지수로 경쟁 강도를 함께 확인하세요."})
        if "trade.index" in tables_present and has("상권 매력도 순위"):
            findings.append({"title": "상권 매력도", "evidence": ["trade.index"], "detail":
                             f"시장규모·성장성·점포당 매출·관광 수요를 종합한 상권 매력도는 {f.get('n_index_regions', '')}개 지역 중 "
                             f"{k['상권 매력도 순위']}입니다."})
        if has("peak_week") and has("peak_time"):
            recs.append({"title": "피크 요일·시간 집중 운영", "detail": f"{f['peak_week']}과 {f['peak_time']}에 인력과 재고를 집중하세요."})
        elif has("peak_week") or has("peak_time"):
            recs.append({"title": "피크 시간 집중 운영", "detail": f"{f.get('peak_week') or f.get('peak_time')}에 인력과 재고를 집중하세요."})
        if has("top_customer"):
            recs.append({"title": "주 고객 맞춤 상품", "detail": f"{f['top_customer']} 대상 메뉴·가격·홍보 채널을 우선 설계하세요."})
        recs.append({"title": "경쟁 밀도 확인 후 입지 확정", "detail": "점포당 매출지수가 평균 이상인 인접 법정동도 후보로 함께 비교하세요."})
        if has("방문인구 대비 매출 지수") and _num(k["방문인구 대비 매출 지수"]) < 100:
            recs.append({"title": "지나가는 방문객을 손님으로", "detail": "방문객은 많지만 업종 지출 전환이 낮습니다. 관광 동선 위 입지·간판·포장 메뉴로 전환율을 높이세요."})
        if not (has("peak_week") and has("peak_time")):
            recs.append({"title": "센터 데이터로 최신화", "detail": "센터에서 최신 카드(1-08)와 요일별·시간대(1-12·1-14)를 넣으면 최근 추세와 영업시간·인력 계획까지 구체화됩니다."})
        business = j(
            "[시장 분석]",
            f"카드 이용 현황 분석 결과 {region} {ind} 업종의 {per or '최근 12개월'} 카드 결제액은 {k['최근 12개월 매출']}"
            + (f"(전년 대비 {k['전년 대비']})" if has("전년 대비") else "") + "로 나타났다." if has("최근 12개월 매출") else "",
            f"매출은 {f['peak_week']}에 가장 높고," if has("peak_week") else "",
            f"{f['peak_time']}의 시간당 매출이 가장 크다." if has("peak_time") else "",
            f"주 고객은 {f['top_customer']}이며" if has("top_customer") else "",
            f"관광객 결제 비중은 {k['관광객 결제 비중']}이다." if has("관광객 결제 비중") else "",
            f"동종 점포는 {k['경쟁 점포 수']}이고," if has("경쟁 점포 수") else "",
            f"업종 내 상권 매력도 순위는 {k['상권 매력도 순위']}이다." if has("상권 매력도 순위") else "",
            ("이에 따라 피크 시간대 중심 운영과 주 고객 맞춤 상품 구성을 핵심 사업 전략으로 삼는다." if (has("peak_week") or has("peak_time"))
             else "이에 따라 주 고객 맞춤 상품 구성과 관광 동선 중심 입지를 핵심 사업 전략으로 삼는다."))

    elif mode == "event_impact":
        ev = params.get("event", "")
        head = j(f"{ev}:",
                 f"행사기간 방문인구 {k['방문인구 증가율']}" if has("방문인구 증가율") else "",
                 f"· 순효과 {k['순효과(이중차분)']}" if has("순효과(이중차분)") else "",
                 f"· 인근 버스 승차 {k['인근 버스 승차 증가율']}" if has("인근 버스 승차 증가율") else "")
        parts = [n for t, n in (("event.daily", "방문인구"), ("event.bus", "인근 버스 승차"), ("event.card", "카드 소비"))
                 if t in tables_present] or ["지역 지표"]
        summary = j(f"{ev} 기간({params.get('start')}~{params.get('end')}) {params.get('region')}의 {'·'.join(parts)} "
                    "변화를 직전 3주 같은 요일 및 비교지역과 대조했습니다.", syn)
        if "event.daily" in tables_present and has("방문인구 증가율"):
            findings.append({"title": "방문인구 증가", "evidence": ["event.daily", "event.lift"], "detail": j(
                f"행사기간 일평균 방문인구는 직전 3주 같은 요일 평균 대비 {k['방문인구 증가율']} 변했습니다.",
                f"전년 같은 기간과 비교하면 {k['전년 대비 방문인구']} 변했습니다." if has("전년 대비 방문인구") else "")})
        if "event.did" in tables_present and has("순효과(이중차분)"):
            findings.append({"title": "비교지역 대비 순효과", "evidence": ["event.did", "event.lift"], "detail":
                             f"같은 기간 비교지역({params.get('control') or '이웃 읍면'})의 변화를 뺀 순효과(이중차분)는 "
                             f"{k['순효과(이중차분)']}입니다. 계절·날씨 같은 공통 요인을 덜어낸 값입니다."})
        if "event.age" in tables_present and has("age_up") and not has("peak_lift_hour"):
            findings.append({"title": "방문객 구성", "evidence": ["event.age"], "detail":
                             f"평시 같은 요일과 비교해 행사기간에 비중이 가장 크게 늘어난 연령대는 {f['age_up']}입니다."})
        if "event.hourly" in tables_present and has("peak_lift_hour"):
            findings.append({"title": "붐비는 시간대", "evidence": ["event.hourly", "event.age"], "detail": j(
                f"평시 같은 요일 대비 방문인구 증가가 가장 큰 시각은 {f['peak_lift_hour']}시입니다.",
                f"연령대별로는 {f['age_up']}의 비중이 가장 크게 늘었습니다." if has("age_up") else "")})
        if "event.bus" in tables_present and has("인근 버스 승차 증가율"):
            findings.append({"title": "대중교통 이용", "evidence": ["event.bus", "event.bus_lift", "event.bus_hour"], "detail": j(
                f"행사장 인근 정류소 승차는 평시 같은 요일 대비 {k['인근 버스 승차 증가율']} 변했습니다.",
                f"추가 승차가 가장 많은 시각은 {f['bus_peak_hour']}시입니다." if has("bus_peak_hour") else "")})
        if "event.card" in tables_present:
            findings.append({"title": "지역 소비 효과(월 단위)", "evidence": ["event.card", "event.card_kpi", "event.tourist"], "detail": j(
                f"전년 동월 대비 증감률에서 비교지역 변화를 뺀 순효과가 가장 큰 업종은 {f['card_top_biz']}입니다." if has("card_top_biz") else "",
                f"비씨카드 결제 기준 추정 추가 소비액은 {k['추정 추가 소비액(비씨카드)']}입니다." if has("추정 추가 소비액(비씨카드)") else "",
                "카드 데이터는 월 단위여서 며칠짜리 행사의 효과가 희석된 보수적 추정입니다.")})
        if "event.weather" in tables_present and "rainy_days" in f:
            findings.append({"title": "날씨", "evidence": ["event.weather"], "detail":
                             f"행사 기간 중 강수 5mm 이상인 날은 {f['rainy_days']}일입니다."})
        recs = [
            {"title": "피크 시간 셔틀·증편", "detail": (f"{f['bus_peak_hour']}시 전후" if has("bus_peak_hour") else "피크 시간대")
             + " 버스 증편이나 행사장 셔틀 운행을 검토하세요."},
            {"title": "수혜 업종 연계 프로모션", "detail": (f"{f['card_top_biz']}" if has("card_top_biz") else "수혜 업종")
             + " 중심으로 지역 상권 쿠폰·맛집 지도를 함께 배포하세요."},
            {"title": "매년 같은 방법으로 평가", "detail": "행사 전후 3주와 비교지역을 미리 정해 두고 같은 방법으로 해마다 효과를 추적하세요."},
        ]
        business = j(
            "[정책 보고 요약]",
            f"{ev}({params.get('start')}~{params.get('end')}) 기간 {params.get('region')} 방문인구는 평시 대비 {k['방문인구 증가율']} 변했고,"
            if has("방문인구 증가율") else "",
            f"비교지역 변화를 제외한 순효과는 {k['순효과(이중차분)']}로 추정된다." if has("순효과(이중차분)") else "",
            f"행사장 인근 버스 승차는 {k['인근 버스 승차 증가율']} 변했다." if has("인근 버스 승차 증가율") else "",
            f"카드 결제 기준 추정 추가 소비액은 {k['추정 추가 소비액(비씨카드)']}이다." if has("추정 추가 소비액(비씨카드)") else "",
            "향후 피크 시간대 교통 대책과 수혜 업종 연계 프로그램을 보완할 필요가 있다.")

    elif "isle.kpi" in tables_present:
        per = params.get("period", "")
        head = j("제주 전역 버스:", f"일평균 승차 {k['일평균 승차']}" if has("일평균 승차") else "",
                 f"· 전년 같은 달 대비 {k['전년 같은 달 대비']}" if has("전년 같은 달 대비") else "")
        summary = j(f"제주데이터허브 공개 실데이터(일별 시간대별 버스 승차, {per})로 제주 전역 버스 이용 패턴을 진단했습니다.",
                    f"평일 승차가 가장 몰리는 시간대는 {f['peak_band']}입니다." if has("peak_band") else "")
        findings = [
            {"title": "이용 규모와 추세", "evidence": ["isle.monthly", "isle.kpi"], "detail": j(
                f"최근 12개월 일평균 승차는 {k['일평균 승차']}입니다." if has("일평균 승차") else "",
                f"전년 같은 달과 비교하면 {k['전년 같은 달 대비']} 변했습니다({f['isle_yoy_months']} 기준)."
                if has("전년 같은 달 대비", "isle_yoy_months") else "")},
            {"title": "시간대 집중", "evidence": ["isle.band", "isle.heat"], "detail": j(
                f"평일 시간당 승차가 가장 많은 시간대는 {f['peak_band']}이며," if has("peak_band") else "",
                f"평일 시간대 평균의 {k['평일 최다 시간대 집중도']}% 수준입니다." if has("평일 최다 시간대 집중도") else "",
                f"주말 일평균 승차는 평일의 {k['주말/평일 비율']}입니다." if has("주말/평일 비율") else "")},
            {"title": "이용자 구성", "evidence": ["isle.users"], "detail": j(
                f"경로(만 70세 이상) 승차 비중은 {k['경로 승차 비중']}입니다." if has("경로 승차 비중") else "",
                "어르신 이용이 많은 시간대에는 저상버스·정류소 편의시설 우선 배치를 검토할 수 있습니다.")},
        ]
        findings = [x for x in findings if x["detail"]]
        recs = [
            {"title": "첨두 시간대 배차 보강", "detail": (f"평일 {f['peak_band']}" if has("peak_band") else "평일 첨두 시간대")
             + " 배차 간격 단축·예비차 투입을 우선 검토하세요."},
            {"title": "어르신 이용 편의", "detail": "경로 승차 비중이 높은 시간대에 저상버스·정류소 그늘막·실시간 안내를 우선 배치하세요."},
            {"title": "정류소 단위로 좁혀 보기", "detail": "센터 3-03·3-04(또는 허브 Open API 612·613)를 넣으면 지역·정류소별 저이용·과밀 정류소까지 진단합니다."},
        ]
        business = j(
            "[개선 검토 메모]",
            f"제주 전역 버스 일평균 승차는 {k['일평균 승차']}이며," if has("일평균 승차") else "",
            f"전년 같은 달 대비 {k['전년 같은 달 대비']} 변했다." if has("전년 같은 달 대비") else "",
            f"평일에는 {f['peak_band']}에 승차가 가장 몰리므로 이 시간대 배차 보강을 우선 검토한다." if has("peak_band") else "")

    else:
        region = params.get("region", "")
        head = j(f"{region} 버스:", f"일평균 승차 {k['일평균 승차']}" if has("일평균 승차") else "",
                 f"· 점검 후보 정류소 {k['점검 후보 정류소']}" if has("점검 후보 정류소") else "")
        hourly = "transit.hourly" in tables_present
        summary = j(f"{region}의 정류소별 {'승하차를 요일·시간대별로' if hourly else '승차를 일 단위로'} 분석했습니다"
                    + (f"({params.get('period')}, 제주데이터허브 Open API 612·613)." if f.get("stop_src_hub") else "."),
                    f"가장 붐비는 정류소는 {f['busiest_stop']}입니다." if has("busiest_stop") else "",
                    f"평일 첨두 시각은 {f['peak_hour']}시입니다." if has("peak_hour") else "", syn)
        findings = [
            {"title": "이용 규모", "evidence": ["transit.kpi", "transit.top"], "detail": j(
                f"분석 정류소 {f.get('n_stops', '')}곳의 일평균 승차는 {k['일평균 승차']}입니다." if has("일평균 승차") else "",
                f"가장 붐비는 정류소는 {f['busiest_stop']}입니다." if has("busiest_stop") else "")},
            ({"title": "첨두 집중", "evidence": ["transit.hourly", "transit.heat"], "detail": j(
                f"평일 첨두 1시간 집중률은 {k['평일 첨두 집중률']}입니다." if has("평일 첨두 집중률") else "",
                f"주말/평일 승차 비율은 {k['주말/평일 비율']}입니다." if has("주말/평일 비율") else "")} if hourly else
             {"title": "요일 패턴", "evidence": ["transit.dow", "ctx.band"], "detail": j(
                f"주말 일평균 승차는 평일의 {k['주말/평일 비율']}입니다." if has("주말/평일 비율") else "",
                "시간대 패턴은 정류소 단위 시간 자료가 없어 제주 전역(허브 512) 그림으로 참고하고, 센터 3-04를 넣으면 이 지역 시간대까지 봅니다."
                if "ctx.band" in tables_present else "")}),
        ]
        if "transit.low" in tables_present:
            findings.append({"title": "점검 후보 정류소", "evidence": ["transit.low"], "detail": (
                f"이용 하위 정류소 중 {f['n_review_stops']}곳은 일평균 승차 5명 미만이거나 지역 중앙값의 25%에도 못 미쳐 "
                "위치·안내·접근성 점검 후보입니다." if f.get("n_review_stops")
                else "뚜렷한 저이용 정류소는 없습니다. 이용 하위 정류소 목록으로 위치·안내를 점검하세요.")})
        if "transit.users" in tables_present:
            findings.append({"title": "이용자 구성", "evidence": ["transit.users"], "detail": "이용자 구분(일반·경로·청소년 등)별 승차 비중은 그림을 참고하세요."})
        if "transit.routes" in tables_present:
            findings.append({"title": "노선", "evidence": ["transit.routes"], "detail": "노선별 일평균 승차 순위는 노선 표를 참고하세요."})
        findings = [x for x in findings if x["detail"]]
        recs = [
            ({"title": "첨두 시간 배차 조정", "detail": (f"평일 {f['peak_hour']}시 전후" if has("peak_hour") else "첨두 시간대")
             + " 배차 간격을 줄이는 방안을 검토하세요."} if hourly else
             {"title": "요일별 배차 점검", "detail": "평일과 주말 수요 차이에 맞춰 주말 배차를 조정하고, 시간대는 센터 3-04로 확인하세요."}),
            {"title": "저이용 정류소 점검", "detail": "저이용 정류소는 위치·접근성·안내 표지를 현장 점검하고 인접 정류소와의 통합 여부를 검토하세요."},
            {"title": "과밀 정류소 편의시설", "detail": (f"{f['busiest_stop']}" if has("busiest_stop") else "최다 이용 정류소")
             + "에 대기 공간·실시간 도착 안내를 우선 보강하세요."},
        ]
        business = j(
            "[개선 검토 메모]",
            f"{region} 정류소의 일평균 승차는 {k['일평균 승차']}이며," if has("일평균 승차") else "",
            f"평일 첨두 1시간 집중률은 {k['평일 첨두 집중률']}이다." if has("평일 첨두 집중률") else "",
            f"이용이 매우 적은 점검 후보 정류소가 {k['점검 후보 정류소']} 확인되어 위치 조정·통합 검토가 필요하다."
            if f.get("n_review_stops") else "",
            "최다 이용 정류소에는 대기 편의시설 보강을 우선 추진한다.")

    tp = set(tables_present)
    limitations = []
    if any(t.startswith(("trade.monthly", "trade.kpi", "event.card")) for t in tp):
        limitations.append("카드 데이터는 비씨카드 결제분으로 전체 소비를 대표하지 않습니다.")
    if f.get("card_source"):
        limitations.append("허브 카드 매출 매쉬업은 2017.01~2021.05 자료(코로나19 기간 포함)라 현재 상권과 다를 수 있습니다. "
                           "센터 최신 카드 데이터(1-08)로 다시 분석하면 최신화됩니다.")
    if any(t.startswith(("trade.population", "trade.pop748", "trade.per_visit", "event.daily", "event.did")) for t in tp):
        limitations.append("유동인구는 통신 신호 기반 추정치입니다(허브 자료는 측정값 합계를 환산한 값).")
    if any(t.startswith("isle.") for t in tp):
        limitations.append("허브 512는 제주 전역 합계 자료라 지역·정류소·노선별 차이는 보이지 않으며, 집계 누락 의심일은 제외했습니다.")
    if f.get("stop_src_hub"):
        limitations.append("허브 613(정류소별 일 이용)은 승차·환승만 있고 하차·시간대가 없으며, 정류소는 이름으로 612와 연결했습니다(승차 기준 약 97% 일치).")
    if "trade.competition" in tp:
        limitations.append("상가정보는 2025.09 기준이라 카드 매출 기간과 시점이 다릅니다(점포 수는 현재 경쟁 강도로 해석).")
    limitations.append("상관관계 분석이므로 인과로 해석할 때 주의가 필요합니다.")
    return {"headline": head, "summary": summary, "findings": findings, "recommendations": recs,
            "business_text": business, "limitations": limitations,
            "export_purpose": f"{title} 결과를 보고서·사업계획 수립에 활용하기 위함(비영리 내부 검토)."}
