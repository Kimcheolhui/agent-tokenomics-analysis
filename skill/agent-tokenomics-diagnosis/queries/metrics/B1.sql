-- B1: 미사용 Tool 상시 전송. tool별 사용률 = 그 tool이 한 번 이상 호출된 에이전트 실행 수 ÷ 에이전트 실행 수.
-- 하위 에이전트의 tool은 tool_runs가 요청한 에이전트 실행에 귀속시킨다.
-- ApproxTok은 정의 문자 수 ÷ chars_per_token인 근사이고, 측정법 1·2로 확정한다.

-- @query tool_use
WITH runs AS (
  SELECT coalesce(OwnerAgent, Agent) AS Agent, count(DISTINCT coalesce(OwnerSpanId, RootSpanId)) AS AgentRuns,
         count(*) AS AgentCalls, 1.0 * sum(CacheTok) / nullif(sum(InTok), 0) AS r,
         avg(PIn) AS PIn, avg(PCache) AS PCache
  FROM calls GROUP BY 1),
used AS (SELECT Agent, ToolName, count(DISTINCT RunId) AS UsedRuns FROM tool_runs GROUP BY 1, 2)
SELECT d.AgentName AS Agent, d.ToolName, d.Chars, round(d.Chars / thrn('chars_per_token')) AS ApproxTok,
       coalesce(u.UsedRuns, 0) AS UsedRuns, r.AgentRuns,
       round(1.0 * coalesce(u.UsedRuns, 0) / nullif(r.AgentRuns, 0), 4) AS UseRate,
       -- 이 tool이 미사용이라면 매 호출에 실려 나간 비용
       round(d.Chars / thrn('chars_per_token') * r.AgentCalls * ((1 - r.r) * r.PIn + r.r * r.PCache) / 1e6, 2) AS IfUnusedWasteUsd
FROM tool_schemas d
LEFT JOIN used u ON u.Agent = d.AgentName AND u.ToolName = d.ToolName
LEFT JOIN runs r ON r.Agent = d.AgentName
ORDER BY d.AgentName, UseRate, d.Chars DESC
