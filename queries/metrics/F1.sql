-- F1: Tool 실패 후 재시도.

-- @query tool_failures
SELECT ToolName, count(*) AS Calls, count(*) FILTER (WHERE NOT Success) AS Failed,
       round(1.0 * count(*) FILTER (WHERE NOT Success) / count(*), 3) AS FailRate,
       any_value(ErrorType) FILTER (WHERE ErrorType IS NOT NULL) AS SampleErr
FROM tools GROUP BY ToolName ORDER BY Failed DESC

-- @query fail_vs_ok_turns
WITH f AS (SELECT OperationId, bool_or(NOT Success) AS HasFail FROM tools GROUP BY 1)
SELECT t.Agent, coalesce(f.HasFail, false) AS HasFail, count(*) AS Turns,
       quantile_cont(t.TurnCost, 0.5) AS P50TurnCost, quantile_cont(t.N, 0.5) AS P50Calls, round(sum(t.TurnCost), 2) AS TotalCost
FROM turn_cost t LEFT JOIN f ON f.OperationId = t.OperationId GROUP BY ALL ORDER BY t.Agent, HasFail

-- @query waste
-- Σ(실패 턴) (턴 비용 − 정상 턴 비용 중앙값)
WITH f AS (SELECT OperationId, bool_or(NOT Success) AS HasFail FROM tools GROUP BY 1),
t AS (SELECT tc.*, coalesce(f.HasFail, false) AS HasFail FROM turn_cost tc LEFT JOIN f ON f.OperationId = tc.OperationId),
ok AS (SELECT Agent, quantile_cont(TurnCost, 0.5) AS OkP50 FROM t WHERE NOT HasFail GROUP BY 1)
SELECT t.Agent, count(*) FILTER (WHERE t.HasFail) AS FailTurns,
       round(coalesce(sum(greatest(t.TurnCost - ok.OkP50, 0)) FILTER (WHERE t.HasFail), 0), 2) AS WasteUsd
FROM t JOIN ok ON ok.Agent = t.Agent GROUP BY t.Agent ORDER BY t.Agent

-- @query error_types
SELECT ToolName, coalesce(ErrorType, '(없음)') AS ErrorType, count(*) AS Failed
FROM tools WHERE NOT Success GROUP BY ALL ORDER BY Failed DESC
