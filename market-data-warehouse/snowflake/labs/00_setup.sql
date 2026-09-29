-- ===========================================================================
-- Lab 0: setup. A private schema to break things in, and a permanent copy
-- of the price fact.
-- Run one statement at a time (Ctrl+Enter).
-- ===========================================================================

USE ROLE MARKET_ETL;
USE WAREHOUSE MARKET_WH;
USE DATABASE MARKET;

CREATE SCHEMA IF NOT EXISTS LAB
  COMMENT = 'Hands-on Snowflake labs; dropped by 99_cleanup.sql';
USE SCHEMA MARKET.LAB;

-- What kind of table did dbt build?
SHOW TABLES LIKE 'FCT_SECURITY_PRICE' IN SCHEMA MARKET.MARTS;
SELECT "name", "kind", "retention_time" FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));
-- EXPECT: FCT_SECURITY_PRICE | TRANSIENT | 1
-- INTERVIEW: "dbt-snowflake creates transient tables by default. That means
--   no Fail-safe and at most a day of Time Travel. It's cheaper storage, and
--   fine for marts you can rebuild from RAW, but I'd want RAW itself
--   permanent. You can change it per model with `transient: false`."

-- Try to zero-copy clone it into a normal (permanent) table:
CREATE OR REPLACE TABLE PRICE_COPY CLONE MARKET.MARTS.FCT_SECURITY_PRICE;
-- EXPECT: ERROR 002120 "Transient object cannot be cloned to a permanent object."
--   (Supposed to fail. Cloning keeps the table type.)

-- So make a permanent copy by copying the rows instead:
CREATE OR REPLACE TABLE PRICE_COPY AS
SELECT * FROM MARKET.MARTS.FCT_SECURITY_PRICE;

SELECT COUNT(*) AS rows_in_copy FROM PRICE_COPY;
-- EXPECT: about 94,454 (9 tickers, 1962 to today)
