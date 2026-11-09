# Foundry Agent 토큰 비용 진단 가이드

작성 2026-11-06 · 김철희 · 원본: [Claude Docs](https://claude.ai/artifact/3X9qWuyUWeXDrTkECgfF6G) (2026-11-09 내보냄)

## 가이드 범위와 공통 전제

이 가이드는 Foundry 프로젝트에 연결된 Application Insights 트레이스로 Agent의 토큰 비용 문제를 진단하는 방법을 정리한다. 원인을 찾고 크기를 추정하는 데까지만 다루며, 조치 방향은 다루지 않는다.

### 진단 흐름

1. **사전 점검.** 아래 "사전 점검" 표의 항목을 확인한다. 통과하지 못한 항목은 영향받는 원인을 "계측 부재"로 기록하고 넘어간다.
2. **공통 정의 준비.** "공통 쿼리 정의"의 `ModelParams`에 고객이 쓰는 모델의 단가와 캐시 파라미터를 채운다.
3. **S_a 측정.** 섹션 3-B 측정법으로 에이전트별 고정 Input 토큰 S_a를 구한다. A 범주 지표 대부분과 B 공통 지표가 S_a를 입력으로 받으므로 A·B 지표보다 먼저 한다. 아래 "S_a를 쓰는 지점" 표를 본다.
4. 섹션 2의 증상표에서 관찰된 증상의 후보 원인을 고른다.
5. 섹션 3에서 해당 항목의 지표를 산출한다.
6. 판정 기준과 감별로 원인을 확정하고, 낭비 추정식으로 크기를 매긴다.

데이터를 매번 Log Analytics에서 쿼리하지 않고 CSV로 한 번 내려 로컬에서 산출하는 방법은 `references/data-contract.md`에 있다. 지표 산출은 `queries/metrics/`의 SQL이 기준이다. 가이드에는 SQL을 옮겨 적지 않고 쿼리 이름과 SQL 위치만 적는다. 로그를 내려받는 KQL은 `queries/export/`에만 있다.

### 분석 단위

| 단위 | 정의 | 식별 방법 |
| --- | --- | --- |
| 대화 | 같은 세션에 속한 턴의 묶음 | `gen_ai.conversation.id`. 계측이 값을 넣은 경우에만 존재 |
| 턴 | 사용자 요청 1건에 대한 에이전트 실행 | 같은 `OperationId`(trace)의 최상위 `invoke_agent` span |
| LLM 호출 | 모델 추론 1회 | `gen_ai.operation.name` = `chat` (또는 `text_completion`, `generate_content`) |
| Tool 호출 | Tool 실행 1회 | `gen_ai.operation.name` = `execute_tool` |

턴 1개 = trace 1개라는 가정은 진단 전에 확인해야 한다. 고객 앱이 대화 전체를 trace 하나로 묶거나, 반대로 한 턴을 여러 trace로 나누면 턴 단위 지표가 틀어진다.

### 텔레메트리 위치

추출 쿼리는 Log Analytics workspace 스키마로 작성한다. content 테이블인 `AppGenAIContent`가 workspace 테이블이기 때문이다.

- `AppDependencies`: GenAI span 대부분. 속성은 `Properties`, trace는 `OperationId`, span은 `Id`, 부모는 `ParentId`, 성공 여부는 `Success`.
- `AppRequests`: server kind로 기록된 span(일부 `invoke_agent`). `AppDependencies`와 union해서 조회한다.
- `AppGenAIContent`: 프롬프트·응답 원문. `TraceId` = `OperationId`, `SpanId` = `Id`로 조인한다. 컬럼은 `InputMessages`, `OutputMessages`, `SystemInstructions`, `ToolDefinitions`, `ToolCallArguments`, `ToolCallResult`.

2026-09-30 이후 수집분부터 content 속성 7종의 값은 `AppGenAIContent`에만 저장되고, `AppDependencies`에는 키와 포인터만 남는다. 이 테이블을 protected로 설정한 경우 Privileged Monitoring Data Reader 역할이 있어야 읽을 수 있다. content recording이 꺼져 있으면 이 테이블은 비어 있다. ([Learn](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/traces-sensitive-content))

### 사용하는 속성

| 속성 | 의미 | 주의 |
| --- | --- | --- |
| `gen_ai.usage.input_tokens` | Input 토큰 | 캐시 히트·쓰기분을 포함한 총량 |
| `gen_ai.usage.cache_read.input_tokens` | 캐시 히트 토큰 | input\_tokens의 부분집합 |
| `gen_ai.usage.cache_write.input_tokens` | 캐시 쓰기 토큰 | 이전 계측은 `cache_creation.input_tokens`로 기록 |
| `gen_ai.usage.output_tokens` | Output 토큰 | Reasoning 토큰 포함 |
| `gen_ai.usage.reasoning.output_tokens` | Reasoning 토큰 | output\_tokens의 부분집합 |
| `gen_ai.response.model` | 실제 응답 모델 | 단가 매핑 키. `request.model`은 배포 이름일 수 있음 |
| `gen_ai.request.max_tokens` | 출력 상한 | 미설정이면 비어 있음 |
| `gen_ai.request.reasoning.level` | Reasoning 수준 | low, medium, high 등 |
| `gen_ai.response.finish_reasons` | 종료 사유 배열 | stop, length, tool\_calls, content\_filter, error |
| `gen_ai.response.status` | 응답 상태 | incomplete = 상한·필터로 중단 |
| `gen_ai.agent.name` | 에이전트 이름 | 주로 `invoke_agent` span에 기록 |
| `gen_ai.conversation.compacted` | 컨텍스트 압축 여부 | 압축 시에만 true로 기록 |
| `gen_ai.tool.name`, `gen_ai.tool.call.id` | Tool 이름, 호출 ID | `execute_tool` span |
| `gen_ai.retrieval.top_k`, `gen_ai.retrieval.documents` | 검색 요청 수, 반환 문서(id, score) | `retrieval` span |
| `error.type`, `server.address` | 오류 유형, 호출 엔드포인트 | 실패 span, 모델 호출 span |

gen\_ai 속성은 아직 Development 단계라 이름이 바뀐다. 예를 들어 `cache_creation`이 `cache_write`로 바뀌었다. ([OTel GenAI registry](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)) 그래서 진단 전에 실제로 쌓이는 키를 먼저 확인한다.

사전 점검 P1~P4, P9에 쓰는 아래 쿼리로 LLM 호출(chat) span에 실제로 기록되는 **속성 키**를 확인한다. 결과에는 키별로 그 키가 들어 있는 span 수가 나온다.

추출 쿼리: `queries/export/00-precheck-keys.kql`. 아래 P10·P11은 `01-precheck-sampling.kql`, `02-precheck-content.kql`로 확인한다. 세 쿼리는 기본 추출에 들어가지 않는다.

`cache_read`나 `reasoning` 키가 없으면 그 키를 쓰는 3장 지표는 산출할 수 없다(cache\_read: A의 캐시율·달성률과 A1·A5·A6 히트율, reasoning: E2 Reasoning 비중과 E1·D3의 가시 출력). 이 경우 진단 결과에 "계측 부재"로 기록한다.

### 사전 점검

진단을 시작하기 전에 아래 항목을 확인한다. 항목마다 실패했을 때 산출할 수 없게 되는 지표가 정해져 있다. 점검 결과는 보고서의 판정(`계측 부재`)과 점검 범위(`meta.sources`)에 그대로 옮긴다.

| # | 점검 항목 | 확인 방법 | 실패하면 영향받는 지표 |
| --- | --- | --- | --- |
| P1 | 속성 키 `cache_read` 수집 | 위 속성 키 쿼리, 또는 로컬 `instrumentation.WithCacheRead` | A 공통 캐시율·달성률, A1·A5·A6 히트율 |
| P2 | 속성 키 `reasoning` 수집 | 같은 쿼리, `WithReasoning` | E2 Reasoning 비중, E1·D3의 가시 출력 |
| P3 | 속성 키 `gen_ai.conversation.id` 수집 | 같은 쿼리, `WithConvId` | C1 전체, F3 빠른 재입력 비율, 대화 기준 지표 |
| P4 | `gen_ai.agent.name` 이 chat span에 기록되는가 | `WithAgentOwner`와 `Agent` 컬럼 비교 | B1·B4·D4의 에이전트 귀속. 기록되지 않으면 호출이 루트 에이전트로 집계된다 |
| P5 | `finish_reasons` 의 tool 호출 값 | `SELECT Finish, count(*) FROM calls GROUP BY 1` | D1·D2·D3·E1·E4·F2의 호출 유형 분류. SQL은 `tool_calls`를 가정한다 |
| P6 | 턴 1개 = trace 1개 | 아래 "턴·trace 점검"의 `turn_trace_check` | 턴 단위 지표 전체 (CallIdx, 턴 비용, 꼬리 비중) |
| P7 | `AppGenAIContent` 접근과 content recording | 테이블이 비어 있지 않은가, Privileged Monitoring Data Reader 역할 | [C] 지표 전체: A1·A2 변형 수, B1~B3 정의 크기, C2 결과 크기, D1 중복, D3 다양성, F3 메시지 |
| P8 | 응답 모델명이 `ModelParams`와 일치 | 로컬 `unpriced_calls`, 또는 `Calls \| where isnull(PIn)` | 일치하지 않는 호출은 `Cost`가 비어 비용 합계에서 경고 없이 빠진다 |
| P9 | `gen_ai.request.max_tokens` 기록 | `WithMaxTokens` | E3 max_tokens 미설정 비율. 미기록과 미설정을 구분할 수 없다 |
| P10 | 샘플링이 없는가 | `queries/export/01-precheck-sampling.kql`의 `MaxItemCount` = 1 | 호출 수와 비용 합계 전체. 지표는 `ItemCount`를 보정하지 않으므로 샘플링이 있으면 작게 나온다 |
| P11 | `AppGenAIContent`가 span당 1행이고 `ToolDefinitions`가 배열인가 | `queries/export/02-precheck-content.kql`의 `MaxRowsPerSpan` = 1, `ToolDefsTypes` = `["array"]` | `content_digest`의 span 단위 지표(A1·A2·C2·D1·D3), `tool_schemas`의 B1·B2 |

**쿼리: 턴·trace 점검.** invoke_agent span이 trace당 몇 개인지, 한 trace가 여러 대화를 묶는지 센다. 대화 전체를 trace 하나로 묶는 앱은 `TracesWithMultiConv`가 크게 나오고, 이 경우 턴 단위 지표가 틀어진다. 반대로 한 턴을 여러 trace로 나누는 경우는 텔레메트리로 판별할 수 없으므로 \[S\]로 앱 구조를 확인한다.

SQL: `queries/views.sql`의 `turn_trace_check` (추출한 `agent_spans`로 계산한다).


점검에서 `cache_read`나 `reasoning` 키가 없으면 해당 지표는 "계측 부재"로 기록한다. P6가 실패하면 턴 단위 지표는 신뢰할 수 없으므로 앱 구조를 확인하고 진단 범위를 조정한다.

### 모델별 캐시 파라미터 (Azure OpenAI)

캐시 동작은 모델과 설정에 따라 다르다. 섹션 3-A의 쿼리와 판정 기준은 아래 값을 변수로 쓴다. 고객이 쓰는 모델마다 공통 쿼리의 `ModelParams` 표에 채운다.

| 모델·설정 | MinTok (최소 캐시 길이) | Round128 (히트 단위) | WindowMin (보존 시간 기준) | RpmLimit (쏠림 기준) | 캐시 쓰기 과금 |
| --- | --- | --- | --- | --- | --- |
| GPT-4o \~ GPT-5.4, 기본(in\_memory) | 1,024 | 128 단위 내림 | 5분. 비활성 5–10분이면 삭제, 최대 1시간 | 분당 약 15건 | 없음 |
| `prompt_cache_retention: "24h"` 설정(지원 모델), GPT-5.5 | 1,024 | 128 단위 내림 | 30분. 최대 24시간 | 분당 약 15건 | 없음 |
| GPT-5.6 이후 | 1,024 | 정확값 | 30분. 최소 보존 시간 | 분당 약 15건 | 있음 |

- WindowMin은 캐시가 살아 있다고 볼 수 있는 간격의 보수적인 값이다. in\_memory는 문서 범위(5–10분)의 하한을, 24h 설정과 GPT-5.5는 OpenAI 문서의 일반적인 유지 시간(약 30분)을 썼다.
- 24h 지원 모델과 GPT-5.5 기본값은 [Azure 문서](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/prompt-caching)를, 머신 단위 캐시 저장과 분당 15건 기준은 [OpenAI 문서](https://developers.openai.com/api/docs/guides/prompt-caching)를 따른다.
- OpenAI 문서는 GPT-5.6 이전 모델의 실제 최소 길이가 요청 설정(tool, 이미지, 출력 스키마, reasoning effort 등)에 따라 달라진다고 설명한다. 1,024는 하한으로 보고, 필요하면 A4에서 실측으로 확인한다.

### 공통 쿼리 정의

섹션 3의 모든 SQL은 아래 정의(`queries/views.sql`)를 기반으로 한다. `ModelParams`(`config/model-params.csv`)에는 고객이 쓰는 모델의 Azure 단가(USD / 1M 토큰)와 캐시 파라미터를 채운다. 응답 모델명에는 버전 접미사가 붙을 수 있으니 실제 값에 맞춘다.

**공통 정의.** 모델별 단가·캐시 파라미터(ModelParams), 턴(Turns), LLM 호출(Calls), Tool 호출(Tools), 원문(Content)을 정의한다. 섹션 3의 모든 지표가 같은 분석 단위와 비용 계산식을 쓰게 하려는 용도다. Calls에는 호출별 비용(Cost), 턴 안 호출 순번(CallIdx), 모델 캐시 파라미터가 붙는다. `cacheable()` 매크로는 캐시 가능 토큰을 계산한다.

공통 정의는 추출 쿼리 `queries/export/10-calls.kql`(Calls)과 로컬 뷰 `queries/views.sql`(Turns, CallIdx, Cost, Tools, Content, ModelParams)에 있다. 이 가이드의 지표는 모두 그 뷰 위에서 `queries/metrics/*.sql`로 계산한다.

### 표기

- 확인 방법 태그: **\[T\]** 텔레메트리 수치만으로 산출, **\[C\]** `AppGenAIContent` 원문 필요, **\[S\]** 에이전트 정의·코드·설정 리뷰.
- S\_a: 에이전트 a의 고정 Input 토큰(System prompt + Tool 정의). 섹션 3-B의 측정법으로 구한다.
- Cacheable(x): 캐시 가능한 최대 토큰. x가 모델의 MinTok 미만이면 0이고, Round128 모델(GPT-5.5 이하)은 128 단위로 내림하며, 그 외 모델은 x를 그대로 쓴다. ([Learn](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/prompt-caching))
- P\_eff: 실효 Input 단가 = (1 − r) × P\_in + r × P\_cache. r은 해당 에이전트의 캐시율(Σ CacheTok ÷ Σ InTok)이다. 고정 Input처럼 매 호출 반복되는 토큰의 낭비를 추정할 때 쓴다.
- 판정 기준의 수치는 초기값이다. 고객 데이터의 분포를 보고 보정한다.
- 수치 없이 "낮으면", "높으면"으로 적은 판정은 근거 없는 수치를 정하지 않는다. 데이터 안에 비교할 기준 집단이 있으면(A3, A5, A6, D1) 단측 검정(유의수준 0.01)으로 차이가 우연으로 보기 어려운지만 보고, 크기는 낭비 추정으로 보인다. "반복되면"은 2건 이상으로 읽는다. 기준 집단도 없으면(D2, D3, F3) 지표 값을 기록하고 \[C\]·\[S\] 확인으로 판정한다.

