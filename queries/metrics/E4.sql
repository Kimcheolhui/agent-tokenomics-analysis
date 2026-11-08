-- E4: 출력 잘림. finish_reasons의 length 또는 response.status = incomplete.

-- @query truncation
WITH x AS (
  SELECT *, (coalesce(Finish, '') LIKE '%length%' OR coalesce(Status, '') = 'incomplete') AS Trunc,
         lead(Cost) OVER (PARTITION BY OperationId, RunKey ORDER BY TimeGenerated, SpanId) AS NextCost
  FROM call_steps)
SELECT Agent, Model, count(*) AS Calls, count(*) FILTER (WHERE Trunc) AS TruncCalls,
       round(1.0 * count(*) FILTER (WHERE Trunc) / count(*), 4) AS TruncRate,
       round(1.0 * sum(ReasonTok) FILTER (WHERE Trunc) / nullif(sum(OutTok) FILTER (WHERE Trunc), 0), 3) AS TruncReasonShare,
       count(*) FILTER (WHERE Trunc AND NextCost IS NOT NULL) AS FollowUpAfterTrunc,
       round(coalesce(sum(Cost) FILTER (WHERE Trunc), 0), 2) AS TruncCost,
       round(coalesce(sum(NextCost) FILTER (WHERE Trunc), 0), 2) AS FollowUpCost
FROM x GROUP BY Agent, Model ORDER BY TruncCost DESC
