-- A2: Tool 정의 변동. 이름 집합은 정렬해서 비교하고(ToolNamesHash), 순서 변형은 정의에 나온 순서(ToolNames)로 센다.

-- @query tooldef_variants
SELECT AgentName, count(*) AS Records, count(DISTINCT ToolDefsHash) AS RawVariants,
       count(DISTINCT ToolNamesHash) AS NameSetVariants, count(DISTINCT ToolNames) AS OrderVariants
FROM content WHERE ToolDefsHash IS NOT NULL GROUP BY AgentName ORDER BY AgentName

-- @query variant_share
SELECT AgentName, ToolNames, count(*) AS Records,
       round(1.0 * count(*) / sum(count(*)) OVER (PARTITION BY AgentName), 3) AS Share
FROM content WHERE ToolDefsHash IS NOT NULL GROUP BY AgentName, ToolNames ORDER BY AgentName, Records DESC

-- @query waste
-- 가장 흔한 정의와 다른 정의를 쓴 턴 첫 호출 수 × Cacheable(S_a) × (P_in − P_cache)
WITH f AS (
  SELECT c.Agent, ct.ToolDefsHash, c.MinTok, c.Round128, c.PIn, c.PCache, s.STok
  FROM calls c JOIN content ct ON ct.SpanId = c.SpanId AND ct.OperationId = c.OperationId
  LEFT JOIN agent_fixed s ON s.Agent = c.Agent
  WHERE c.CallIdx = 1 AND ct.ToolDefsHash IS NOT NULL),
modal AS (SELECT Agent, mode(ToolDefsHash) AS ModalHash FROM f GROUP BY Agent)
SELECT f.Agent, count(*) AS FirstCalls, count(*) FILTER (WHERE f.ToolDefsHash <> m.ModalHash) AS OffModalFirstCalls,
       round(coalesce(sum(cacheable(f.STok, f.MinTok, f.Round128) * (f.PIn - f.PCache) / 1e6)
                      FILTER (WHERE f.ToolDefsHash <> m.ModalHash), 0), 2) AS WasteUsd
FROM f JOIN modal m ON m.Agent = f.Agent GROUP BY f.Agent
