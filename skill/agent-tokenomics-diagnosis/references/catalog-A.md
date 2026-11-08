# 문제 카탈로그 A

## A. Prefix 변동 (캐시 미스)

캐시는 앞에서부터 바이트 단위로 같은 prefix만 재사용한다. 그래서 앞부분이 한 글자만 달라도 그 뒤 전체가 미스 난다. A 범주의 모든 항목은 아래 공통 지표에서 출발한다.

### A 공통 지표: 캐시 달성률

- **캐시율** = Σ CacheTok ÷ Σ InTok. 실제로 할인받은 Input 비중이다.
- **이론 캐시 가능량** Theo\_i: 턴 첫 호출은 Cacheable(S\_a), 두 번째 이후 호출은 Cacheable(직전 호출 InTok). 에이전트 루프에서는 직전 호출의 Input 전체가 다음 호출의 prefix가 되기 때문이다.
- **캐시 달성률** = Σ CacheTok ÷ Σ Theo\_i. 이론 상한 대비 실제 히트 비율이다.

**쿼리: 에이전트별 캐시율·캐시 달성률.** 실제 캐시 히트 토큰을 이론 상한과 비교해, A 범주를 점검할지 판단한다. 첫 줄의 S 표에는 3-B에서 측정한 에이전트별 고정 Input 토큰을 넣는다.

```kusto
let S = datatable(Agent:string, STok:long) [ "<에이전트명>", 0 ];   // 3-B 측정값
Calls
| extend PrevIn = iff(CallIdx > 1, prev(InTok), long(null))
| join kind=leftouter S on Agent
| extend Theo = min_of(iff(CallIdx == 1, Cacheable(STok, MinTok, Round128),
                                         Cacheable(PrevIn, MinTok, Round128)), InTok)
| summarize Input = sum(InTok), Actual = sum(CacheTok), Theoretical = sum(Theo) by Agent
| extend CacheRate   = round(1.0 * Actual / Input, 3),
         Achievement = round(1.0 * Actual / Theoretical, 3)
```

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

```kusto
let S = datatable(Agent:string, STok:long) [ "<에이전트명>", 0 ];   // 3-B 측정값
let Rpm = Calls | summarize MinuteCalls = count() by Agent, Minute = bin(TimeGenerated, 1m);
Calls
| where CallIdx == 1
| sort by Agent asc, TimeGenerated asc
| extend GapMin = iff(prev(Agent) == Agent,
                      datetime_diff('second', TimeGenerated, prev(TimeGenerated)) / 60.0, real(null)),
         Minute = bin(TimeGenerated, 1m)
| join kind=inner S on Agent
| join kind=leftouter Rpm on Agent, Minute
| where STok >= MinTok and GapMin < WindowMin and MinuteCalls <= RpmLimit
| summarize WarmFirstCalls = count(), Miss = countif(CacheTok == 0) by Agent
| extend WarmMissRate = round(1.0 * Miss / WarmFirstCalls, 3)
```

**쿼리: System prompt 변형 수.** 같은 에이전트의 `SystemInstructions`가 몇 가지로 기록됐는지 센다. 전체 변형(Variants)과 앞 4,000자 변형(HeadVariants)을 함께 보고, 동적 값이 앞부분에 있는지 확인한다.

```kusto
Content
| where isnotempty(SystemInstructions)
| summarize Records = count(),
            Variants = dcount(hash_sha256(SystemInstructions)),
            HeadVariants = dcount(hash_sha256(substring(SystemInstructions, 0, 4000)))
  by AgentName
| extend VariantRatio = round(1.0 * Variants / Records, 3)
```

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

```kusto
Content
| where isnotempty(ToolDefinitions)
| extend Names = tostring(array_sort_asc(extract_all(@'"name"\s*:\s*"([^"]+)"', ToolDefinitions)))
| summarize Records = count(),
            RawVariants = dcount(hash_sha256(ToolDefinitions)),
            NameSetVariants = dcount(hash_sha256(Names))
  by AgentName
```

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

```kusto
Calls
| where isnotempty(Conv)
| sort by Conv asc, TimeGenerated asc
| extend PrevIn = iff(prev(Conv) == Conv, prev(InTok), long(null)),
         Compacted = tobool(Properties["gen_ai.conversation.compacted"])
| extend Shrink = PrevIn > InTok
| summarize Calls = count(),
            ShrinkEvents = countif(Shrink),
            CompactedCalls = countif(Compacted),
            HitAfterShrink = round(avgif(1.0 * CacheTok / InTok, Shrink), 3),
            HitNormal = round(avgif(1.0 * CacheTok / InTok, not(Shrink) and CallIdx > 1), 3)
  by Agent
```

- **판정**
  - 축소 이벤트가 있고 HitAfterShrink가 HitNormal보다 크게 낮으면 A3로 판정한다.
  - 턴 경계마다 축소가 생기면, 이전 턴의 tool 결과나 중간 메시지를 이력에서 빼는 구조다. 이 경우 다음 턴 첫 호출은 고정부까지만 히트한다.
  - 축소 없이 CallIdx별 달성률만 떨어지면 A1(재렌더링)이나 A2다.
- **낭비 추정**: Σ(축소 이벤트) \[Cacheable(편집 지점 이전 공유 길이) − CacheTok\] × (P\_in − P\_cache). 공유 길이는 \[C\] `InputMessages`를 비교해 구한다. 편집으로 줄어든 Input Σ(PrevIn − InTok)도 함께 기록해야 순효과를 판단할 수 있다.

### A4. Prefix 1,024 미만

- **정의**: 고정 prefix가 모델의 최소 캐시 길이(MinTok)보다 짧으면 턴 첫 호출은 구조적으로 캐시되지 않는다. 짧은 단발 호출(분류, 라우팅)도 마찬가지다.
- **확인 방법**: \[S\] S\_a를 측정한다. \[T\] InTok < MinTok인 호출의 비중을 본다.
- **지표**: 캐시 불가 호출 비중, 캐시 불가 비용 비중

**쿼리: 캐시 불가 호출 비중.** Input이 모델의 MinTok보다 짧아 캐시 대상이 될 수 없는 호출의 건수와 비용 비중을 구한다. A4의 영향 크기를 가늠한다.

```kusto
Calls
| extend Small = InTok < MinTok
| summarize Calls = count(), Cost = sum(Cost),
            SmallCalls = countif(Small), SmallCost = sumif(Cost, Small)
  by Agent
| extend SmallCallShare = round(1.0 * SmallCalls / Calls, 3),
         SmallCostShare = round(SmallCost / Cost, 3)
```

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

**쿼리: 요청 간격별 첫 호출 히트율.** 같은 에이전트의 연속된 턴 첫 호출 사이 간격을 모델 보존 시간(WindowMin)의 배수 구간으로 나눠 히트율을 비교한다. 보존 시간을 넘는 구간에서 히트율이 급락하는지로 캐시 만료(A5)를 확인한다.

```kusto
Calls
| where CallIdx == 1
| sort by Agent asc, TimeGenerated asc
| extend GapMin = iff(prev(Agent) == Agent,
                      datetime_diff('second', TimeGenerated, prev(TimeGenerated)) / 60.0, real(null))
| where isnotnull(GapMin)
| extend Bucket = case(GapMin < WindowMin,     "a. 보존 시간 안",
                       GapMin < 2 * WindowMin, "b. 1–2배",
                       GapMin < 6 * WindowMin, "c. 2–6배",
                                               "d. 6배 이상")
| summarize FirstCalls = count(), HitRate = round(1.0 * countif(CacheTok > 0) / count(), 3)
  by Agent, Bucket
| order by Agent asc, Bucket asc
```

- **판정**
  - "보존 시간 안" 구간의 히트율은 높은데 그 밖의 구간에서 급락하면 A5로 판정한다.
  - "보존 시간 안" 구간부터 히트율이 낮으면 A1, A2, A6을 확인한다.
- **낭비 추정**: Σ(보존 시간 밖 첫 호출) Cacheable(S\_a) × (P\_in − P\_cache) × ("보존 시간 안" 히트율 − 해당 구간 히트율).

### A6. 트래픽 분산·쏠림

- **정의**: 두 가지 경우가 있다.
  - 분산: 같은 prefix 요청이 여러 엔드포인트로 흩어진다. Azure 구독 간에는 캐시가 공유되지 않는다.
  - 쏠림: 캐시는 개별 머신에 저장된다. 같은 prefix·`prompt_cache_key` 조합의 요청이 분당 약 15건(RpmLimit)을 넘으면 일부가 캐시 없는 머신으로 넘어가 미스 난다.
- **확인 방법**: \[T\] 엔드포인트별 히트율과 분당 요청 수 구간별 히트율을 본다. \[S\] 게이트웨이 라우팅 구성과 `prompt_cache_key` 사용 여부를 확인한다.
- **지표**: 엔드포인트별 히트율, 분당 요청 수 구간별 히트율

**쿼리: 엔드포인트별 히트율.** 에이전트가 호출한 엔드포인트별로 캐시 히트율을 비교해 트래픽 분산(A6)을 확인한다.

```kusto
Calls
| summarize Calls = count(), HitRate = round(1.0 * countif(CacheTok > 0) / count(), 3)
  by Agent, Endpoint
```

**쿼리: 분당 요청 수 구간별 히트율.** 에이전트별 분당 LLM 호출 수를 RpmLimit 대비 구간으로 나눠 히트율을 비교한다. RpmLimit을 넘는 구간에서 생기는 쏠림(A6)을 확인한다.

```kusto
Calls
| summarize Rpm = count(), Hits = countif(CacheTok > 0), RpmLimit = any(RpmLimit)
  by Agent, Minute = bin(TimeGenerated, 1m)
| extend RpmBucket = case(Rpm <= RpmLimit,     "a. 기준 이하",
                          Rpm <= 2 * RpmLimit, "b. 기준의 1–2배",
                                               "c. 기준의 2배 초과")
| summarize Minutes = count(), HitRate = round(1.0 * sum(Hits) / sum(Rpm), 3) by Agent, RpmBucket
```

- **판정**
  - 한 에이전트가 여러 엔드포인트를 쓰고 엔드포인트별 히트율이 다르면 분산으로 판정한다. 리전·배포 간 공유 여부는 문서에 명시되지 않았으므로 이 실측값으로 판단한다.
  - RpmLimit을 넘는 구간에서 히트율이 떨어지면 쏠림으로 판정한다.
- **낭비 추정**: Σ(해당 구간 호출) Cacheable(S\_a) × (기준 구간 히트율 − 해당 구간 히트율) × (P\_in − P\_cache).

