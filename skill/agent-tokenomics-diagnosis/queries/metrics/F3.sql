-- F3: 사용자 재질문. [T] 근사는 빠른 재입력 비율이고, 재질문 판정은 [C] 메시지 유사도가 필요하다.
-- quick_pairs가 content_by_span 2차 추출에 넘길 SpanId를 준다.

-- @query quick_reinput
WITH g AS (
  SELECT RootAgent AS Agent, TurnConv,
         date_diff('second', lag(TurnEnd) OVER (PARTITION BY TurnConv ORDER BY TurnStart), TurnStart) AS GapSec
  FROM turns WHERE TurnConv IS NOT NULL)
SELECT Agent, count(*) AS Turns, count(*) FILTER (WHERE GapSec BETWEEN 0 AND thrn('quick_retry_sec')) AS QuickTurns,
       round(1.0 * count(*) FILTER (WHERE GapSec BETWEEN 0 AND thrn('quick_retry_sec')) / count(*), 3) AS QuickRetryRate
FROM g GROUP BY Agent ORDER BY Agent

-- @query quick_pairs
-- 빠른 재입력 쌍. PrevFirstSpanId와 FirstSpanId는 각 턴 첫 호출의 SpanId다(content_by_span 입력).
WITH g AS (
  SELECT OperationId, RootAgent AS Agent, TurnConv, TurnStart,
         lag(OperationId) OVER w AS PrevOp,
         date_diff('second', lag(TurnEnd) OVER w, TurnStart) AS GapSec
  FROM turns WHERE TurnConv IS NOT NULL WINDOW w AS (PARTITION BY TurnConv ORDER BY TurnStart)),
fc AS (SELECT OperationId, SpanId FROM calls WHERE CallIdx = 1),
tc AS (SELECT OperationId, TurnCost FROM turn_cost)
SELECT g.Agent, g.TurnConv AS Conv, g.PrevOp AS PrevOperationId, g.OperationId, g.GapSec,
       round(tcp.TurnCost, 4) AS PrevTurnCost, fcp.SpanId AS PrevFirstSpanId, fcn.SpanId AS FirstSpanId
FROM g JOIN fc fcn ON fcn.OperationId = g.OperationId
JOIN fc fcp ON fcp.OperationId = g.PrevOp JOIN tc tcp ON tcp.OperationId = g.PrevOp
WHERE g.GapSec BETWEEN 0 AND thrn('quick_retry_sec') ORDER BY g.TurnStart LIMIT 200

-- @query waste
-- Σ(빠른 재입력 직전 턴) 턴 비용. [T] 근사이므로 후보로만 기록한다.
WITH g AS (
  SELECT RootAgent AS Agent, TurnStart, lag(OperationId) OVER w AS PrevOp,
         date_diff('second', lag(TurnEnd) OVER w, TurnStart) AS GapSec
  FROM turns WHERE TurnConv IS NOT NULL WINDOW w AS (PARTITION BY TurnConv ORDER BY TurnStart))
SELECT g.Agent, count(*) AS QuickPairs, round(sum(t.TurnCost), 2) AS WasteUsd
FROM g JOIN turn_cost t ON t.OperationId = g.PrevOp
WHERE g.GapSec BETWEEN 0 AND thrn('quick_retry_sec') GROUP BY g.Agent
