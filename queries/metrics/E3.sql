-- E3: 장황한 최종 응답. 범용 수치 기준은 없다. 고객이 정한 목표 길이(e3_target_tok)가 있어야 판정한다.

-- @query final_response
SELECT Agent, count(*) AS Finals, quantile_cont(VisibleTok, 0.5) AS P50Visible, quantile_cont(VisibleTok, 0.95) AS P95Visible,
       round(1.0 * count(*) FILTER (WHERE MaxTok IS NULL) / count(*), 3) AS NoMaxTokens
FROM call_steps WHERE CallIdx = N AND coalesce(Finish, '') NOT LIKE '%tool_calls%' GROUP BY Agent ORDER BY Agent

-- @query output_cost_share
SELECT Agent, round(sum(OutTok * POut / 1e6) / nullif(sum(Cost), 0), 3) AS OutCostShare FROM calls GROUP BY Agent ORDER BY Agent

-- @query waste_vs_target
SELECT Agent, thrn('e3_target_tok') AS TargetTok,
       round(sum(greatest(VisibleTok - thrn('e3_target_tok'), 0) * POut / 1e6), 2) AS WasteUsd
FROM call_steps WHERE CallIdx = N AND coalesce(Finish, '') NOT LIKE '%tool_calls%' AND thrn('e3_target_tok') IS NOT NULL
GROUP BY Agent
