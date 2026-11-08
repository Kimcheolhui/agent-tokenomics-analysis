-- 로컬 분석 기본 뷰. 가이드 "공통 쿼리 정의"와 1:1로 대응한다.
-- 리포지터리 루트에서 실행한다:  duckdb -init queries/views.sql
--
-- KQL과 다른 점 세 가지를 여기서 흡수한다.
--   1. KQL tostring()은 값이 없을 때 빈 문자열을 준다. blank()로 NULL로 돌린다.
--      CSV 추론이 컬럼을 숫자나 불리언으로 잡는 경우가 있어 nullif(x, '')는 그때 깨진다.
--      blank()는 VARCHAR로 캐스팅한 뒤 비교하므로 추론 타입에 영향받지 않는다.
--   2. CallIdx와 Cost는 추출 때 계산하지 않는다. 일 단위 파일이 턴을 가를 수 있어서 전체 기간을 모아 계산한다.
--   3. ResultCode는 숫자(HTTP 상태)와 문자열이 섞여 들어오므로 VARCHAR로 통일한다.

-- 시각은 UTC로 고정한다. 추출한 타임스탬프는 TIMESTAMP WITH TIME ZONE으로 읽히고,
-- 세션 타임존에 따라 날짜 경계와 일별 집계가 달라져 보고서 수치가 재현되지 않는다.
-- 고객의 영업일 기준으로 보려면 이 줄을 고객 타임존으로 바꾸고 보고서에 적는다.
SET TimeZone = 'UTC';

CREATE OR REPLACE MACRO blank(x) AS nullif(CAST(x AS VARCHAR), '');

CREATE OR REPLACE MACRO cacheable(x, min_tok, round128) AS
  CASE WHEN x IS NULL OR min_tok IS NULL OR x < min_tok THEN 0
       WHEN round128 THEN CAST(floor(x / 128.0) * 128 AS BIGINT)
       ELSE CAST(x AS BIGINT) END;

-- 설정 ---------------------------------------------------------------------

CREATE OR REPLACE VIEW model_params AS
  SELECT * FROM read_csv_auto('config/model-params.csv', header = true);

CREATE OR REPLACE VIEW agent_fixed AS
  SELECT Agent, STok, Method FROM read_csv_auto('config/agents.csv', header = true);

-- 추출 원본 ----------------------------------------------------------------

CREATE OR REPLACE VIEW calls_raw AS
  SELECT * FROM read_csv_auto('data/calls/*.csv', header = true, union_by_name = true);

CREATE OR REPLACE VIEW agent_spans AS
  SELECT TimeGenerated, OperationId, SpanId, blank(ParentId) AS ParentId,
         blank(Agent) AS Agent, blank(ConvId) AS ConvId, Success,
         blank(ResultCode) AS ResultCode, blank(ErrorType) AS ErrorType, DurationMs
  FROM read_csv_auto('data/agent_spans/*.csv', header = true, union_by_name = true);

CREATE OR REPLACE VIEW tools AS
  SELECT TimeGenerated, OperationId, SpanId, blank(ParentId) AS ParentId,
         blank(ToolName) AS ToolName, blank(ToolCallId) AS ToolCallId, Success,
         blank(ResultCode) AS ResultCode, blank(ErrorType) AS ErrorType, DurationMs
  FROM read_csv_auto('data/tools/*.csv', header = true, union_by_name = true);

CREATE OR REPLACE VIEW retrieval AS
  SELECT TimeGenerated, OperationId, SpanId, blank(ParentId) AS ParentId,
         TopK, DocCount, blank(DocId) AS DocId, Score
  FROM read_csv_auto('data/retrieval/*.csv', header = true, union_by_name = true);

CREATE OR REPLACE VIEW tool_schemas AS
  SELECT AgentName, ToolName, Chars, DescChars
  FROM (SELECT *, row_number() OVER (PARTITION BY AgentName, ToolName ORDER BY CapturedAt DESC) AS rn
        FROM read_csv_auto('data/tool_schemas/*.csv', header = true, union_by_name = true))
  WHERE rn = 1;

-- content 요약. 원문은 없고 해시와 길이만 있다.
-- ToolNames는 정의에 나온 순서다. 순서를 무시한 비교는 ToolNamesHash를 쓴다(A2 이름 집합 변형).
CREATE OR REPLACE VIEW content AS
  SELECT TimeGenerated, TraceId AS OperationId, SpanId, blank(AgentName) AS AgentName,
         SysChars, blank(SysHash) AS SysHash, blank(SysHeadHash) AS SysHeadHash,
         ToolDefsChars, blank(ToolDefsHash) AS ToolDefsHash,
         ToolCount, blank(ToolNames) AS ToolNames,
         CASE WHEN blank(ToolNames) IS NULL THEN NULL
              ELSE md5(array_to_string(list_sort(string_split(CAST(ToolNames AS VARCHAR), '|')), '|')) END
           AS ToolNamesHash,
         InChars, blank(InHash) AS InHash,
         OutChars, blank(OutHash) AS OutHash,
         ArgsChars, blank(ArgsHash) AS ArgsHash,
         ResultChars, blank(ResultHash) AS ResultHash
  FROM read_csv_auto('data/content_digest/*.csv', header = true, union_by_name = true);

-- 분석 단위 ----------------------------------------------------------------

-- 턴 = OperationId별 최초 invoke_agent span. 가이드의 Turns에 대응한다.
-- TurnEnd는 F3(빠른 재입력 비율)에 쓴다.
CREATE OR REPLACE VIEW turns AS
  SELECT OperationId, SpanId AS RootSpanId, TimeGenerated AS TurnStart,
         Success AS TurnOk, Agent AS RootAgent, ConvId AS TurnConv, DurationMs AS TurnMs,
         TimeGenerated + INTERVAL (coalesce(DurationMs, 0)) MILLISECOND AS TurnEnd
  FROM (SELECT *, row_number() OVER (PARTITION BY OperationId ORDER BY TimeGenerated, SpanId) AS rn
        FROM agent_spans)
  WHERE rn = 1;

-- 루트가 아닌 invoke_agent span = 하위 에이전트 실행. D4에 쓴다.
CREATE OR REPLACE VIEW sub_agents AS
  SELECT a.OperationId, a.SpanId AS SubSpanId, a.ParentId, a.Agent AS SubAgent,
         a.TimeGenerated, a.Success, a.DurationMs
  FROM agent_spans a JOIN turns t ON t.OperationId = a.OperationId
  WHERE a.SpanId <> t.RootSpanId;

-- span 부모 링크. call_owner의 탐색용이다. 추출한 span 종류만 들어 있다.
CREATE OR REPLACE VIEW span_parents AS
            SELECT OperationId, SpanId, blank(ParentId) AS ParentId FROM calls_raw
  UNION ALL SELECT OperationId, SpanId, ParentId FROM agent_spans
  UNION ALL SELECT OperationId, SpanId, ParentId FROM tools
  UNION ALL SELECT DISTINCT OperationId, SpanId, ParentId FROM retrieval;

-- 호출을 가장 가까운 조상 invoke_agent span에 귀속시킨다.
-- gen_ai.agent.name이 chat span에 없으면 호출이 루트 에이전트로 집계된다. 멀티 에이전트에서는
-- 하위 에이전트의 호출과 tool이 상위로 잘못 붙는다(가이드 B1 판정의 주의사항, D4의 에이전트 매핑).
-- 중간 span이 추출 대상 밖이면 탐색이 끊기고, 그 호출은 calls에서 RootAgent로 되돌아간다.
CREATE OR REPLACE VIEW call_owner AS
  WITH RECURSIVE walk AS (
      SELECT SpanId AS CallSpanId, OperationId, blank(ParentId) AS Cur, 0 AS Depth
      FROM calls_raw
    UNION ALL
      SELECT w.CallSpanId, w.OperationId, p.ParentId, w.Depth + 1
      FROM walk w
      JOIN span_parents p ON p.OperationId = w.OperationId AND p.SpanId = w.Cur
      LEFT JOIN agent_spans a ON a.OperationId = w.OperationId AND a.SpanId = w.Cur
      WHERE w.Cur IS NOT NULL AND a.SpanId IS NULL AND w.Depth < 16
  )
  SELECT w.CallSpanId AS SpanId, w.OperationId,
         a.SpanId AS OwnerSpanId, a.Agent AS OwnerAgent, w.Depth
  FROM walk w
  JOIN agent_spans a ON a.OperationId = w.OperationId AND a.SpanId = w.Cur;

-- LLM 호출. CallIdx, Cost, 모델 파라미터가 붙는다. 가이드의 Calls에 대응한다.
-- Agent는 호출 자신의 값 → 조상 invoke_agent → 루트 순으로 정한다.
-- 엄격한 귀속이 필요한 지표(B1, B4, D4)는 OwnerAgent를 직접 쓴다.
CREATE OR REPLACE VIEW calls AS
  WITH base AS (
    SELECT TimeGenerated, OperationId, SpanId, blank(ParentId) AS ParentId,
           blank(Agent) AS CallAgent, blank(ConvId) AS CallConv,
           blank(Model) AS Model, InTok, CacheTok, WriteTok, OutTok, ReasonTok,
           blank(Finish) AS Finish, blank(Status) AS Status, MaxTok,
           blank(ReasonLevel) AS ReasonLevel, blank(Compacted) AS Compacted,
           blank(Endpoint) AS Endpoint, blank(ErrorType) AS ErrorType,
           Success, blank(ResultCode) AS ResultCode, DurationMs,
           row_number() OVER (PARTITION BY OperationId ORDER BY TimeGenerated, SpanId) AS CallIdx
    FROM calls_raw
  )
  SELECT b.TimeGenerated, b.OperationId, b.SpanId, b.ParentId, b.CallIdx,
         coalesce(b.CallAgent, o.OwnerAgent, t.RootAgent) AS Agent,
         o.OwnerAgent, o.OwnerSpanId,
         coalesce(b.CallConv, t.TurnConv) AS Conv,
         b.Model, b.InTok, b.CacheTok, b.WriteTok, b.OutTok, b.ReasonTok,
         b.OutTok - b.ReasonTok AS VisibleTok,
         b.Finish, b.Status, b.MaxTok, b.ReasonLevel, b.Compacted, b.Endpoint,
         b.ErrorType, b.Success, b.ResultCode, b.DurationMs,
         t.TurnOk, t.RootSpanId, t.RootAgent,
         m.PIn, m.PCache, m.PWrite, m.POut, m.MinTok, m.Round128, m.WindowMin, m.RpmLimit,
         ((b.InTok - b.CacheTok - b.WriteTok) * m.PIn + b.CacheTok * m.PCache
          + b.WriteTok * m.PWrite + b.OutTok * m.POut) / 1e6 AS Cost
  FROM base b
  LEFT JOIN call_owner o ON o.SpanId = b.SpanId AND o.OperationId = b.OperationId
  LEFT JOIN turns t ON t.OperationId = b.OperationId
  LEFT JOIN model_params m ON m.Model = b.Model;

-- 연속 호출 간 증가분. C2 ΔIn·재전송 계수, F2 재요청 추정에 쓴다.
-- 이전 reasoning이 다음 Input에 포함되지 않는 API면 Delta 대신 DeltaNoReason을 쓴다.
CREATE OR REPLACE VIEW call_steps AS
  SELECT *,
         InTok - PrevIn - PrevOut                AS Delta,
         InTok - PrevIn - (PrevOut - PrevReason) AS DeltaNoReason,
         N - CallIdx + 1                         AS Remaining
  FROM (
    SELECT c.*,
           lag(InTok)     OVER w AS PrevIn,
           lag(OutTok)    OVER w AS PrevOut,
           lag(ReasonTok) OVER w AS PrevReason,
           lag(Finish)    OVER w AS PrevFinish,
           max(CallIdx)   OVER (PARTITION BY OperationId) AS N
    FROM calls c
    WINDOW w AS (PARTITION BY OperationId ORDER BY CallIdx)
  );

-- 호출 유형. E1의 CallType 분류이고 E2–E4가 같은 분류를 쓴다.
CREATE OR REPLACE VIEW call_types AS
  SELECT *,
         CASE WHEN Finish LIKE '%tool_calls%' AND CallIdx = 1 THEN '1. 계획·첫 tool 선택'
              WHEN Finish LIKE '%tool_calls%'                 THEN '2. tool 결과 처리·다음 tool 선택'
              WHEN VisibleTok < 50                            THEN '3. 짧은 출력'
              WHEN CallIdx = N                                THEN '4. 최종 응답'
              ELSE '5. 기타' END AS CallType
  FROM call_steps;

-- 점검용 ------------------------------------------------------------------

-- config/model-params.csv와 매칭되지 않은 모델. 여기 행이 있으면 그 호출의 Cost가 NULL이고
-- 비용 합계에서 조용히 빠진다. 분석을 시작하기 전에 비워야 한다.
CREATE OR REPLACE VIEW unpriced_calls AS
  SELECT coalesce(Model, '(없음)') AS Model, count(*) AS Calls
  FROM calls WHERE PIn IS NULL GROUP BY 1 ORDER BY Calls DESC;

-- S_a가 비어 있는 에이전트. A 공통·A1·A4·B 공통 지표를 산출할 수 없다.
CREATE OR REPLACE VIEW missing_sa AS
  SELECT c.Agent, count(*) AS Calls
  FROM calls c LEFT JOIN agent_fixed s ON s.Agent = c.Agent
  WHERE s.STok IS NULL OR s.STok = 0 GROUP BY c.Agent ORDER BY Calls DESC;

-- 계측 부재 점검. 0이면 그 키를 쓰는 지표를 산출할 수 없다.
CREATE OR REPLACE VIEW instrumentation AS
  SELECT count(*) AS Calls,
         count_if(CacheTok > 0)        AS WithCacheRead,
         count_if(ReasonTok > 0)       AS WithReasoning,
         count_if(Conv IS NOT NULL)    AS WithConvId,
         count_if(Finish IS NOT NULL)  AS WithFinish,
         count_if(MaxTok IS NOT NULL)  AS WithMaxTokens,
         count_if(OwnerAgent IS NOT NULL) AS WithAgentOwner
  FROM calls;

-- 보고서 meta에 넣는 전체 수치.
CREATE OR REPLACE VIEW overview AS
  SELECT min(TimeGenerated)::DATE AS PeriodFrom, max(TimeGenerated)::DATE AS PeriodTo,
         count(DISTINCT OperationId) AS Turns, count(*) AS Calls,
         round(sum(Cost), 2) AS TotalCost,
         round(1.0 * sum(CacheTok) / nullif(sum(InTok), 0), 3) AS CacheRate,
         round(sum(OutTok * POut / 1e6) / nullif(sum(Cost), 0), 3) AS OutCostShare
  FROM calls;
