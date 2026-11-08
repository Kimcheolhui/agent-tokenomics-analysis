-- B2: 장황한 Tool schema. 같은 에이전트 tool 정의 크기 중앙값 대비 배수로 후보를 올린다.
-- 축약 가능 토큰은 [S] 리뷰로 정하므로 낭비 추정은 여기서 만들지 않는다.

-- @query tool_schema_size
SELECT AgentName, ToolName, Chars, DescChars, round(Chars / thrn('chars_per_token')) AS ApproxTok,
       median(Chars) OVER (PARTITION BY AgentName) AS MedianChars,
       round(1.0 * Chars / nullif(median(Chars) OVER (PARTITION BY AgentName), 0), 2) AS Multiple
FROM tool_schemas ORDER BY AgentName, Chars DESC
