-- C1: 대화 이력 무제한 누적. gen_ai.conversation.id가 있어야 한다.
-- 턴 순번은 대화 안에서 턴 첫 호출의 시간순이다.

-- @query turn_idx_input
WITH f AS (
  SELECT Agent, Conv, InTok, row_number() OVER (PARTITION BY Conv ORDER BY TimeGenerated) AS TurnIdx
  FROM calls WHERE CallIdx = 1 AND Conv IS NOT NULL)
SELECT Agent, TurnIdx, count(DISTINCT Conv) AS Convs, quantile_cont(InTok, 0.5) AS P50In
FROM f WHERE TurnIdx <= thrn('c1_turn_cap') GROUP BY 1, 2 ORDER BY 1, 2

-- @query conv_cost
WITH cc AS (
  SELECT Agent, Conv, count(DISTINCT OperationId) AS Turns, sum(Cost) AS Cost
  FROM calls WHERE Conv IS NOT NULL GROUP BY 1, 2),
p AS (SELECT Agent, quantile_cont(Turns, 0.5) AS P50Turns, quantile_cont(Turns, thrn('long_conv_pct')) AS P90Turns
      FROM cc GROUP BY 1)
SELECT cc.Agent, count(*) AS Convs, any_value(p.P50Turns) AS P50Turns, any_value(p.P90Turns) AS P90Turns,
       round(sum(cc.Cost) FILTER (WHERE cc.Turns >= p.P90Turns) / nullif(sum(cc.Cost), 0), 3) AS LongConvCostShare
FROM cc JOIN p ON p.Agent = cc.Agent GROUP BY cc.Agent ORDER BY cc.Agent

-- @query cross_turn
WITH f AS (
  SELECT Agent, Conv, InTok, row_number() OVER (PARTITION BY Conv ORDER BY TimeGenerated) AS TurnIdx
  FROM calls WHERE CallIdx = 1 AND Conv IS NOT NULL),
p90 AS (
  SELECT Agent, CAST(quantile_cont(MaxIdx, thrn('long_conv_pct')) AS INTEGER) AS P90Turn
  FROM (SELECT Agent, Conv, max(TurnIdx) AS MaxIdx FROM f GROUP BY 1, 2) GROUP BY 1),
st AS (SELECT Agent, TurnIdx, quantile_cont(InTok, 0.5) AS P50In FROM f GROUP BY 1, 2)
SELECT st.Agent, any_value(p90.P90Turn) AS P90Turn,
       max(P50In) FILTER (WHERE TurnIdx = 1) AS In1,
       max(P50In) FILTER (WHERE TurnIdx = p90.P90Turn) AS InP90,
       round(max(P50In) FILTER (WHERE TurnIdx = p90.P90Turn) / nullif(max(P50In) FILTER (WHERE TurnIdx = 1), 0), 2) AS CrossTurnMultiple
FROM st JOIN p90 ON p90.Agent = st.Agent GROUP BY st.Agent ORDER BY st.Agent

-- @query waste_baseline
-- 최근 k턴(c1_keep_turns)만 필요하다고 볼 때의 기준선 추정. k는 리뷰로 정하므로 이 값은 기준선이다.
-- 턴 첫 호출 Input이 k번째 턴의 중앙값을 넘는 부분을 그 턴의 모든 호출이 다시 싣는다고 본다.
WITH f AS (
  SELECT OperationId, Agent, InTok, PIn, PCache,
         row_number() OVER (PARTITION BY Conv ORDER BY TimeGenerated) AS TurnIdx
  FROM calls WHERE CallIdx = 1 AND Conv IS NOT NULL),
ref AS (SELECT Agent, quantile_cont(InTok, 0.5) AS RefIn FROM f WHERE TurnIdx = thrn('c1_keep_turns') GROUP BY 1),
cr AS (SELECT Agent, 1.0 * sum(CacheTok) / nullif(sum(InTok), 0) AS r FROM calls GROUP BY 1)
SELECT f.Agent, CAST(thrn('c1_keep_turns') AS INTEGER) AS KeepTurns,
       count(*) FILTER (WHERE f.TurnIdx > thrn('c1_keep_turns')) AS TurnsBeyond,
       round(coalesce(sum(greatest(f.InTok - ref.RefIn, 0) * t.N * ((1 - cr.r) * f.PIn + cr.r * f.PCache) / 1e6)
                      FILTER (WHERE f.TurnIdx > thrn('c1_keep_turns')), 0), 2) AS WasteUsd
FROM f JOIN ref ON ref.Agent = f.Agent JOIN cr ON cr.Agent = f.Agent JOIN turn_cost t ON t.OperationId = f.OperationId
GROUP BY f.Agent ORDER BY f.Agent
