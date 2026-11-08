-- D2: 병렬 가능한 호출의 순차 처리. 연속 tool이 서로 의존하는지는 [C] 인자 비교로 확인한다.
-- ChainWasteUsdUpper는 상한이다: 순차 연쇄 턴에서 tool 호출 라운드의 (k−1)/k만큼을 병렬로 합칠 수 있다고 본다.

-- @query round_tools
WITH rounds AS (
  SELECT OperationId, any_value(RootAgent) AS Agent,
         count(*) FILTER (WHERE coalesce(Finish, '') LIKE '%tool_calls%') AS ToolRounds,
         sum(Cost) FILTER (WHERE coalesce(Finish, '') LIKE '%tool_calls%') AS RoundCost
  FROM calls GROUP BY OperationId),
tn AS (SELECT OperationId, count(*) AS ToolN FROM tools GROUP BY 1)
SELECT r.Agent, count(*) AS Turns, round(avg(1.0 * tn.ToolN / r.ToolRounds), 2) AS AvgToolsPerRound,
       round(1.0 * count(*) FILTER (WHERE r.ToolRounds >= 3 AND tn.ToolN = r.ToolRounds) / count(*), 3) AS SerialChainShare,
       round(coalesce(sum((r.ToolRounds - 1) * 1.0 / r.ToolRounds * r.RoundCost)
                      FILTER (WHERE r.ToolRounds >= 3 AND tn.ToolN = r.ToolRounds), 0), 2) AS ChainWasteUsdUpper
FROM rounds r JOIN tn ON tn.OperationId = r.OperationId WHERE r.ToolRounds >= 1 GROUP BY r.Agent ORDER BY r.Agent
