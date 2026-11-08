-- E1: 단순 단계에 상위 모델. 호출 유형 1–3(계획·tool 처리·짧은 출력)을 후보로 본다.
-- 잠재 절감은 e1_small_model(config/thresholds.csv)의 단가로 다시 계산한 비용과의 차이다.
-- 소형 모델이 model-params.csv에 없으면 SmallModelFound가 false이고 판정을 보류한다.

-- @query type_model_cost
SELECT Agent, CallType, Model, count(*) AS Calls, round(sum(Cost), 2) AS Cost,
       quantile_cont(VisibleTok, 0.5) AS P50Visible, quantile_cont(ReasonTok, 0.5) AS P50Reason
FROM call_types GROUP BY ALL ORDER BY Agent, CallType, Cost DESC

-- @query savings
WITH sm AS (SELECT PIn AS sPIn, PCache AS sPCache, PWrite AS sPWrite, POut AS sPOut
            FROM model_params WHERE Model = thrs('e1_small_model')),
x AS (
  SELECT ct.*, sm.sPIn, sm.sPOut,
         (substr(ct.CallType, 1, 1) IN ('1', '2', '3') AND ct.PIn > sm.sPIn) AS Cand,
         ct.Cost - ((ct.InTok - ct.CacheTok - ct.WriteTok) * sm.sPIn + ct.CacheTok * sm.sPCache
                    + ct.WriteTok * sm.sPWrite + ct.OutTok * sm.sPOut) / 1e6 AS Saving
  FROM call_types ct LEFT JOIN sm ON true)
SELECT thrs('e1_small_model') AS SmallModel, count(sPIn) > 0 AS SmallModelFound,
       round(sum(Cost), 2) AS TotalCost,
       count(*) FILTER (WHERE Cand) AS CandidateCalls,
       round(coalesce(sum(Cost) FILTER (WHERE Cand), 0), 2) AS CandidateCost,
       round(coalesce(sum(Saving) FILTER (WHERE Cand), 0), 2) AS SavingUsd,
       round(coalesce(sum(Saving) FILTER (WHERE Cand), 0) / nullif(sum(Cost), 0), 3) AS SavingShare
FROM x

-- @query savings_by_type
WITH sm AS (SELECT PIn AS sPIn, PCache AS sPCache, PWrite AS sPWrite, POut AS sPOut
            FROM model_params WHERE Model = thrs('e1_small_model'))
SELECT ct.Agent, ct.CallType, ct.Model, count(*) AS Calls, round(sum(ct.Cost), 2) AS Cost,
       round(sum(ct.Cost - ((ct.InTok - ct.CacheTok - ct.WriteTok) * sm.sPIn + ct.CacheTok * sm.sPCache
                            + ct.WriteTok * sm.sPWrite + ct.OutTok * sm.sPOut) / 1e6), 2) AS SavingUsd
FROM call_types ct JOIN sm ON ct.PIn > sm.sPIn
WHERE substr(ct.CallType, 1, 1) IN ('1', '2', '3') GROUP BY ALL ORDER BY SavingUsd DESC
