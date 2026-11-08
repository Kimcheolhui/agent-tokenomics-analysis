-- B4: 하위 에이전트 prompt 중복. 에이전트별 고정 Input 크기와 정의 대비 사용 tool 수를 비교한다.
-- 에이전트 간 공통 블록은 원문 비교가 필요하다([C]).

-- @query agent_fixed_size
WITH sz AS (
  SELECT AgentName, max(SysChars) AS SysChars, max(ToolDefsChars) AS ToolChars, max(ToolCount) AS DefinedTools
  FROM content WHERE SysHash IS NOT NULL OR ToolDefsHash IS NOT NULL GROUP BY AgentName),
used AS (SELECT Agent, count(DISTINCT ToolName) AS UsedTools FROM tool_runs GROUP BY 1)
SELECT sz.AgentName, sz.SysChars, sz.ToolChars, sz.DefinedTools, coalesce(u.UsedTools, 0) AS UsedTools,
       round(1.0 * coalesce(u.UsedTools, 0) / nullif(sz.DefinedTools, 0), 3) AS ToolUseShare, s.STok
FROM sz LEFT JOIN used u ON u.Agent = sz.AgentName LEFT JOIN agent_fixed s ON s.Agent = sz.AgentName
ORDER BY sz.AgentName
