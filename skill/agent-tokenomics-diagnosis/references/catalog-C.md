# 문제 카탈로그 C

## C. 컨텍스트 누적

이력과 tool 결과는 한 번 들어오면 이후 모든 호출에 다시 실린다. 그래서 같은 토큰이라도 턴 초반, 대화 초반에 들어올수록 비싸다. C 범주는 "얼마나 들어오는가"와 "몇 번 재전송되는가"를 함께 잰다.

### C1. 대화 이력 무제한 누적

- **정의**: 대화 이력에 상한이나 요약이 없어, 턴이 쌓일수록 매 호출이 전체 이력을 다시 보낸다. Responses API의 `previous_response_id`나 conversation처럼 서버가 이력을 관리해도, 체인의 이전 Input 토큰은 매번 Input으로 과금된다. ([OpenAI](https://developers.openai.com/api/docs/guides/conversation-state))
- **확인 방법**: \[T\] 대화 안 턴 순번별 첫 호출 InTok을 본다. `gen_ai.conversation.id`가 있어야 한다.
- **지표**
  - 턴 간 Input 증가 배수 = 대화 길이 p90 턴의 첫 호출 InTok p50 ÷ 1턴 첫 호출 InTok p50. 턴 순번(TurnIdx)별 첫 호출 InTok 추이(그래프)도 함께 본다
  - 대화당 턴 수 p50, p90
  - 긴 대화 비용 비중 = 턴 수 상위 10% 대화의 비용 ÷ 전체 비용

**쿼리: 턴 순번별 첫 호출 Input.** 대화의 몇 번째 턴인지에 따라 첫 호출 InTok 중앙값이 어떻게 늘어나는지 선 그래프로 그린다. 이력에 상한이 있는지(C1) 확인한다.

```kusto
Calls
| where CallIdx == 1 and isnotempty(Conv)
| sort by Conv asc, TimeGenerated asc
| extend TurnIdx = row_number(1, prev(Conv) != Conv)
| where TurnIdx <= 30
| summarize Convs = dcount(Conv), P50In = percentile(InTok, 50) by Agent, TurnIdx
| order by Agent asc, TurnIdx asc
| render linechart
```

**쿼리: 긴 대화의 비용 비중.** 대화당 턴 수 분포와, 턴 수 상위 10% 대화가 전체 비용에서 차지하는 비중을 구한다. C1의 영향 크기를 가늠한다.

```kusto
let ConvCost = Calls
    | where isnotempty(Conv)
    | summarize Turns = dcount(OperationId), Cost = sum(Cost) by Agent, Conv;
ConvCost
| join kind=inner (ConvCost | summarize P50Turns = percentile(Turns, 50),
                                      P90Turns = percentile(Turns, 90) by Agent) on Agent
| summarize P50Turns = any(P50Turns), P90Turns = any(P90Turns),
            LongConvCostShare = round(sumif(Cost, Turns >= P90Turns) / sum(Cost), 3) by Agent
```

**쿼리: 턴 간 Input 증가 배수.** 에이전트별로 대화 길이의 p90에 해당하는 턴을 구하고, 그 턴의 첫 호출 InTok 중앙값을 1턴의 값과 비교한다. C1 판정 기준을 계산할 때 쓴다.

```kusto
let FirstCalls = Calls
    | where CallIdx == 1 and isnotempty(Conv)
    | sort by Conv asc, TimeGenerated asc
    | extend TurnIdx = row_number(1, prev(Conv) != Conv);
let P90Turn = FirstCalls
    | summarize Turns = max(TurnIdx) by Agent, Conv
    | summarize P90Turn = toint(percentile(Turns, 90)) by Agent;
FirstCalls
| summarize P50In = percentile(InTok, 50) by Agent, TurnIdx
| join kind=inner P90Turn on Agent
| summarize In1 = maxif(P50In, TurnIdx == 1), InP90 = maxif(P50In, TurnIdx == P90Turn),
            P90Turn = any(P90Turn) by Agent
| extend CrossTurnMultiple = round(1.0 * InP90 / In1, 2)
```

- **판정**
  - 턴 간 Input 증가 배수가 2 이상이면, 대화 길이 p90 시점에 이력이 기본 Input보다 커진 것이므로 C1로 판정한다(초기값). C2의 턴 내 증가 배수와 같은 기준이다.
  - 턴 순번 그래프가 일정 턴 이후 평탄해지거나 톱니 모양이면 절단·요약이 있는 것이다. 이때는 A3(이력 편집에 따른 캐시 손실)와 함께 본다.
  - LongConvCostShare는 C1의 영향 크기를 해석할 때 쓴다.
  - conversation.id가 없으면 이 항목은 진단할 수 없다. "계측 부재"로 기록한다.
- **낭비 추정**: 최근 k턴만 필요하다고 볼 때, 초과 이력 ≈ Σ(턴 순번 t > k) (턴 t 첫 호출 InTok − 턴 k 첫 호출 InTok) × 턴 t의 LLM 호출 수 × P\_eff. k는 리뷰로 정한다.

### C2. Tool 결과 원문 전달

- **정의**: tool 결과(전체 JSON, HTML, 로그, DB 행 전체)를 가공 없이 다음 호출 Input에 넣는다. 결과는 이력에 남아 이후 모든 호출에 다시 실린다.
- **확인 방법**: \[T\] 턴 안에서 Input이 얼마나 불어나는지 비율로 본다. \[C\] `ToolCallResult`로 tool별 크기를 직접 잰다.
- **지표**
  - 턴 내 Input 증가 배수 = 턴 마지막 호출 InTok ÷ 턴 첫 호출 InTok. 호출이 2회 이상인 턴만 센다.
  - 호출당 증가율 = ΔIn\_i ÷ InTok\_(i−1). ΔIn\_i = InTok\_i − InTok\_(i−1) − OutTok\_(i−1)로, 직전 Input과 직전 Output(tool 호출 메시지)을 빼고 남는 부분이며 대부분 tool 결과다. 이전 reasoning이 다음 Input에 포함되지 않는 API 방식이면 OutTok 대신 OutTok − ReasonTok을 뺀다.
  - tool별 결과 크기 p50, p95 \[C\]
  - 재전송 계수 = Σ(ΔIn × 그 시점 이후 턴 안에 남은 호출 수) ÷ Σ ΔIn. 결과 토큰 하나가 턴 안에서 평균 몇 번 실리는지를 뜻하며, 영향 크기를 해석할 때 쓴다.

**쿼리: 턴 내 Input 증가 배수와 호출당 증가율.** 턴 첫 호출 대비 마지막 호출의 Input 배수와, 연속 호출 사이 증가분(ΔIn)이 직전 Input에서 차지하는 비율을 에이전트별로 구한다. 재전송 계수도 함께 구한다. content 없이 C2를 진단할 때 쓴다.

```kusto
let TurnCalls = Calls | summarize N = max(CallIdx) by OperationId;
let Steps = Calls
    | extend PrevIn  = iff(CallIdx > 1, prev(InTok), long(null)),
             PrevOut = iff(CallIdx > 1, prev(OutTok), long(null))
    | where CallIdx > 1
    | join kind=inner TurnCalls on OperationId
    | extend Delta = InTok - PrevIn - PrevOut, Remaining = N - CallIdx + 1
    | extend GrowthRate = 1.0 * Delta / PrevIn;
let TurnGrowth = Calls
    | summarize FirstIn = maxif(InTok, CallIdx == 1), N = max(CallIdx) by Agent, OperationId
    | where N >= 2
    | join kind=inner (Calls | project OperationId, CallIdx, LastIn = InTok)
        on $left.OperationId == $right.OperationId, $left.N == $right.CallIdx
    | extend GrowthMultiple = 1.0 * LastIn / FirstIn;
TurnGrowth
| summarize Turns = count(),
            P50Multiple = round(percentile(GrowthMultiple, 50), 2),
            P95Multiple = round(percentile(GrowthMultiple, 95), 2) by Agent
| join kind=leftouter (Steps
    | summarize P95GrowthRate = round(percentile(GrowthRate, 95), 2),
                CarryFactor = round(1.0 * sum(Delta * Remaining) / sum(Delta), 2) by Agent) on Agent
| project Agent, Turns, P50Multiple, P95Multiple, P95GrowthRate, CarryFactor
```

**쿼리: Tool별 결과 크기.** `ToolCallResult` 원문 길이의 tool별 p50, p95를 구한다. 결과가 큰 tool(C2)을 특정한다.

```kusto
Tools
| join kind=inner (Content
                   | where isnotempty(ToolCallResult)
                   | project SpanId, ResultChars = strlen(ToolCallResult)) on $left.Id == $right.SpanId
| summarize Calls = count(), P50Chars = percentile(ResultChars, 50),
            P95Chars = percentile(ResultChars, 95) by ToolName
| order by P95Chars desc
```

- **판정**
  - 턴 내 Input 증가 배수 p50이 2 이상이면, 턴 안에서 Input이 두 배 넘게 불어나는 것이므로 이 증상이 있다고 본다(초기값).
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

```kusto
Spans
| where Op == "retrieval"
| extend TopK = toint(Properties["gen_ai.retrieval.top_k"]),
         Docs = parse_json(tostring(Properties["gen_ai.retrieval.documents"]))
| mv-expand Doc = Docs
| extend DocId = tostring(Doc.id), Score = todouble(Doc.score)
| summarize Searches = dcount(Id), ReturnedDocs = count(), AvgTopK = avg(TopK),
            LowScoreShare = round(1.0 * countif(Score < 0.5) / count(), 3),
            DupInTurn = count() - dcount(strcat(OperationId, "|", DocId))
```

점수 임계값(위 예시는 0.5)은 검색 엔진의 점수 척도에 맞춰 바꾼다. BM25, 벡터 유사도, semantic reranker의 척도가 서로 다르다.

- **판정**: 검색당 결과 토큰이 크고, 저점수 문서 비율이나 턴 내 중복이 높으면 C3로 판정한다. 결과가 크더라도 대부분 고점수이고 응답에 인용되면 정상이다.
- **낭비 추정**: (저점수 + 중복 문서 토큰) × 이후 남은 호출 수 × P\_eff.

