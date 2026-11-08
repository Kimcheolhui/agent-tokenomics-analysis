-- F2: LLM 호출 오류·재요청. 토큰이 기록되지 않은 실패(예: 429)는 비용 집계에서 빠지며, 비용보다 지연 문제다.

-- @query llm_failures
WITH x AS (
  SELECT *, (NOT coalesce(Success, true) OR coalesce(Finish, '') LIKE '%content_filter%'
             OR coalesce(Finish, '') LIKE '%error%') AS Failed,
         (PrevIn IS NOT NULL AND abs(InTok - PrevIn) <= thrn('resend_tok_diff')
          AND coalesce(PrevFinish, '') NOT LIKE '%tool_calls%') AS Resend
  FROM call_steps)
SELECT Agent, count(*) AS Calls, count(*) FILTER (WHERE Failed) AS FailedCalls,
       round(coalesce(sum(Cost) FILTER (WHERE Failed), 0), 2) AS FailedCost,
       count(*) FILTER (WHERE Resend) AS Resends, round(coalesce(sum(Cost) FILTER (WHERE Resend), 0), 2) AS ResendCost,
       round(1.0 * count(*) FILTER (WHERE Failed OR Resend) / count(*), 4) AS FailOrResendRate
FROM x GROUP BY Agent ORDER BY Agent

-- @query error_breakdown
SELECT Agent, coalesce(ErrorType, '(없음)') AS ErrorType, coalesce(ResultCode, '') AS ResultCode, count(*) AS Calls
FROM calls WHERE NOT coalesce(Success, true) GROUP BY ALL ORDER BY Calls DESC
