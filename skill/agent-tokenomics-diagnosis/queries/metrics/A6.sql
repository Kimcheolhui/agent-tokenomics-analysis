-- A6: 트래픽 분산·쏠림.

-- @query endpoint_hit
SELECT Agent, coalesce(Endpoint, '(없음)') AS Endpoint, count(*) AS Calls,
       round(1.0 * count(*) FILTER (WHERE CacheTok > 0) / count(*), 3) AS HitRate
FROM calls GROUP BY Agent, Endpoint ORDER BY Agent, Calls DESC

-- @query rpm_hit
-- WasteUsd = 구간 호출 수 × 호출당 절감 가능액 × max(0, 기준 구간 히트율 − 구간 히트율)
WITH m AS (
  SELECT c.Agent, date_trunc('minute', c.TimeGenerated) AS Minute, count(*) AS Rpm,
         count(*) FILTER (WHERE c.CacheTok > 0) AS Hits, any_value(c.RpmLimit) AS RpmLimit,
         avg(cacheable(s.STok, c.MinTok, c.Round128) * (c.PIn - c.PCache) / 1e6) AS UnitUsd
  FROM calls c LEFT JOIN agent_fixed s ON s.Agent = c.Agent GROUP BY 1, 2),
b AS (SELECT *, CASE WHEN Rpm <= RpmLimit THEN 'a. 기준 이하'
                     WHEN Rpm <= 2 * RpmLimit THEN 'b. 기준의 1–2배'
                     ELSE 'c. 기준의 2배 초과' END AS RpmBucket FROM m),
st AS (SELECT Agent, RpmBucket, count(*) AS Minutes, sum(Rpm) AS Calls,
              1.0 * sum(Hits) / sum(Rpm) AS HitRate, sum(Rpm * UnitUsd) / sum(Rpm) AS UnitUsd
       FROM b GROUP BY Agent, RpmBucket)
SELECT s.Agent, s.RpmBucket, s.Minutes, s.Calls, round(s.HitRate, 3) AS HitRate,
       round(CASE WHEN s.RpmBucket = 'a. 기준 이하' THEN 0
                  ELSE s.Calls * s.UnitUsd * greatest(0, coalesce(a.HitRate, 0) - s.HitRate) END, 2) AS WasteUsd
FROM st s LEFT JOIN st a ON a.Agent = s.Agent AND a.RpmBucket = 'a. 기준 이하'
ORDER BY s.Agent, s.RpmBucket
