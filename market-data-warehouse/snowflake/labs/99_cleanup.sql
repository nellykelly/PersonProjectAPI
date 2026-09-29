-- ===========================================================================
-- Cleanup: puts the account back the way setup.sql left it.
-- Run one statement at a time. Anything that says "does not exist" just
-- means you skipped that lab. That's fine.
-- ===========================================================================

USE SECONDARY ROLES ALL;

-- Lab 3: detach the masking policy first (a policy that's still attached
-- can't be dropped). If a dbt build ran since lab 3, the policy is already
-- detached and this may say so.
USE ROLE MARKET_ETL;
ALTER TABLE MARKET.MARTS.DIM_SECURITY MODIFY COLUMN SECURITY_NAME UNSET MASKING POLICY;
DROP SCHEMA IF EXISTS MARKET.GOVERNANCE;

-- Labs 0, 1, 2, 4, 5: everything else lives in LAB.
DROP SCHEMA IF EXISTS MARKET.LAB;

-- Lab 1: the cloned database.
USE ROLE ACCOUNTADMIN;
DROP DATABASE IF EXISTS MARKET_DEV;

-- Lab 2: make sure the warehouse is back to X-Small.
ALTER WAREHOUSE MARKET_WH SET WAREHOUSE_SIZE = 'XSMALL';
SHOW WAREHOUSES LIKE 'MARKET_WH';
-- EXPECT: size X-Small, auto_suspend 60

-- Lab 3: the analyst role. Optional: keeping it does no harm.
-- USE ROLE SECURITYADMIN;
-- DROP ROLE IF EXISTS MARKET_ANALYST;

USE ROLE MARKET_ETL;
