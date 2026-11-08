-- A1: Prefix 앞부분의 동적 값.
-- 보존 시간 내 첫 호출 미스율은 만료(A5)와 쏠림(A6)이 생기지 않는 조건의 턴 첫 호출만 센다.

-- @query warm_first_miss
WITH f AS (
  SELECT c.Agent, c.TimeGenerated, c.CacheTok, c.MinTok, c.Round128, c.WindowMin, c.RpmLimit, c.PIn, c.PCache,
         s.STok, date_trunc('minute', c.TimeGenerated) AS Minute,
         date_diff('second', lag(c.TimeGenerated) OVER (PARTITION BY c.Agent ORDER BY c.TimeGenerated),
                   c.TimeGenerated) / 60.0 AS GapMin
  FROM calls c LEFT JOIN agent_fixed s ON s.Agent = c.Agent
  WHERE c.CallIdx = 1),
rpm AS (SELECT Agent, date_trunc('minute', TimeGenerated) AS Minute, count(*) AS MinuteCalls FROM calls GROUP BY 1, 2)
SELECT f.Agent, count(*) AS WarmFirstCalls, count(*) FILTER (WHERE f.CacheTok = 0) AS Miss,
       round(1.0 * count(*) FILTER (WHERE f.CacheTok = 0) / count(*), 3) AS WarmMissRate,
       -- 낭비 추정은 보존 시간 안의 미스만 센다. 만료로 인한 미스는 A5가 센다.
       round(coalesce(sum(cacheable(f.STok, f.MinTok, f.Round128) * (f.PIn - f.PCache) / 1e6)
                      FILTER (WHERE f.CacheTok = 0), 0), 2) AS WasteUsd
FROM f JOIN rpm r ON r.Agent = f.Agent AND r.Minute = f.Minute
WHERE f.STok >= f.MinTok AND f.GapMin < f.WindowMin AND r.MinuteCalls <= f.RpmLimit
GROUP BY f.Agent ORDER BY WarmMissRate DESC

-- @query inturn_misses
-- 턴 안 두 번째 이후 호출도 미스 나면 프레임워크가 호출마다 템플릿을 다시 렌더링하는 유형이다.
SELECT Agent, count(*) AS InTurnCalls, count(*) FILTER (WHERE CacheTok = 0) AS InTurnMiss,
       round(1.0 * count(*) FILTER (WHERE CacheTok = 0) / count(*), 3) AS InTurnMissRate,
       round(coalesce(sum(cacheable(PrevIn, MinTok, Round128) * (PIn - PCache) / 1e6)
                      FILTER (WHERE CacheTok = 0), 0), 2) AS WasteUsd
FROM call_steps WHERE RunIdx > 1 AND PrevIn >= MinTok
GROUP BY Agent ORDER BY InTurnMissRate DESC

-- @query sys_variants
SELECT AgentName, count(*) AS Records, count(DISTINCT SysHash) AS Variants,
       count(DISTINCT SysHeadHash) AS HeadVariants,
       round(1.0 * count(DISTINCT SysHash) / count(*), 3) AS VariantRatio
FROM content WHERE SysHash IS NOT NULL GROUP BY AgentName ORDER BY VariantRatio DESC
