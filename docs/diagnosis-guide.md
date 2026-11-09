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

데이터를 매번 Log Analytics에서 쿼리하지 않고 CSV로 한 번 내려 로컬에서 산출하는 방법은 `docs/data-contract.md`에 있다. 지표 산출은 `queries/metrics/`의 SQL이 기준이다. 가이드에는 SQL을 옮겨 적지 않고 쿼리 이름과 SQL 위치만 적는다. 로그를 내려받는 KQL은 `queries/export/`에만 있다.

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

## 1. 문제 범주 개요

토큰 비용 문제는 6개 범주, 25개 패턴으로 나뉜다. 각 범주는 아래 턴당 비용 식에서 서로 다른 항을 키운다.

```latex
\mathrm{Cost}_{turn} = \sum_{i=1}^{N} \left[ (I_i - C_i)\,P_{in} + C_i\,P_{cache} + O_i\,P_{out} \right]
```

```latex
I_i = S + H_i + T_i + U
```

- N: 턴당 LLM 호출 수
- I\_i: 호출 i의 Input 토큰(캐시 히트분 포함), C\_i: 그중 캐시 히트 토큰, O\_i: Output 토큰(Reasoning 포함)
- S: 고정 Input(System prompt + Tool 정의), H\_i: 대화 이력, T\_i: Tool 결과·검색 컨텍스트, U: 사용자 메시지
- P\_in, P\_cache, P\_out: 모델별 토큰 단가. GPT-5.6 이후 모델은 캐시 쓰기 단가 P\_write 항이 추가된다.

| 범주 | 키우는 항 | 패턴 |
| --- | --- | --- |
| A. Prefix 변동 | C\_i 감소 → (I\_i − C\_i) 증가 | A1 동적 값, A2 Tool 정의 변동, A3 이력 앞부분 편집, A4 Prefix 1,024 미만, A5 캐시 만료, A6 트래픽 분산·쏠림 |
| B. 고정 Input 비대 | S | B1 미사용 Tool 상시 전송, B2 장황한 Tool schema, B3 System prompt 비대, B4 하위 에이전트 prompt 중복 |
| C. 컨텍스트 누적 | H\_i, T\_i | C1 이력 무제한 누적, C2 Tool 결과 원문 전달, C3 검색 컨텍스트 과다 |
| D. 호출 구조 | N | D1 루프·중복 Tool 호출, D2 병렬 가능한 호출의 순차 처리, D3 결정적 단계의 LLM 처리, D4 에이전트 간 컨텍스트 중복 |
| E. 모델·출력 설정 | P, O\_i | E1 단순 단계에 상위 모델, E2 Reasoning 과다, E3 장황한 응답, E4 출력 잘림 |
| F. 실패 낭비 | 결과 없는 호출·턴 전체 | F1 Tool 실패 재시도, F2 LLM 호출 오류·재요청, F3 사용자 재질문, F4 실패로 끝난 턴 |

B의 낭비 크기는 A의 결과에 달려 있다. 고정 Input S는 캐시가 맞으면 P\_cache로, 안 맞으면 P\_in으로 과금되기 때문이다. F 범주는 식의 특정 항과 대응하지 않는다. 결과를 내지 못한 호출과 턴의 비용 전체를 낭비로 본다.

## 2. 증상 → 후보 원인 매핑

진단은 관찰된 증상에서 시작해 후보 원인 항목으로 좁혀 간다. 한 증상에 원인이 여러 개인 경우가 많으므로, 후보를 모두 확인한 뒤 각 항목의 판정 기준으로 감별한다.

| 증상 | 증상을 확인하는 지표 | 후보 원인 |
| --- | --- | --- |
| 캐시 달성률이 낮다 | 캐시 달성률(A 공통) | A1–A6 |
| 턴 첫 호출의 캐시 미스가 잦다 | 보존 시간 내 첫 호출 미스율(A1) | A1, A2, A4, A5, A6 |
| 턴 중간에 캐시율이 떨어진다 | 호출 순번별 캐시 달성률(A 공통) | A3, A2 |
| 고정 Input의 비용 비중이 크다 | 고정 Input 비용 비중(B 공통) | B1–B4 |
| 턴 안에서 Input이 크게 늘어난다 | 턴 내 Input 증가 배수(C2) | C2, C3, D1 |
| 대화가 길어질수록 Input이 늘어난다 | 턴 간 Input 증가 배수(C1) | C1 |
| 턴당 호출 수의 꼬리가 길다 | 꼬리 턴 비용 비중(D1) | D1, D2, F1, F2 |
| 하위 에이전트의 첫 호출 Input이 크다 | 위임 시 Input 전달 비율(D4) | D4, B4 |
| 단순 단계에도 Reasoning이 많다 | 호출 유형별 Reasoning 수준·토큰(E2) | E2, E1 |
| 최종 응답 Output이 길다 | 목표 길이 대비 최종 응답 길이(E3) | E3 |
| 상위 모델의 비용 비중이 크다 | 상위 모델 잠재 절감 비중(E1) | E1, D3 |
| 결과 없이 끝난 호출·턴의 비용이 크다 | 늦은 실패 계수(F4) | F1–F4, E4 |

## 3. 문제 카탈로그

항목마다 정의, 확인 방법, 지표와 산출, 판정과 감별, 낭비 추정을 둔다. SQL은 모두 섹션 "공통 쿼리 정의"의 뷰 위에서 실행한다.

## A. Prefix 변동 (캐시 미스)

캐시는 앞에서부터 바이트 단위로 같은 prefix만 재사용한다. 그래서 앞부분이 한 글자만 달라도 그 뒤 전체가 미스 난다. A 범주의 모든 항목은 아래 공통 지표에서 출발한다.

### A 공통 지표: 캐시 달성률

- **캐시율** = Σ CacheTok ÷ Σ InTok. 실제로 할인받은 Input 비중이다.
- **이론 캐시 가능량** Theo\_i: 턴 첫 호출은 Cacheable(S\_a), 두 번째 이후 호출은 Cacheable(직전 호출 InTok). 에이전트 루프에서는 직전 호출의 Input 전체가 다음 호출의 prefix가 되기 때문이다.
- **캐시 달성률** = Σ CacheTok ÷ Σ Theo\_i. 이론 상한 대비 실제 히트 비율이다.

**쿼리: 에이전트별 캐시율·캐시 달성률.** 실제 캐시 히트 토큰을 이론 상한과 비교해, A 범주를 점검할지 판단한다. 첫 줄의 S 표에는 3-B에서 측정한 에이전트별 고정 Input 토큰을 넣는다.

SQL: `queries/metrics/A-common.sql`의 `agent_cache`, `overall`, `by_callidx`.

- 달성률이 0.7 미만이면 A1–A6을 점검한다.
- 달성률이 1을 넘을 수 있다. 같은 대화의 이전 턴 이력까지 히트한 경우이며 정상이다. 턴 첫 호출의 이론치를 고정부로만 잡았기 때문이다.
- 호출 순번(CallIdx)별로도 나눠 본다. 첫 호출만 낮으면 A1·A2·A4·A5·A6, 두 번째 이후가 낮으면 A3이나 호출마다 prompt를 다시 만드는 구조다.

### A1. Prefix 앞부분의 동적 값

- **정의**: System prompt 앞부분에 요청마다 바뀌는 값이 있다. 예: 현재 시각, 사용자 이름·ID, 요청 ID, 무작위 순서의 예시. 그 지점 이후 전체가 미스 난다.
- **확인 방법**: \[S\] 프롬프트 템플릿에서 변수 위치를 확인한다. \[C\] 같은 에이전트의 `SystemInstructions` 변형 수를 센다. \[T\] 보존 시간 내 첫 호출 미스율을 본다.
- **지표**
  - 보존 시간 내 첫 호출 미스율 = 아래 세 조건을 모두 만족하는 턴 첫 호출 중 CacheTok = 0인 비율. 만료(A5)와 쏠림(A6)을 걸러내서, 남은 미스를 prefix 변동으로 볼 수 있게 한다.
    - S\_a ≥ MinTok: 고정 prefix가 캐시 가능한 길이다.
    - 같은 에이전트의 직전 첫 호출과의 간격 < WindowMin: 캐시가 만료되지 않았다.
    - 그 1분 동안 에이전트의 LLM 호출 수 ≤ RpmLimit: 쏠림이 없다.
  - System prompt 변형 비율 = 고유 `SystemInstructions` 수 ÷ 기록 수

**쿼리: 보존 시간 내 첫 호출 미스율.** 만료(A5)와 쏠림(A6)이 생기지 않는 조건의 턴 첫 호출만 골라 캐시 미스 비율을 구한다. 이 조건에서는 캐시가 살아 있어야 하므로, 미스가 많으면 prefix 자체가 바뀐다고 볼 수 있다. A1 판정의 1차 신호로 쓴다.

SQL: `queries/metrics/A1.sql`의 `warm_first_miss`, `inturn_misses`.

**쿼리: System prompt 변형 수.** 같은 에이전트의 `SystemInstructions`가 몇 가지로 기록됐는지 센다. 전체 변형(Variants)과 앞 4,000자 변형(HeadVariants)을 함께 보고, 동적 값이 앞부분에 있는지 확인한다.

SQL: `queries/metrics/A1.sql`의 `sys_variants`.

- **판정**
  - 보존 시간 내 첫 호출 미스율(WarmMissRate)이 0.2 이상이고 VariantRatio가 0.1 이상이면 A1로 판정한다. 변형 2건을 꺼내 첫 차이 위치를 확인하면 원인 변수를 특정할 수 있다.
  - 이 조건에서도 트래픽이 시작되는 순간 동시에 들어온 요청은 미스가 날 수 있으므로, 미스율이 낮게 유지되면 정상으로 본다.
  - HeadVariants가 크면 차이가 앞부분에 있다는 뜻이라 영향이 크다.
  - 턴 안 두 번째 이후 호출도 미스 나면, 프레임워크가 호출마다 템플릿을 다시 렌더링해서 같은 턴 안에서도 시각 값이 바뀌는 경우다.
  - 변형이 1에 가까운데 미스율이 높으면 A2를 확인한다.
- **낭비 추정**: Σ(CacheTok = 0인 턴 첫 호출) Cacheable(S\_a) × (P\_in − P\_cache). 재렌더링 유형이면 턴 안 미스분도 더한다.

### A2. Tool 정의 변동

- **정의**: Tool 정의는 메시지보다 앞쪽 prefix에 들어간다. 그런데 tool 목록의 순서나 내용(설명에 들어간 동적 값 등)이 요청마다 달라진다. 원인은 dict 순회 순서, 사용자 권한별 동적 목록 등이다. Structured output 스키마도 system message 앞에 붙으므로 요청마다 바뀌면 같은 현상이 생긴다. ([Learn](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/prompt-caching))
- **확인 방법**: \[C\] `ToolDefinitions` 변형을 센다. \[S\] tool 목록과 응답 스키마를 만드는 코드를 확인한다.
- **지표**
  - 원문 변형 수 = 고유 `ToolDefinitions` 수
  - 이름 집합 변형 수 = 정렬한 tool 이름 집합의 고유 수

**쿼리: Tool 정의 변형 수.** `ToolDefinitions` 원문의 변형 수와 tool 이름 집합의 변형 수를 비교한다. 순서·설명 변동(A2)과 tool 목록 자체의 변동을 구분하려는 용도다.

SQL: `queries/metrics/A2.sql`의 `tooldef_variants`, `variant_share`.

- **판정**
  - RawVariants가 NameSetVariants보다 크면, 같은 tool 집합인데 순서나 설명이 바뀐 것이다. A2로 판정한다.
  - NameSetVariants가 2 이상이면 tool 목록 자체가 요청마다 다르다. 목록 종류 수만큼 prefix가 갈라진다.
  - `ToolDefinitions`가 `invoke_agent`에만 기록되면 턴 단위 변동만 보인다. 턴 안의 변동은 CallIdx별 달성률로 확인한다.
- **낭비 추정**: 가장 흔한 정의와 다른 정의를 쓴 턴의 첫 호출 수 × Cacheable(S\_a) × (P\_in − P\_cache).

### A3. 이력 앞부분 편집

- **정의**: 컨텍스트 관리 로직이 이력 앞부분을 바꾼다. 예: 오래된 메시지 요약·절단, 과거 tool 결과 삭제, 메시지 수정. 편집 지점 이후가 미스 난다. 문서는 이력을 append-only로 유지하라고 권장한다.
- **확인 방법**: \[T\] 같은 대화에서 Input이 직전보다 줄어든 호출(축소 이벤트)을 찾는다. \[T\] `gen_ai.conversation.compacted` 기록을 확인한다. \[C\] 연속 호출의 `InputMessages`를 비교한다.
- **지표**
  - 축소 이벤트 수 = 같은 대화에서 InTok\_i < InTok\_(i−1)인 호출 수
  - 축소 후 캐시율 vs 평상시 캐시율

**쿼리: 이력 축소 이벤트와 전후 캐시율.** 같은 대화에서 Input이 직전보다 줄어든 호출을 찾고, 그 직후 캐시율을 평상시와 비교한다. 이력 편집이 캐시를 깨는지(A3) 확인한다.

SQL: `queries/metrics/A3.sql`의 `shrink`, `conv_call_profile`.

- **판정**
  - 축소 이벤트가 있고 HitAfterShrink가 HitNormal보다 낮으면(단측 검정) A3 후보로 올린다. \[C\]로 편집 지점을 확인해 A3로 판정한다.
  - 턴 경계마다 축소가 생기면, 이전 턴의 tool 결과나 중간 메시지를 이력에서 빼는 구조다. 이 경우 다음 턴 첫 호출은 고정부까지만 히트한다.
  - 축소 없이 CallIdx별 달성률만 떨어지면 A1(재렌더링)이나 A2다.
- **낭비 추정**: Σ(축소 이벤트) \[Cacheable(편집 지점 이전 공유 길이) − CacheTok\] × (P\_in − P\_cache). 공유 길이는 \[C\] `InputMessages`를 비교해 구한다. 편집으로 줄어든 Input Σ(PrevIn − InTok)도 함께 기록해야 순효과를 판단할 수 있다.

### A4. Prefix 1,024 미만

- **정의**: 고정 prefix가 모델의 최소 캐시 길이(MinTok)보다 짧으면 턴 첫 호출은 구조적으로 캐시되지 않는다. 짧은 단발 호출(분류, 라우팅)도 마찬가지다.
- **확인 방법**: \[S\] S\_a를 측정한다. \[T\] InTok < MinTok인 호출의 비중을 본다.
- **지표**: 캐시 불가 호출 비중, 캐시 불가 비용 비중

**쿼리: 캐시 불가 호출 비중.** Input이 모델의 MinTok보다 짧아 캐시 대상이 될 수 없는 호출의 건수와 비용 비중을 구한다. A4의 영향 크기를 가늠한다.

SQL: `queries/metrics/A4.sql`의 `small_calls`.

- **판정**
  - S\_a < MinTok이고 턴 첫 호출의 CacheTok이 항상 0이면 A4로 판정한다. A1과는 S\_a 크기로 구분한다.
  - S\_a가 1,024를 조금 넘는데도 첫 호출이 늘 미스 나면, 요청 설정 때문에 실제 최소 길이가 더 클 수 있다(모델별 캐시 파라미터 참고).
  - SmallCostShare가 작으면 영향이 미미하다.
  - GPT-5.6 이후 모델에서 `prompt_cache_options.mode`가 `explicit`인데 breakpoint가 없으면 캐시가 아예 동작하지 않는다. \[S\]로 함께 확인한다.
- **낭비 추정**: 구조적 한계에 해당하므로 캐시 불가 비용 = Σ(InTok < MinTok) Cost로 기록한다.

### A5. 캐시 만료

- **정의**: 같은 prefix의 요청 간격이 캐시 보존 시간보다 길어 매번 미스 난다. 트래픽이 적은 에이전트에서 주로 생긴다. 보존 시간은 모델과 retention 설정에 따라 다르므로 "모델별 캐시 파라미터" 표의 WindowMin을 기준으로 쓴다.
- **확인 방법**: \[T\] 같은 에이전트의 연속 첫 호출 간격을 WindowMin 배수 구간으로 나눠 히트율을 비교한다. \[S\] retention 설정(`prompt_cache_retention`)을 확인한다.
- **지표**: 간격 구간별 첫 호출 히트율

**쿼리: 요청 간격별 첫 호출 히트율.** 같은 에이전트의 연속된 턴 첫 호출 사이 간격을 모델 보존 시간(WindowMin)의 배수 구간으로 나눠 히트율을 비교한다. 보존 시간을 넘는 구간에서 히트율이 크게 낮아지는지로 캐시 만료(A5)를 확인한다.

SQL: `queries/metrics/A5.sql`의 `gap_hit`.

- **판정**
  - "보존 시간 밖" 구간의 히트율이 "보존 시간 안" 구간보다 낮으면(단측 검정) A5로 판정한다.
  - "보존 시간 안" 구간부터 히트율이 낮으면 A1, A2, A6을 확인한다.
- **낭비 추정**: Σ(보존 시간 밖 첫 호출) Cacheable(S\_a) × (P\_in − P\_cache) × ("보존 시간 안" 히트율 − 해당 구간 히트율).

### A6. 트래픽 분산·쏠림

- **정의**: 두 가지 경우가 있다.
  - 분산: 같은 prefix 요청이 여러 엔드포인트로 흩어진다. Azure 구독 간에는 캐시가 공유되지 않는다.
  - 쏠림: 캐시는 개별 머신에 저장된다. 같은 prefix·`prompt_cache_key` 조합의 요청이 분당 약 15건(RpmLimit)을 넘으면 일부가 캐시 없는 머신으로 넘어가 미스 난다.
- **확인 방법**: \[T\] 엔드포인트별 히트율과 분당 요청 수 구간별 히트율을 본다. \[S\] 게이트웨이 라우팅 구성과 `prompt_cache_key` 사용 여부를 확인한다.
- **지표**: 엔드포인트별 히트율, 분당 요청 수 구간별 히트율

**쿼리: 엔드포인트별 히트율.** 에이전트가 호출한 엔드포인트별로 캐시 히트율을 비교해 트래픽 분산(A6)을 확인한다.

SQL: `queries/metrics/A6.sql`의 `endpoint_hit`.

**쿼리: 분당 요청 수 구간별 히트율.** 에이전트별 분당 LLM 호출 수를 RpmLimit 대비 구간으로 나눠 히트율을 비교한다. RpmLimit을 넘는 구간에서 생기는 쏠림(A6)을 확인한다.

SQL: `queries/metrics/A6.sql`의 `rpm_hit`.

- **판정**
  - 한 에이전트가 여러 엔드포인트를 쓰고 최저 엔드포인트의 히트율이 최고 엔드포인트보다 낮으면(단측 검정) 분산으로 판정한다. 리전·배포 간 공유 여부는 문서에 명시되지 않았으므로 이 실측값으로 판단한다.
  - RpmLimit을 넘는 구간의 히트율이 RpmLimit 이하 구간보다 낮으면(단측 검정) 쏠림으로 판정한다.
- **낭비 추정**: Σ(해당 구간 호출) Cacheable(S\_a) × (기준 구간 히트율 − 해당 구간 히트율) × (P\_in − P\_cache).

## B. 고정 Input 비대

고정 Input S\_a는 에이전트 a의 모든 LLM 호출에 반복해서 포함된다. 그래서 낭비는 "줄일 수 있는 토큰 × 호출 수 × P\_eff"로 커진다. 캐시가 잘 맞으면 P\_eff가 P\_cache에 가까워져 영향이 작아지므로, 항상 A의 결과와 함께 본다.

### S\_a를 쓰는 지점

S\_a는 A 범주와 B 범주의 지표 대부분과 D4가 입력으로 받는다. 아래 지점은 S\_a 없이 산출할 수 없으므로 3-B 측정을 먼저 한다.

| 지점 | S\_a를 쓰는 방식 |
| --- | --- |
| A 공통: 캐시 달성률 | 턴 첫 호출의 이론 캐시 가능량 Cacheable(S\_a) |
| A1 | 보존 시간 내 첫 호출 조건 S\_a ≥ MinTok, 낭비 추정 |
| A2 | 낭비 추정 Cacheable(S\_a) |
| A4 | S\_a < MinTok 판정 |
| A5 | 낭비 추정 Cacheable(S\_a) |
| A6 | 낭비 추정 Cacheable(S\_a) |
| B 공통: 고정 Input 비용 비중 | S\_a × LLM 호출 수 × P\_eff |
| B4 | 하위 에이전트와 상위 에이전트의 S\_a 비교, 낭비 추정 |
| D4 | 위임 시 전달 비율의 분자에서 S\_sub, 분모에서 S\_parent를 뺀다 |

S\_a를 구하는 방법마다 정확도와 준비물이 다르다.

- 측정법 1(실험 호출)은 고객 배포에 직접 요청을 보내야 하므로 텔레메트리만으로는 할 수 없다. 사전 준비 항목으로 따로 잡는다.
- 측정법 3(턴 첫 호출 InTok 1백분위수)은 하한 근사다. 실제 S\_a보다 작게 나오므로, 이 값으로 구한 낭비 추정은 낮게 나온다. 보고서에 근사값임을 밝힌다.

### B 공통: S\_a 측정법

1. **실험 호출 \[S\] (가장 정확)**: 같은 배포에 System prompt와 tool을 그대로 두고, 사용자 메시지 "hi"만 보낸다. 응답의 input\_tokens에서 메시지 몇 토큰을 빼면 S\_a다. tool 하나를 뺀 요청과의 차이가 그 tool의 실제 토큰이다.
2. **원문 토큰화 \[C\]**: `SystemInstructions`와 `ToolDefinitions`를 tiktoken `o200k_base`로 센다. 서비스가 tool 정의를 내부 형식으로 바꾸므로 근사값이다.
3. **하한 근사 \[T\]**: 턴 첫 호출 InTok의 1백분위수 ≈ S\_a + 짧은 사용자 메시지.

**코드: 원문 토큰 수 계산(측정법 2).** `SystemInstructions`와 `ToolDefinitions` 원문을 tiktoken `o200k_base`로 토큰화해 S\_a를 근사한다.

```python
import tiktoken
enc = tiktoken.get_encoding("o200k_base")
print(len(enc.encode(system_instructions)), len(enc.encode(tool_definitions_json)))
```

**쿼리: 턴 첫 호출 Input 분포(측정법 3).** 턴 첫 호출 InTok의 1백분위수를 S\_a의 하한 근사로 쓴다. 실험 호출이나 원문으로 S\_a를 잴 수 없을 때의 대안이다.

SQL: `queries/metrics/B-common.sql`의 `first_call_dist`.

**고정 Input 비용 비중** = Σ(S\_a × 에이전트 a의 LLM 호출 수 × P\_eff) ÷ 전체 비용. 고정 Input이 비용에서 차지하는 비중이며, 0.3 이상이면 B 범주를 점검한다(초기값). P\_eff에 캐시 효과가 들어 있어서, 캐시가 잘 맞는 에이전트는 토큰이 커도 비중이 낮게 나온다.

**쿼리: 고정 Input 비용 비중.** 에이전트별로 S\_a × LLM 호출 수 × P\_eff로 고정 Input 비용을 구하고, 그 에이전트 전체 비용 대비 비중을 계산한다. 전체 비중은 FixedCost 합 ÷ Cost 합으로 구한다. B 범주를 점검할지 판단할 때 쓴다.

SQL: `queries/metrics/B-common.sql`의 `fixed_share`, `overall`.

한 에이전트가 여러 모델을 쓰면 그중 한 모델의 단가만 쓰므로, 이 경우 Agent와 Model로 나눠 계산한다.

### B1. 미사용 Tool 상시 전송

- **정의**: 정의된 tool 중 거의 호출되지 않는 tool의 schema가 모든 LLM 호출에 포함된다.
- **확인 방법**: \[C\] `ToolDefinitions`에서 정의된 tool 목록을 뽑는다. \[T\] `execute_tool` span으로 실제 사용을 센다.
- **지표**: tool별 사용률 = 그 tool이 한 번 이상 호출된 턴 수 ÷ 에이전트 전체 턴 수

**쿼리: Tool별 사용률.** 최근 `ToolDefinitions`에서 정의된 tool 목록을 뽑고, `execute_tool` span으로 실제 호출된 턴 수를 붙인다. 정의만 되어 있고 쓰이지 않는 tool(B1)을 찾는다.

SQL: `queries/metrics/B1.sql`의 `tool_use`.

- **판정**
  - 관찰 기간(최소 2주) 동안 0회이거나 사용률 1% 미만이고, 해당 tool의 토큰이 크면 B1로 판정한다.
  - &#91;S\]로 드물지만 반드시 필요한 tool(예외 처리, 에스컬레이션)인지 확인한다.
  - 하위 에이전트의 tool은 RootAgent로 집계되므로, 멀티 에이전트에서는 D4의 에이전트 매핑을 쓴다.
- **낭비 추정**: Σ(미사용 tool) tool 토큰 × 에이전트 LLM 호출 수 × P\_eff.

### B2. 장황한 Tool schema

- **정의**: tool description, 파라미터 설명, enum, 예시가 tool 선택과 인자 생성에 필요한 정도를 넘어 길다.
- **확인 방법**: \[C\] tool별 정의 크기를 비교한다. \[S\] 상위 tool을 리뷰한다.
- **지표**: tool별 정의 문자 수, description 문자 수(상대 비교용). 토큰 수는 측정법 1·2로 확정한다.

**쿼리: Tool별 정의 크기.** 최근 `ToolDefinitions`를 tool 단위로 펼쳐 정의 전체와 description의 문자 수를 구한다. 다른 tool보다 유난히 긴 schema(B2)를 찾는다.

SQL: `queries/metrics/B2.sql`의 `tool_schema_size`.

- **판정**: 정의 크기가 같은 에이전트 tool 중앙값의 3배 이상이면 후보로 올린다. 리뷰에서 긴 enum, 반복 설명, 사용 예시가 대부분을 차지하면 B2로 판정한다.
- **낭비 추정**: 리뷰로 산정한 축약 가능 토큰 × 에이전트 LLM 호출 수 × P\_eff.

### B3. System prompt 비대

- **정의**: 중복·충돌·사문화된 규칙, 과다한 few-shot 예시, 참조 데이터 전문(정책 문서, 제품 목록)이 System prompt에 들어 있다.
- **확인 방법**: \[C\] `SystemInstructions`를 꺼내 토큰화한다. \[S\] 구성 요소별로 나눠 리뷰한다.
- **지표**
  - System prompt 토큰
  - 고정 Input 비용 비중(B 공통)
  - 구성별 토큰 비중: 규칙, 예시, 참조 데이터, 페르소나 \[S\]

**쿼리: 에이전트별 최신 System prompt.** 에이전트마다 가장 최근 `SystemInstructions` 원문과 길이를 꺼낸다. 토큰화와 구성 요소 리뷰(B3)의 입력으로 쓴다.

SQL: `queries/metrics/B3.sql`의 `system_prompt_size`.

- **판정**
  - 고정 Input 비용 비중이 0.3 이상이고, 리뷰에서 예시·참조 데이터·중복 규칙이 큰 비중이면 B3로 판정한다.
  - 고정 Input 비용 비중은 P\_eff로 캐시 효과를 이미 반영한다. 토큰으로는 커도 비용 비중이 작으면 캐시가 잘 맞는 경우이므로 우선순위를 낮춘다.
- **낭비 추정**: 축약 가능 토큰 × 에이전트 LLM 호출 수 × P\_eff.

### B4. 하위 에이전트 prompt 중복

- **정의**: 멀티 에이전트 구조에서 하위 에이전트들이 같은 공통 블록(회사 정책, 공통 규칙)을 각자 포함한다. 또는 쓰지 않는 instruction과 tool까지 상위에서 그대로 물려받는다.
- **확인 방법**: \[C\] 에이전트별 System prompt·tool 정의 크기와 공통 블록을 비교한다. \[T\] 에이전트별 호출 수와 tool 사용률(B1)을 본다.
- **지표**: 에이전트별 S\_a, 에이전트 간 공통 블록 토큰, 에이전트별 정의 tool 수 대비 사용 tool 수

**쿼리: 에이전트별 고정 Input 크기.** 에이전트마다 System prompt 길이, tool 정의 길이, 정의된 tool 수를 구한다. 하위 에이전트끼리 비교해 중복(B4)을 찾는다.

SQL: `queries/metrics/B4.sql`의 `agent_fixed_size`.

- **판정**: 하위 에이전트 S\_a가 상위와 비슷하거나 공통 블록이 여러 에이전트에 반복되고, 하위 에이전트의 tool 사용률이 낮으면 B4로 판정한다.
- **낭비 추정**: Σ(에이전트 a) (S\_a − 역할에 필요한 토큰) × 에이전트 a의 LLM 호출 수 × P\_eff.

## C. 컨텍스트 누적

이력과 tool 결과는 한 번 들어오면 이후 모든 호출에 다시 포함된다. 그래서 같은 토큰이라도 턴 초반, 대화 초반에 들어올수록 비싸다. C 범주는 "얼마나 들어오는가"와 "몇 번 재전송되는가"를 함께 잰다.

### C1. 대화 이력 무제한 누적

- **정의**: 대화 이력에 상한이나 요약이 없어, 턴이 쌓일수록 매 호출이 전체 이력을 다시 보낸다. Responses API의 `previous_response_id`나 conversation처럼 서버가 이력을 관리해도, 체인의 이전 Input 토큰은 매번 Input으로 과금된다. ([OpenAI](https://developers.openai.com/api/docs/guides/conversation-state))
- **확인 방법**: \[T\] 대화 안 턴 순번별 첫 호출 InTok을 본다. `gen_ai.conversation.id`가 있어야 한다.
- **지표**
  - 턴 간 Input 증가 배수 = 대화 길이 p90 턴의 첫 호출 InTok p50 ÷ 1턴 첫 호출 InTok p50. 턴 순번(TurnIdx)별 첫 호출 InTok 추이(그래프)도 함께 본다
  - 대화당 턴 수 p50, p90
  - 긴 대화 비용 비중 = 턴 수 상위 10% 대화의 비용 ÷ 전체 비용

**쿼리: 턴 순번별 첫 호출 Input.** 대화의 몇 번째 턴인지에 따라 첫 호출 InTok 중앙값이 어떻게 늘어나는지 선 그래프로 그린다. 이력에 상한이 있는지(C1) 확인한다.

SQL: `queries/metrics/C1.sql`의 `turn_idx_input`.

**쿼리: 긴 대화의 비용 비중.** 대화당 턴 수 분포와, 턴 수 상위 10% 대화가 전체 비용에서 차지하는 비중을 구한다. C1의 영향 크기를 가늠한다.

SQL: `queries/metrics/C1.sql`의 `conv_cost`.

**쿼리: 턴 간 Input 증가 배수.** 에이전트별로 대화 길이의 p90에 해당하는 턴을 구하고, 그 턴의 첫 호출 InTok 중앙값을 1턴의 값과 비교한다. C1 판정 기준을 계산할 때 쓴다.

SQL: `queries/metrics/C1.sql`의 `cross_turn`.

- **판정**
  - 턴 간 Input 증가 배수가 2 이상이면, 대화 길이 p90 시점에 이력이 기본 Input보다 커진 것이므로 C1로 판정한다(초기값). C2의 턴 내 증가 배수와 같은 기준이다.
  - 턴 순번 그래프가 일정 턴 이후 평탄해지거나 톱니 모양이면 절단·요약이 있는 것이다. 이때는 A3(이력 편집에 따른 캐시 손실)와 함께 본다.
  - LongConvCostShare는 C1의 영향 크기를 해석할 때 쓴다.
  - conversation.id가 없으면 이 항목은 진단할 수 없다. "계측 부재"로 기록한다.
- **낭비 추정**: 최근 k턴만 필요하다고 볼 때, 초과 이력 ≈ Σ(턴 순번 t > k) (턴 t 첫 호출 InTok − 턴 k 첫 호출 InTok) × 턴 t의 LLM 호출 수 × P\_eff. k는 리뷰로 정한다.

### C2. Tool 결과 원문 전달

- **정의**: tool 결과(전체 JSON, HTML, 로그, DB 행 전체)를 가공 없이 다음 호출 Input에 넣는다. 결과는 이력에 남아 이후 모든 호출에 다시 포함된다.
- **확인 방법**: \[T\] 턴 안에서 Input이 얼마나 늘어나는지 비율로 본다. \[C\] `ToolCallResult`로 tool별 크기를 직접 잰다.
- **지표**
  - 턴 내 Input 증가 배수 = 턴 마지막 호출 InTok ÷ 턴 첫 호출 InTok. 호출이 2회 이상인 턴만 센다.
  - 호출당 증가율 = ΔIn\_i ÷ InTok\_(i−1). ΔIn\_i = InTok\_i − InTok\_(i−1) − OutTok\_(i−1)로, 직전 Input과 직전 Output(tool 호출 메시지)을 빼고 남는 부분이며 대부분 tool 결과다. 이전 reasoning이 다음 Input에 포함되지 않는 API 방식이면 OutTok 대신 OutTok − ReasonTok을 뺀다.
  - tool별 결과 크기 p50, p95 \[C\]
  - 재전송 계수 = Σ(ΔIn × 그 시점 이후 턴 안에 남은 호출 수) ÷ Σ ΔIn. 결과 토큰 하나가 턴 안에서 평균 몇 번 실리는지를 뜻하며, 영향 크기를 해석할 때 쓴다.

**쿼리: 턴 내 Input 증가 배수와 호출당 증가율.** 턴 첫 호출 대비 마지막 호출의 Input 배수와, 연속 호출 사이 증가분(ΔIn)이 직전 Input에서 차지하는 비율을 에이전트별로 구한다. 재전송 계수도 함께 구한다. content 없이 C2를 진단할 때 쓴다.

SQL: `queries/metrics/C2.sql`의 `turn_growth`, `growth_steps`.

**쿼리: Tool별 결과 크기.** `ToolCallResult` 원문 길이의 tool별 p50, p95를 구한다. 결과가 큰 tool(C2)을 특정한다.

SQL: `queries/metrics/C2.sql`의 `result_size`, `result_carry`.

- **판정**
  - 턴 내 Input 증가 배수 p50이 2 이상이면, 턴 안에서 Input이 두 배 넘게 늘어나는 것이므로 이 증상이 있다고 본다(초기값).
  - 호출당 증가율 p95가 0.5 이상이면 한 번에 직전 Input의 절반 이상이 더해지는 호출이 있다. 결과가 큰 C2나 C3이며, \[C\] 샘플에서 응답에 실제 쓰인 부분이 일부뿐이면 C2로 판정한다.
  - 증가율은 낮은데 배수가 크면 작은 증가가 여러 번 쌓인 것이므로 호출 수가 많은 D1을 본다.
  - 큰 증가분 사이에 `execute_tool` span이 없으면 C3(검색 컨텍스트)나 D4(하위 에이전트 응답)를 확인한다.
  - tool을 특정할 때는 같은 에이전트 안에서 결과 크기 p95가 큰 tool부터 본다.
- **낭비 추정**: Σ(tool 결과) (결과 토큰 − 필요 토큰) × 이후 남은 호출 수 × P\_eff. 필요 토큰 비율은 \[C\] 샘플 리뷰로 정한다.

### C3. 검색 컨텍스트 과다

- **정의**: 검색 tool(file\_search, Azure AI Search 등)이 너무 많은 문서(top-k 과다), 큰 chunk, 관련도가 낮거나 중복된 문서를 돌려준다.
- **확인 방법**: \[T\] `retrieval` span의 `gen_ai.retrieval.top_k`와 `gen_ai.retrieval.documents`(id, score)를 본다. 검색이 tool로만 기록되면 C2 방식으로 결과 크기를 잰다. \[S\] top\_k, chunk 크기, 점수 임계값 설정을 확인한다.
- **지표**
  - 검색 1회당 반환 문서 수와 top\_k
  - 저점수 문서 비율 = 점수가 임계값 미만인 문서 비중
  - 턴 내 중복 문서 수 = 같은 턴에서 같은 문서 id가 다시 반환된 건수
  - 검색 1회당 결과 토큰(C2 쿼리를 검색 tool로 한정)

**쿼리: 검색 결과 품질.** `retrieval` span의 반환 문서를 펼쳐 검색당 문서 수, top\_k, 저점수 문서 비율, 턴 내 중복 문서 수를 구한다. 검색 컨텍스트 과다(C3)를 확인한다.

SQL: `queries/metrics/C3.sql`의 `retrieval_quality`, `search_result`.

저점수 기준(`config/thresholds.csv`의 `low_score`, 0.5)은 검색 엔진의 점수 척도에 맞춰 바꾼다. BM25, 벡터 유사도, semantic reranker의 척도가 서로 다르다.

- **판정**: 검색당 결과 토큰이 크고, 저점수·중복 문서 비율이 0.5 이상이면(고점수 문서가 과반이 아니면) C3 후보로 올린다. 결과가 크더라도 대부분 고점수이고 응답에 인용되면 정상이다.
- **낭비 추정**: (저점수 + 중복 문서 토큰) × 이후 남은 호출 수 × P\_eff.

## D. 호출 구조

턴 비용은 호출 수에 비례하는 것보다 빠르게 늘어난다. 호출마다 그때까지 쌓인 컨텍스트 전체를 다시 보내기 때문이다. D 범주는 불필요한 호출이 어디서 생기는지를 찾는다.

`gen_ai.response.finish_reasons` 값의 표기는 계측과 API에 따라 다를 수 있다. OTel 규약에서 이 속성은 문자열 배열(`["tool_calls"]`)이고 OpenAI 계열은 `tool_calls`를 쓴다. 아래 SQL은 이 값을 가정하므로, 먼저 `SELECT Finish, count(*) FROM calls GROUP BY 1`로 실제 값을 확인한다.

### D1. 루프·중복 Tool 호출

- **정의**: 같은 tool을 같은 인자로 반복 호출하거나, 목표에 도달하지 못한 채 같은 단계를 반복하다 최대 반복 횟수에 걸린다.
- **확인 방법**: \[T\] 턴당 호출 수 분포와 턴 안 같은 tool 반복을 본다. \[C\] tool 인자 해시로 완전 중복을 찾는다.
- **지표**
  - 턴당 LLM 호출 수 p50, p95, max와 꼬리 비율 = p95 ÷ p50
  - 꼬리 턴 비용 비중 = 호출 수 p95 이상 턴의 비용 ÷ 전체 비용
  - 중복 tool 호출 비율 \[C\] = 같은 턴에서 (tool 이름, 인자) 쌍이 반복된 호출 ÷ 전체 tool 호출

**쿼리: 턴당 호출 수 분포와 꼬리 비용.** 에이전트별 턴당 LLM 호출 수의 p50, p95, max와, p95 이상 턴의 비용 비중을 구한다. 호출이 비정상적으로 많은 턴(D1)이 있는지 본다.

SQL: `queries/metrics/D1.sql`의 `tail_turns`, `n_hist`.

**쿼리: 중복 Tool 호출.** 같은 턴에서 같은 tool을 같은 인자로 다시 부른 호출을 tool별로 센다. 루프·중복 호출(D1)을 확정할 때 쓴다.

SQL: `queries/metrics/D1.sql`의 `dup_tool_calls`, `dup_tail_vs_rest`.

content가 없으면 \[T\] 근사로, 한 턴에서 같은 tool을 3회 이상 부른 턴의 비율을 본다. SQL은 `queries/metrics/D1.sql`의 `repeat_turns`다.

- **판정**
  - 꼬리 턴 비용 비중이 0.15 이상이면, 호출 수 상위 5% 턴이 균등 분배 비중(5%)의 3배 넘는 비용을 차지하는 것이므로 꼬리 턴을 샘플로 연다(초기값). 에이전트 토큰 사용은 원래 꼬리가 길고 같은 작업도 실행마다 크게 달라서, 꼬리가 차지하는 비용을 기준으로 삼는다([Bai et al., 2026](https://arxiv.org/pdf/2604.22750)).
  - MaxN이 프레임워크의 최대 반복 횟수와 같은 턴이 있으면 반복 상한에 걸린 것이므로 바로 D1로 확정한다.
  - 꼬리 턴의 중복 tool 호출 비율이 나머지 턴보다 높으면(단측 검정) D1로 판정한다.
  - 꼬리 비율(p95 ÷ p50)은 분포 모양을 해석할 때 쓴다.
  - 꼬리 턴에 tool 실패가 많으면 F1, LLM 오류가 많으면 F2를 본다.
- **낭비 추정**: Σ(중복 tool 호출) 그 결과를 처리한 다음 LLM 호출의 비용. 간이식은 꼬리 턴 비용 − 꼬리 턴 수 × 턴 비용 중앙값이다.

### D2. 병렬 가능한 호출의 순차 처리

- **정의**: 서로 독립인 tool 호출을 LLM 호출 1회에 하나씩 실행한다. 병렬 tool 호출이면 한 번에 끝날 일을 여러 번 왕복하고, 왕복마다 누적 컨텍스트를 다시 보낸다.
- **확인 방법**: \[T\] tool 호출로 끝난 LLM 호출 1회당 실행된 tool 수를 본다. \[S\] 병렬 tool 호출 설정과 instruction을 확인한다. \[C\] 연속된 tool이 서로 의존하는지(다음 tool 인자에 이전 결과 값이 들어가는지) 본다.
- **지표**
  - 라운드당 tool 수 = 턴의 tool 호출 수 ÷ 턴에서 tool 호출로 끝난 LLM 호출 수
  - 순차 연쇄 턴 비율 = tool 라운드가 3회 이상인데 라운드마다 tool이 1개인 턴의 비중

**쿼리: 라운드당 Tool 수.** tool 호출로 끝난 LLM 호출 1회당 실행된 tool 수와, tool을 하나씩만 부르는 순차 연쇄 턴의 비율을 구한다. 순차 처리(D2)를 확인한다.

SQL: `queries/metrics/D2.sql`의 `round_tools`.

- **판정**: 라운드당 tool 수가 1에 가깝고 순차 연쇄 턴이 있는데, \[C\]로 보니 연속 tool이 서로의 결과를 인자로 쓰지 않으면 D2로 판정한다. 의존 관계가 있으면 정상이다. 순차 연쇄 턴 비율의 수치 기준은 두지 않는다. 비율과 낭비 상한을 기록하고 \[C\] 확인 전까지 판정을 보류한다.
- **낭비 추정**: 연쇄 길이 k인 턴마다 (k − 1) × 해당 라운드들의 평균 InTok × P\_eff.

### D3. 결정적 단계의 LLM 처리

- **정의**: 규칙으로 처리할 수 있는 단계(라우팅, 의도 분류, 형식 변환, 단순 추출)를 LLM 호출로 처리한다.
- **확인 방법**: \[T\] 짧은 출력 호출의 비중과 비용을 본다. \[C\] 그 출력이 소수의 고정 값으로 수렴하는지 본다.
- **지표**
  - 짧은 출력 호출 = (OutTok − ReasonTok) < 50이고 tool 호출이 아닌 호출. 그 비중과 비용 비중
  - 출력 다양성 \[C\] = 고유 `OutputMessages` 수 ÷ 짧은 출력 호출 수

**쿼리: 짧은 출력 호출 비중.** 가시 출력이 50 토큰 미만이고 tool 호출이 아닌 호출의 건수와 비용 비중을 에이전트·모델별로 구한다. 규칙으로 대체할 수 있는 단계(D3)의 후보를 찾는다.

SQL: `queries/metrics/D3.sql`의 `short_output`.

**쿼리: 짧은 출력의 다양성.** 짧은 출력 호출의 `OutputMessages`가 몇 가지 값으로 나오는지 센다. 소수의 레이블로 수렴하는지 보고 D3를 판정한다.

SQL: `queries/metrics/D3.sql`의 `output_diversity`.

- **판정**: 짧은 출력 호출의 비용 비중이 의미 있고 출력 다양성이 낮으면(출력 종류가 몇 개의 레이블뿐이면) D3로 판정한다. 비용 비중과 다양성의 수치 기준은 두지 않는다. 두 값을 기록하고 \[S\]로 규칙 처리가 가능한 단계인지 확인하기 전까지 판정을 보류한다. 그 호출이 상위 모델이면 E1과 겹친다.
- **낭비 추정**: 해당 호출의 비용 전체. 규칙으로 대체할 수 있다고 볼 때의 상한이다.

### D4. 에이전트 간 컨텍스트 중복

- **정의**: 상위 에이전트가 하위 에이전트에 전체 이력과 이전 tool 결과를 그대로 넘긴다. 또는 하위 에이전트의 긴 응답 전체를 다시 Input으로 받는다.
- **확인 방법**: \[T\] 루트가 아닌 `invoke_agent` span으로 하위 에이전트 실행을 찾는다. 하위 첫 호출 InTok과 위임 직전 상위 호출 InTok을 비교한다. \[C\] 실제로 전달된 메시지를 확인한다.
- **지표**
  - 위임 시 전달 비율 = (하위 첫 호출 InTok − S\_sub) ÷ (위임 직전 상위 호출 InTok − S\_parent). 분모는 상위 호출의 가변 컨텍스트(이력, tool 결과, 사용자 입력)다. 하위의 고정 Input과 상위의 고정 Input을 양쪽에서 빼므로 상위 가변 컨텍스트를 모두 넘기면 1이 된다.
  - 하위 응답 반입 크기 = 하위 실행 직후 상위 다음 호출의 ΔIn(C2 정의)

**쿼리: 위임 시 Input 전달량.** 하위 에이전트의 첫 호출 InTok과 위임 직전 상위 에이전트 호출의 InTok을 짝지어 비교한다. 양쪽에서 S\_a를 빼고 전달 비율을 구해 상위 이력이 하위로 통째로 넘어가는지(D4) 확인한다.

SQL: `queries/metrics/D4.sql`의 `delegation`.

하위 에이전트의 LLM 호출 span은 그 `invoke_agent` span의 자손이라고 가정한다. 하위 `invoke_agent`의 부모는 상위 `invoke_agent`이거나, 에이전트를 tool로 부르는 구조에서는 위임 `execute_tool` span이다. `queries/views.sql`의 `call_owner`와 `sub_agents.ParentRunSpanId`가 가장 가까운 조상 `invoke_agent`를 찾으므로 어느 쪽이든 같은 결과가 나온다. 같은 귀속으로 하위 에이전트별 호출 수를 구해 B1, B4에도 쓴다.

- **판정**: 전달 비율 p50이 0.8 이상이면 상위 가변 컨텍스트 대부분이 하위로 넘어가는 것이다(초기값). 비율 1이 "상위 가변 컨텍스트 전체 전달"을 뜻하므로 기준의 의미가 분명하다. 상위 에이전트의 S\_a를 모르면 판정을 보류한다. \[C\]로 하위 에이전트 역할에 필요한 부분인지 확인해 D4로 판정한다. 하위 응답 반입이 크면 상위 쪽에서 C2와 같은 재전송 문제가 생긴다.
- **낭비 추정**: Σ(위임) (전달 토큰 − 필요 토큰) × 하위 에이전트 호출 수 × P\_eff.

## E. 모델·출력 설정

Output 토큰은 Input보다 단가가 몇 배 높고, Reasoning 토큰은 보이지 않지만 Output 단가로 과금된다. E 범주는 단계에 비해 비싼 모델이나 설정이 쓰이는지를 찾는다. 품질 영향은 진단 범위 밖이므로, 여기서 찾은 것은 "후보"로 기록한다.

### E1. 단순 단계에 상위 모델

- **정의**: 계획, tool 인자 생성, 분류처럼 출력이 짧은 단계까지 최상위 모델이나 reasoning 모델을 쓴다.
- **확인 방법**: \[T\] LLM 호출을 유형으로 나누고, 유형 × 모델별 비용을 본다.
- **지표**: 호출 유형별 모델 비용 비중, 유형별 가시 출력(OutTok − ReasonTok) p50, 잠재 절감 비중

**쿼리: 호출 유형별·모델별 비용.** LLM 호출을 계획, tool 처리, 짧은 출력, 최종 응답, 기타로 나누고 유형 × 모델별 비용과 출력 크기를 구한다. 단순 단계에 상위 모델을 쓰는지(E1) 본다.

SQL: `queries/metrics/E1.sql`의 `type_model_cost`, `savings`, `savings_by_type`.

이 호출 유형 분류(`queries/views.sql`의 `call_types`)는 E2–E4에서도 같이 쓴다.

- **판정**: 잠재 절감 비중 = Σ(유형 1–3 상위 모델 호출의 Cost − 소형 모델 단가로 다시 계산한 Cost) ÷ 전체 비용. 이 값이 0.1 이상이면 E1 후보로 기록한다(초기값). 품질 평가를 돌릴 만한 최소 절감폭이라는 운영 판단이며, 소형 모델로 품질이 유지되는지는 별도 평가 대상이다.
- **낭비 추정 (상한)**: 잠재 절감 비중의 분자, 즉 Σ(후보 호출) \[Cost − 소형 모델 단가로 다시 계산한 Cost\].

### E2. Reasoning 과다

- **정의**: reasoning 모델이 단순 단계에서도 높은 reasoning 수준으로 실행되어 보이지 않는 Output 토큰이 쌓인다.
- **확인 방법**: \[T\] `gen_ai.usage.reasoning.output_tokens`와 `gen_ai.request.reasoning.level`을 본다. \[S\] 코드에 설정된 reasoning effort를 확인한다.
- **지표**
  - Reasoning 비중 = Σ ReasonTok ÷ Σ OutTok
  - 호출 유형·reasoning 수준별 ReasonTok p50, p95
  - Reasoning 비용 = Σ ReasonTok × P\_out

**쿼리: Reasoning 비중.** 에이전트·모델·reasoning 수준별로 Output 중 reasoning 토큰의 비중과 분포를 구한다. Reasoning 과다(E2)를 확인한다.

SQL: `queries/metrics/E2.sql`의 `reasoning`, `reasoning_by_type`.

- **판정**: 수치 기준은 두지 않는다. reasoning 토큰은 문제 복잡도에 따라 수백에서 수만까지 달라져서 적정 수준이 작업에 종속되기 때문이다([OpenAI](https://developers.openai.com/api/docs/guides/reasoning)). 대신 단순 단계(유형 1–3)가 최종 응답 단계(유형 4)와 같은 reasoning 수준으로 실행되고 ReasonTok 분포도 비슷하면 E2 후보로 기록한다. Level이 비어 있으면 \[S\]로 설정값을 확인한다.
- **낭비 추정**: Σ(후보 호출) (ReasonTok − 같은 유형 저수준 호출의 ReasonTok p50) × P\_out. 저수준 호출이 없으면 소수 샘플을 낮은 수준으로 다시 실행해 비교한다.

### E3. 장황한 최종 응답

- **정의**: 최종 응답이 용도에 비해 길다. 예: 반복 요약, 서론과 맺음말, 과도한 형식. 최종 응답은 다음 턴의 이력으로 다시 포함된다.
- **확인 방법**: \[T\] 최종 응답 호출의 가시 출력 분포와 max\_tokens 설정을 본다. \[C\] 샘플을 리뷰한다.
- **지표**
  - 최종 응답 가시 출력 p50, p95
  - Output 비용 비중 = Σ(OutTok × P\_out) ÷ Σ Cost
  - max\_tokens 미설정 비율

**쿼리: 최종 응답 길이.** 턴 마지막 호출 중 tool 호출이 아닌 응답의 가시 출력 분포와 max\_tokens 미설정 비율을 구한다. 장황한 응답(E3)을 확인한다.

SQL: `queries/metrics/E3.sql`의 `final_response`.

**쿼리: Output 비용 비중.** 에이전트별 전체 비용 중 Output 토큰 비용의 비중을 구한다. E3의 영향 크기를 가늠한다.

SQL: `queries/metrics/E3.sql`의 `output_cost_share`.

- **판정**: 범용 수치 기준은 두지 않는다. 적정 응답 길이는 서비스 용도(짧은 Q&A, 요약, 보고서 생성 등)에 따라 수십에서 수천 토큰까지 갈리기 때문이다. 고객이 정한 목표 길이가 있으면, P95가 목표를 넘고 \[C\] 샘플에서 반복이나 불필요한 구간이 확인될 때 E3로 판정한다. 목표 길이가 없으면 판정을 보류하고 고객에게 확인할 항목으로 남긴다.
- **낭비 추정**: Σ(최종 응답) (가시 출력 − 목표 길이) × P\_out. 이력이 유지되면 이후 턴의 재전송분 × P\_eff를 더한다.

### E4. 출력 잘림

- **정의**: 출력 상한에 걸려 응답이 중간에 끊긴다. 생성된 토큰은 과금되지만 쓸 수 없고, 재요청이나 후속 호출로 이어진다. reasoning 모델은 reasoning이 상한을 다 써서 실제 출력이 비는 경우도 있다.
- **확인 방법**: \[T\] finish\_reasons의 `length`와 `gen_ai.response.status`의 `incomplete`를 본다.
- **지표**
  - 잘림 비율
  - 잘린 호출의 Reasoning 비중(reasoning 소진형 판별)
  - 잘린 뒤 같은 턴에서 후속 호출이 이어진 건수

**쿼리: 출력 잘림.** finish\_reasons의 length나 status incomplete로 끝난 호출의 비율, reasoning 비중, 후속 호출 수, 비용을 구한다. 출력 잘림(E4)을 확인한다.

SQL: `queries/metrics/E4.sql`의 `truncation`.

- **판정**: 잘림이 반복되고(2건 이상) 잘린 뒤 후속 호출이 이어지면 E4로 판정한다. TruncReasonShare가 0.8 이상이면 reasoning 소진형이다.
- **낭비 추정**: Σ 잘린 호출 Cost + 잘림 때문에 생긴 후속 호출 Cost.

## F. 실패로 인한 낭비

결과를 내지 못한 호출과 턴의 비용은 전액 낭비다. 게다가 실패 결과와 재시도가 컨텍스트에 쌓여 이후 호출까지 비싸진다. F 범주는 실패가 어디서 나고 얼마를 쓰고 끝나는지를 잰다.

### F1. Tool 실패 후 재시도

- **정의**: tool이 실패하고(잘못된 인자, 타임아웃, 권한 오류) LLM이 다시 시도한다. 실패 결과와 재시도 호출이 모두 컨텍스트에 쌓인다.
- **확인 방법**: \[T\] `execute_tool` span의 `Success`, `error.type`, `ResultCode`를 본다.
- **지표**
  - tool별 실패율
  - 실패가 있는 턴과 없는 턴의 비용 중앙값, 호출 수 중앙값

**쿼리: Tool별 실패율.** `execute_tool` span의 성공 여부로 tool별 실패 건수와 실패율을 구하고 오류 유형 예시를 붙인다. 실패가 잦은 tool(F1)을 찾는다.

SQL: `queries/metrics/F1.sql`의 `tool_failures`, `error_types`.

**쿼리: 실패 턴과 정상 턴 비교.** tool 실패가 있는 턴과 없는 턴의 비용 중앙값, 호출 수 중앙값을 나란히 구한다. 실패가 비용을 얼마나 키우는지(F1) 본다.

SQL: `queries/metrics/F1.sql`의 `fail_vs_ok_turns`.

- **판정**
  - 특정 tool의 실패가 반복되고(2건 이상), 실패 턴의 비용 중앙값이 정상 턴보다 크면 F1로 판정한다. 실패율의 수치 기준은 두지 않고 실패 턴과 정상 턴의 비용을 비교한다.
  - `error.type`으로 인자 오류와 외부 장애를 나눈다. 인자 오류가 많으면 tool 설명이 불명확한 경우가 많으니 B2 리뷰와 함께 본다.
- **낭비 추정**: Σ(실패 턴) (턴 비용 − 정상 턴 비용 중앙값).

### F2. LLM 호출 오류·재요청

- **정의**: 모델 호출이 실패하거나(콘텐츠 필터, 서버 오류, 출력 파싱 실패), 앱이 같은 입력을 다시 보낸다.
- **확인 방법**: \[T\] LLM 호출 span의 `Success`, `ResultCode`, `error.type`과 finish\_reasons의 `content_filter`, `error`를 본다. \[T\] 같은 턴에서 직전과 Input이 거의 같은 호출을 재요청으로 추정한다.
- **지표**
  - 실패 호출 비율(오류 유형별)
  - 토큰이 기록된 실패 호출의 비용. 토큰 기록이 없는 실패는 비용 집계에서 빠진다.
  - 재요청 추정 건수 = 같은 턴에서 |InTok − 직전 InTok| ≤ 50이고, 직전 호출이 tool 호출로 끝나지 않은 호출

**쿼리: LLM 호출 실패와 재요청.** 실패로 끝난 LLM 호출과, 같은 턴에서 직전과 거의 같은 Input으로 다시 보낸 호출을 세고 비용을 구한다. F2를 확인한다.

SQL: `queries/metrics/F2.sql`의 `llm_failures`, `error_breakdown`.

실패 호출은 `ResultCode`와 `error.type`으로 다시 나눠 본다(`error_breakdown`).

- **판정**
  - 실패 호출이나 재요청이 반복되면(2건 이상) F2 후보로 올린다. 실패 비율의 수치 기준은 두지 않는다. 비용은 낭비 추정으로 보인다.
  - 토큰이 기록되지 않은 실패(예: 429)는 비용보다 지연 문제다.
  - 재요청이 출력 파싱 실패 뒤에 몰리면 \[S\]로 structured output 사용 여부를 확인한다.
- **낭비 추정**: 토큰이 기록된 실패 호출 Cost + 재요청 호출 Cost.

### F3. 사용자 재질문

- **정의**: 응답이 불충분해서 사용자가 같은 의도를 다시 묻는다. 직전 턴 비용이 낭비가 되고, 이력도 늘어난다.
- **확인 방법**: \[T\] 근사로, 직전 턴이 끝나고 짧은 간격 안에 시작된 턴을 센다. \[C\] 연속된 사용자 메시지의 유사도를 잰다.
- **지표**
  - 빠른 재입력 비율 \[T\] = 직전 턴 종료 후 60초 안에 시작된 턴 ÷ 전체 턴
  - 재질문 비율 \[C\] = 연속 사용자 메시지의 임베딩 코사인 유사도가 0.85 이상인 턴 ÷ 전체 턴

**쿼리: 빠른 재입력 비율.** 같은 대화에서 직전 턴이 끝나고 60초 안에 시작된 턴의 비율을 구한다. 재질문(F3)을 텔레메트리만으로 근사할 때 쓴다.

SQL: `queries/metrics/F3.sql`의 `quick_reinput`.

**쿼리: 대화별 사용자 메시지 추출.** 턴 첫 호출의 `InputMessages`에서 마지막 user 메시지를 꺼내 대화·시간순으로 정렬한다. 연속 메시지 유사도 계산(F3)의 입력으로 쓴다.

SQL: `queries/metrics/F3.sql`의 `quick_pairs`.

`quick_pairs`로 SpanId를 특정한 뒤 `queries/export/90-content-by-span.kql`로 원문을 받고, 연속 메시지의 임베딩 유사도는 Python 등으로 계산한다. 메시지 구조(`parts[0].content`)는 계측에 따라 다를 수 있으니 실제 JSON을 먼저 확인한다.

- **판정**: \[C\] 재질문 비율이 높고, 재질문 직전 턴의 응답이 샘플에서 불완전하거나 엉뚱하면 F3로 판정한다. 빠른 재입력 비율의 수치 기준은 두지 않는다. \[T\] 근사만 있으면 비율과 직전 턴 비용을 기록하고 판정을 보류한다. 빠른 재입력이 꼭 재질문은 아니기 때문이다.
- **낭비 추정**: Σ(재질문 직전 턴) 턴 비용.

### F4. 실패로 끝난 턴

- **정의**: 루트 `invoke_agent`가 오류로 끝난 턴이다. 그 턴에 쓴 토큰 전체가 결과 없이 사라진다.
- **확인 방법**: \[T\] 루트 `invoke_agent` span의 `Success`를 본다.
- **지표**: 실패 턴 비율, 실패 턴 비용 비중, 실패 턴과 정상 턴의 평균 호출 수, 늦은 실패 계수(실패 턴 비용 비중 ÷ 실패 턴 비율)

**쿼리: 실패 턴 비용.** 루트 `invoke_agent`의 성공 여부로 실패 턴 비율, 실패 턴 비용 비중, 실패·정상 턴의 평균 호출 수를 구한다. F4를 확인한다.

SQL: `queries/metrics/F4.sql`의 `failed_turns`.

- **판정**
  - 늦은 실패 계수(FailCostShare ÷ FailTurnRate)가 1.5 이상이면, 실패 턴이 평균 턴보다 1.5배 넘게 비싼 것이므로 많이 쓰고 나서 실패하는 "늦은 실패"로 판정한다(초기값). 1을 넘으면 실패 턴이 평균보다 비싸다는 뜻이고, 0.5는 잡음 여유다. D1, F1과 연결해 본다.
  - 앱이 예외를 잡아 정상 응답으로 감싸면 `Success`가 true로 남는다. \[S\]로 오류 처리 방식을 확인한다.
- **낭비 추정**: Σ 실패 턴 비용.

## 출처

- [Agent tracing overview – Microsoft Foundry](https://learn.microsoft.com/en-us/azure/foundry/observability/concepts/trace-agent-concept)
- [Restrict access to sensitive content in Foundry traces](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/traces-sensitive-content)
- [AppGenAIContent table reference – Azure Monitor](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appgenaicontent)
- [Prompt caching – Azure OpenAI in Foundry](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/prompt-caching)
- [OpenTelemetry GenAI attribute registry](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)
- [Conversation state – OpenAI API docs](https://developers.openai.com/api/docs/guides/conversation-state)
