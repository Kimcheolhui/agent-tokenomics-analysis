# 텔레메트리 공식 문서 대조

추출 쿼리와 합성 데이터가 가정한 텔레메트리 구조를 공식 문서와 맞춘 결과다. 2026-11-09에 확인했다. 확인한 문서는 맨 아래에 있다.

## 확인된 것

| 항목 | 공식 문서 내용 | 이 폴더의 사용 |
| --- | --- | --- |
| Log Analytics 테이블 | `AppDependencies`, `AppRequests`는 `OperationId`, `Id`, `ParentId`, `Properties`(dynamic), `Success`, `ResultCode`, `DurationMs`, `ItemCount`를 가진다. `Target`은 `AppDependencies`에만 있다 | `10-calls.kql` 등이 `union AppDependencies, AppRequests`에서 같은 컬럼을 쓴다. `Target`은 한쪽 테이블에만 있어도 union에서 null로 채워진다 |
| `AppGenAIContent` | 컬럼: `TraceId`, `SpanId`, `ParentSpanId`, `AgentId`, `AgentName`, `ModelName`, `SystemInstructions`, `InputMessages`, `OutputMessages`, `ToolDefinitions`, `ToolCallArguments`, `ToolCallResult`, `EvaluationExplanation`, `Attributes`, `TimeGenerated`. **`OperationId` 컬럼은 없다** | `20-content-digest.kql`이 `TraceId`로 내려받고 로컬 뷰가 `OperationId`로 이름을 맞춘다 |
| `gen_ai.usage.input_tokens` | 캐시 토큰과 모든 모달리티의 입력을 포함한다 | `InTok`이 캐시 포함 총량이라는 비용 식의 전제와 같다 |
| `gen_ai.usage.output_tokens` | reasoning 토큰을 포함한다 | `OutTok`에 reasoning이 들어 있고 가시 출력은 `OutTok − ReasonTok`로 구하는 가정과 같다 |
| 캐시 속성 | `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_write.input_tokens`. 둘 다 `input_tokens`에 포함된다 | `cache_creation`은 옛 이름이다. `10-calls.kql`이 둘 다 읽는다 |
| `gen_ai.response.finish_reasons` | **문자열 배열**이다. 예: `["stop"]`, `["tool_calls"]`, `["length"]`, `["stop","error"]`. OTel 예시에서 tool 호출 값은 `tool_calls`다 | `Properties`에는 JSON 배열 문자열로 저장된다. 지표 SQL은 `LIKE '%tool_calls%'`로 읽는다. 합성 데이터도 배열 형식이다 |
| `gen_ai.request.reasoning.level`, `gen_ai.conversation.compacted`, `gen_ai.response.status` | 레지스트리에 있다 | `10-calls.kql`이 읽는다 |
| span 이름과 종류 | `invoke_agent {agent}`, `execute_tool {tool}`, `{operation} {model}`(chat). tool span은 `INTERNAL`이다 | `gen_ai.operation.name`으로 구분하므로 span 이름에 의존하지 않는다 |
| 검색 span | `gen_ai.operation.name` = `retrieval`, 속성은 `gen_ai.retrieval.documents`(`id`, `score`), `gen_ai.retrieval.top_k` | `13-retrieval.kql`과 같다 |
| 도입 상태 | `gen_ai.*` 전체가 Development 단계다. Foundry 문서도 span과 속성이 바뀔 수 있다고 적는다 | 가이드 "알려진 위험"에 있다 |

## 문서와 달라서 고친 것

| 항목 | 이전 가정 | 공식 문서 | 고친 곳 |
| --- | --- | --- | --- |
| `execute_tool` span의 부모 | 요청한 chat span | `invoke_agent` span의 자식이다(OTel 예시, Foundry 문서의 span 표). 요청한 chat 호출은 부모로 알 수 없다 | `tool_runs` 뷰가 같은 에이전트 실행에서 tool 시작 시각 이전의 마지막 호출을 요청한 호출로 잡는다. C2 `result_carry`가 이 값을 쓴다 |
| 하위 에이전트 `invoke_agent`의 부모 | 상위 `invoke_agent` span | 상위 `invoke_agent`이거나, 에이전트를 tool로 부르는 구조에서는 위임 `execute_tool` span이다 | `sub_agents.ParentRunSpanId`가 가장 가까운 조상 `invoke_agent`를 찾는다. D4가 이 값으로 위임 직전 상위 호출을 짝짓는다 |
| `ToolDefinitions`에서 tool 이름 추출 | 정규식 `"name"\s*:\s*"…"` | 정의는 `parameters` 안에 `name`이라는 속성을 가질 수 있어 정규식이 tool 수를 실제보다 크게 센다 | `20-content-digest.kql`이 `parse_json`과 `mv-apply`로 최상위 원소의 이름만 읽는다 |
| `AppGenAIContent` 조회 | 일반 테이블처럼 일 단위로 쿼리 | Basic 요금제 테이블이다. 이 테이블이 들어간 쿼리는 최근 30일 이내만 조회되고 `timespan`을 명시해야 한다. 리소스 범위 쿼리는 지원하지 않는다 | `export.py`가 `--timespan`을 넘기고 30일을 넘는 날은 경고한다. `--workspace` 범위로만 쿼리한다 |
| `AppGenAIContent` 쿼리 비용 | 고려하지 않음 | Basic 테이블이 들어간 쿼리는 스캔한 데이터 양으로 과금된다 | `02-precheck-content.kql`이 하루 수집량(`BilledMB`)을 낸다. 이 테이블을 읽는 기본 추출은 `content_digest`, `tool_schemas` 2개라서 기간 전체 스캔량은 수집량의 약 2배다 |
| 샘플링 | 고려하지 않음 | `ItemCount`가 1보다 크면 샘플링된 행이다 | `01-precheck-sampling.kql`을 추가했다. 지표는 `ItemCount`를 보정하지 않으므로 샘플링이 있으면 호출 수와 비용이 작게 나온다 |

## 확인하지 못한 것

공식 문서에 없어서 실제 환경에서만 확인할 수 있다.

- **Foundry 서버 측 추적이 chat span에 `gen_ai.usage.*`를 기록하는지.** 문서는 입력, 출력, tool 사용, 토큰 소비를 캡처한다고만 적는다. 속성 목록은 없다. `00-precheck-keys.kql`로 확인한다.
- **chat span에 `gen_ai.agent.name`이 있는지.** OTel 규약은 이 속성을 `invoke_agent` span에 두고 chat span에는 요구하지 않는다. 이 폴더는 조상 `invoke_agent`로 귀속하므로 chat span에 없어도 된다. 부모 사슬이 끊기면 루트 에이전트로 집계된다.
- **`AppGenAIContent`의 레코드 단위.** span마다 1행인지, 속성별로 여러 행인지 문서에 없다. 여러 행이면 `content_digest`의 행 수와 `SpanId` 중복이 늘고 `Names` leftouter join의 행 수가 늘어난다. 추출 전에 `02-precheck-content.kql`의 `MaxRowsPerSpan`으로 확인한다.
- **`ToolDefinitions`의 정확한 JSON 형식.** OTel 규약은 `[{"type":"function","name":…}]`이다. OpenAI 형식(`{"function":{"name":…}}`)도 받도록 썼지만 실제 값은 보지 못했다. `02-precheck-content.kql`의 `ToolDefsTypes`(배열인지)와 `ToolDefKeys`(첫 원소의 최상위 키)로 확인한다.
- **`az monitor log-analytics query`로 Basic 테이블을 읽을 수 있는지.** 이 명령은 Query API를 쓴다. Basic 테이블 문서의 REST 예시는 Search API(`/search?timespan=…`)를 쓰고, Query API 지원 여부는 적혀 있지 않다. `export.py`가 `content_digest`에서 실패하면 포털에서 내려받는다.
- **미리보기 기간의 변동.** `AppGenAIContent`는 2026-07에 공개 미리보기로 나왔다. 2026-09-30 이전에는 같은 속성이 `Properties`와 `AppGenAIContent`에 함께 기록될 수 있다고 알려져 있다. 이 폴더는 `AppGenAIContent`만 읽는다.
- **추출 KQL의 실행 결과.** 11개 파일 모두 실제 Log Analytics에서 실행하지 못했다. 문법과 컬럼 이름은 Microsoft Kusto 파서(`@kusto/language-service-next` 12.4.1)로 공식 테이블 스키마에 대해 검사했다(`tests/kql/check_kql.js`). 오류 0건이고, 결과 컬럼 이름이 각 파일의 `//! columns`와 같다. 파서는 속성 값의 실제 형식, 행 수, 실행 시간, 서비스 쪽 제한을 보지 못한다.

## 확인한 문서

- OpenTelemetry GenAI 규약(저장소 `open-telemetry/semantic-conventions-genai`): `docs/registry/attributes/gen-ai.md`, `docs/gen-ai/client-inference.md`, `client-invoke-agent.md`, `execute-tool.md`, `retrieval.md`, `non-normative/examples-llm-calls.md`, `examples-agent-calls.md`. GenAI 규약은 `semantic-conventions` 저장소에서 이 저장소로 옮겨졌다
- Azure Monitor 테이블 레퍼런스: [AppDependencies](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appdependencies), [AppRequests](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apprequests), [AppGenAIContent](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/appgenaicontent)
- [Basic·Auxiliary 테이블 쿼리](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/basic-logs-query): 여러 테이블을 쓰는 전체 KQL을 지원한다. `externaldata`, 리소스 범위 쿼리는 지원하지 않는다. 스캔량 과금
- Foundry: [에이전트 추적 개요](https://learn.microsoft.com/en-us/azure/foundry/observability/concepts/trace-agent-concept), [클라이언트 측 추적](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-client-side)
- Azure 공지 AZ567594: 민감한 생성형 AI 텔레메트리를 `AppGenAIContent`로 분리하는 공개 미리보기
