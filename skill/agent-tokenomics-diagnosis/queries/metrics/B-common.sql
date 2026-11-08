-- B 공통: 고정 Input 비용 비중 = Σ(S_a × 호출 수 × P_eff) ÷ 전체 비용.
-- 한 에이전트가 여러 모델을 쓰면 Agent × Model로 나눠 계산한다(가이드의 any() 문제를 피한다).

-- @query fixed_share
WITH a AS (
  SELECT Agent, Model, count(*) AS Calls, sum(Cost) AS Cost,
         1.0 * sum(CacheTok) / nullif(sum(InTok), 0) AS CacheRate,
         any_value(PIn) AS PIn, any_value(PCache) AS PCache
  FROM calls GROUP BY Agent, Model),
b AS (
  SELECT a.*, s.STok, (1 - CacheRate) * PIn + CacheRate * PCache AS PEff,
         s.STok * Calls * ((1 - CacheRate) * PIn + CacheRate * PCache) / 1e6 AS FixedCost
  FROM a LEFT JOIN agent_fixed s ON s.Agent = a.Agent)
SELECT Agent, Model, Calls, round(Cost, 2) AS Cost, round(CacheRate, 3) AS CacheRate, STok,
       round(PEff, 4) AS PEff, round(FixedCost, 2) AS FixedCost,
       round(FixedCost / nullif(Cost, 0), 3) AS FixedCostShare
FROM b ORDER BY Cost DESC

-- @query overall
-- S_a가 있는 에이전트만 센다. 없는 에이전트가 있으면 AgentsWithSa < AgentsTotal이고 비중은 하한이다.
WITH a AS (
  SELECT Agent, Model, count(*) AS Calls, sum(Cost) AS Cost,
         1.0 * sum(CacheTok) / nullif(sum(InTok), 0) AS r, any_value(PIn) AS PIn, any_value(PCache) AS PCache
  FROM calls GROUP BY Agent, Model),
b AS (SELECT a.*, s.STok, s.STok * Calls * ((1 - r) * PIn + r * PCache) / 1e6 AS FixedCost
      FROM a LEFT JOIN agent_fixed s ON s.Agent = a.Agent)
SELECT round(sum(FixedCost), 2) AS FixedCost, round(sum(Cost), 2) AS TotalCost,
       round(sum(FixedCost) / nullif(sum(Cost) FILTER (WHERE STok > 0), 0), 3) AS FixedCostShare,
       count(DISTINCT Agent) FILTER (WHERE STok > 0) AS AgentsWithSa, count(DISTINCT Agent) AS AgentsTotal
FROM b

-- @query first_call_dist
-- 측정법 3(하한 근사): 에이전트 실행 첫 호출 InTok의 1백분위수 ≈ S_a + 짧은 사용자 메시지.
SELECT Agent, count(*) AS FirstCalls, quantile_cont(InTok, 0.01) AS P01, quantile_cont(InTok, 0.5) AS P50
FROM call_steps WHERE RunIdx = 1 GROUP BY Agent ORDER BY Agent
