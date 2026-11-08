-- D4: 에이전트 간 컨텍스트 중복. 하위 에이전트 = 루트가 아닌 invoke_agent span.
-- 하위 첫 호출 Input과, 위임 직전 상위 에이전트 호출의 Input을 짝지어 비교한다.
-- 가정: 하위 에이전트의 LLM 호출 span이 그 invoke_agent span 아래에 달린다(call_owner가 거슬러 올라간다).

-- @query delegation
WITH subfirst AS (
  SELECT s.OperationId, s.SubSpanId, s.ParentId, s.SubAgent, c.InTok AS SubFirstIn, c.TimeGenerated AS FirstAt,
         (SELECT count(*) FROM calls k WHERE k.OwnerSpanId = s.SubSpanId AND k.OperationId = s.OperationId) AS SubCalls
  FROM sub_agents s
  JOIN call_steps c ON c.OwnerSpanId = s.SubSpanId AND c.OperationId = s.OperationId AND c.RunIdx = 1),
pc AS (SELECT OperationId, OwnerSpanId, TimeGenerated, InTok, Agent AS ParentAgent FROM calls),
paired AS (
  SELECT f.*, p.InTok AS ParentIn, p.ParentAgent
  FROM subfirst f ASOF LEFT JOIN pc p
    ON f.OperationId = p.OperationId AND f.ParentId = p.OwnerSpanId AND f.FirstAt >= p.TimeGenerated),
cr AS (SELECT Agent, 1.0 * sum(CacheTok) / nullif(sum(InTok), 0) AS r, avg(PIn) AS PIn, avg(PCache) AS PCache
       FROM calls GROUP BY 1)
SELECT p.SubAgent, count(*) AS Delegations, any_value(s.STok) AS SubSa,
       quantile_cont(p.SubFirstIn, 0.5) AS P50SubFirstIn, quantile_cont(p.ParentIn, 0.5) AS P50ParentIn,
       -- PassRatio: (하위 첫 Input − S_sub) ÷ (상위 직전 Input − S_parent). 상위 가변 컨텍스트 중 하위로 넘어간 비율이다.
       --   1이면 상위 가변 컨텍스트 전체가 넘어간 것이다. S_parent가 없거나 분모가 0 이하면 NULL이다.
       round(quantile_cont((p.SubFirstIn - coalesce(s.STok, 0)) * 1.0
                           / CASE WHEN p.ParentIn - ps.STok > 0 THEN p.ParentIn - ps.STok END, 0.5), 3) AS P50PassRatio,
       any_value(ps.STok) AS ParentSa,
       -- 상한: 전달한 컨텍스트(S_sub 제외)가 모두 불필요하다고 본다. 필요 비율은 [C] 리뷰로 정한다(d4_needed_fraction).
       round(sum(greatest(p.SubFirstIn - coalesce(s.STok, 0), 0) * p.SubCalls
                 * ((1 - cr.r) * cr.PIn + cr.r * cr.PCache) / 1e6), 2) AS PassedUsd
FROM paired p LEFT JOIN agent_fixed s ON s.Agent = p.SubAgent LEFT JOIN agent_fixed ps ON ps.Agent = p.ParentAgent
LEFT JOIN cr ON cr.Agent = p.SubAgent
WHERE p.ParentIn IS NOT NULL GROUP BY p.SubAgent ORDER BY p.SubAgent
