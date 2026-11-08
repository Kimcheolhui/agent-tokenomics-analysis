-- F4: 실패로 끝난 턴. 늦은 실패 계수 = 실패 턴 비용 비중 ÷ 실패 턴 비율.
-- 앱이 예외를 잡아 정상 응답으로 감싸면 Success가 true로 남는다. [S]로 오류 처리 방식을 확인한다.

-- @query failed_turns
SELECT Agent, count(*) AS Turns,
       round(1.0 * count(*) FILTER (WHERE NOT coalesce(TurnOk, true)) / count(*), 3) AS FailTurnRate,
       round(sum(TurnCost) FILTER (WHERE NOT coalesce(TurnOk, true)) / nullif(sum(TurnCost), 0), 3) AS FailCostShare,
       round((sum(TurnCost) FILTER (WHERE NOT coalesce(TurnOk, true)) / nullif(sum(TurnCost), 0))
             / nullif(1.0 * count(*) FILTER (WHERE NOT coalesce(TurnOk, true)) / count(*), 0), 2) AS LateFailIndex,
       round(avg(N) FILTER (WHERE NOT coalesce(TurnOk, true)), 1) AS AvgCallsFail,
       round(avg(N) FILTER (WHERE coalesce(TurnOk, true)), 1) AS AvgCallsOk,
       round(coalesce(sum(TurnCost) FILTER (WHERE NOT coalesce(TurnOk, true)), 0), 2) AS FailCost
FROM turn_cost GROUP BY Agent ORDER BY Agent
