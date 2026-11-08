# 문제 카탈로그 B

## B. 고정 Input 비대

고정 Input S\_a는 에이전트 a의 모든 LLM 호출에 반복해서 실린다. 그래서 낭비는 "줄일 수 있는 토큰 × 호출 수 × P\_eff"로 커진다. 캐시가 잘 맞으면 P\_eff가 P\_cache에 가까워져 영향이 작아지므로, 항상 A의 결과와 함께 본다.

### S\_a를 쓰는 지점

S\_a는 A 범주와 B 범주의 지표 대부분이 입력으로 받는다. 아래 지점은 S\_a 없이 산출할 수 없으므로 3-B 측정을 먼저 한다.

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

```kusto
Calls
| where CallIdx == 1
| summarize FirstCalls = count(), P01 = percentile(InTok, 1), P50 = percentile(InTok, 50) by Agent
```

**고정 Input 비용 비중** = Σ(S\_a × 에이전트 a의 LLM 호출 수 × P\_eff) ÷ 전체 비용. 고정 Input이 비용에서 차지하는 몫이며, 0.3 이상이면 B 범주를 점검한다(초기값). P\_eff에 캐시 효과가 들어 있어서, 캐시가 잘 맞는 에이전트는 토큰이 커도 비중이 낮게 나온다.

**쿼리: 고정 Input 비용 비중.** 에이전트별로 S\_a × LLM 호출 수 × P\_eff로 고정 Input 비용을 구하고, 그 에이전트 전체 비용 대비 비중을 계산한다. 전체 비중은 FixedCost 합 ÷ Cost 합으로 구한다. B 범주를 점검할지 판단할 때 쓴다.

```kusto
let S = datatable(Agent:string, STok:long) [ "<에이전트명>", 0 ];   // 측정법 1–3으로 구한 S_a
Calls
| summarize Calls = count(), Cost = sum(Cost),
            CacheRate = 1.0 * sum(CacheTok) / sum(InTok),
            PIn = any(PIn), PCache = any(PCache) by Agent
| join kind=inner S on Agent
| extend PEff = (1 - CacheRate) * PIn + CacheRate * PCache
| extend FixedCost = STok * Calls * PEff / 1e6
| project Agent, Calls, STok, CacheRate = round(CacheRate, 3), FixedCost, Cost,
          FixedCostShare = round(FixedCost / Cost, 3)
```

한 에이전트가 여러 모델을 쓰면 단가를 `any()`로 하나만 가져오므로, 이 경우 Agent와 Model로 나눠 계산한다.

### B1. 미사용 Tool 상시 전송

- **정의**: 정의된 tool 중 거의 호출되지 않는 tool의 schema가 모든 LLM 호출에 실린다.
- **확인 방법**: \[C\] `ToolDefinitions`에서 정의된 tool 목록을 뽑는다. \[T\] `execute_tool` span으로 실제 사용을 센다.
- **지표**: tool별 사용률 = 그 tool이 한 번 이상 호출된 턴 수 ÷ 에이전트 전체 턴 수

**쿼리: Tool별 사용률.** 최근 `ToolDefinitions`에서 정의된 tool 목록을 뽑고, `execute_tool` span으로 실제 호출된 턴 수를 붙인다. 정의만 되어 있고 쓰이지 않는 tool(B1)을 찾는다.

```kusto
let TurnsByAgent = Calls | where CallIdx == 1 | summarize AgentTurns = count() by Agent;
let Defined = Content
    | where isnotempty(ToolDefinitions)
    | summarize arg_max(TimeGenerated, ToolDefinitions) by Agent = AgentName
    | mv-expand ToolName = extract_all(@'"name"\s*:\s*"([^"]+)"', ToolDefinitions) to typeof(string)
    | project Agent, ToolName;
let Used = Tools
    | join kind=leftouter Turns on OperationId
    | summarize UsedTurns = dcount(OperationId) by Agent = RootAgent, ToolName;
Defined
| join kind=leftouter Used on Agent, ToolName
| join kind=leftouter TurnsByAgent on Agent
| project Agent, ToolName, UsedTurns = coalesce(UsedTurns, 0),
          UseRate = round(1.0 * coalesce(UsedTurns, 0) / AgentTurns, 4)
| order by Agent asc, UseRate asc
```

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

```kusto
Content
| where isnotempty(ToolDefinitions)
| summarize arg_max(TimeGenerated, ToolDefinitions) by AgentName
| mv-expand T = parse_json(ToolDefinitions)
| extend ToolName  = coalesce(tostring(T.name), tostring(T["function"].name)),
         Chars     = strlen(tostring(T)),
         DescChars = strlen(coalesce(tostring(T.description), tostring(T["function"].description)))
| project AgentName, ToolName, Chars, DescChars
| order by AgentName asc, Chars desc
```

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

```kusto
Content
| where isnotempty(SystemInstructions)
| summarize arg_max(TimeGenerated, SystemInstructions) by AgentName
| project AgentName, SysChars = strlen(SystemInstructions), SystemInstructions
```

- **판정**
  - 고정 Input 비용 비중이 0.3 이상이고, 리뷰에서 예시·참조 데이터·중복 규칙이 큰 비중이면 B3로 판정한다.
  - 고정 Input 비용 비중은 P\_eff로 캐시 효과를 이미 반영한다. 토큰으로는 커도 비용 비중이 작으면 캐시가 잘 맞는 경우이므로 우선순위를 낮춘다.
- **낭비 추정**: 축약 가능 토큰 × 에이전트 LLM 호출 수 × P\_eff.

### B4. 하위 에이전트 prompt 중복

- **정의**: 멀티 에이전트 구조에서 하위 에이전트들이 같은 공통 블록(회사 정책, 공통 규칙)을 각자 싣는다. 또는 쓰지 않는 instruction과 tool까지 상위에서 그대로 물려받는다.
- **확인 방법**: \[C\] 에이전트별 System prompt·tool 정의 크기와 공통 블록을 비교한다. \[T\] 에이전트별 호출 수와 tool 사용률(B1)을 본다.
- **지표**: 에이전트별 S\_a, 에이전트 간 공통 블록 토큰, 에이전트별 정의 tool 수 대비 사용 tool 수

**쿼리: 에이전트별 고정 Input 크기.** 에이전트마다 System prompt 길이, tool 정의 길이, 정의된 tool 수를 구한다. 하위 에이전트끼리 비교해 중복(B4)을 찾는다.

```kusto
Content
| where isnotempty(SystemInstructions) or isnotempty(ToolDefinitions)
| summarize SysChars  = max(strlen(SystemInstructions)),
            ToolChars = max(strlen(ToolDefinitions)),
            ToolCount = max(array_length(extract_all(@'"name"\s*:\s*"([^"]+)"', ToolDefinitions)))
  by AgentName
```

- **판정**: 하위 에이전트 S\_a가 상위와 비슷하거나 공통 블록이 여러 에이전트에 반복되고, 하위 에이전트의 tool 사용률이 낮으면 B4로 판정한다.
- **낭비 추정**: Σ(에이전트 a) (S\_a − 역할에 필요한 토큰) × 에이전트 a의 LLM 호출 수 × P\_eff.

