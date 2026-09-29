-- ===========================================================================
-- Lab 5: change data capture with a STREAM, and how it relates to Qlik
-- Replicate.
-- Run one statement at a time (Ctrl+Enter).
-- ===========================================================================

USE ROLE MARKET_ETL;
USE WAREHOUSE MARKET_WH;
USE SCHEMA MARKET.LAB;

-- CDC_SOURCE plays the RAW table that a replication tool (e.g. Qlik
-- Replicate) keeps in sync with a source system. CDC_TARGET is a downstream
-- table we want to keep up to date using ONLY the changes.
CREATE OR REPLACE TABLE CDC_SOURCE AS
  SELECT TICKER, TRADE_DATE, CLOSE, VOLUME FROM MARKET.MARTS.FCT_SECURITY_PRICE;
CREATE OR REPLACE TABLE CDC_TARGET CLONE CDC_SOURCE;

-- The stream: a bookmark on CDC_SOURCE that records every change after this point.
CREATE OR REPLACE STREAM CDC_CHANGES ON TABLE CDC_SOURCE;

SELECT SYSTEM$STREAM_HAS_DATA('CDC_CHANGES');
-- EXPECT: FALSE (nothing has changed yet)

-- ---------------------------------------------------------------------------
-- 1. Activity on the source: an update, a delete, an insert
-- ---------------------------------------------------------------------------
UPDATE CDC_SOURCE SET CLOSE = ROUND(CLOSE * 1.01, 4)
WHERE TICKER = 'KO' AND TRADE_DATE >= DATEADD(DAY, -14, CURRENT_DATE());
-- EXPECT: about 10 rows updated (KO's last two weeks of trading days)

DELETE FROM CDC_SOURCE WHERE TICKER = 'XOM' AND TRADE_DATE < '1993-02-10';
-- EXPECT: several thousand rows deleted (XOM's history goes back to 1962)

INSERT INTO CDC_SOURCE VALUES ('LAB', CURRENT_DATE(), 123.45, 1000);

-- ---------------------------------------------------------------------------
-- 2. What the stream saw
-- ---------------------------------------------------------------------------
SELECT METADATA$ACTION, METADATA$ISUPDATE, COUNT(*)
FROM CDC_CHANGES GROUP BY 1, 2 ORDER BY 1, 2;
-- EXPECT:
--   DELETE | FALSE | (the XOM deletes)
--   DELETE | TRUE  | (old version of each updated KO row)
--   INSERT | FALSE | 1  (the LAB row)
--   INSERT | TRUE  | (new version of each updated KO row)
-- An UPDATE shows up as a DELETE + INSERT pair flagged ISUPDATE = TRUE.

SELECT METADATA$ACTION, METADATA$ISUPDATE, TICKER, TRADE_DATE, CLOSE
FROM CDC_CHANGES WHERE TICKER = 'KO' ORDER BY TRADE_DATE, METADATA$ACTION LIMIT 6;
-- See the before/after close for the same day side by side.

-- ---------------------------------------------------------------------------
-- 3. Apply the changes downstream with MERGE (this consumes the stream)
-- ---------------------------------------------------------------------------
MERGE INTO CDC_TARGET t
USING (SELECT * FROM CDC_CHANGES
       WHERE NOT (METADATA$ACTION = 'DELETE' AND METADATA$ISUPDATE)) s   -- drop the "old" half of updates
  ON t.TICKER = s.TICKER AND t.TRADE_DATE = s.TRADE_DATE
WHEN MATCHED AND s.METADATA$ACTION = 'DELETE' THEN DELETE
WHEN MATCHED AND s.METADATA$ACTION = 'INSERT' THEN
  UPDATE SET t.CLOSE = s.CLOSE, t.VOLUME = s.VOLUME
WHEN NOT MATCHED AND s.METADATA$ACTION = 'INSERT' THEN
  INSERT (TICKER, TRADE_DATE, CLOSE, VOLUME)
  VALUES (s.TICKER, s.TRADE_DATE, s.CLOSE, s.VOLUME);
-- EXPECT: inserted 1, updated ~10, deleted several thousand

SELECT SYSTEM$STREAM_HAS_DATA('CDC_CHANGES');
-- EXPECT: FALSE. Using the stream in a DML statement moved its bookmark
--   forward, so the same changes are never applied twice.

-- Prove the target now matches the source exactly:
SELECT
  (SELECT COUNT(*) FROM (SELECT * FROM CDC_SOURCE MINUS SELECT * FROM CDC_TARGET)) AS only_in_source,
  (SELECT COUNT(*) FROM (SELECT * FROM CDC_TARGET MINUS SELECT * FROM CDC_SOURCE)) AS only_in_target;
-- EXPECT: 0 | 0
-- INTERVIEW: "Qlik Replicate does log-based CDC outside Snowflake: it reads
--   the source database's transaction log and lands inserts, updates and
--   deletes in Snowflake. Inside Snowflake, a stream tracks changes on a
--   table, a MERGE applies them, and a TASK can run that MERGE on a schedule
--   only when SYSTEM$STREAM_HAS_DATA is true. I haven't used Qlik, but I've
--   built the apply side, and the part I'd watch is updates arriving as
--   delete+insert pairs and late or out-of-order changes."

-- (Not created, to avoid a scheduled job burning trial credit. This is
--  what automating it looks like, and it needs EXECUTE TASK granted by
--  ACCOUNTADMIN:)
-- CREATE TASK APPLY_CDC WAREHOUSE = MARKET_WH SCHEDULE = '5 MINUTE'
--   WHEN SYSTEM$STREAM_HAS_DATA('CDC_CHANGES')
--   AS MERGE INTO CDC_TARGET ... ;      -- the MERGE above
-- ALTER TASK APPLY_CDC RESUME;          -- tasks are created suspended
