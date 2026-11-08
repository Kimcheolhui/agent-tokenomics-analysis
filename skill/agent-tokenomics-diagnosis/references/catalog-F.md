# 문제 카탈로그 F

## F. 실패로 인한 낭비

결과를 내지 못한 호출과 턴의 비용은 전액 낭비다. 게다가 실패 결과와 재시도가 컨텍스트에 쌓여 이후 호출까지 비싸진다. F 범주는 실패가 어디서 나고 얼마를 쓰고 끝나는지를 잰다.

### F1. Tool 실패 후 재시도

- **정의**: tool이 실패하고(잘못된 인자, 타임아웃, 권한 오류) LLM이 다시 시도한다. 실패 결과와 재시도 호출이 모두 컨텍스트에 쌓인다.
- **확인 방법**: \[T\] `execute_tool` span의 `Success`, `error.type`, `ResultCode`를 본다.
- **지표**
  - tool별 실패율
  - 실패가 있는 턴과 없는 턴의 비용 중앙값, 호출 수 중앙값

**쿼리: Tool별 실패율.** `execute_tool` span의 성공 여부로 tool별 실패 건수와 실패율을 구하고 오류 유형 예시를 붙인다. 실패가 잦은 tool(F1)을 찾는다.

```kusto
Tools
| summarize Calls = count(), Failed = countif(Success == false),
            FailRate = round(1.0 * countif(Success == false) / count(), 3),
            SampleErr = take_anyif(ToolErr, isnotempty(ToolErr))
  by ToolName
| order by Failed desc
```

**쿼리: 실패 턴과 정상 턴 비교.** tool 실패가 있는 턴과 없는 턴의 비용 중앙값, 호출 수 중앙값을 나란히 구한다. 실패가 비용을 얼마나 키우는지(F1) 본다.

```kusto
let FailTurns = Tools | summarize HasFail = countif(Success == false) > 0 by OperationId;
Calls
| summarize TurnCost = sum(Cost), N = max(CallIdx) by Agent, OperationId
| join kind=leftouter FailTurns on OperationId
| extend HasFail = coalesce(HasFail, false)
| summarize Turns = count(), P50TurnCost = percentile(TurnCost, 50), P50Calls = percentile(N, 50),
            TotalCost = sum(TurnCost) by Agent, HasFail
```

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

```kusto
Calls
| extend PrevIn     = iff(CallIdx > 1, prev(InTok), long(null)),
         PrevFinish = iff(CallIdx > 1, prev(Finish), "")
| extend Failed = Success == false or Finish contains "content_filter" or Finish contains "error",
         Resend = isnotnull(PrevIn) and abs(InTok - PrevIn) <= 50 and not(PrevFinish contains "tool_calls")
| summarize Calls = count(), FailedCalls = countif(Failed),
            FailedCost = round(sumif(Cost, Failed), 2),
            Resends = countif(Resend), ResendCost = round(sumif(Cost, Resend), 2)
  by Agent
```

실패 호출은 `ResultCode`와 `tostring(Properties["error.type"])`로 다시 나눠 본다.

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

```kusto
Spans
| where Op == "invoke_agent"
| summarize arg_min(TimeGenerated, DurationMs, Properties) by OperationId
| extend Conv = tostring(Properties["gen_ai.conversation.id"]),
         TurnEnd = datetime_add('millisecond', toint(DurationMs), TimeGenerated)
| where isnotempty(Conv)
| sort by Conv asc, TimeGenerated asc
| extend GapSec = iff(prev(Conv) == Conv, datetime_diff('second', TimeGenerated, prev(TurnEnd)), long(null))
| summarize Turns = count(),
            QuickRetryRate = round(1.0 * countif(GapSec between (0 .. 60)) / count(), 3)
```

**쿼리: 대화별 사용자 메시지 추출.** 턴 첫 호출의 `InputMessages`에서 마지막 user 메시지를 꺼내 대화·시간순으로 정렬한다. 연속 메시지 유사도 계산(F3)의 입력으로 쓴다.

```kusto
Calls
| where CallIdx == 1 and isnotempty(Conv)
| join kind=inner (Content | project SpanId, InputMessages) on $left.Id == $right.SpanId
| mv-apply M = parse_json(InputMessages) on (
      where tostring(M.role) == "user" | summarize Users = make_list(M))
| project Conv, TimeGenerated, UserText = tostring(Users[-1].parts[0].content)
| order by Conv asc, TimeGenerated asc
```

두 번째 쿼리로 대화별 사용자 메시지를 뽑은 뒤, 연속 메시지의 임베딩 유사도는 Python 등으로 계산한다. 메시지 구조(`parts[0].content`)는 계측에 따라 다를 수 있으니 실제 JSON을 먼저 확인한다.

- **판정**: \[C\] 재질문 비율이 높고, 재질문 직전 턴의 응답이 샘플에서 불완전하거나 엉뚱하면 F3로 판정한다. 빠른 재입력 비율의 수치 기준은 두지 않는다. \[T\] 근사만 있으면 비율과 직전 턴 비용을 기록하고 판정을 보류한다. 빠른 재입력이 꼭 재질문은 아니기 때문이다.
- **낭비 추정**: Σ(재질문 직전 턴) 턴 비용.

### F4. 실패로 끝난 턴

- **정의**: 루트 `invoke_agent`가 오류로 끝난 턴이다. 그 턴에 쓴 토큰 전체가 결과 없이 사라진다.
- **확인 방법**: \[T\] 루트 `invoke_agent` span의 `Success`를 본다.
- **지표**: 실패 턴 비율, 실패 턴 비용 비중, 실패 턴과 정상 턴의 평균 호출 수, 늦은 실패 계수(실패 턴 비용 비중 ÷ 실패 턴 비율)

**쿼리: 실패 턴 비용.** 루트 `invoke_agent`의 성공 여부로 실패 턴 비율, 실패 턴 비용 비중, 실패·정상 턴의 평균 호출 수를 구한다. F4를 확인한다.

```kusto
Calls
| summarize TurnCost = sum(Cost), N = max(CallIdx), TurnOk = any(TurnOk) by Agent, OperationId
| summarize Turns = count(),
            FailTurnRate  = round(1.0 * countif(TurnOk == false) / count(), 3),
            FailCostShare = round(sumif(TurnCost, TurnOk == false) / sum(TurnCost), 3),
            AvgCallsFail  = round(avgif(N, TurnOk == false), 1),
            AvgCallsOk    = round(avgif(N, TurnOk == true), 1)
  by Agent
```

- **판정**
  - 늦은 실패 계수(FailCostShare ÷ FailTurnRate)가 1.5 이상이면, 실패 턴이 평균 턴보다 1.5배 넘게 비싼 것이므로 많이 쓰고 나서 실패하는 "늦은 실패"로 판정한다(초기값). 1을 넘으면 실패 턴이 평균보다 비싸다는 뜻이고, 0.5는 잡음 여유다. D1, F1과 연결해 본다.
  - 앱이 예외를 잡아 정상 응답으로 감싸면 `Success`가 true로 남는다. \[S\]로 오류 처리 방식을 확인한다.
- **낭비 추정**: Σ 실패 턴 비용.

