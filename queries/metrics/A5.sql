-- A5: 캐시 만료. 같은 에이전트의 연속 턴 첫 호출 간격을 WindowMin 배수 구간으로 나눠 히트율을 비교한다.
-- WasteUsd = 구간 첫 호출 수 × 호출당 절감 가능액 × max(0, "보존 시간 안" 히트율 − 구간 히트율)

-- @query gap_hit
WITH g AS (
  SELECT c.Agent, c.CacheTok, c.MinTok, c.Round128, c.PIn, c.PCache, c.WindowMin, s.STok,
         date_diff('second', lag(c.TimeGenerated) OVER (PARTITION BY c.Agent ORDER BY c.TimeGenerated),
                   c.TimeGenerated) / 60.0 AS GapMin
  FROM calls c LEFT JOIN agent_fixed s ON s.Agent = c.Agent WHERE c.CallIdx = 1),
b AS (
  SELECT *, CASE WHEN GapMin < WindowMin THEN 'a. 보존 시간 안'
                 WHEN GapMin < 2 * WindowMin THEN 'b. 1–2배'
                 WHEN GapMin < 6 * WindowMin THEN 'c. 2–6배'
                 ELSE 'd. 6배 이상' END AS Bucket FROM g WHERE GapMin IS NOT NULL),
st AS (
  SELECT Agent, Bucket, count(*) AS FirstCalls,
         avg(CASE WHEN CacheTok > 0 THEN 1.0 ELSE 0.0 END) AS HitRate,
         avg(cacheable(STok, MinTok, Round128) * (PIn - PCache) / 1e6) AS UnitUsd
  FROM b GROUP BY Agent, Bucket)
SELECT s.Agent, s.Bucket, s.FirstCalls, round(s.HitRate, 3) AS HitRate,
       round(CASE WHEN s.Bucket = 'a. 보존 시간 안' THEN 0
                  ELSE s.FirstCalls * s.UnitUsd * greatest(0, coalesce(a.HitRate, 0) - s.HitRate) END, 2) AS WasteUsd
FROM st s LEFT JOIN st a ON a.Agent = s.Agent AND a.Bucket = 'a. 보존 시간 안'
ORDER BY s.Agent, s.Bucket
