-- B3: System prompt 비대. 구성 요소(규칙, 예시, 참조 데이터)별 비중은 [S] 리뷰로 정한다.

-- @query system_prompt_size
SELECT AgentName, count(*) AS Records, max(SysChars) AS SysChars,
       round(max(SysChars) / thrn('chars_per_token')) AS ApproxTok
FROM content WHERE SysHash IS NOT NULL GROUP BY AgentName ORDER BY SysChars DESC
