# 로드맵: Agent skill 완성까지

목표는 진단 가이드와 보고서 템플릿을 하나의 Agent skill로 묶는 것이다. skill은 CSV 추출부터 보고서 HTML 생성까지 한 번에 안내한다. 최종 위치는 `skill/agent-tokenomics-diagnosis/`이다.

## 목표 구조

```
skill/agent-tokenomics-diagnosis/
  SKILL.md                    진단 흐름, 사전 점검 게이트, 증상 → 후보 원인
  references/
    guide-common.md           공통 전제, 표기, 모델별 캐시 파라미터
    catalog-A.md … F.md       범주별 카탈로그 (이상 증상이 걸린 범주만 읽는다)
    data-contract.md          CSV 스키마
    report-schema.md          REPORT 객체 스키마
  scripts/                    export.py, run_metrics.py, build_report.py, validate_report.py
  assets/                     views.sql, export/*.kql, metrics/*.sql, report-template.html, config 템플릿
```

## 데이터 흐름

```
Log Analytics ──export.py──▶ data/*.csv ──views.sql──▶ DuckDB 뷰
                                                          │ metrics/*.sql
                                                          ▼
                                              out/metrics.json (원인별 지표)
                                                          │ thresholds + 판정 규칙
                                                          ▼
                                                REPORT 객체 ──▶ report.html
```

판정은 두 층이다. 텔레메트리만으로 정해지는 것은 스크립트가 `후보`까지 판정한다. 원문 리뷰 \[C\]와 코드 리뷰 \[S\]가 필요한 것은 skill이 Agent에게 확인 절차를 안내하고, 확인 전에는 `후보` 또는 `판정 보류`로 둔다. 스크립트가 `해당`을 단정하지 않는다.

## 단계

| # | 단계 | 산출물 | 백로그 | 끝났다고 볼 조건 |
| --- | --- | --- | --- | --- |
| 1 | 가이드에 사전 점검 게이트와 S_a 선행 순서 반영 | `docs/diagnosis-guide.md` 수정 | 3, 4 | 진단 흐름이 점검 → S_a → 지표 순서이고, S_a를 쓰는 8곳에 표시가 있다 |
| 2 | 섹션 3 지표를 로컬 SQL로 이전 | `queries/metrics/*.sql`, `scripts/run_metrics.py`, `config/thresholds.csv` | 6 | 25개 원인의 T 지표가 합성 데이터로 산출된다 |
| 3 | REPORT 스키마와 검증기 | `docs/report-schema.md`, `scripts/validate_report.py` | 2 | 템플릿의 예시 데이터가 검증을 통과한다 |
| 4 | 지표 → REPORT 빌더 | `scripts/build_report.py` | — | metrics.json에서 `report.html`이 만들어진다 |
| 5 | Skill 조립 | `skill/agent-tokenomics-diagnosis/` | 5 | SKILL.md 본문이 500줄 이하이고 참조 파일이 분할되어 있다 |
| 6 | 종단 검증 | `scripts/gen_synthetic.py`, 검증 기록 | — | 심어 둔 문제가 보고서에서 `후보` 이상으로 잡힌다 |

단계마다 커밋한다. origin에는 push하지 않는다.

## 설계 결정 (기본값)

사용자가 자는 동안 정한 값이다. 아침에 바꾸고 싶으면 알려 달라.

- **의존성은 Python 표준 라이브러리와 `duckdb`뿐이다.** YAML, pandas를 쓰지 않는다. 설정은 CSV와 JSON이다.
- **수치 기준은 `config/thresholds.csv`에 둔다.** SQL과 코드에 박지 않는다. 가이드의 기준이 "초기값"이라 고객 데이터를 보고 보정하기 때문이다.
- **SQL 파일 하나가 원인 하나(또는 공통 지표 하나)에 대응한다.** 파일 이름은 `A-common.sql`, `A1.sql`처럼 가이드의 ID를 따른다.
- **지표 SQL의 출력은 평평한 표다.** 실행기가 JSON으로 모으고 빌더가 읽는다.
- **계측이 없으면 SQL이 `NULL`을 돌려주지 않고 실행기가 `계측 부재`로 표시한다.** `instrumentation` 뷰를 보고 판정한다.
- **원문이 필요한 판정은 Agent가 한다.** 스크립트는 `후보`까지만 간다.
- **KQL 원본은 가이드 본문에 남겨 둔다.** 본문 분리는 skill 조립(5단계)에서 `references/`로 옮길 때 한다. 지금 가이드를 크게 고치면 claude.ai 원본과 갈라지는 폭이 커진다.

## 알려진 위험

- 추출 KQL은 실제 Log Analytics에서 실행해 보지 않았다. 고객 환경에서 `AppGenAIContent` 컬럼명과 `union`의 `Target` 컬럼을 확인해야 한다.
- 합성 데이터는 내가 심은 문제만 검증한다. 실제 텔레메트리의 모양과 다를 수 있다.
- 일부 지표(F3 임베딩 유사도, A3 축소 이벤트의 일부)는 SQL만으로 끝나지 않는다. 이 경우 SQL은 입력 표까지만 만들고 skill이 후속 절차를 안내한다.
