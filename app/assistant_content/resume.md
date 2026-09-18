---
title: "Resume: Nelson Koskela"
kind: resume
---

# Professional summary

Nelson is a software engineer with four-plus years of experience building and operating
production data infrastructure in Python and SQL within financial services. He
independently planned and led a parallel "prod vs. prod" validation framework end to
end, reconciling risk-data systems across two independently-built platforms to guarantee
correctness before every release. Agentic engineering is a core part of how he works day
to day, including directing an AI coding agent through the full build of a live
application and automating a recurring data-analysis task that cut a support burden in
half. He also designed and built a full Kimball-style dimensional data warehouse from
real market data, portable across three database engines. He is comfortable partnering
directly with stakeholders to turn ambiguous requirements into dependable, well-tested
systems.

# Experience

**J.P. Morgan Chase & Co. — Software Engineer II, Corporate & Investment Banking**
Houston, TX | July 2022 -- September 2026

- Engineered and operated data pipelines and analytics infrastructure supporting
  regulatory and risk reporting, reliably processing multi-terabyte datasets across
  global business units in a production, always-on environment.
- Partnered directly with business stakeholders to define requirements and build new
  reporting and infrastructure for enterprise risk-data systems, delivering data in
  live, end-of-day, and start-of-day formats to meet real decision-making needs.
- Stepped up as de facto project lead on a full system migration amid a team staffing
  shortage, owning prioritization of downstream requests, cross-team coordination, and
  bug triage on a tight timeline.
- Designed and led a parallel "prod vs. prod" validation framework end to end, with
  automated reconciliation ensuring the two environments matched to the record before
  cutover.
- Applied agentic engineering to analyze large volumes of data and conduct deep dives
  into the Athena ecosystem, cutting support workload by 50%.
- Partnered with downstream and cross-functional Agile teams to enforce data quality,
  security, and regulatory compliance, operating within strict access-control,
  auditability, and governance requirements.

He joined J.P. Morgan a year earlier, in 2021, as a Software Engineering Program (SEP)
intern. He is now working independently, including on the projects on this site.

**StreetCode Academy — Diversity & Inclusion Officer & Freelance Developer**
Remote | Fall 2020 -- Fall 2022

- Developed responsive web applications using HTML, CSS, and JavaScript, enhancing
  customer engagement and usability.
- Applied Agile Scrum methodology to plan sprints, track progress, and deliver client
  solutions on time.
- Managed logistics and database tracking systems for internal inventory, improving item
  tracking accuracy by 50% by building and delivering full, accurate analytics
  reporting.

# Skills

- **Languages:** Python, SQL, JavaScript, Java, C#, Swift, HTML/CSS
- **AI and agentic tooling:** agentic engineering, AI-agent orchestration, AI-assisted
  development workflows
- **Data and analytics:** dbt, dimensional/Kimball modeling, Snowflake, DuckDB,
  MotherDuck, PostgreSQL, SQLite, Athena (J.P. Morgan Chase's internal data system)
- **Frameworks and libraries:** Flask, React, Bootstrap, Ruby on Rails
- **Cloud and infrastructure:** AWS, Docker, Redis, Unix/Linux, Git
- **Methodologies:** Agile/Scrum, object-oriented programming, distributed systems, data
  reconciliation

# Projects

The two projects he lists on his resume are both live on this site:

- **Market Data Warehouse** (`/projects/market-warehouse`) — a Kimball-style dimensional
  data warehouse built with dbt: three conformed dimensions, a five-table fact family,
  and SCD-1/SCD-2 history, fed from a live external market-data API. It runs unchanged
  across DuckDB, MotherDuck, and Snowflake, with no engine-specific code paths. The
  fact tables include tested quant and risk metrics — Sharpe ratio, Sortino ratio, beta,
  volatility, and a three-method price-projection model (OLS trend, random-walk drift,
  AR(1) mean reversion) — backed by 85 automated data-quality tests.
- **This portfolio site** (`nelsonkoskela.dev`) — an independently scoped, built, and
  shipped, containerized Flask/Python application, including Pipeline World (a live
  SDLC visualizer streaming pipeline runs over WebSockets, built by authoring detailed
  specs and directing a coding agent through implementation end to end) and the SRE/Infra
  layer demonstrating Redis-based queueing and caching underneath it.

# Education

- **Cogswell University of Silicon Valley** — B.S. in Computer Science, 2019 -- 2022.
  Relevant coursework: Data Structures, Database Systems, Linux Programming, Software
  Engineering Methods.
- **Howard University** — B.S. in Computer Science, 2017 -- 2019 (transferred). Relevant
  coursework: Large Scale Programming, Game Design, Affective Computing.

# The full document

This page is a summary drafted for the chat assistant, not a replacement for the actual
resume. The full PDF is public and linked from the About page (`/about`) — both "View
Resume" and "Download Resume" point at the same file.
