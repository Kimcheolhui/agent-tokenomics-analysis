-- D3: 결정적 단계의 LLM 처리. 짧은 출력 = 가시 출력이 short_output_tok 미만이고 tool 호출이 아닌 호출.

-- @query short_output
WITH x AS (
  SELECT *, (VisibleTok < thrn('short_output_tok') AND coalesce(Finish, '') NOT LIKE '%tool_calls%') AS Short FROM calls)
SELECT Agent, Model, count(*) AS Calls, count(*) FILTER (WHERE Short) AS ShortCalls,
       round(coalesce(sum(Cost) FILTER (WHERE Short), 0), 2) AS ShortCost,
       round(coalesce(sum(Cost) FILTER (WHERE Short), 0) / nullif(sum(Cost), 0), 3) AS ShortCostShare
FROM x GROUP BY Agent, Model ORDER BY ShortCost DESC

-- @query output_diversity
-- 짧은 출력이 소수의 고정 값으로 수렴하는지. content_digest의 OutHash가 필요하다.
SELECT c.Agent, count(*) AS ShortCalls, count(DISTINCT ct.OutHash) AS DistinctOutputs,
       round(1.0 * count(DISTINCT ct.OutHash) / count(*), 3) AS OutputDiversity, round(sum(c.Cost), 2) AS ShortCost
FROM calls c JOIN content ct ON ct.SpanId = c.SpanId AND ct.OperationId = c.OperationId
WHERE c.VisibleTok < thrn('short_output_tok') AND coalesce(c.Finish, '') NOT LIKE '%tool_calls%' AND ct.OutHash IS NOT NULL
GROUP BY c.Agent ORDER BY ShortCost DESC
