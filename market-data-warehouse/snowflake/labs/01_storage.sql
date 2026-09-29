-- ===========================================================================
-- Lab 1: storage. Time Travel, UNDROP, zero-copy cloning.
-- Needs 00_setup.sql. Run one statement at a time (Ctrl+Enter).
-- ===========================================================================

USE ROLE MARKET_ETL;
USE WAREHOUSE MARKET_WH;
USE SCHEMA MARKET.LAB;

-- ---------------------------------------------------------------------------
-- 1. How far back can this table go?
-- ---------------------------------------------------------------------------
SHOW PARAMETERS LIKE 'DATA_RETENTION_TIME_IN_DAYS' IN TABLE PRICE_COPY;
-- EXPECT: value 1 (one day, the default). Enterprise edition allows up to 90.

-- ---------------------------------------------------------------------------
-- 2. The "someone ran a bad DELETE" drill
-- ---------------------------------------------------------------------------
SELECT COUNT(*) FROM PRICE_COPY WHERE TICKER = 'AAPL';
-- EXPECT: 11,540 (write it down)

DELETE FROM PRICE_COPY WHERE TICKER = 'AAPL';
SET oops = LAST_QUERY_ID();        -- remember exactly which statement did it

SELECT COUNT(*) FROM PRICE_COPY WHERE TICKER = 'AAPL';
-- EXPECT: 0

-- Read the table as it was just BEFORE that statement ran:
SELECT COUNT(*) FROM PRICE_COPY BEFORE(STATEMENT => $oops) WHERE TICKER = 'AAPL';
-- EXPECT: 11,540. The rows still exist in Time Travel.

-- Put back only what was lost:
INSERT INTO PRICE_COPY
SELECT * FROM PRICE_COPY BEFORE(STATEMENT => $oops) WHERE TICKER = 'AAPL';

SELECT COUNT(*) FROM PRICE_COPY;
-- EXPECT: back to the full count from lab 0
-- INTERVIEW: "Time Travel lets you query a table at a statement, an offset
--   or a timestamp. For a bad delete I'd restore with
--   INSERT ... SELECT FROM t BEFORE(STATEMENT => query_id), which is
--   surgical, instead of restoring the whole table."

-- Wait at least a minute after lab 0 created PRICE_COPY, then:
SELECT COUNT(*) FROM PRICE_COPY AT(OFFSET => -60);
-- EXPECT: a count. If you get error 000707 "Time travel data is not
--   available", the table is younger than 60 seconds. Wait and retry.

-- ---------------------------------------------------------------------------
-- 3. The "someone dropped the table" drill
-- ---------------------------------------------------------------------------
DROP TABLE PRICE_COPY;

SELECT COUNT(*) FROM PRICE_COPY;
-- EXPECT: ERROR "does not exist or not authorized" (supposed to fail)

SHOW TABLES HISTORY LIKE 'PRICE_COPY' IN SCHEMA MARKET.LAB;
-- EXPECT: one row with DROPPED_ON filled in

UNDROP TABLE PRICE_COPY;
SELECT COUNT(*) FROM PRICE_COPY;
-- EXPECT: the full count again
-- INTERVIEW: "UNDROP works inside the retention window. After that, a
--   permanent table has 7 more days of Fail-safe, but only Snowflake support
--   can recover from it, and transient tables don't have it at all."

-- ---------------------------------------------------------------------------
-- 4. Zero-copy clone of a table
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE PRICE_COPY_DEV CLONE PRICE_COPY;
-- EXPECT: succeeds instantly. It's a metadata copy, with no data rewritten
--   and no extra storage until one side changes.

UPDATE PRICE_COPY_DEV SET CLOSE = 0 WHERE TICKER = 'KO';
SELECT
  (SELECT COUNT(*) FROM PRICE_COPY     WHERE TICKER = 'KO' AND CLOSE = 0) AS zeros_in_original,
  (SELECT COUNT(*) FROM PRICE_COPY_DEV WHERE TICKER = 'KO' AND CLOSE = 0) AS zeros_in_clone;
-- EXPECT: 0 in the original, thousands in the clone. They're independent now.

-- ---------------------------------------------------------------------------
-- 5. (admin) Clone the WHOLE database, e.g. for a dev or test environment
--    MARKET_ETL can't create databases (least privilege), so switch to the
--    role that owns MARKET (setup.sql created it as ACCOUNTADMIN).
-- ---------------------------------------------------------------------------
USE ROLE ACCOUNTADMIN;
CREATE DATABASE IF NOT EXISTS MARKET_DEV CLONE MARKET;
-- EXPECT: succeeds in seconds, with RAW, STAGING, MARTS, SNAPSHOTS and LAB
--   all copied. The dbt tables stay TRANSIENT in the clone.

SELECT COUNT(*) FROM MARKET_DEV.MARTS.FCT_SECURITY_PRICE;
-- EXPECT: same count as MARKET.MARTS.FCT_SECURITY_PRICE
-- INTERVIEW: "Zero-copy cloning is how I'd give a team a full copy of prod
--   for testing, or snapshot before a risky migration. It's instant and only
--   costs storage for what changes."

USE ROLE MARKET_ETL;
-- (99_cleanup.sql drops MARKET_DEV.)
