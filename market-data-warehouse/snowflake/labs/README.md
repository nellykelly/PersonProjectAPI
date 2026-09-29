# Snowflake hands-on labs

Five short labs on the real `MARKET` warehouse, one per area a Snowflake data
engineering job asks about: **storage, performance, security, ingestion, and
change data capture**. Each takes 10-20 minutes and costs a few cents of trial
credit on the X-Small warehouse.

The SQL was test-run against this account on 2026-09-28 as `MARKET_ETL`
(validation log: `S:\worklogs\2026-09-28-snowflake-labs.md`). Statements that
need an admin role (creating a role, cloning the database, resizing the
warehouse) couldn't be tested that way and are marked **admin** in the files.

## How to run them

1. Snowsight → **Projects → Workspaces** (older layouts call it Worksheets) →
   **+ → SQL File**, and paste in one lab file.
2. Run **one statement at a time**: put the cursor in it and press
   **Ctrl+Enter**. Don't use Run All. Some statements are meant to fail, and
   Run All stops at the first error.
3. Read the `-- EXPECT:` comment under each statement and check it against
   what you got. The `-- INTERVIEW:` lines are the thing to be able to say
   afterwards, in your own words.

| # | File | Role(s) | What you'll be able to say you've done |
|---|---|---|---|
| 0 | `00_setup.sql` | MARKET_ETL | Built a lab schema and a permanent working copy of the price fact |
| 1 | `01_storage.sql` | MARKET_ETL, ACCOUNTADMIN (admin) | Recovered deleted rows with Time Travel, UNDROPped a table, zero-copy-cloned a table and a whole database, and found out dbt's tables are TRANSIENT |
| 2 | `02_performance.sql` | MARKET_ETL, ACCOUNTADMIN (admin) | Read a query profile, proved partition pruning (8 → 1 partitions), compared warehouse sizes, and saw the result cache serve a query with 0 bytes scanned |
| 3 | `03_security.sql` | SECURITYADMIN (admin), MARKET_ETL | Built a read-only analyst role with future grants and masked a column by role |
| 4 | `04_ingestion.sql` | MARKET_ETL | Staged files, validated and COPY-loaded them, watched load metadata skip a re-load, and handled a bad file |
| 5 | `05_cdc_stream.sql` | MARKET_ETL | Captured inserts, updates and deletes with a STREAM, applied them with MERGE, and proved source = target |
| 9 | `99_cleanup.sql` | all | Put everything back |

Run `00_setup.sql` first. After that, the labs work in any order.

## Things the test run taught us (worth knowing going in)

- **dbt-snowflake builds TRANSIENT tables by default.** A transient table gets
  at most 1 day of Time Travel and **no Fail-safe**, Snowflake's extra 7-day
  recovery copy. You also can't clone one into a permanent table. That's why
  lab 0 makes a permanent copy with `CREATE TABLE ... AS SELECT`.
- **Your Snowsight session has secondary roles set to ALL.** When you switch
  to a small role, your admin roles quietly stay active. Any "this role can't
  see X" test first needs `USE SECONDARY ROLES NONE`, or it proves nothing.
- **You can't time-travel to before a table existed.** `AT(OFFSET => -60)`
  on a table that's 20 seconds old fails, so wait a minute.
- **dbt rebuilds tables with CREATE OR REPLACE.** Anything attached to the old
  table by hand, like a grant or a masking policy, disappears on the next
  `dbt build`. Labs 1 and 3 show the fix.
