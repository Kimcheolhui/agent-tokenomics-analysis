-- A 공통: 캐시 달성률. 가이드 "A 공통 지표".
-- 이론 캐시 가능량: 에이전트 실행의 첫 호출은 Cacheable(S_a), 이후 호출은 Cacheable(직전 호출 InTok).
-- 멀티 에이전트에서 하위 에이전트의 첫 호출도 자신의 S_a를 쓴다(call_steps 설명 참고).

-- @query agent_cache
WITH x AS (
  SELECT c.Agent, c.InTok, c.CacheTok, s.STok,
         least(CASE WHEN c.RunIdx = 1 THEN cacheable(s.STok, c.MinTok, c.Round128)
                    ELSE cacheable(c.PrevIn, c.MinTok, c.Round128) END, c.InTok) AS Theo
  FROM call_steps c LEFT JOIN agent_fixed s ON s.Agent = c.Agent)
SELECT Agent, count(*) AS Calls, sum(InTok) AS Input, sum(CacheTok) AS Actual, sum(Theo) AS Theoretical,
       round(1.0 * sum(CacheTok) / nullif(sum(InTok), 0), 3) AS CacheRate,
       round(1.0 * sum(CacheTok) / nullif(sum(Theo), 0), 3) AS Achievement,
       coalesce(bool_and(STok IS NOT NULL AND STok > 0), false) AS HasSa
FROM x GROUP BY Agent ORDER BY Input DESC

-- @query overall
WITH x AS (
  SELECT c.InTok, c.CacheTok, c.RunIdx, s.STok,
         least(CASE WHEN c.RunIdx = 1 THEN cacheable(s.STok, c.MinTok, c.Round128)
                    ELSE cacheable(c.PrevIn, c.MinTok, c.Round128) END, c.InTok) AS Theo
  FROM call_steps c LEFT JOIN agent_fixed s ON s.Agent = c.Agent)
SELECT sum(InTok) AS Input, sum(CacheTok) AS Actual, sum(Theo) AS Theoretical,
       round(1.0 * sum(CacheTok) / nullif(sum(InTok), 0), 3) AS CacheRate,
       round(1.0 * sum(CacheTok) / nullif(sum(Theo), 0), 3) AS Achievement,
       round(1.0 * sum(CacheTok) FILTER (WHERE RunIdx > 1) / nullif(sum(Theo) FILTER (WHERE RunIdx > 1), 0), 3) AS AchievementSecondPlus,
       round(1.0 * sum(CacheTok) FILTER (WHERE RunIdx = 1) / nullif(sum(Theo) FILTER (WHERE RunIdx = 1), 0), 3) AS AchievementFirst
FROM x

-- @query by_callidx
WITH x AS (
  SELECT c.Agent, least(c.RunIdx, 8) AS Idx, c.InTok, c.CacheTok, s.STok,
         least(CASE WHEN c.RunIdx = 1 THEN cacheable(s.STok, c.MinTok, c.Round128)
                    ELSE cacheable(c.PrevIn, c.MinTok, c.Round128) END, c.InTok) AS Theo
  FROM call_steps c LEFT JOIN agent_fixed s ON s.Agent = c.Agent)
SELECT Agent, Idx AS RunIdx, count(*) AS Calls, sum(CacheTok) AS Actual, sum(Theo) AS Theoretical,
       sum(InTok - CacheTok) AS NonCached,
       round(1.0 * sum(CacheTok) / nullif(sum(Theo), 0), 3) AS Achievement
FROM x GROUP BY Agent, Idx ORDER BY Agent, Idx
