# 지표 → REPORT 대응과 리뷰 반영

`scripts/build_report.py`가 `out/metrics.json`에서 REPORT 객체를 만든다. 이 문서는 지표 쿼리가 REPORT의 어느 필드로 가는지, 판정이 어떤 규칙으로 정해지는지, 리뷰 결과를 어떻게 반영하는지 적는다.

```bash
python3 scripts/run_metrics.py --root . --out out/metrics.json
python3 scripts/build_report.py --metrics out/metrics.json --root . --meta config/report-meta.json --out out/report.html
python3 scripts/validate_report.py out/report.json
```

`build_report.py`는 같은 위치에 `report.json`(REPORT 객체)과 `report.html`을 쓴다. 검증은 내부에서도 한다.

## 판정의 두 층

1. **스크립트 판정.** 텔레메트리 지표와 `config/thresholds.csv`로 정한다. `[C]` 원문이나 `[S]` 코드 리뷰가 필요한 원인은 `후보`까지만 간다. 스크립트는 확인이 끝나지 않은 것을 `해당`으로 단정하지 않는다.
2. **리뷰 반영.** 사람 또는 Agent가 원문·코드를 확인한 뒤 `review.json`으로 판정을 바꾼다.

`해당`이 나오는 경우는 가이드가 지표만으로 "판정한다"고 한 원인이다. A1(변형 비율까지 확인), A2, A4, A5, A6(요청 집중), C1, D1(중복 호출까지 확인), D3, E4, F1, F4가 여기에 든다.

## 증상 S1–S12

| 증상 | 값 | 기준(`thresholds.csv`) | 값을 산출할 수 없을 때 |
| --- | --- | --- | --- |
| S1 캐시 달성률 | `A-common.agent_cache`에서 S_a가 있는 에이전트의 ΣActual ÷ ΣTheoretical | `cache_achievement` | `cache_read` 미수집 또는 S_a 없음 → nodata |
| S2 첫 호출 미스 | `A1.warm_first_miss`의 ΣMiss ÷ ΣWarmFirstCalls | `a1_warm_miss` | `cache_read` 미수집 → nodata |
| S3 턴 중간 | `A-common.overall.AchievementSecondPlus` | `cache_achievement` (같은 지표) | 위와 같다 |
| S4 고정 Input 비중 | `B-common.overall.FixedCostShare` | `b_fixed_share` | S_a 없음 → nodata |
| S5 턴 내 증가 | `C2.turn_growth.P50Multiple` 최악 에이전트 | `c2_multiple` | |
| S6 턴 간 증가 | `C1.cross_turn.CrossTurnMultiple` 최악 | `c1_multiple` | `conversation.id` 없음 → nodata |
| S7 꼬리 비용 | `D1.tail_turns.TailCostShare` 최악 | `d1_tail_share` | |
| S8 위임 시 전달 | `D4.delegation.P50PassRatio` 최악 | `d4_pass_ratio` | 위임 없음 또는 상위 S_a 없음 → nodata |
| S9 Reasoning | 단순 단계(유형 1–3) 대 최종 응답(유형 4) 비교 | 수치 기준 없음 | `reasoning` 미수집 → nodata |
| S10 응답 길이 | `E3.final_response.P95Visible` | `e3_target_tok`가 없으면 hold | |
| S11 상위 모델 | `E1.savings.SavingShare` | `e1_saving_share` | 소형 모델 단가 없음 → hold |
| S12 늦은 실패 | `F4.failed_turns.LateFailIndex` 최악 | `f4_late_fail` | |

증상 값은 **에이전트 중 가장 나쁜 값**이다. 가중 평균은 한 에이전트의 값을 다른 에이전트의 값과 평균하므로 그 에이전트가 기준을 넘어도 증상이 나오지 않을 수 있다. 표본이 `min_sample`보다 작은 에이전트는 뺀다.

## 원인 A1–F4

| 원인 | 입력 지표 | `해당` 또는 `후보` 조건 | 낭비 추정 |
| --- | --- | --- | --- |
| A1 | `A1.warm_first_miss`, `inturn_misses`, `sys_variants` | 첫 호출 미스율 ≥ `a1_warm_miss`. 변형 비율 ≥ `a1_variant_ratio`까지 만족하면 `해당`, 아니면 `후보` | 보존 시간 안 첫 호출 미스 + (턴 안 미스율도 높으면) 턴 안 미스 |
| A2 | `A2.tooldef_variants`, `variant_share`, `waste` | 원문 변형 > 이름 집합 변형, 또는 이름 집합 변형 ≥ 2 | 다른 정의를 쓴 첫 호출 |
| A3 | `A3.shrink`, `conv_call_profile` | 축소 이벤트·평상시 호출 ≥ `min_sample`이고 축소 직후 캐시율 < 평상시(단측 검정, `alpha`) → `후보` | 상한(공유 길이를 모름) |
| A4 | `A4.small_calls` | S_a < MinTok이고 실행 첫 호출의 95% 이상이 히트 0 | 캐시 불가 호출 비용(0이면 카드 없음) |
| A5 | `A5.gap_hit` | 보존 시간 밖 히트율 < 안 히트율(단측 검정, `alpha`) → `해당` | 구간별 계산 |
| A6 | `A6.rpm_hit`, `endpoint_hit` | 요청이 집중된 구간 히트율 < 기준 이하 구간(단측 검정, `alpha`) → `해당`, 엔드포인트 간 차이만 유의하면 `후보` | 요청이 집중된 구간 계산 |
| B1 | `B1.tool_use` | 사용률 < `b1_use_rate` → `후보` | 미사용 tool 토큰 × 호출 수 × P_eff |
| B2 | `B2.tool_schema_size` | 중앙값의 `b2_ratio`배 이상 → `후보` | 없음(`[S]` 리뷰) |
| B3 | `B3.system_prompt_size`, `B-common` | 고정 Input 비중 ≥ `b_fixed_share` → `후보` | 없음(`[S]` 리뷰) |
| B4 | `B4.agent_fixed_size` | 항상 `판정 보류`(공통 블록 비교는 원문이 필요) | 없음 |
| C1 | `C1.cross_turn`, `waste_baseline` | 배수 ≥ `c1_multiple` | 기준선(k = `c1_keep_turns`) |
| C2 | `C2.growth_steps`, `turn_growth`, `result_carry` | 호출당 증가율 p95 ≥ `c2_growth_rate` 또는 턴 내 배수 ≥ `c2_multiple` → `후보` | 재전송 비용 × (1 − `c2_needed_fraction`) |
| C3 | `C3.retrieval_quality`, `score_hist` | 저점수·중복 비율 ≥ `c3_low_or_dup` → `후보` | 검색 tool 재전송 비용 × 저점수·중복 비율 |
| D1 | `D1.tail_turns`, `dup_tool_calls`, `n_hist` | 꼬리 비중 ≥ `d1_tail_share` → `후보`. 꼬리 턴 중복률 > 나머지 턴(`dup_tail_vs_rest`, 단측 검정)이면 `해당` | 간이식(꼬리 초과 비용) |
| D2 | `D2.round_tools` | 순차 연쇄 턴이 있으면 `판정 보류`(수치 기준 없음). 근거에 비율과 상한을 적는다 | 상한(근거 문장에만) |
| D3 | `D3.short_output`, `output_diversity` | 짧은 출력 호출이 있으면 `판정 보류`(수치 기준 없음). 근거에 비용 비중·다양성을 적는다 | 짧은 출력 호출 비용(근거 문장에만) |
| D4 | `D4.delegation` | `P50PassRatio` ≥ `d4_pass_ratio` → `후보`. 상위 S_a가 없으면 `판정 보류` | 전달분 × (1 − `d4_needed_fraction`) |
| E1 | `E1.savings`, `savings_by_type` | 절감 비중 ≥ `e1_saving_share` → `후보` | 소형 모델 재계산 차이 |
| E2 | `E2.reasoning_by_type`, `waste` | 단순 단계가 최종 응답 단계와 같은 수준이거나 p50이 절반 이상 → `후보` | 저수준 기준선이 있을 때만 |
| E3 | `E3.final_response`, `waste_vs_target` | 목표 길이(`e3_target_tok`)가 없으면 `판정 보류` | 목표 길이가 있을 때만 |
| E4 | `E4.truncation` | 잘림 ≥ 2건이고 후속 호출 있음 | 잘린 호출 + 후속 호출 비용 |
| F1 | `F1.tool_failures`, `fail_vs_ok_turns`, `waste` | 실패 ≥ 2건인 tool이 있고 실패 턴 비용 중앙값이 더 크면 `해당` | 실패 턴 초과 비용 |
| F2 | `F2.llm_failures` | 실패 ≥ 2건 또는 재요청 추정 ≥ 2건 → `후보` | 실패 + 재요청 추정 비용 |
| F3 | `F3.quick_reinput`, `quick_pairs`, `waste` | 빠른 재입력 턴이 있으면 `판정 보류`(수치 기준 없음) | 직전 턴 비용([T] 근사, 근거 문장에만) |
| F4 | `F4.failed_turns` | 늦은 실패 계수 ≥ `f4_late_fail` | 실패 턴 비용 |

**게이트.** A 범주는 캐시 달성률이 `cache_achievement` 이상이면 `비해당`이다(가이드: 미만일 때 점검). B 범주는 고정 Input 비용 비중이 `b_fixed_share` 미만이면 `비해당`이다. A4는 게이트 없이 본다.

**데이터가 없을 때.** `cache_read` 없음 → A1·A3·A4·A5·A6 `계측 부재`. `reasoning` 없음 → E2 `계측 부재`. `conversation.id` 없음 → A3·C1·F3 `계측 부재`. S_a 없음 → A1·A4·A5·A6·B 범주 `미점검`. content 없음 → A2 `미점검`, A1은 `후보`로 낮춘다. tool 정의 크기 없음 → B 범주 `미점검`.

## review.json

`--review`로 넘긴다. 확인한 원인만 적는다.

```json
{
  "meta": { "customer": "고객사", "author": "홍길동", "date": "2026-11-10" },
  "summary": ["직접 쓴 요약 문장 1", "…"],
  "causes": {
    "A1": {
      "verdict": "해당",
      "finding": "System prompt 첫 줄 '현재 시각'이 호출마다 바뀐다 …",
      "differential": ["A2: ToolDefinitions 변형이 1종이다."],
      "waste": { "usd": 590, "formula": "…", "inputs": "…", "bound": false },
      "charts_add": [ { "type": "diff", "title": "…", "aTag": "호출 1", "bTag": "호출 2", "lines": [ { "same": "…" } ] } ]
    },
    "B1": { "verdict": "비해당", "basis": "escalate_to_human은 에스컬레이션에 필수인 tool이다" }
  }
}
```

| 키 | 효과 |
| --- | --- |
| `verdict`, `finding`, `basis`, `differential`, `rule` | 스크립트 값을 덮어쓴다 |
| `waste` | 객체면 덮어쓰고, `null`이면 카드를 없앤다 |
| `charts_add` | 스크립트가 만든 그림 뒤에 붙인다(예: 원문 diff, 재질문 쌍) |
| `charts` | 그림 전체를 바꾼다 |
| `summary` | 요약 문장 전체를 바꾼다 |
| `meta` | 머리말 필드를 덮어쓴다 |

**리뷰가 필요한 원인.** `후보`로 나온 원인은 `references/guide-common.md 와 references/catalog-*.md`의 "확인 방법" 항목대로 `[C]` 원문과 `[S]` 코드를 확인하고 `review.json`에 판정을 적는다. 확인 방법은 `content_by_span` 2차 추출(원문 diff, 사용자 메시지 쌍)과 코드 리뷰다.
