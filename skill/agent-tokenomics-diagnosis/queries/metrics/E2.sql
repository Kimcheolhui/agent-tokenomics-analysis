-- E2: Reasoning 과다. 수치 기준은 두지 않는다. 단순 단계(유형 1–3)와 최종 응답 단계(유형 4)를 비교한다.

-- @query reasoning
SELECT Agent, Model, coalesce(ReasonLevel, '(없음)') AS Level, count(*) AS Calls,
       round(1.0 * sum(ReasonTok) / nullif(sum(OutTok), 0), 3) AS ReasonShare,
       quantile_cont(ReasonTok, 0.5) AS P50Reason, quantile_cont(ReasonTok, 0.95) AS P95Reason,
       quantile_cont(VisibleTok, 0.5) AS P50Visible
FROM calls GROUP BY ALL ORDER BY Agent, Model, Level

-- @query reasoning_by_type
-- 상자 그림용 5수치 요약
SELECT Agent, CallType, coalesce(ReasonLevel, '(없음)') AS Level, count(*) AS Calls,
       min(ReasonTok) AS Min, quantile_cont(ReasonTok, 0.25) AS Q1, quantile_cont(ReasonTok, 0.5) AS Median,
       quantile_cont(ReasonTok, 0.75) AS Q3, max(ReasonTok) AS Max
FROM call_types GROUP BY ALL ORDER BY Agent, CallType

-- @query waste
-- 같은 모델·같은 유형의 저수준(minimal, low, none) 호출이 있어야 기준선이 생긴다. 없으면 행이 나오지 않는다.
WITH base AS (
  SELECT Model, CallType, quantile_cont(ReasonTok, 0.5) AS BaseReason
  FROM call_types WHERE ReasonLevel IN ('minimal', 'low', 'none') GROUP BY 1, 2)
SELECT c.Model, c.CallType, count(*) AS Calls,
       round(sum(greatest(c.ReasonTok - b.BaseReason, 0) * c.POut / 1e6), 2) AS WasteUsd
FROM call_types c JOIN base b ON b.Model = c.Model AND b.CallType = c.CallType
WHERE substr(c.CallType, 1, 1) IN ('1', '2', '3') AND c.ReasonLevel IN ('medium', 'high', 'xhigh')
GROUP BY ALL
