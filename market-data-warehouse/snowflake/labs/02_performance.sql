-- ===========================================================================
-- Lab 2: performance. Query profile, partition pruning, warehouse size,
-- result cache.
-- Needs 00_setup.sql. Run one statement at a time (Ctrl+Enter).
-- ===========================================================================

USE ROLE MARKET_ETL;
USE WAREHOUSE MARKET_WH;
USE SCHEMA MARKET.LAB;

-- Your data (~94k rows) is too small to feel anything, so make it 50x bigger:
CREATE OR REPLACE TABLE PRICE_BIG AS
SELECT p.*, g.copy_no
FROM MARKET.MARTS.FCT_SECURITY_PRICE p,
     (SELECT SEQ4() AS copy_no FROM TABLE(GENERATOR(ROWCOUNT => 50))) g;

SELECT COUNT(*) FROM PRICE_BIG;
-- EXPECT: about 4.7 million

-- Turn the result cache OFF, so every timing below is real work:
ALTER SESSION SET USE_CACHED_RESULT = FALSE;

-- ---------------------------------------------------------------------------
-- 1. Read a query profile
-- ---------------------------------------------------------------------------
ALTER SESSION SET QUERY_TAG = 'lab-xs';

SELECT COUNT(*), AVG(vol_252) FROM (
  SELECT STDDEV_SAMP(DAILY_RETURN) OVER (
           PARTITION BY TICKER, copy_no ORDER BY TRADE_DATE
           ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS vol_252
  FROM PRICE_BIG);
-- EXPECT: well under a second or two on X-Small

-- Now open it visually: Monitoring → Query History → click that query →
-- Query Profile. Or get the same numbers in SQL:
SET q = (SELECT QUERY_ID FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION())
         WHERE QUERY_TAG = 'lab-xs' AND EXECUTION_STATUS = 'SUCCESS'
           AND QUERY_TEXT ILIKE '%STDDEV_SAMP%'
         ORDER BY START_TIME DESC LIMIT 1);

SELECT OPERATOR_ID, OPERATOR_TYPE,
       ROUND(EXECUTION_TIME_BREAKDOWN:overall_percentage::FLOAT * 100) AS pct_of_time,
       OPERATOR_STATISTICS:output_rows::NUMBER AS rows_out
FROM TABLE(GET_QUERY_OPERATOR_STATS($q))
ORDER BY pct_of_time DESC NULLS LAST;
-- EXPECT: WindowFunction is the biggest step (the test run saw 59-82%).
-- INTERVIEW: "First place I look for a slow query is the query profile: which
--   operator eats the time, bytes spilled to local or remote storage, and
--   partitions scanned versus total."

-- ---------------------------------------------------------------------------
-- 2. Partition pruning: why data layout matters
-- ---------------------------------------------------------------------------
SELECT COUNT(*) FROM PRICE_BIG WHERE TRADE_DATE >= '2025-01-01';
SET q_unsorted = LAST_QUERY_ID();

CREATE OR REPLACE TABLE PRICE_BIG_SORTED AS
SELECT * FROM PRICE_BIG ORDER BY TRADE_DATE;

SELECT COUNT(*) FROM PRICE_BIG_SORTED WHERE TRADE_DATE >= '2025-01-01';
SET q_sorted = LAST_QUERY_ID();

SELECT 'unsorted' AS layout,
       OPERATOR_STATISTICS:pruning:partitions_scanned::NUMBER AS scanned,
       OPERATOR_STATISTICS:pruning:partitions_total::NUMBER   AS total
FROM TABLE(GET_QUERY_OPERATOR_STATS($q_unsorted)) WHERE OPERATOR_TYPE = 'TableScan'
UNION ALL
SELECT 'sorted',
       OPERATOR_STATISTICS:pruning:partitions_scanned::NUMBER,
       OPERATOR_STATISTICS:pruning:partitions_total::NUMBER
FROM TABLE(GET_QUERY_OPERATOR_STATS($q_sorted)) WHERE OPERATOR_TYPE = 'TableScan';
-- EXPECT: unsorted scans 8 of 8 partitions; sorted scans 1 of 8. Same answer,
--   1/8th of the data read.

SELECT SYSTEM$CLUSTERING_INFORMATION('PRICE_BIG', '(TRADE_DATE)');
-- EXPECT: high average_depth (8) = badly clustered for date filters.
--   Run it on PRICE_BIG_SORTED and compare.
-- INTERVIEW: "Snowflake keeps min/max per micro-partition and skips any that
--   can't match the filter. If data lands in date order, date filters prune
--   for free. If it doesn't, a clustering key keeps it organised, but it
--   costs background credits, so I'd only add one to a big table that is
--   filtered on that column all the time."

-- ---------------------------------------------------------------------------
-- 3. (admin) Warehouse size: does bigger mean faster?
-- ---------------------------------------------------------------------------
USE ROLE ACCOUNTADMIN;                         -- ACCOUNTADMIN owns MARKET_WH
ALTER WAREHOUSE MARKET_WH SET WAREHOUSE_SIZE = 'SMALL';
USE ROLE MARKET_ETL;
ALTER SESSION SET QUERY_TAG = 'lab-small';

SELECT COUNT(*), AVG(vol_252) FROM (
  SELECT STDDEV_SAMP(DAILY_RETURN) OVER (
           PARTITION BY TICKER, copy_no ORDER BY TRADE_DATE
           ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS vol_252
  FROM PRICE_BIG);

USE ROLE ACCOUNTADMIN;
ALTER WAREHOUSE MARKET_WH SET WAREHOUSE_SIZE = 'XSMALL';   -- put it back!
USE ROLE MARKET_ETL;

SELECT QUERY_TAG, WAREHOUSE_SIZE, TOTAL_ELAPSED_TIME AS ms, BYTES_SCANNED
FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION())
WHERE QUERY_TAG IN ('lab-xs', 'lab-small') AND EXECUTION_STATUS = 'SUCCESS'
  AND QUERY_TEXT ILIKE '%STDDEV_SAMP%'
ORDER BY START_TIME;
-- EXPECT: Small is somewhat faster, or barely faster. At this size the query
--   is mostly startup overhead.
-- INTERVIEW: "Each size up doubles credits per second. That pays off when the
--   query is big enough to use the extra nodes, like large scans or spilling
--   joins. For a sub-second query it just doubles the bill. I size by
--   measuring, not by guessing."

-- ---------------------------------------------------------------------------
-- 4. Result cache
-- ---------------------------------------------------------------------------
ALTER SESSION SET USE_CACHED_RESULT = TRUE;
ALTER SESSION SET QUERY_TAG = 'lab-cache';

SELECT TICKER, COUNT(*) FROM PRICE_BIG GROUP BY TICKER ORDER BY TICKER;
SELECT TICKER, COUNT(*) FROM PRICE_BIG GROUP BY TICKER ORDER BY TICKER;   -- identical text

SELECT TOTAL_ELAPSED_TIME AS ms, BYTES_SCANNED
FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION())
WHERE QUERY_TAG = 'lab-cache' AND EXECUTION_STATUS = 'SUCCESS'
  AND QUERY_TEXT = 'SELECT TICKER, COUNT(*) FROM PRICE_BIG GROUP BY TICKER ORDER BY TICKER'
ORDER BY START_TIME;
-- EXPECT: the first run scans ~300 MB; the second scans 0 bytes. The query
--   profile for the second shows "QUERY RESULT REUSE".
-- INTERVIEW: "The result cache returns an identical query's result for 24
--   hours if the underlying data hasn't changed, and uses no warehouse at
--   all. That's why dashboards hitting the same query are cheap. It's also
--   why you turn it off when benchmarking."

ALTER SESSION UNSET QUERY_TAG;
