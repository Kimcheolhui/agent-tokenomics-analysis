# 문제 카탈로그 D

## D. 호출 구조

턴 비용은 호출 수에 비례하는 것보다 빠르게 늘어난다. 호출마다 그때까지 쌓인 컨텍스트 전체를 다시 보내기 때문이다. D 범주는 불필요한 호출이 어디서 생기는지를 찾는다.

`gen_ai.response.finish_reasons` 값의 표기는 계측과 API에 따라 다를 수 있다. 아래 쿼리는 tool 호출 응답을 `tool_calls`로 가정하므로, 먼저 `Calls | summarize count() by Finish`로 실제 값을 확인한다.

### D1. 루프·중복 Tool 호출

- **정의**: 같은 tool을 같은 인자로 반복 호출하거나, 목표에 도달하지 못한 채 같은 단계를 반복하다 최대 반복 횟수에 걸린다.
- **확인 방법**: \[T\] 턴당 호출 수 분포와 턴 안 같은 tool 반복을 본다. \[C\] tool 인자 해시로 완전 중복을 찾는다.
- **지표**
  - 턴당 LLM 호출 수 p50, p95, max와 꼬리 비율 = p95 ÷ p50
  - 꼬리 턴 비용 비중 = 호출 수 p95 이상 턴의 비용 ÷ 전체 비용
  - 중복 tool 호출 비율 \[C\] = 같은 턴에서 (tool 이름, 인자) 쌍이 반복된 호출 ÷ 전체 tool 호출

**쿼리: 턴당 호출 수 분포와 꼬리 비용.** 에이전트별 턴당 LLM 호출 수의 p50, p95, max와, p95 이상 턴의 비용 비중을 구한다. 호출이 비정상적으로 많은 턴(D1)이 있는지 본다.

```kusto
let TurnStats = Calls | summarize N = max(CallIdx), TurnCost = sum(Cost) by Agent, OperationId;
let Cut = TurnStats | summarize P50N = percentile(N, 50), P95N = percentile(N, 95), MaxN = max(N) by Agent;
TurnStats
| join kind=inner Cut on Agent
| summarize P50N = any(P50N), P95N = any(P95N), MaxN = any(MaxN),
            TailCostShare = round(sumif(TurnCost, N >= P95N) / sum(TurnCost), 3) by Agent
| extend TailRatio = round(1.0 * P95N / P50N, 2)
```

**쿼리: 중복 Tool 호출.** 같은 턴에서 같은 tool을 같은 인자로 다시 부른 호출을 tool별로 센다. 루프·중복 호출(D1)을 확정할 때 쓴다.

```kusto
Tools
| join kind=inner (Content
                   | where isnotempty(ToolCallArguments)
                   | project SpanId, ArgHash = hash_sha256(ToolCallArguments)) on $left.Id == $right.SpanId
| summarize Cnt = count() by OperationId, ToolName, ArgHash
| summarize ToolCalls = sum(Cnt), DupCalls = sumif(Cnt - 1, Cnt > 1) by ToolName
| extend DupRate = round(1.0 * DupCalls / ToolCalls, 3)
| order by DupCalls desc
```

content가 없으면 \[T\] 근사로, 한 턴에서 같은 tool을 3회 이상 부른 턴의 비율을 본다: `Tools | summarize Cnt = count() by OperationId, ToolName | summarize Turns = count(), RepeatTurns = countif(Cnt >= 3) by ToolName`.

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

```kusto
let ToolN = Tools | summarize ToolN = count() by OperationId;
Calls
| where Finish contains "tool_calls"
| summarize ToolRounds = count() by Agent, OperationId
| join kind=inner ToolN on OperationId
| summarize Turns = count(),
            AvgToolsPerRound = round(avg(1.0 * ToolN / ToolRounds), 2),
            SerialChainShare = round(1.0 * countif(ToolRounds >= 3 and ToolN == ToolRounds) / count(), 3)
  by Agent
```

- **판정**: 라운드당 tool 수가 1에 가깝고 순차 연쇄 턴이 있는데, \[C\]로 보니 연속 tool이 서로의 결과를 인자로 쓰지 않으면 D2로 판정한다. 의존 관계가 있으면 정상이다. 순차 연쇄 턴 비율의 수치 기준은 두지 않는다. 비율과 낭비 상한을 기록하고 \[C\] 확인 전까지 판정을 보류한다.
- **낭비 추정**: 연쇄 길이 k인 턴마다 (k − 1) × 해당 라운드들의 평균 InTok × P\_eff.

### D3. 결정적 단계의 LLM 처리

- **정의**: 규칙으로 처리할 수 있는 단계(라우팅, 의도 분류, 형식 변환, 단순 추출)를 LLM 호출로 처리한다.
- **확인 방법**: \[T\] 짧은 출력 호출의 비중과 비용을 본다. \[C\] 그 출력이 소수의 고정 값으로 수렴하는지 본다.
- **지표**
  - 짧은 출력 호출 = (OutTok − ReasonTok) < 50이고 tool 호출이 아닌 호출. 그 비중과 비용 비중
  - 출력 다양성 \[C\] = 고유 `OutputMessages` 수 ÷ 짧은 출력 호출 수

**쿼리: 짧은 출력 호출 비중.** 가시 출력이 50 토큰 미만이고 tool 호출이 아닌 호출의 건수와 비용 비중을 에이전트·모델별로 구한다. 규칙으로 대체할 수 있는 단계(D3)의 후보를 찾는다.

```kusto
Calls
| extend Short = (OutTok - ReasonTok) < 50 and not(Finish contains "tool_calls")
| summarize Calls = count(), ShortCalls = countif(Short),
            ShortCostShare = round(sumif(Cost, Short) / sum(Cost), 3) by Agent, Model
```

**쿼리: 짧은 출력의 다양성.** 짧은 출력 호출의 `OutputMessages`가 몇 가지 값으로 나오는지 센다. 소수의 레이블로 수렴하는지 보고 D3를 판정한다.

```kusto
Calls
| where (OutTok - ReasonTok) < 50 and not(Finish contains "tool_calls")
| join kind=inner (Content | project SpanId, Out = OutputMessages) on $left.Id == $right.SpanId
| summarize ShortCalls = count(), DistinctOutputs = dcount(hash_sha256(Out)) by Agent
| extend OutputDiversity = round(1.0 * DistinctOutputs / ShortCalls, 3)
```

- **판정**: 짧은 출력 호출의 비용 비중이 의미 있고 출력 다양성이 낮으면(출력 종류가 몇 개의 레이블뿐이면) D3로 판정한다. 비용 비중과 다양성의 수치 기준은 두지 않는다. 두 값을 기록하고 \[S\]로 규칙 처리가 가능한 단계인지 확인하기 전까지 판정을 보류한다. 그 호출이 상위 모델이면 E1과 겹친다.
- **낭비 추정**: 해당 호출의 비용 전체. 규칙으로 대체할 수 있다고 볼 때의 상한이다.

### D4. 에이전트 간 컨텍스트 중복

- **정의**: 상위 에이전트가 하위 에이전트에 전체 이력과 이전 tool 결과를 그대로 넘긴다. 또는 하위 에이전트의 긴 응답 전체를 다시 Input으로 받는다.
- **확인 방법**: \[T\] 루트가 아닌 `invoke_agent` span으로 하위 에이전트 실행을 찾는다. 하위 첫 호출 InTok과 위임 직전 상위 호출 InTok을 비교한다. \[C\] 실제로 전달된 메시지를 확인한다.
- **지표**
  - 위임 시 전달 비율 = (하위 첫 호출 InTok − S\_sub) ÷ (위임 직전 상위 호출 InTok − S\_parent). 분모는 상위 호출의 가변 컨텍스트(이력, tool 결과, 사용자 입력)다. 하위의 고정 Input과 상위의 고정 Input을 양쪽에서 빼므로 상위 가변 컨텍스트를 모두 넘기면 1이 된다.
  - 하위 응답 반입 크기 = 하위 실행 직후 상위 다음 호출의 ΔIn(C2 정의)

**쿼리: 위임 시 Input 전달량.** 하위 에이전트의 첫 호출 InTok과 위임 직전 상위 에이전트 호출의 InTok을 짝지어 비교한다. 양쪽에서 S\_a를 빼고 전달 비율을 구해 상위 이력이 하위로 통째로 넘어가는지(D4) 확인한다.

```kusto
let S = datatable(Agent:string, STok:long) [ "<에이전트명>", 0 ];   // 측정법 1–3으로 구한 S_a
let Roots = Spans | where Op == "invoke_agent"
    | summarize arg_min(TimeGenerated, Id) by OperationId | project OperationId, RootSpanId = Id;
let SubAgentSpans = Spans | where Op == "invoke_agent"
    | join kind=inner Roots on OperationId
    | where Id != RootSpanId
    | project OperationId, SubSpanId = Id, SubAgent = tostring(Properties["gen_ai.agent.name"]);
let SubFirst = Calls
    | join kind=inner SubAgentSpans on $left.ParentId == $right.SubSpanId
    | summarize arg_min(TimeGenerated, InTok) by OperationId, SubSpanId, SubAgent;
let ParentCalls = Calls
    | join kind=inner Roots on OperationId
    | where ParentId == RootSpanId
    | project OperationId, ParentTime = TimeGenerated, ParentIn = InTok, ParentAgent = Agent;
SubFirst
| join kind=inner ParentCalls on OperationId
| where ParentTime < TimeGenerated
| summarize arg_max(ParentTime, ParentIn, ParentAgent) by OperationId, SubSpanId, SubAgent, SubFirstIn = InTok
| join kind=inner (S | project SubAgent = Agent, SSub = STok) on SubAgent
| join kind=inner (S | project ParentAgent = Agent, SParent = STok) on ParentAgent
| where ParentIn > SParent
| extend PassRatio = todouble(SubFirstIn - SSub) / (ParentIn - SParent)
| summarize Delegations = count(),
            P50SubFirstIn = percentile(SubFirstIn, 50),
            P50ParentIn = percentile(ParentIn, 50),
            P50PassRatio = percentile(PassRatio, 50) by SubAgent
```

이 쿼리는 LLM 호출 span이 하위 `invoke_agent`의 직계 자식이라고 가정한다. 중간 span이 끼는 프레임워크라면 ParentId를 한 단계 더 올라가 매핑한다. 같은 매핑으로 하위 에이전트별 호출 수를 구해 B1, B4에도 쓴다.

- **판정**: 전달 비율 p50이 0.8 이상이면 상위 가변 컨텍스트 대부분이 하위로 넘어가는 것이다(초기값). 비율 1이 "상위 가변 컨텍스트 전체 전달"을 뜻하므로 기준의 의미가 분명하다. 상위 에이전트의 S\_a를 모르면 판정을 보류한다. \[C\]로 하위 에이전트 역할에 필요한 부분인지 확인해 D4로 판정한다. 하위 응답 반입이 크면 상위 쪽에서 C2와 같은 재전송 문제가 생긴다.
- **낭비 추정**: Σ(위임) (전달 토큰 − 필요 토큰) × 하위 에이전트 호출 수 × P\_eff.

