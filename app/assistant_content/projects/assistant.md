---
title: "Project: AI Assistant"
kind: project
---

# What it is

The chat assistant on this site, reachable at `/assistant` (page) and `POST
/api/assistant/chat` (the endpoint it actually runs over). It answers questions about
Nelson and about every project on this site, grounded only in a curated Markdown corpus
— this file included — and it can also *act*: it carries a set of tools onto public,
already-live features of the site (opening a simulated trade, scoring a company,
joining a character into Pipeline World, and more) and decides for itself, mid-turn,
whether and in what order to call them. That decision-making loop, not the chat UI
around it, is the part meant to demonstrate an autonomous system rather than a chatbot
wrapper.

# Retrieval

Every question is embedded locally (`fastembed`, a small BGE model, no GPU or external
embedding API) and compared against the corpus with cosine similarity — `pgvector` on
Postgres in production, an equivalent in-process comparison on SQLite for local
development, chosen automatically by which database is actually running. The corpus
itself is this directory: every file under `app/assistant_content/` is chunked on
headings, embedded, and reloaded into the vector store by a `flask assistant reindex`
command. The assistant only ever answers from what that retrieval step actually
surfaces; if nothing relevant comes back, it says so rather than guessing.

# The agent loop

Retrieval feeds a small agent loop built on LangGraph: a **retrieve** step (no model
call) hands the top matching chunks to an **agent** step (one call to Groq's API, in an
OpenAI-compatible tool-calling format), which either answers directly or calls one or
more **tools**. A tool call routes to a **tools** node (again no model call — it's a
plain dispatch to the matching Python function) and loops back to the agent with the
result, so the model can read what a tool returned and decide its next move — call
another tool, or answer. That loop is bounded: a fixed cap on model-tool round trips per
chat turn keeps a confused or adversarial exchange from running away, and per-tool call
limits stop the same expensive or write-y tool from being dispatched twice in one turn.
The loop, the cap, and the per-tool limits are the same mechanism the assistant has run
since its tool-calling layer first shipped — LangGraph is the framework the loop is
built on, not new behavior bolted on top of it.

# Autonomous stock-predictor: the flagship example

The clearest example of the assistant deciding its own multi-step plan is a stock
analysis chain over a small, whitelisted set of tickers (the intersection of the Trading
Simulator's and the Market Data Warehouse's own symbol lists: AAPL, MSFT, AMZN, GOOGL,
NVDA, JPM, KO, XOM, and SPY). Asked something like "what does the data say about NVDA,"
the assistant can pull a live quote, pull the warehouse's own trend projection for that
symbol, and pull a risk report — three separate tools, three separate pieces of this
site's own infrastructure — and chain them in whatever order the situation calls for,
because it chose to, not because a script told it to. Every output in that chain carries
the same **not financial advice, no demonstrated predictive power** framing the Market
Data Warehouse and Company Scorer already use elsewhere on the site; autonomy here means
the assistant decides *which tools and in what order*, not that it makes claims the
underlying data doesn't support.

# Tool access and safety boundaries

Two tiers of tools exist. Public tools — covering the Trading Simulator, Pipeline World,
Company Scorer, and Timed-Squares — are offered to every visitor on every turn, because
each one only wraps an action that's already a public, unauthenticated feature elsewhere
on the site. Anything that writes (opening a position, joining a character) follows a
preview-then-confirm pattern: the assistant previews the action, shows the visitor what
it parsed, and only calls the real, writing tool after an explicit yes and a one-time
token minted by the preview — a crafted chat message can't skip straight to the write.
Every tool that writes or does real work shares the same per-IP rate-limit bucket as its
equivalent web form, so asking the assistant to do something doesn't grant a second,
unlimited quota. A second, much smaller set of tools — reading and writing the private
job-application tracker — exists only when the site owner is signed in and has
separately unlocked that section; for everyone else those tool schemas are never even
constructed, so there is nothing for a stray instruction to invoke.

# Usage board

`/assistant/stats` is a public, token-free usage board: message volume, tone, what
people tend to ask about, and how the assistant answered (answered / gap / redirect /
refused / error), computed once per message with plain lexicon-and-regex classification
and aggregated from logged rows — it runs no model calls of its own and costs nothing to
load.

# What it demonstrates

An autonomous tool-using agent loop (not a single-shot chatbot call), retrieval grounded
in the site's own content so its claims about Nelson and about this site stay accurate,
and a multi-step plan the model assembles itself over real, live infrastructure — with
the rate limits, confirmation tokens, and per-turn governors that make "autonomous" safe
to run on a public, unauthenticated endpoint.
