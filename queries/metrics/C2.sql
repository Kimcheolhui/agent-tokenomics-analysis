-- C2: Tool 결과 원문 전달.
-- 턴 내 증가 배수는 루트 에이전트 실행만 본다. 하위 에이전트 호출이 끼면 첫/마지막 호출 비교가 틀어진다.

-- @query turn_growth
WITH r AS (
  SELECT OperationId, RootAgent AS Agent,
         max(InTok) FILTER (WHERE RunIdx = 1) AS FirstIn,
         max(InTok) FILTER (WHERE RunIdx = NRun) AS LastIn, max(NRun) AS NRun
  FROM call_steps WHERE IsRoot GROUP BY OperationId, RootAgent)
SELECT Agent, count(*) AS Turns,
       round(quantile_cont(1.0 * LastIn / FirstIn, 0.5), 2) AS P50Multiple,
       round(quantile_cont(1.0 * LastIn / FirstIn, 0.95), 2) AS P95Multiple
FROM r WHERE NRun >= 2 AND FirstIn > 0 GROUP BY Agent ORDER BY Agent

-- @query growth_steps
-- ΔIn = InTok − 직전 InTok − 직전 OutTok. 대부분 tool 결과다. 재전송 계수는 증가분이 양수인 단계만 센다.
SELECT Agent, count(*) AS Steps,
       round(quantile_cont(1.0 * Delta / PrevIn, 0.5), 3) AS P50GrowthRate,
       round(quantile_cont(1.0 * Delta / PrevIn, 0.95), 3) AS P95GrowthRate,
       round(sum(Delta * Remaining) FILTER (WHERE Delta > 0) * 1.0 / nullif(sum(Delta) FILTER (WHERE Delta > 0), 0), 2) AS CarryFactor
FROM call_steps WHERE RunIdx > 1 AND PrevIn > 0 AND Delta IS NOT NULL GROUP BY Agent ORDER BY Agent

-- @query result_size
SELECT t.ToolName, count(*) AS Calls, quantile_cont(c.ResultChars, 0.5) AS P50Chars,
       quantile_cont(c.ResultChars, 0.95) AS P95Chars
FROM tools t JOIN content c ON c.SpanId = t.SpanId AND c.OperationId = t.OperationId
WHERE c.ResultChars IS NOT NULL AND t.Success GROUP BY 1 ORDER BY P95Chars DESC

-- @query result_carry
-- tool 결과 토큰이 이후 호출에 몇 번 실리는지. 결과 토큰 ≈ ResultChars ÷ chars_per_token(근사).
-- 낭비 추정은 CarriedUsd × (1 − 필요 토큰 비율)이다. 필요 비율은 [C] 샘플 리뷰로 정하며 c2_needed_fraction이다.
WITH cr AS (SELECT Agent, 1.0 * sum(CacheTok) / nullif(sum(InTok), 0) AS r FROM calls GROUP BY 1),
x AS (
  SELECT t.ToolName, cs.Agent, c.ResultChars / thrn('chars_per_token') AS ResultTok, cs.Remaining - 1 AS RemAfter,
         (1 - cr.r) * cs.PIn + cr.r * cs.PCache AS PEff
  FROM tools t
  JOIN content c ON c.SpanId = t.SpanId AND c.OperationId = t.OperationId
  JOIN call_steps cs ON cs.SpanId = t.ParentId AND cs.OperationId = t.OperationId
  JOIN cr ON cr.Agent = cs.Agent
  WHERE c.ResultChars IS NOT NULL AND t.Success)
SELECT ToolName, count(*) AS Calls, round(avg(ResultTok)) AS AvgResultTok, round(avg(RemAfter), 2) AS AvgCarriedCalls,
       round(sum(ResultTok * RemAfter)) AS CarriedTokCalls,
       round(sum(ResultTok * RemAfter * PEff) / 1e6, 2) AS CarriedUsd
FROM x GROUP BY ToolName ORDER BY CarriedUsd DESC
