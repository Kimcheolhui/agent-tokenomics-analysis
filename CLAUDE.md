# Agent Tokenomics Analysis

고객 Agent의 토큰 비용을 최적화하기 위한 작업 폴더다. 지금 단계는 **진단**이며, Microsoft Foundry 프로젝트에 연결된 Application Insights 텔레메트리로 비용 문제의 원인을 찾고 크기를 추정한다. 조치(개선안)는 이 단계에서 다루지 않는다.

이 파일은 claude.ai 대화(2026-11-06 ~ 11-09)에서 정한 내용을 Claude Code로 넘기기 위한 인수인계 노트다.

## 파일

| 경로 | 내용 | 원본 |
| --- | --- | --- |
| `docs/diagnosis-guide.md` | 진단 가이드. 문제 범주 6개, 증상 → 원인 매핑, 원인 25개(A1–F4)별 지표(SQL 위치)·판정·낭비 추정식 | [Claude Docs](https://claude.ai/artifact/3X9qWuyUWeXDrTkECgfF6G) |
| `report/token-diagnosis-report.html` | 진단 보고서 HTML 템플릿. 예시 데이터 포함 | [Artifact](https://claude.ai/artifact/YNvRjVVwTMxiym5VeFCma8) |
| `docs/backlog.md` | Skill로 만들기 전에 정리할 일 5개. 의존 순서대로 | — |
| `docs/data-contract.md` | CSV 스키마 계약. 추출 파일별 컬럼, 원인별 필요 파일, 텔레메트리 밖의 입력 | — |
| `queries/export/*.kql` | 추출 쿼리 8개와 사전 점검 쿼리 3개. 스크립트와 포털이 같은 파일을 쓴다 | — |
| `queries/views.sql` | 로컬 분석 기본 뷰. 가이드 "공통 쿼리 정의"에 대응한다 | — |
| `scripts/export.py` | az CLI 추출. 하루 단위 파일로 내린다 | — |
| `config/` | 모델 단가·캐시 파라미터와 에이전트별 S_a. 고객마다 채운다 | — |
| `config/thresholds.csv` | 판정 기준(judge)과 정의용 매개변수(param). 가이드에 수치가 없는 판정은 임의 수치를 두지 않는다(`docs/metrics-notes.md`) | — |
| `queries/metrics/*.sql` | 원인별 지표 SQL 27개 파일, 62개 쿼리. 파일 ID는 가이드의 원인 ID를 따른다 | — |
| `scripts/` | `export.py`(추출), `run_metrics.py`(지표), `build_report.py`(보고서), `validate_report.py`, `build_skill.py`, `gen_synthetic.py`(합성 데이터) | — |
| `docs/data-contract.md`, `report-schema.md`, `build-mapping.md`, `metrics-notes.md` | CSV 스키마, REPORT 스키마, 지표 → REPORT 대응과 `review.json`, 가이드와 SQL이 다른 곳 | — |
| `docs/writing-style.md`, `scripts/lint_text.py` | REPORT 문체 규칙(비유·AI체 제외)과 검사기 | — |
| `docs/roadmap.md` | skill 완성까지의 단계와 설계 결정 | — |
| `docs/telemetry-reference.md` | 공식 문서(OTel GenAI 규약, Azure Monitor 테이블, Foundry)와 대조한 결과. 고친 가정과 확인하지 못한 것 | — |
| `skill/agent-tokenomics-diagnosis/` | 완성된 Agent skill. `SKILL.md`만 직접 편집하고 나머지는 `scripts/build_skill.py`로 생성한다 | — |
| `tests/e2e.py` | 합성 데이터로 skill 스크립트를 처음부터 끝까지 검증한다 | — |
| `scripts/kql/` | 추출 KQL 오프라인 검사기(node 필요, 선택). skill에도 들어간다. Kusto 파서로 공식 테이블 스키마(`schema.json`)에 대해 문법·컬럼을 보고, 결과 컬럼을 `//! columns`와 맞춘다 | — |

진단은 Log Analytics에서 CSV를 한 번 내린 뒤 로컬에서 산출한다. 쿼리를 매번 실행하지 않는다. 지표 산출 쿼리는 `queries/metrics/*.sql` 한 곳에만 있다. 가이드는 쿼리 이름과 SQL 위치만 적고 SQL을 옮겨 적지 않는다. KQL은 로그를 내려받는 `queries/export/*.kql`에만 있다.

두 원본은 claude.ai에 남아 있다. 이 폴더의 파일을 고친 뒤로는 이 폴더가 기준이고, 원본은 더 이상 동기화하지 않는다.

## 진단 가이드의 구조와 결정 사항

- 문서는 **진단 성격**이다. 평가 절차(Part A), 조치 방향, 부록은 넣지 않기로 했다.
- 구성: 공통 전제 → 1. 문제 범주 개요 → 2. 증상 → 후보 원인 매핑 → 3. 문제 카탈로그(A–F).
- 카탈로그 항목 템플릿: 정의, 확인 방법([T] 텔레메트리 / [C] `AppGenAIContent` 원문 / [S] 정의·코드·설정 리뷰), 지표와 산출(쿼리 이름과 SQL 위치), 판정과 감별, 낭비 추정식.
- 지표는 추상적으로 쓰지 않는다. 계산식이나 Application Insights의 실제 속성·컬럼 이름으로 산출 방법을 적는다.
- 모든 쿼리 블록 앞에 `**쿼리: 이름.**`과 목적을 한두 문장으로 붙인다.
- 턴 비용 식: `Cost_turn = Σ[(I_i − C_i)·P_in + C_i·P_cache + O_i·P_out]`, `I_i = S + H_i + T_i + U`. 고정 Input은 `S`(범주 F와 헷갈리지 않게 F를 쓰지 않는다).
- 표기: `S_a` = 에이전트 a의 고정 Input(System prompt + Tool 정의), `P_eff = (1 − r)·P_in + r·P_cache`(r = 캐시율), `Cacheable(x)` = 모델별 캐시 가능 토큰.

### 대상 범위

- **Azure OpenAI(OpenAI 계열) 모델만** 다룬다. Anthropic 모델은 제외하기로 했다.
- 모델별 캐시 파라미터는 공통 전제의 표와 공통 쿼리의 `ModelParams`에 둔다: `MinTok`(1,024), `Round128`(GPT-5.5 이하 128 단위 내림), `WindowMin`(in_memory 5분, 24h 설정·GPT-5.5·GPT-5.6+ 30분), `RpmLimit`(분당 약 15건).
- 쏠림(A6)은 PTU 단일 엔드포인트에서도 같은 prefix로 분당 15건을 넘으면 생길 수 있어 Optional로 두지 않았다.

### 판정 기준 원칙

- 에이전트 토큰 사용은 꼬리가 길고 같은 작업도 실행마다 크게 달라서(Bai et al., 2026, arXiv 2604.22750), **절대 토큰 수는 기준으로 쓰지 않는다**. 의미가 분명한 비율이나 자기 분포 대비 값을 쓴다.
- 모든 수치 기준은 "초기값"이며 고객 데이터를 보고 보정한다.
- 가이드에 수치가 없는 판정은 근거 없는 수치를 두지 않는다. 가이드 문구에서 값을 유도하거나, 데이터 안의 기준 집단과 단측 검정으로 비교하거나, 판정을 보류하고 리뷰로 넘긴다.

| 증상 / 원인 | 지표 | 기준 |
| --- | --- | --- |
| 캐시 달성률 (A 공통) | Σ CacheTok ÷ Σ 이론 캐시 가능량 | < 0.7 |
| A1 | 보존 시간 내 첫 호출 미스율 (간격 < WindowMin, 분당 ≤ RpmLimit 조건) | ≥ 0.2, System prompt 변형 비율 ≥ 0.1 |
| B 공통 | 고정 Input 비용 비중 = Σ(S_a × 호출 수 × P_eff) ÷ 전체 비용 | ≥ 0.3 |
| C1 | 턴 간 Input 증가 배수 (대화 길이 p90 턴 ÷ 1턴, 첫 호출 InTok p50) | ≥ 2 |
| C2 | 턴 내 Input 증가 배수 p50 (마지막 ÷ 첫 호출 InTok), 호출당 증가율 p95 | ≥ 2, ≥ 0.5 |
| D1 | 꼬리 턴 비용 비중 (호출 수 상위 5% 턴) | ≥ 0.15, 최대 반복 도달 시 확정 |
| D4 | 위임 시 Input 전달 비율 = (하위 첫 InTok − S_sub) ÷ (상위 직전 InTok − S_parent) | ≥ 0.8 |
| E1 | 상위 모델 잠재 절감 비중 | ≥ 0.1 |
| E2 | 단순 단계와 최종 응답 단계의 reasoning 수준·분포 비교 | 수치 기준 없음 (작업 복잡도에 종속) |
| E3 | 목표 길이 대비 최종 응답 길이 | 고객 목표 길이가 없으면 판정 보류 |
| F4 | 늦은 실패 계수 = 실패 턴 비용 비중 ÷ 실패 턴 비율 | ≥ 1.5 |

## 보고서 템플릿

- 순서: **1. 요약 → 2. 증상 점검 → 3. 원인 상세 → 4. 점검 범위**. 원인 상세가 길어서, 증상 점검으로 큰 그림을 먼저 보여준 뒤 이상 증상에서 이어진 원인만 상세로 넘어간다.
- 나열 기준은 원인(A1–F4)이다. 증상은 "어떻게 발견했나", 지표는 근거 그림으로 원인 카드 안에 들어간다.
- 증상 점검은 기준 대비 배수로 정규화한 가로 막대 하나로 보여준다. 낮을수록 나쁜 지표는 기준 ÷ 값으로 뒤집는다. 수치 기준이 없는 증상은 상태 칩으로 표시한다.
- 원인 카드는 해당·후보이면서 낭비 추정이 있는 원인만, 낭비 추정 큰 순으로 싣는다. 원인끼리 같은 토큰을 겹쳐 셀 수 있어 낭비 추정은 합산하지 않는다.
- "그림 카탈로그" 보기는 25개 원인 전체의 그림 예시를 보여준다.
- 데이터는 파일 아래쪽 `<script>`의 `REPORT` 객체 하나에 있다. 필드 설명은 페이지 맨 아래 "템플릿 채우는 법"에 있다. 그림 type: `hbar`, `stackbar`, `segbar`, `columns`, `line`, `box`, `diff`, `listdiff`, `pairs`.
- 단가(`meta.prices`)와 모든 수치는 예시 값이다.

## 실행 방법

```bash
python3 scripts/export.py --workspace <GUID> --from 2026-10-20 --to 2026-11-03      # 추출
python3 scripts/run_metrics.py --root . --out out/metrics.json                      # 지표
python3 scripts/build_report.py --metrics out/metrics.json --root . --out out/report.html
python3 tests/e2e.py                                                                # 종단 검증 (duckdb 필요)
(cd scripts/kql && npm install) && node scripts/kql/check_kql.js                       # 추출 KQL 검사 (e2e도 설치돼 있으면 돌린다)
python3 scripts/build_skill.py                                                      # skill 폴더 재생성 (--check로 동기화 확인)
```

`scripts/`나 `queries/`를 고친 뒤에는 `build_skill.py`를 다시 돌린다. `tests/e2e.py`가 skill 폴더와 원본이 다르면 실패한다.

## 남은 일과 열린 질문

- 먼저 `queries/export/00-precheck-keys.kql`로 `cache_read`, `reasoning`, `conversation.id` 키가 쌓이는지, `01-precheck-sampling.kql`로 샘플링이 있는지, `02-precheck-content.kql`로 `AppGenAIContent`의 span당 행 수와 `ToolDefinitions` 형식, 하루 수집량을 확인한다.
- 증상 "턴 중간에 캐시율이 떨어진다"는 A 공통 캐시 달성률을 두 번째 이후 호출로 좁힌 같은 지표라서 같은 기준 0.7을 쓴다.
- `gen_ai.response.finish_reasons`는 OTel 규약상 문자열 배열이고 OTel 예시의 tool 호출 값은 `tool_calls`다(`docs/telemetry-reference.md`). 고객 환경의 실제 값은 확인해야 한다.
- 턴 1개 = trace 1개 가정을 고객 앱에서 확인해야 한다.
- gen_ai 속성은 OTel에서 아직 Development 단계라 이름이 바뀔 수 있다. `cache_creation`은 이미 `cache_write`로 바뀌었다.
- **추출 KQL 11개는 실제 Log Analytics에서 실행해 보지 않았다.** 문법과 컬럼 이름은 Kusto 파서로 오프라인 검사했다(2026-11-09, `scripts/kql/`). 속성 값의 실제 형식과 행 수는 확인하지 못했다. DuckDB 쪽(뷰·지표 SQL·빌더)은 합성 데이터로만 검증했다. 합성 데이터는 내가 심은 문제만 검증하며, 실제 텔레메트리의 모양과 다를 수 있다.
- 가이드에 수치 기준이 없는 판정 12개는 임의 수치를 두지 않았다(2026-11-09). 가이드 문구에서 유도(C3 0.5, E4·F1·F2 "반복" = 2건 이상, 턴 중간 캐시율 = A 공통 0.7), 기준 집단과의 단측 검정(A3·A5·A6·D1, 유의수준 0.01), 판정 보류(D2·D3·F3) 중 하나로 처리한다. 검정은 크기를 보지 않아 표본이 크면 작은 차이도 `후보`가 된다. 합성 데이터에서 심지 않은 A3·F2가 `후보`로 나온다. 목록은 `docs/metrics-notes.md`에 있다.
- 가이드와 SQL이 다른 정의를 쓰는 곳 6개는 `docs/metrics-notes.md`에 있다. D4 전달 비율은 분모에서 S_parent를 빼도록 가이드 정의를 고쳤다(2026-11-09).

## 작성 규칙

- 한국어 문서는 "~한다" 평서문으로 쓴다. 짧고 직접적인 문장을 쓴다.
- **REPORT에는 비유적 표현과 AI체를 쓰지 않는다.** 기준과 패턴은 `docs/writing-style.md`에 있고, `scripts/lint_text.py`가 검사한다. `validate_report.py`와 `tests/e2e.py`가 같은 검사를 한다. 가이드와 템플릿의 문구를 고칠 때도 같은 기준을 쓴다.
- "~가 아니라 ~다" 식의 대비 말투를 쓰지 않는다. "하단 쿼리를 통해 속성 키를 확인할 수 있다"처럼 바로 서술한다.
- "수천 토큰"처럼 범위가 모호한 표현을 기준으로 쓰지 않는다.
