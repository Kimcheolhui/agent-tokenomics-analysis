-- D1: 루프·중복 Tool 호출. 턴 단위 지표의 에이전트는 루트 에이전트다.
-- finish_reasons의 tool 호출 값을 tool_calls로 가정한다(사전 점검 P5).

-- @query tail_turns
-- 꼬리 턴 = 에이전트별로 호출 수가 많은 순(동률이면 비용이 큰 순)으로 상위 (1 − tail_pct) 비율의 턴.
-- 호출 수는 정수라 "N >= p95"로 자르면 동률 때문에 상위 5%보다 훨씬 많은 턴이 꼬리로 잡힌다.
WITH r AS (
  SELECT *, row_number() OVER (PARTITION BY Agent ORDER BY N DESC, TurnCost DESC) AS Rk,
         count(*) OVER (PARTITION BY Agent) AS Cnt, quantile_cont(TurnCost, 0.5) OVER (PARTITION BY Agent) AS P50TurnCost
  FROM turn_cost),
cut AS (
  SELECT Agent, quantile_cont(N, 0.5) AS P50N, quantile_cont(N, thrn('tail_pct')) AS P95N, max(N) AS MaxN
  FROM turn_cost GROUP BY Agent)
SELECT r.Agent, count(*) AS Turns, any_value(c.P50N) AS P50N, any_value(c.P95N) AS P95N, any_value(c.MaxN) AS MaxN,
       round(any_value(c.P95N) / nullif(any_value(c.P50N), 0), 2) AS TailRatio,
       count(*) FILTER (WHERE r.Rk <= ceil((1 - thrn('tail_pct')) * r.Cnt)) AS TailTurns,
       round(sum(r.TurnCost) FILTER (WHERE r.Rk <= ceil((1 - thrn('tail_pct')) * r.Cnt)) / nullif(sum(r.TurnCost), 0), 3) AS TailCostShare,
       -- 간이식: 꼬리 턴 비용 − 꼬리 턴 수 × 턴 비용 중앙값
       round(coalesce(sum(greatest(r.TurnCost - r.P50TurnCost, 0)) FILTER (WHERE r.Rk <= ceil((1 - thrn('tail_pct')) * r.Cnt)), 0), 2) AS TailExcessUsd
FROM r JOIN cut c ON c.Agent = r.Agent GROUP BY r.Agent ORDER BY r.Agent

-- @query dup_tool_calls
-- 같은 턴에서 (tool 이름, 인자 해시)가 반복된 호출. content_digest의 ArgsHash가 필요하다.
WITH g AS (
  SELECT t.OperationId, t.ToolName, c.ArgsHash, count(*) AS Cnt
  FROM tools t JOIN content c ON c.SpanId = t.SpanId AND c.OperationId = t.OperationId
  WHERE c.ArgsHash IS NOT NULL GROUP BY 1, 2, 3)
SELECT ToolName, sum(Cnt) AS ToolCalls, coalesce(sum(Cnt - 1) FILTER (WHERE Cnt > 1), 0) AS DupCalls,
       round(coalesce(sum(Cnt - 1) FILTER (WHERE Cnt > 1), 0) * 1.0 / sum(Cnt), 3) AS DupRate
FROM g GROUP BY ToolName ORDER BY DupCalls DESC

-- @query repeat_turns
-- content가 없을 때의 [T] 근사: 한 턴에서 같은 tool을 repeat_tool_calls회 이상 부른 턴
WITH g AS (SELECT OperationId, ToolName, count(*) AS Cnt FROM tools GROUP BY 1, 2)
SELECT ToolName, count(*) AS Turns, count(*) FILTER (WHERE Cnt >= thrn('repeat_tool_calls')) AS RepeatTurns,
       round(1.0 * count(*) FILTER (WHERE Cnt >= thrn('repeat_tool_calls')) / count(*), 4) AS RepeatShare
FROM g GROUP BY ToolName ORDER BY RepeatTurns DESC
