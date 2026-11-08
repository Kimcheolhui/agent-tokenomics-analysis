# REPORT 객체 스키마

보고서 HTML(`report/token-diagnosis-report.html`)은 `<script>` 안의 `REPORT` 객체 하나로 그려진다. 이 문서는 그 객체의 필드와 제약을 정의한다. 검증기는 `scripts/validate_report.py`이고, 이 문서와 같은 규칙을 코드로 가진다.

```bash
python3 scripts/validate_report.py out/report.json
python3 scripts/validate_report.py report/token-diagnosis-report.html
```

오류가 있으면 종료 코드 1이다. 경고는 종료 코드에 영향을 주지 않는다.

## 최상위

| 키 | 타입 | 설명 |
| --- | --- | --- |
| `meta` | 객체 | 머리말, 요약 문장, 비용·턴·호출 수, 단가 |
| `categories` | 객체 | 범주 A–F의 이름과 비용 식에서 키우는 항 |
| `symptoms` | 배열 12개 | 증상 S1–S12. 순서가 고정이다 |
| `causes` | 배열 25개 | 원인 A1–F4. 순서가 고정이다 |

## meta

| 키 | 타입 | 설명 |
| --- | --- | --- |
| `sample` | bool | true면 "예시 데이터" 안내가 붙는다. 실제 보고서는 false |
| `title`, `customer`, `service`, `project`, `appInsights`, `models`, `sources`, `author`, `date` | 문자열 | 머리말. `sources`는 사용한 테이블과 content 샘플링 여부, `date`는 작성일 |
| `period` | `{from, to, days}` | `from`·`to`는 `YYYY-MM-DD`, `days`는 정수 |
| `agents` | 문자열 배열 | 분석한 에이전트. 비어 있으면 안 된다 |
| `totalCost` | 숫자 | 분석 기간 전체 LLM 비용(USD) |
| `turns`, `calls` | 정수 | 턴 수, LLM 호출 수 |
| `cacheRate` | 숫자 0–1 | 전체 캐시율 = Σ CacheTok ÷ Σ InTok |
| `fixedInput` | 숫자 | 대표 에이전트의 고정 Input 토큰 S_a |
| `peff` | 숫자 | 실효 Input 단가(USD / 1M 토큰) |
| `prices` | 배열 | `{model, pin, pcache, pout}`. 모델별 단가 |
| `summary` | 문자열 배열 | 요약 문장. 1개 이상. 첫 문장이 가장 큰 원인을 말한다 |

## symptoms

| 키 | 타입 | 설명 |
| --- | --- | --- |
| `id` | 문자열 | `S1`–`S12` 순서 고정 |
| `name`, `metric` | 문자열 | 증상 이름, 증상을 확인하는 지표 이름 |
| `status` | 열거 | `abnormal` 기준을 넘음, `normal` 기준 안, `nodata` 지표를 산출하지 못함, `hold` 판정 보류 |
| `causes` | 문자열 배열 | 후보 원인 ID. 가이드 섹션 2의 매핑 |
| `value`, `threshold` | 숫자 | 수치 기준이 있을 때 |
| `dir` | 열거 | `higher` 클수록 나쁨, `lower` 작을수록 나쁨 |
| `fmt` | 열거 | `int`, `pct`, `ratio`, `tok`, `usd`, `x` |
| `display`, `thresholdText` | 문자열 | 수치 기준이 없는 증상(S9, S10)의 표시 문구 |
| `note` | 문자열 | `nodata`일 때 이유. 없으면 경고 |

`abnormal`과 `normal`은 (`value`, `threshold`, `dir`, `fmt`) 또는 (`display`, `thresholdText`)가 있어야 한다.

## causes

| 키 | 타입 | 설명 |
| --- | --- | --- |
| `id`, `name` | 문자열 | `A1`–`F4` 순서 고정 |
| `verdict` | 열거 | `해당`, `후보`, `비해당`, `계측 부재`, `판정 보류`, `미점검` |
| `rule` | 문자열 | 판정 규칙(가이드의 판정 기준) |
| `from` | 문자열 배열 | 이 원인을 발견한 증상 ID. 존재하는 증상이어야 한다 |
| `finding` | 문자열 | `해당`·`후보`일 때 필수. 무엇이 어떻게 관찰됐는지 |
| `waste` | 객체 | 낭비 추정. 아래 참고 |
| `differential` | 문자열 배열 | 감별. 다른 원인을 어떻게 배제했는지 |
| `basis` | 문자열 | 카드가 아닌 원인의 판정 근거. **카드가 아니면 필수** |
| `charts` | 배열 | 그림 1–2개. 그림 카탈로그에는 항상 나온다 |

**카드 조건.** `verdict`가 `해당` 또는 `후보`이고 `waste`가 있으면 3장에 카드가 된다. 카드는 낭비 추정이 큰 순으로 나온다. 카드가 아닌 원인은 `basis`가 4장 표에 나오므로 `basis`가 필요하다. `해당`·`후보`인데 `waste`가 없으면 경고한다.

### waste

| 키 | 타입 | 설명 |
| --- | --- | --- |
| `usd` | 숫자 ≥ 0 | 낭비 추정(USD). 분석 기간 전체 |
| `formula` | 문자열 | 가이드의 낭비 추정식 |
| `inputs` | 문자열 | 식에 넣은 값 |
| `bound` | bool | true면 "상한"으로 표시된다. 상한·기준선·간이식이면 true |

원인끼리 같은 토큰을 겹쳐 셀 수 있어 `usd`는 합산하지 않는다.

## charts

공통 키: `type`(필수), `title`(필수), `sub`, `wide`(bool), `fmt`. 그림 type별 필수 키는 아래와 같다. 괄호는 선택 키다.

| type | 필수 | 선택 | 쓰는 원인 |
| --- | --- | --- | --- |
| `hbar` | `fmt`, `rows[{label, value}]` | `threshold{value, dir(above\|below), label}`, `max`, `legend`, `labelWidth`, 행의 `aside`·`tone`·`tag` | A1, A4, B1, B2, C2, D3, D4, E4, F1, F2, F4 |
| `stackbar` | `fmt`, `series[{name}]`, `rows[{label, segs[]}]` | `labelWidth` | B4, E1 |
| `segbar` | `fmt`, `parts[{name, value}]` | | B3 |
| `columns` | `fmt`, `bins[]` | `markers[{at, label, kind}]`, `xLabel`, `xName`, `yMax`, `capLabels`, `note` | A3, A5, A6, C3, D1, D2, E3 |
| `line` | `fmt`, `points[{x(문자열), y}]` | `threshold{value, label}`, `vmarkers[{i, label}]`, `annotations`, `endLabel`, `xLabel`, `xName`, `note` | C1, C2 |
| `box` | `fmt`, `rows[{label, v[5]}]` | `note` | E2 |
| `diff` | `aTag`, `bTag`, `lines[]` | `meta` | A1 |
| `listdiff` | `aTag`, `bTag`, `a[]`, `b[]` | `note` | A2 |
| `pairs` | `pairs[{a, b}]` | 쌍의 `gap`, `sim` | F3 |

- **`columns`**는 두 형태다. 단일 시리즈는 `bins[{label, value}]`(선택 `n`, `tone`)이고, 누적은 `series[{name}]`와 `bins[{label, segs[]}]`다. `segs` 길이는 `series` 길이와 같아야 한다.
- **`box`**의 `v`는 `[최소, Q1, 중앙값, Q3, 최대]` 오름차순이다.
- **`diff`**의 `lines`는 `{same}`, `{a, b}`, `{more}` 중 하나다.
- `tone` 값은 `crit`, `quiet`, `v1` 등 템플릿 스타일이 정한 이름이다.

## 지표와의 연결

`scripts/build_report.py`가 `out/metrics.json`에서 이 객체를 만든다. 지표 쿼리와 REPORT 필드의 대응은 `references/build-mapping.md`에 있다.
