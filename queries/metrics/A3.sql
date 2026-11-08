-- A3: 이력 앞부분 편집. 같은 대화·같은 에이전트에서 Input이 직전보다 줄어든 호출을 축소 이벤트로 본다.
-- 에이전트별로 나누는 이유: 하위 에이전트의 작은 Input을 축소로 오인하지 않기 위해서다.

-- @query shrink
WITH x AS (
  SELECT Agent, Conv, InTok, CacheTok, MinTok, Round128, PIn, PCache, Compacted,
         lag(InTok) OVER (PARTITION BY Conv, Agent ORDER BY TimeGenerated, SpanId) AS PrevInConv, CallIdx
  FROM calls WHERE Conv IS NOT NULL),
y AS (SELECT *, (PrevInConv > InTok) AS Shrink FROM x)
SELECT Agent, count(*) AS Calls, count(*) FILTER (WHERE Shrink) AS ShrinkEvents,
       count(*) FILTER (WHERE Compacted = 'true') AS CompactedCalls,
       round(avg(1.0 * CacheTok / InTok) FILTER (WHERE Shrink), 3) AS HitAfterShrink,
       round(avg(1.0 * CacheTok / InTok) FILTER (WHERE NOT coalesce(Shrink, false) AND CallIdx > 1), 3) AS HitNormal,
       -- 단측 검정(축소 직후 캐시율 < 평상시)에 쓰는 표본 수와 표준편차
       count(*) FILTER (WHERE NOT coalesce(Shrink, false) AND CallIdx > 1) AS NormalCalls,
       round(stddev_samp(1.0 * CacheTok / InTok) FILTER (WHERE Shrink), 4) AS SdAfterShrink,
       round(stddev_samp(1.0 * CacheTok / InTok) FILTER (WHERE NOT coalesce(Shrink, false) AND CallIdx > 1), 4) AS SdNormal,
       coalesce(sum(PrevInConv - InTok) FILTER (WHERE Shrink), 0) AS ShrunkInputTok,
       -- 상한: 편집 지점 이전 공유 길이를 모르므로 직전 prefix 전체가 히트했어야 한다고 본다. [C] 비교로 줄인다.
       round(coalesce(sum(greatest(cacheable(least(PrevInConv, InTok), MinTok, Round128) - CacheTok, 0)
                          * (PIn - PCache) / 1e6) FILTER (WHERE Shrink), 0), 2) AS UpperBoundWasteUsd
FROM y GROUP BY Agent ORDER BY ShrinkEvents DESC

-- @query conv_call_profile
-- 대화 안 호출 순번별 평균 Input 구성(캐시 히트 / 비캐시). 그림용.
WITH x AS (
  SELECT Agent, InTok, CacheTok,
         row_number() OVER (PARTITION BY Conv ORDER BY TimeGenerated, SpanId) AS ConvCallIdx
  FROM calls WHERE Conv IS NOT NULL)
SELECT least(ConvCallIdx, 12) AS ConvCallIdx, count(*) AS Calls,
       round(avg(CacheTok)) AS AvgCached, round(avg(InTok - CacheTok)) AS AvgNonCached
FROM x GROUP BY 1 ORDER BY 1
