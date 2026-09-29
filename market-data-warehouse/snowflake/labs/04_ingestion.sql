-- ===========================================================================
-- Lab 4: ingestion. Files → stage → COPY INTO, the way most client data
-- actually arrives.
-- Run one statement at a time (Ctrl+Enter).
-- ===========================================================================

USE ROLE MARKET_ETL;
USE WAREHOUSE MARKET_WH;
USE SCHEMA MARKET.LAB;

-- A file format (how to read the files) and a named internal stage (where they sit):
CREATE OR REPLACE FILE FORMAT CSV_GZ
  TYPE = CSV SKIP_HEADER = 1 FIELD_OPTIONALLY_ENCLOSED_BY = '"'
  COMPRESSION = GZIP NULL_IF = ('');
CREATE OR REPLACE STAGE LAB_STAGE FILE_FORMAT = CSV_GZ;

-- ---------------------------------------------------------------------------
-- 1. Get a file into the stage
-- ---------------------------------------------------------------------------
-- In real life an upstream system drops files (or you PUT them from a
-- laptop with SnowSQL / Snowflake CLI, or upload them in Snowsight). Here,
-- Snowflake writes one for us by UNLOADING a query result:
COPY INTO @LAB_STAGE/prices/aapl_
  FROM (SELECT TICKER, TRADE_DATE, OPEN, HIGH, LOW, CLOSE, VOLUME
        FROM MARKET.MARTS.FCT_SECURITY_PRICE WHERE TICKER = 'AAPL')
  FILE_FORMAT = (TYPE = CSV COMPRESSION = GZIP FIELD_OPTIONALLY_ENCLOSED_BY = '"')
  HEADER = TRUE OVERWRITE = TRUE;
-- EXPECT: rows_unloaded 11,540

LIST @LAB_STAGE/prices/;
-- EXPECT: one .csv.gz file, about 220 KB

-- Peek inside the file without loading it:
SELECT $1 AS ticker, $2 AS trade_date, $6 AS close
FROM @LAB_STAGE/prices/ (FILE_FORMAT => 'CSV_GZ') LIMIT 5;

-- ---------------------------------------------------------------------------
-- 2. Load it: validate first, then COPY
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE PRICES_FROM_FILES (
  TICKER STRING, TRADE_DATE DATE, OPEN FLOAT, HIGH FLOAT, LOW FLOAT,
  CLOSE FLOAT, VOLUME NUMBER);

COPY INTO PRICES_FROM_FILES FROM @LAB_STAGE/prices/ VALIDATION_MODE = RETURN_ERRORS;
-- EXPECT: no rows = the file would load cleanly. Nothing is loaded yet.

COPY INTO PRICES_FROM_FILES FROM @LAB_STAGE/prices/;
-- EXPECT: status LOADED, rows_loaded 11,540

COPY INTO PRICES_FROM_FILES FROM @LAB_STAGE/prices/;
-- EXPECT: "Copy executed with 0 files processed."
-- INTERVIEW: "COPY INTO keeps 64 days of load metadata per table, so
--   re-running a load skips files it already loaded. That's idempotency for
--   free. FORCE = TRUE overrides it, which is how you get duplicates."

SELECT COUNT(*) FROM PRICES_FROM_FILES;
-- EXPECT: 11,540, not 23,080

-- ---------------------------------------------------------------------------
-- 3. A bad file
-- ---------------------------------------------------------------------------
COPY INTO @LAB_STAGE/bad/bad_
  FROM (SELECT 'AAPL' AS TICKER, 'not-a-date' AS TRADE_DATE,
               1 AS OPEN, 2 AS HIGH, 3 AS LOW, 4 AS CLOSE, 5 AS VOLUME)
  FILE_FORMAT = (TYPE = CSV COMPRESSION = GZIP FIELD_OPTIONALLY_ENCLOSED_BY = '"')
  HEADER = TRUE OVERWRITE = TRUE;

COPY INTO PRICES_FROM_FILES FROM @LAB_STAGE/bad/;
-- EXPECT: ERROR 100040 "Date 'not-a-date' is not recognized" (supposed to
--   fail: the default ON_ERROR = ABORT_STATEMENT loads nothing)

COPY INTO PRICES_FROM_FILES FROM @LAB_STAGE/bad/ ON_ERROR = CONTINUE;
-- EXPECT: status LOAD_FAILED with first_error = the bad date. Good rows in a
--   file would load; bad ones are skipped.

SELECT ERROR, LINE, COLUMN_NAME
FROM TABLE(VALIDATE(PRICES_FROM_FILES, JOB_ID => '_last'));
-- EXPECT: the rejected row, its line number and the column

SELECT FILE_NAME, STATUS, ROW_COUNT, ERROR_COUNT
FROM TABLE(INFORMATION_SCHEMA.COPY_HISTORY(
  TABLE_NAME => 'MARKET.LAB.PRICES_FROM_FILES',
  START_TIME => DATEADD(HOUR, -1, CURRENT_TIMESTAMP())));
-- EXPECT: prices file Loaded / 11,540; bad file Load failed / 1 error
-- INTERVIEW: "For file loads I validate before loading, choose ON_ERROR
--   deliberately (abort for financial data, continue plus a reject review for
--   noisy feeds), and monitor COPY_HISTORY. Snowpipe is the same COPY INTO
--   triggered automatically when a file lands in S3 or Azure Blob, with no
--   warehouse to manage."
