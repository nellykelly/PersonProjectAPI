-- ===========================================================================
-- One-time Snowflake account setup for the market data warehouse.
--
-- Run in Snowsight (Projects -> Worksheets -> + SQL Worksheet) as the user
-- you signed up with. Paste the public key from
-- `python tools/snowflake_keygen.py` where marked, then "Run All".
-- Safe to re-run: everything is IF NOT EXISTS / idempotent grants.
--
-- What it builds (least privilege, cost-capped):
--   MARKET_ETL  role      -- owns everything the pipeline creates; nothing else
--   MARKET_WH   warehouse -- X-Small, suspends after 60s idle, starts suspended
--   MARKET      database  -- RAW / STAGING / MARTS / SNAPSHOTS schemas get
--                            created inside it by the loader and dbt
--   MARKET_SVC  user      -- TYPE=SERVICE: key-pair login only, no password,
--                            so no MFA prompt can stall a scheduled run
--   MARKET_RM   monitor   -- suspends the warehouse at 20 credits/month
-- ===========================================================================

USE ROLE ACCOUNTADMIN;

-- --- role -----------------------------------------------------------------
CREATE ROLE IF NOT EXISTS MARKET_ETL
  COMMENT = 'Market data warehouse pipeline (ingest + dbt)';
-- Custom roles should roll up to SYSADMIN so admins keep visibility.
GRANT ROLE MARKET_ETL TO ROLE SYSADMIN;
-- ...and to you, so you can browse the tables in Snowsight as this role.
SET me = CURRENT_USER();
GRANT ROLE MARKET_ETL TO USER IDENTIFIER($me);

-- --- compute --------------------------------------------------------------
CREATE WAREHOUSE IF NOT EXISTS MARKET_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60            -- seconds idle before it stops billing
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Market data warehouse: ingest + dbt';
GRANT USAGE, OPERATE ON WAREHOUSE MARKET_WH TO ROLE MARKET_ETL;

-- --- cost guardrail ------------------------------------------------------
CREATE RESOURCE MONITOR IF NOT EXISTS MARKET_RM
  WITH CREDIT_QUOTA = 20
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  TRIGGERS ON 75 PERCENT DO NOTIFY
           ON 100 PERCENT DO SUSPEND;
ALTER WAREHOUSE MARKET_WH SET RESOURCE_MONITOR = MARKET_RM;

-- --- storage --------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS MARKET
  COMMENT = 'yfinance -> RAW -> dbt star schema';
-- Only USAGE + CREATE SCHEMA: the role then OWNS the schemas it creates
-- (RAW from the loader, STAGING/MARTS/SNAPSHOTS from dbt) and everything
-- in them, and can't touch any other database in the account.
GRANT USAGE, CREATE SCHEMA ON DATABASE MARKET TO ROLE MARKET_ETL;

-- --- service user (key-pair only) -----------------------------------------
CREATE USER IF NOT EXISTS MARKET_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = MARKET_ETL
  DEFAULT_WAREHOUSE = MARKET_WH
  DEFAULT_NAMESPACE = MARKET
  COMMENT = 'Market data warehouse pipeline';
-- Paste the one-line key from tools/snowflake_keygen.py (no BEGIN/END lines).
ALTER USER MARKET_SVC SET RSA_PUBLIC_KEY = '<PASTE_PUBLIC_KEY_HERE>';
GRANT ROLE MARKET_ETL TO USER MARKET_SVC;

-- --- verify ---------------------------------------------------------------
-- RSA_PUBLIC_KEY_FP should match the fingerprint keygen printed.
DESC USER MARKET_SVC;
SHOW GRANTS TO ROLE MARKET_ETL;
-- Your account identifier for SNOWFLAKE_ACCOUNT in .env (ORGNAME-ACCOUNTNAME):
SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS snowflake_account;
