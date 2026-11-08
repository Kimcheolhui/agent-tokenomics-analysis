---
name: agent-tokenomics-diagnosis
description: Microsoft Foundry·Azure OpenAI 기반 Agent의 토큰 비용 문제를 Application Insights(Log Analytics) 텔레메트리로 진단하고, 원인 25개의 낭비 추정이 담긴 보고서 HTML을 만든다. 고객 Agent의 토큰 비용 최적화 현황 분석, 비용이 왜 큰지 진단, 캐시 히트율이 낮은 이유, prompt caching 미스, gen_ai 트레이스와 AppGenAIContent 분석, 토큰 비용 진단 보고서 작성을 요청받으면 이 skill을 쓴다. 개선안 제시는 범위 밖이고 원인 파악과 크기 추정까지만 한다.
---

# Agent 토큰 비용 진단

Foundry 프로젝트에 연결된 Application Insights 텔레메트리로 Agent의 토큰 비용 문제를 찾고 크기를 추정해 보고서를 만든다. 조치(개선안)는 이 단계에서 다루지 않는다.

## 범위

- **Azure OpenAI(OpenAI 계열) 모델만** 다룬다. Anthropic 모델은 제외한다.
- 원인 25개(A1–F4)를 6개 범주로 나눈다: A Prefix 변동(캐시 미스), B 고정 Input 비대, C 컨텍스트 누적, D 호출 구조, E 모델·출력 설정, F 실패 낭비.
- 쿼리를 매번 Log Analytics에서 실행하지 않는다. CSV로 한 번 내려 로컬(DuckDB)에서 산출한다.
- 모든 수치 기준은 **초기값**이다. 고객 데이터를 보고 `config/thresholds.csv`에서 보정한다.

## 준비물

| 항목 | 용도 | 확인 |
| --- | --- | --- |
| Python 3.9+ | 모든 스크립트 | `python3 --version` |
| `duckdb` 패키지 | 지표 산출 | `pip install duckdb` |
| Azure CLI | 스크립트로 추출할 때만. 없으면 포털에서 수동 추출 | `az account show` |
| Node.js | 선택. 템플릿의 JS 객체 리터럴을 읽을 때만 | `node --version` |

작업 폴더는 이 skill 폴더가 아니라 고객 진단용 별도 폴더다. `data/`, `config/`, `out/`이 그 안에 생긴다.

## 작업 흐름

각 단계가 끝나면 사용자에게 한두 문장으로 상태를 알린다. 스크립트는 이 skill 폴더의 `scripts/`에 있고, `--root`로 작업 폴더를 준다.

### 0. 작업 폴더와 설정

`assets` 역할의 `config/`를 작업 폴더로 복사해 고객 값을 채운다.

- `config/model-params.csv`: 고객이 쓰는 모델의 단가와 캐시 파라미터. `Model`은 `gen_ai.response.model` 값과 정확히 같아야 한다.
- `config/agents.csv`: 에이전트별 고정 Input 토큰 S_a. 3단계에서 채운다.
- `config/thresholds.csv`: 판정 기준과 정의용 매개변수. 먼저 기본값으로 시작한다.
- `config/report-meta.json`: 고객명, 서비스, 프로젝트, 작성자 등 머리말.

### 1. 사전 점검

`references/guide-common.md`의 "사전 점검" 표(P1–P9)를 따른다. 점검이 실패한 항목은 영향받는 원인이 `계측 부재`가 되므로 먼저 사용자에게 알린다.

`queries/export/00-precheck-keys.kql`을 포털 Logs에서 실행해 `cache_read`, `reasoning`, `conversation.id` 키가 쌓이는지 본다. **턴 1개 = trace 1개** 가정은 가이드의 "턴·trace 점검" 쿼리로 확인한다. 이 가정이 깨지면 턴 단위 지표가 틀어지므로 진단 범위를 조정한다.

### 2. 추출

```bash
python3 <skill>/scripts/export.py --workspace <GUID> --from 2026-10-20 --to 2026-11-03 --out data
```

- 하루 단위 파일로 `data/<name>/<YYYY-MM-DD>.csv`를 만든다. 이미 있는 날은 건너뛰고, 실패한 날만 다시 돌린다.
- 기본 추출에는 고객 프롬프트 원문이 없다. 해시와 길이만 내린다.
- `az` 권한이 없으면 `queries/export/*.kql`을 포털에 붙여 넣고 CSV로 내보내 같은 폴더 구조에 둔다. 스키마는 `references/data-contract.md`다.
- 하루 분량이 10만 행을 넘으면(포털) 또는 50만 행에 가까우면(API) 스크립트가 경고한다. 그 날을 시간 단위로 쪼갠다.
- **원문이 들어가는 추출**(`system_prompts`, `content_by_span`)은 `--include-sensitive`가 있어야 돌아간다. 고객 콘텐츠가 로컬에 남으므로 사용자 확인 없이 실행하지 않는다.

### 3. S_a 측정

A 범주 지표 대부분과 B 공통 지표가 S_a를 입력으로 받는다. `references/catalog-B.md`의 "B 공통: S_a 측정법"을 따른다.

1. 실험 호출(가장 정확): 고객 배포에 직접 요청을 보내야 하므로 사용자에게 요청한다.
2. 원문 토큰화: `system_prompts` 추출 + tiktoken `o200k_base`.
3. 하한 근사: 지표 `B-common.first_call_dist`의 P01. 낮게 나오므로 낭비 추정이 낮아진다. 보고서에 근사값임을 밝힌다.

결과를 `config/agents.csv`에 `Method`와 함께 적는다. S_a가 없으면 A1·A4·A5·A6·B 범주는 `미점검`이다.

### 4. 지표 산출

```bash
python3 <skill>/scripts/run_metrics.py --root . --out out/metrics.json
```

실행 뒤 `out/metrics.json`의 `_checks`를 먼저 본다.

| 항목 | 0이 아니면 |
| --- | --- |
| `unpriced_calls` | 단가가 매칭되지 않은 모델. 그 호출의 `Cost`가 비용 합계에서 빠진다. `model-params.csv`를 고친다 |
| `missing_sa` | S_a가 없는 에이전트 |
| `instrumentation` | 값이 0인 키는 계측 부재 |

쿼리 오류가 있으면 스크립트가 에러로 알린다. 오류 쿼리는 해당 원인을 `미점검`으로 만든다.

### 5. 보고서 초안

```bash
python3 <skill>/scripts/build_report.py --metrics out/metrics.json --root . \
    --meta config/report-meta.json --out out/report.html
```

스크립트는 증상 12개와 원인 25개를 판정하고 낭비 추정이 큰 순으로 요약한다. 스크립트는 확인이 끝나지 않은 원인을 `해당`으로 단정하지 않고 `후보`까지만 올린다. 대응 규칙은 `references/build-mapping.md`다.

### 6. 후보 확인

`후보`와 `판정 보류`는 원문·코드 확인이 필요하다. 이상 증상이 걸린 범주의 `references/catalog-<범주>.md`만 읽는다. 전체를 읽지 않는다. 확인 절차는 아래 표를 따른다.

| 원인 | 확인할 것 | 방법 |
| --- | --- | --- |
| A1 | System prompt 변형 2건의 첫 차이 | `content_by_span`으로 두 SpanId 추출 → 첫 차이 위치 확인. `diff` 그림 추가 |
| A2 | Tool 정의 순서·내용 변형 | `A2.variant_share`의 ToolNames로 `listdiff` 그림. 코드는 [S] |
| A3 | 연속 호출의 InputMessages 비교 | `content_by_span` 2건 |
| B1 | 미사용 tool이 필수(예외 처리, 에스컬레이션)인가 | [S]. 필수면 `비해당`으로 내린다 |
| B2·B3·B4 | 축약 가능 토큰, 구성 요소, 공통 블록 | [S] 리뷰 + `system_prompts` 토큰화 |
| C2 | tool 결과 중 응답에 쓰인 부분의 비율 | `content_by_span` 샘플 → `c2_needed_fraction` |
| C3 | top_k, chunk 크기, 점수 임계값 | [S] |
| D1 | 프레임워크의 최대 반복 횟수 | [S]. 같은 호출 수의 턴이 있으면 확정 |
| D2 | 연속 tool이 서로 결과를 인자로 쓰는가 | `content_by_span` 인자 확인 |
| D4 | 전달된 메시지가 하위 역할에 필요한가 | `content_by_span` → `d4_needed_fraction` |
| E1 | 소형 모델의 품질 유지 | 진단 범위 밖. `후보`로 기록 |
| E2 | reasoning effort 설정 | [S]. 낮은 수준으로 소수 샘플 재실행 |
| E3 | 고객의 목표 응답 길이 | 고객에게 확인 → `e3_target_tok` |
| F3 | 연속 사용자 메시지의 임베딩 유사도 ≥ 0.85 | `F3.quick_pairs`의 SpanId로 `content_by_span` → 로컬에서 계산 |

확인 결과는 `out/review.json`에 적는다(형식은 `references/build-mapping.md`). 확인하지 못한 항목은 `후보`로 둔다.

### 7. 최종 보고서

```bash
python3 <skill>/scripts/build_report.py --metrics out/metrics.json --root . \
    --meta config/report-meta.json --review out/review.json --out out/report.html
python3 <skill>/scripts/validate_report.py out/report.json
```

검증을 통과한 `out/report.html`을 사용자에게 준다. 사용자에게는 결과를 세 가지로 요약한다: 비용과 낭비가 큰 원인 상위 3개, 계측 부재·미점검 목록, 사용자가 해야 할 일(S_a 실험 호출, 목표 길이 확인 등).

## 증상 → 후보 원인

관찰된 증상에서 시작해 후보 원인으로 좁힌다. 한 증상에 원인이 여러 개인 경우가 많으므로 후보를 모두 확인한 뒤 각 항목의 판정 기준으로 감별한다.

| 증상 | 지표 | 후보 원인 |
| --- | --- | --- |
| 캐시 달성률이 낮다 | 캐시 달성률 | A1–A6 |
| 턴 첫 호출의 캐시 미스가 잦다 | 보존 시간 내 첫 호출 미스율 | A1, A2, A4, A5, A6 |
| 턴 중간에 캐시율이 떨어진다 | 호출 순번별 캐시 달성률 | A3, A2 |
| 고정 Input의 비용 비중이 크다 | 고정 Input 비용 비중 | B1–B4 |
| 턴 안에서 Input이 가파르게 늘어난다 | 턴 내 Input 증가 배수 | C2, C3, D1 |
| 대화가 길어질수록 Input이 늘어난다 | 턴 간 Input 증가 배수 | C1 |
| 턴당 호출 수의 꼬리가 길다 | 꼬리 턴 비용 비중 | D1, D2, F1, F2 |
| 하위 에이전트의 첫 호출 Input이 크다 | 위임 시 Input 전달 비율 | D4, B4 |
| 단순 단계에도 Reasoning이 많다 | 호출 유형별 Reasoning 수준·토큰 | E2, E1 |
| 최종 응답 Output이 길다 | 목표 길이 대비 최종 응답 길이 | E3 |
| 상위 모델의 비용 비중이 크다 | 상위 모델 잠재 절감 비중 | E1, D3 |
| 결과 없이 끝난 호출·턴의 비용이 크다 | 늦은 실패 계수 | F1–F4, E4 |

## 해석 규칙

- 에이전트 토큰 사용은 꼬리가 길고 같은 작업도 실행마다 크게 달라서(Bai et al., 2026, arXiv 2604.22750) **절대 토큰 수는 기준으로 쓰지 않는다**. 의미가 분명한 비율이나 자기 분포 대비 값을 쓴다.
- 낭비 추정은 **원인끼리 합산하지 않는다**. 같은 토큰을 겹쳐 셀 수 있다. 상한·기준선·간이식으로 구한 값은 보고서에 "상한" 표시가 붙는다.
- 가이드에 수치 기준이 없는 판정은 `config/thresholds.csv`에서 Note에 "임시"로 표시했다. 보고서에서 그 기준을 쓴 판정은 임시 기준을 썼다고 밝힌다.
- `지표를 산출하지 못한 것`은 낮은 값으로 처리하지 않는다. `계측 부재`, `미점검`, `판정 보류`로 따로 기록한다.
- 품질 영향은 진단 범위 밖이다. E 범주에서 찾은 것은 `후보`로 기록한다.

## 작성 규칙

- 한국어 문서는 "~한다" 평서문으로 쓴다. 짧고 직접적인 문장을 쓴다.
- "~가 아니라 ~다" 식의 대비 말투를 쓰지 않는다.
- "수천 토큰"처럼 범위가 모호한 표현을 기준으로 쓰지 않는다.
- 지표는 추상적으로 쓰지 않는다. 계산식이나 Application Insights의 실제 속성·컬럼 이름으로 산출 방법을 적는다.

## 참조 파일

필요할 때만 읽는다.

| 파일 | 언제 읽는가 |
| --- | --- |
| `references/guide-common.md` | 사전 점검, 모델별 캐시 파라미터, 공통 쿼리 정의, 표기 |
| `references/guide-overview.md` | 비용 식과 범주별로 키우는 항 |
| `references/catalog-A.md` … `catalog-F.md` | 이상 증상이 걸린 범주의 원인별 정의·확인 방법·판정·낭비 추정식 |
| `references/data-contract.md` | CSV 스키마, 원인별 필요 파일 |
| `references/build-mapping.md` | 지표 → REPORT 대응, 게이트, `review.json` 형식 |
| `references/report-schema.md` | REPORT 객체 스키마 |
| `references/metrics-notes.md` | 가이드와 SQL 정의가 다른 곳, 임시 기준, 낭비 추정의 성격 |
| `references/sources.md` | 출처 |

## 알려진 한계

- 추출 KQL은 실제 Log Analytics에서 실행해 검증하지 않았다. 고객 환경에서 `AppGenAIContent` 컬럼명, `union`의 `Target` 컬럼, `finish_reasons`의 tool 호출 값(`tool_calls` 가정)을 먼저 확인한다.
- gen_ai 속성은 OpenTelemetry에서 아직 Development 단계라 이름이 바뀔 수 있다(예: `cache_creation` → `cache_write`).
- 토큰 수를 문자 수로 근사하는 곳이 있다(`chars_per_token`). 측정법 1·2로 확정하기 전의 근사다.
