-- A4: Prefix 1,024 미만. 낭비 추정은 구조적 한계에 해당하는 캐시 불가 비용 전체다.
-- FirstCallZeroHit은 에이전트 실행의 첫 호출 중 CacheTok이 0인 비율이다(하위 에이전트 포함).

-- @query small_calls
SELECT c.Agent, count(*) AS Calls, round(sum(c.Cost), 2) AS Cost,
       count(*) FILTER (WHERE c.InTok < c.MinTok) AS SmallCalls,
       round(coalesce(sum(c.Cost) FILTER (WHERE c.InTok < c.MinTok), 0), 2) AS SmallCost,
       round(1.0 * count(*) FILTER (WHERE c.InTok < c.MinTok) / count(*), 3) AS SmallCallShare,
       round(coalesce(sum(c.Cost) FILTER (WHERE c.InTok < c.MinTok), 0) / nullif(sum(c.Cost), 0), 3) AS SmallCostShare,
       any_value(s.STok) AS STok, any_value(c.MinTok) AS MinTok,
       round(1.0 * count(*) FILTER (WHERE c.RunIdx = 1 AND c.CacheTok = 0) / nullif(count(*) FILTER (WHERE c.RunIdx = 1), 0), 3) AS FirstCallZeroHit
FROM call_steps c LEFT JOIN agent_fixed s ON s.Agent = c.Agent
GROUP BY c.Agent ORDER BY SmallCost DESC
