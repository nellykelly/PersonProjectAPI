-- ===========================================================================
-- Lab 3: security. A read-only analyst role, and a masked column.
-- Run one statement at a time (Ctrl+Enter).
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. (admin) A read-only role for analysts: MARTS only, nothing else
-- ---------------------------------------------------------------------------
USE ROLE SECURITYADMIN;       -- the role meant for creating roles and grants

CREATE ROLE IF NOT EXISTS MARKET_ANALYST
  COMMENT = 'Read-only on MARKET.MARTS';
GRANT ROLE MARKET_ANALYST TO ROLE SYSADMIN;   -- keep the role hierarchy tidy
SET me = CURRENT_USER();
GRANT ROLE MARKET_ANALYST TO USER IDENTIFIER($me);   -- so you can test it

GRANT USAGE ON WAREHOUSE MARKET_WH           TO ROLE MARKET_ANALYST;
GRANT USAGE ON DATABASE MARKET               TO ROLE MARKET_ANALYST;
GRANT USAGE ON SCHEMA MARKET.MARTS           TO ROLE MARKET_ANALYST;
GRANT SELECT ON ALL TABLES IN SCHEMA MARKET.MARTS    TO ROLE MARKET_ANALYST;
GRANT SELECT ON FUTURE TABLES IN SCHEMA MARKET.MARTS TO ROLE MARKET_ANALYST;
-- INTERVIEW: "ALL TABLES covers what exists today. FUTURE TABLES is the one
--   that matters with dbt, because every dbt build drops and recreates the
--   tables, and a grant on the old table dies with it. Future grants, or
--   dbt's own `grants:` config, reapply automatically."

SHOW GRANTS TO ROLE MARKET_ANALYST;

-- ---------------------------------------------------------------------------
-- 2. Test it properly
-- ---------------------------------------------------------------------------
-- Your session has secondary roles = ALL, which would quietly add your
-- admin rights to any role you switch to. Turn that off first:
USE SECONDARY ROLES NONE;
USE ROLE MARKET_ANALYST;
USE WAREHOUSE MARKET_WH;

SELECT COUNT(*) FROM MARKET.MARTS.DIM_SECURITY;
-- EXPECT: 9. Analysts can read marts.

SELECT COUNT(*) FROM MARKET.RAW.PRICE_HISTORY;
-- EXPECT: ERROR "does not exist or not authorized" (supposed to fail: no RAW access)

DELETE FROM MARKET.MARTS.DIM_SECURITY;
-- EXPECT: ERROR "insufficient privileges" (supposed to fail: read-only)
-- INTERVIEW: "Always test least privilege with secondary roles off. In
--   Snowsight they default to ALL, so a test can pass only because your own
--   admin role is still active behind the scenes."

-- ---------------------------------------------------------------------------
-- 3. Mask a column by role (needs Enterprise edition, which the trial is)
-- ---------------------------------------------------------------------------
USE ROLE MARKET_ETL;     -- owns the tables, so it can create and attach the policy

CREATE SCHEMA IF NOT EXISTS MARKET.GOVERNANCE
  COMMENT = 'Masking policies; dropped by 99_cleanup.sql';

CREATE MASKING POLICY IF NOT EXISTS MARKET.GOVERNANCE.MASK_NAME
  AS (val STRING) RETURNS STRING ->
  CASE WHEN CURRENT_ROLE() IN ('MARKET_ETL') THEN val
       ELSE '*** masked ***' END;
-- (Pretend SECURITY_NAME is sensitive, like a policyholder's name or SSN.)

ALTER TABLE MARKET.MARTS.DIM_SECURITY
  MODIFY COLUMN SECURITY_NAME SET MASKING POLICY MARKET.GOVERNANCE.MASK_NAME;

SELECT TICKER, SECURITY_NAME FROM MARKET.MARTS.DIM_SECURITY ORDER BY TICKER;
-- EXPECT (as MARKET_ETL): real names, e.g. "Apple Inc."

USE ROLE MARKET_ANALYST;
SELECT TICKER, SECURITY_NAME FROM MARKET.MARTS.DIM_SECURITY ORDER BY TICKER;
-- EXPECT (as MARKET_ANALYST): every name is "*** masked ***". Same table,
--   same query, different answer by role.

USE ROLE ACCOUNTADMIN;
SELECT TICKER, SECURITY_NAME FROM MARKET.MARTS.DIM_SECURITY ORDER BY TICKER;
-- EXPECT: masked for ACCOUNTADMIN too. Being an admin doesn't bypass a policy;
--   only the roles the policy names see the data.
-- INTERVIEW: "Masking policies are column-level and decided at query time by
--   role, so one table serves everyone. In a regulated shop like insurance
--   that's how PII stays hidden from analysts. Because dbt recreates the
--   table, I'd attach the policy in a dbt post-hook or use tag-based masking,
--   so a rebuild can't silently drop it."

USE SECONDARY ROLES ALL;   -- back to Snowsight's normal behaviour
USE ROLE MARKET_ETL;

-- Note: the next `dbt build` recreates DIM_SECURITY without the policy.
-- That's the point made above. 99_cleanup.sql removes it either way.
