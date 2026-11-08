-- C3: 검색 컨텍스트 과다. 점수 임계값(low_score)은 검색 엔진의 점수 척도에 맞춘다.

-- @query retrieval_quality
WITH d AS (
  SELECT *, row_number() OVER (PARTITION BY OperationId, DocId ORDER BY SpanId) > 1 AS IsDup
  FROM retrieval WHERE DocId IS NOT NULL)
SELECT count(DISTINCT SpanId) AS Searches, count(*) AS ReturnedDocs, round(avg(TopK), 1) AS AvgTopK,
       round(1.0 * count(*) FILTER (WHERE Score < thrn('low_score')) / nullif(count(*), 0), 3) AS LowScoreShare,
       count(*) FILTER (WHERE IsDup) AS DupInTurn,
       round(1.0 * count(*) FILTER (WHERE Score < thrn('low_score') OR IsDup) / nullif(count(*), 0), 3) AS LowOrDupShare
FROM d

-- @query search_result
-- search_tools에 적은 검색 tool의 결과 크기(C2 쿼리를 검색 tool로 한정)
SELECT t.ToolName, count(*) AS Calls, round(quantile_cont(c.ResultChars, 0.5) / thrn('chars_per_token')) AS P50ResultTok
FROM tools t JOIN content c ON c.SpanId = t.SpanId AND c.OperationId = t.OperationId
WHERE c.ResultChars IS NOT NULL AND list_contains(string_split(thrs('search_tools'), ','), t.ToolName)
GROUP BY 1
