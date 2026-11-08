# 문제 카탈로그 E

## E. 모델·출력 설정

Output 토큰은 Input보다 단가가 몇 배 높고, Reasoning 토큰은 보이지 않지만 Output 단가로 과금된다. E 범주는 단계에 비해 비싼 모델이나 설정이 쓰이는지를 찾는다. 품질 영향은 진단 범위 밖이므로, 여기서 찾은 것은 "후보"로 기록한다.

### E1. 단순 단계에 상위 모델

- **정의**: 계획, tool 인자 생성, 분류처럼 출력이 짧은 단계까지 최상위 모델이나 reasoning 모델을 쓴다.
- **확인 방법**: \[T\] LLM 호출을 유형으로 나누고, 유형 × 모델별 비용을 본다.
- **지표**: 호출 유형별 모델 비용 비중, 유형별 가시 출력(OutTok − ReasonTok) p50, 잠재 절감 비중

**쿼리: 호출 유형별·모델별 비용.** LLM 호출을 계획, tool 처리, 짧은 출력, 최종 응답, 기타로 나누고 유형 × 모델별 비용과 출력 크기를 구한다. 단순 단계에 상위 모델을 쓰는지(E1) 본다.

```kusto
let LastIdx = Calls | summarize LastIdx = max(CallIdx) by OperationId;
Calls
| join kind=inner LastIdx on OperationId
| extend CallType = case(
      Finish contains "tool_calls" and CallIdx == 1, "1. 계획·첫 tool 선택",
      Finish contains "tool_calls",                  "2. tool 결과 처리·다음 tool 선택",
      (OutTok - ReasonTok) < 50,                     "3. 짧은 출력",
      CallIdx == LastIdx,                            "4. 최종 응답",
                                                     "5. 기타")
| summarize Calls = count(), Cost = round(sum(Cost), 2),
            P50Visible = percentile(OutTok - ReasonTok, 50), P50Reason = percentile(ReasonTok, 50)
  by Agent, CallType, Model
| order by Agent asc, CallType asc, Cost desc
```

이 CallType 분류는 E2–E4에서도 같은 방식으로 붙여 쓴다.

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

```kusto
Calls
| extend Level = tostring(Properties["gen_ai.request.reasoning.level"])
| summarize Calls = count(),
            ReasonShare = round(1.0 * sum(ReasonTok) / sum(OutTok), 3),
            P50Reason = percentile(ReasonTok, 50), P95Reason = percentile(ReasonTok, 95),
            P50Visible = percentile(OutTok - ReasonTok, 50)
  by Agent, Model, Level
```

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

```kusto
let LastIdx = Calls | summarize LastIdx = max(CallIdx) by OperationId;
Calls
| join kind=inner LastIdx on OperationId
| where CallIdx == LastIdx and not(Finish contains "tool_calls")
| extend Visible = OutTok - ReasonTok,
         MaxTok  = toint(Properties["gen_ai.request.max_tokens"])
| summarize Finals = count(), P50Visible = percentile(Visible, 50), P95Visible = percentile(Visible, 95),
            NoMaxTokens = round(1.0 * countif(isnull(MaxTok)) / count(), 3) by Agent
```

**쿼리: Output 비용 비중.** 에이전트별 전체 비용 중 Output 토큰 비용의 비중을 구한다. E3의 영향 크기를 가늠한다.

```kusto
Calls
| summarize OutCostShare = round(sum(OutTok * POut / 1e6) / sum(Cost), 3) by Agent
```

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

```kusto
let LastIdx = Calls | summarize LastIdx = max(CallIdx) by OperationId;
Calls
| join kind=inner LastIdx on OperationId
| extend Truncated = Finish contains "length"
                     or tostring(Properties["gen_ai.response.status"]) == "incomplete"
| summarize Calls = count(), TruncCalls = countif(Truncated),
            TruncRate = round(1.0 * countif(Truncated) / count(), 4),
            TruncReasonShare = round(1.0 * sumif(ReasonTok, Truncated) / sumif(OutTok, Truncated), 3),
            FollowUpAfterTrunc = countif(Truncated and CallIdx < LastIdx),
            TruncCost = round(sumif(Cost, Truncated), 2)
  by Agent, Model
```

- **판정**: 잘림이 반복되고(2건 이상) 잘린 뒤 후속 호출이 이어지면 E4로 판정한다. TruncReasonShare가 0.8 이상이면 reasoning 소진형이다.
- **낭비 추정**: Σ 잘린 호출 Cost + 잘림 때문에 생긴 후속 호출 Cost.

