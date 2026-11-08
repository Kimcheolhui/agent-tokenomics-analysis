# 데이터 계약

진단은 Log Analytics에서 CSV를 한 번 내린 뒤 로컬에서 산출한다. **CSV 스키마가 계약이다.** 추출 수단은 두 가지이고 둘 다 같은 스키마를 만든다.

| 경로 | 수단 | 쓰는 경우 |
| --- | --- | --- |
| 스크립트 | `scripts/export.py` (az CLI) | 주 경로. workspace에 az로 붙을 수 있을 때 |
| 수동 | `queries/export/*.kql`을 포털 Logs에 붙여 넣고 CSV로 내보내기 | az 권한이 없을 때. 고객이 직접 뽑아 넘길 때 |

스크립트는 `queries/export/*.kql` 파일을 읽어 실행한다. 포털에 붙여 넣는 쿼리와 같은 파일이라서 두 경로의 결과가 같다. 쿼리 본문은 한 곳에만 있다.

## 디렉터리

```
config/            분석 입력. 고객마다 채운다
  model-params.csv   모델별 단가와 캐시 파라미터
  agents.csv         에이전트별 고정 Input 토큰 S_a
queries/
  export/*.kql       추출 쿼리. 두 경로가 공유한다
  views.sql          로컬 분석 기본 뷰. 가이드 "공통 쿼리 정의"에 대응한다
scripts/export.py    az CLI 추출. 하루 단위로 파일을 만든다
data/<name>/<YYYY-MM-DD>.csv
```

기간은 하루 단위 파일로 쪼갠다. 기간을 늘려도 이미 뽑은 날은 다시 뽑지 않고, 실패한 날만 다시 돌리면 된다. 분석은 `data/<name>/*.csv`를 전부 읽어서 있는 만큼을 기간으로 삼으므로, 하루치인지 2주치인지 미리 정하지 않아도 된다.

## 공통 규약

- **빈 문자열은 값이 없다는 뜻이다.** KQL `tostring()`은 속성이 없을 때 빈 문자열을 준다. `views.sql`의 `blank()` 매크로가 NULL로 돌린다. CSV 타입 추론이 컬럼을 숫자나 불리언으로 잡는 경우가 있어 `nullif(x, '')`는 그때 깨지므로, VARCHAR로 캐스팅한 뒤 비교한다.
- **파생 값은 추출하지 않는다.** `CallIdx`, `Cost`, `CallType`, `Delta`는 `views.sql`에서 계산한다. 일 단위 파일이 턴을 가를 수 있어서, 턴 안 순번은 전체 기간을 모아야 맞는다.
- **행 순서는 보장하지 않는다.** 정렬은 로컬에서 한다.
- **시각은 UTC로 고정한다.** `views.sql`이 `SET TimeZone = 'UTC'`를 건다. 세션 타임존에 따라 날짜 경계가 달라지면 보고서 수치가 재현되지 않는다.
- **`ResultCode`는 VARCHAR다.** 숫자 상태 코드와 문자열이 섞여 들어온다.
- **원문은 기본으로 내리지 않는다.** `sensitive: true`인 추출은 `--include-sensitive`를 줘야 돌아간다.
- **행 수 한계.** Log Analytics API는 쿼리당 50만 행, 포털 UI는 10만 행이다. 스크립트는 하루 분량이 이 선에 가까우면 경고하고, 그 날을 시간 단위로 더 쪼개라고 알린다.

## 추출 파일

### `calls` — LLM 호출 1건 = 1행

비용 식과 대부분의 지표가 여기서 나온다. `chat`, `text_completion`, `generate_content` span.

| 컬럼 | 타입 | 출처 | 비고 |
| --- | --- | --- | --- |
| `TimeGenerated` | timestamp | span | |
| `OperationId` | text | span | 턴 식별자(trace) |
| `SpanId` | text | `Id` | content 조인 키 |
| `ParentId` | text | span | `call_owner` 귀속 탐색에 쓴다 |
| `Op` | text | `gen_ai.operation.name` | |
| `Agent` | text | `gen_ai.agent.name` | chat span에 없는 경우가 많다 |
| `ConvId` | text | `gen_ai.conversation.id` | 없으면 C1 진단 불가 |
| `Model` | text | `gen_ai.response.model` | 단가 매핑 키 |
| `RequestModel` | text | `gen_ai.request.model` | 배포 이름일 수 있다 |
| `InTok` | bigint | `usage.input_tokens` | 캐시 히트·쓰기분 포함 |
| `CacheTok` | bigint | `usage.cache_read.input_tokens` | 없으면 0 |
| `WriteTok` | bigint | `usage.cache_write.input_tokens` | `cache_creation`으로 대체 조회 |
| `OutTok` | bigint | `usage.output_tokens` | reasoning 포함 |
| `ReasonTok` | bigint | `usage.reasoning.output_tokens` | 없으면 0 |
| `Finish` | text | `response.finish_reasons` | 배열이 문자열로 들어온다 |
| `Status` | text | `response.status` | `incomplete` = 중단 |
| `MaxTok` | bigint | `request.max_tokens` | 미설정이면 비어 있다 |
| `ReasonLevel` | text | `request.reasoning.level` | |
| `Compacted` | text | `conversation.compacted` | 압축 시에만 기록 |
| `Endpoint` | text | `server.address` 또는 `Target` | A6 쏠림 |
| `ErrorType` | text | `error.type` | |
| `Success` | boolean | span | |
| `ResultCode` | text | span | |
| `DurationMs` | bigint | span | |

### `agent_spans` — `invoke_agent` span 1건 = 1행

루트만 뽑지 않는다. D4가 루트가 아닌 `invoke_agent` span을 하위 에이전트 실행으로 쓴다. 루트는 `OperationId`별 최초 span으로 로컬에서 정한다.

컬럼: `TimeGenerated`, `OperationId`, `SpanId`, `ParentId`, `Agent`, `ConvId`, `Success`, `ResultCode`, `ErrorType`, `DurationMs`.

`DurationMs`는 F3의 턴 종료 시각(`TurnEnd`) 계산에 쓴다.

### `tools` — `execute_tool` span 1건 = 1행

컬럼: `TimeGenerated`, `OperationId`, `SpanId`, `ParentId`, `ToolName`, `ToolCallId`, `Success`, `ResultCode`, `ErrorType`, `DurationMs`.

### `retrieval` — `retrieval` span을 반환 문서 단위로 펼친 것

컬럼: `TimeGenerated`, `OperationId`, `SpanId`, `ParentId`, `TopK`, `DocCount`, `DocId`, `Score`.

문서가 0건인 span도 `DocId`가 빈 1행으로 남는다. 검색 횟수는 `count(DISTINCT SpanId)`로 센다.

### `content_digest` — `AppGenAIContent` 레코드 1건 = 1행

**원문을 내리지 않는다.** 7개 content 컬럼의 해시와 길이만 내린다. A1은 변형 수, A2는 원문·이름 집합 변형, C2는 결과 길이, D1은 인자 해시, D3는 출력 해시만 쓰므로 원문이 필요 없다. 고객 프롬프트와 tool 결과가 로컬에 남지 않고 CSV도 작게 유지된다.

컬럼: `TimeGenerated`, `TraceId`, `SpanId`, `AgentName`, `SysChars`, `SysHash`, `SysHeadHash`, `ToolDefsChars`, `ToolDefsHash`, `ToolCount`, `ToolNames`, `InChars`, `InHash`, `OutChars`, `OutHash`, `ArgsChars`, `ArgsHash`, `ResultChars`, `ResultHash`.

- `SysHeadHash`는 앞 4,000자의 해시다. 차이가 앞부분에 있는지(A1 영향 크기) 확인한다.
- `ToolNames`는 정의에 나온 **순서를 그대로** 둔 파이프 구분 목록이다. A2가 순서 변형을 보기 때문이다. 순서를 무시한 이름 집합 해시(`ToolNamesHash`)는 `views.sql`에서 정렬해 만든다.

### `tool_schemas` — 에이전트별 최신 정의의 tool 1건 = 1행

컬럼: `AgentName`, `CapturedAt`, `ToolName`, `Chars`, `DescChars`.

정의 크기와 description 크기만 내리고 설명 원문은 내리지 않는다. 기간을 쪼개 돌리면 날짜마다 1세트가 나오고, `views.sql`이 `CapturedAt` 최대값을 쓴다.

### `system_prompts` — 에이전트 1건 = 1행 · `sensitive`

컬럼: `AgentName`, `CapturedAt`, `SysChars`, `SystemInstructions`.

B3 구성 요소 리뷰와 S_a 측정법 2(tiktoken)의 입력이다. 고객 콘텐츠가 로컬에 저장된다. 에이전트 수만큼의 행이므로 분량은 작다.

### `content_by_span` — 지정한 SpanId 1건 = 1행 · `sensitive`

1차 분석으로 SpanId를 특정한 뒤 2차로 돌린다. A1·A2 diff 그림의 변형 2건, F3 사용자 메시지 유사도, C2·D3 샘플 리뷰에 쓴다. 필요한 SpanId만 뽑으므로 원문 노출이 최소가 된다.

```bash
python3 scripts/export.py --workspace <GUID> --from 2026-10-20 --to 2026-11-03 \
    --only content_by_span --spans spans.txt --include-sensitive
```

## 원인별 필요 파일

25개 원인이 이 스키마만으로 산출되는지 확인한 결과다. `★`는 `--include-sensitive`가 필요하다.

| 원인 | calls | agent_spans | tools | retrieval | content_digest | tool_schemas | 원문 | config |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| A 공통 달성률 | ● | | | | | | | S_a, 캐시 |
| A1 동적 값 | ● | | | | ● | | ★ diff | S_a, 캐시 |
| A2 Tool 정의 변동 | ● | | | | ● | | | 캐시 |
| A3 이력 앞부분 편집 | ● | | | | ● | | | 캐시 |
| A4 Prefix 1,024 미만 | ● | | | | | | | S_a, 캐시 |
| A5 캐시 만료 | ● | | | | | | | S_a, 캐시 |
| A6 트래픽 분산·쏠림 | ● | | | | | | | S_a, 캐시 |
| B 공통 비중 | ● | | | | | | | S_a, 단가 |
| B1 미사용 Tool | ● | ● | ● | | ● | ● | | |
| B2 장황한 schema | ● | | | | | ● | | |
| B3 System prompt 비대 | ● | | | | ● | | ★ 원문 | S_a, 단가 |
| B4 하위 prompt 중복 | ● | ● | ● | | ● | ● | ★ 원문 | S_a |
| C1 이력 누적 | ● | | | | | | | 단가 |
| C2 Tool 결과 원문 | ● | | ● | | ● | | ★ 샘플 | 단가 |
| C3 검색 컨텍스트 | ● | | ● | ● | ● | | | 단가 |
| D1 루프·중복 호출 | ● | | ● | | ● | | | 단가 |
| D2 순차 처리 | ● | | ● | | | | ★ 인자 | 단가 |
| D3 결정적 단계 | ● | | | | ● | | | 단가 |
| D4 에이전트 간 중복 | ● | ● | | | | | ★ 샘플 | S_a, 단가 |
| E1 상위 모델 | ● | | | | | | | 단가 |
| E2 Reasoning 과다 | ● | | | | | | | 단가 |
| E3 장황한 응답 | ● | | | | | | ★ 샘플 | 단가 |
| E4 출력 잘림 | ● | | | | | | | 단가 |
| F1 Tool 실패 재시도 | ● | | ● | | | | | 단가 |
| F2 LLM 오류·재요청 | ● | | | | | | | 단가 |
| F3 사용자 재질문 | ● | ● | | | | | ★ 메시지 | 단가 |
| F4 실패로 끝난 턴 | ● | ● | | | | | | 단가 |

기본 추출(`calls`, `agent_spans`, `tools`, `retrieval`, `content_digest`, `tool_schemas`)로 25개 원인의 **텔레메트리 지표를 모두 산출할 수 있다.** 원문은 증거 그림과 리뷰에만 필요하고, 모두 2차 추출로 범위를 좁혀 받는다.

## 텔레메트리 밖의 입력

추출로 해결되지 않는 것이다. 진단 계획에 따로 잡아야 한다.

| 항목 | 쓰는 곳 | 필요한 것 |
| --- | --- | --- |
| S_a 측정법 1 (실험 호출) | A·B 범주 전체 | 고객 배포에 직접 요청을 보낸다 |
| 단가·캐시 파라미터 | 전체 비용 계산 | `config/model-params.csv`에 채운다 |
| 코드·설정 리뷰 \[S\] | B2, B3, C3, D2, D3, E2 | 에이전트 정의, 프롬프트 템플릿, top_k·reasoning 설정 |
| 목표 응답 길이 | E3 판정 | 고객이 정한 값. 없으면 판정 보류 |
| 임베딩 유사도 | F3 판정 | `content_by_span`으로 메시지를 받은 뒤 로컬에서 계산 |
| 최대 반복 횟수 | D1 확정 | 프레임워크 설정값 |

## 점검 뷰

분석을 시작하기 전에 비어 있어야 하는 것과 확인할 것이다.

| 뷰 | 뜻 |
| --- | --- |
| `unpriced_calls` | `config/model-params.csv`와 매칭되지 않은 모델. 행이 있으면 그 호출의 `Cost`가 NULL이고 비용 합계에서 경고 없이 빠진다 |
| `missing_sa` | S_a가 비어 있는 에이전트. A 공통·A1·A4·B 공통을 산출할 수 없다 |
| `instrumentation` | `cache_read`, `reasoning`, `conversation.id`, `finish_reasons`, `max_tokens`, 에이전트 귀속의 기록 건수. 0이면 그 키를 쓰는 지표는 "계측 부재"로 기록한다 |
