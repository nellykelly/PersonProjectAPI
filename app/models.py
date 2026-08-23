from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Instrument(db.Model):
    """Reference/master data for one specific contract -- the thing a
    real trade-booking system keeps separate from the trades themselves,
    so a hundred visitors trading the same AAPL $230 call expiring the
    same day all point at *one* instrument row instead of each embedding
    their own copy of its strike/expiry/exercise style. Deduped on
    (underlying_ticker, instrument_type, strike, expiry) -- see
    `get_or_create_instrument` in app/services/instruments.py.

    Modeled loosely on FpML's option product definition (strike, exercise
    style, buyer/seller) rather than inventing our own field names --
    `instrument_type` uses this app's existing 'stock'/'call'/'put'
    vocabulary (see Leg.kind's old docstring) rather than FpML's 'equity',
    since that's what the rest of this codebase (pricing.py, market_data.py)
    already speaks.
    """

    __tablename__ = "instruments"

    id = db.Column(db.Integer, primary_key=True)
    underlying_ticker = db.Column(db.String(10), nullable=False, index=True)
    # 10, not 4: 'stock' is 5 characters and never fit. SQLite ignores
    # VARCHAR limits entirely, so this passed every test and only failed
    # against Postgres, where opening any stock position 500'd on insert.
    # See tests/test_models_column_widths.py, which now checks the whole
    # table for this rather than waiting to hit it one column at a time.
    instrument_type = db.Column(db.String(10), nullable=False)  # 'stock' | 'call' | 'put'

    strike = db.Column(db.Float, nullable=True)
    expiry = db.Column(db.Date, nullable=True)

    # Real US equity options are always American-style, physically
    # settled (shares actually change hands) -- both null for 'stock'
    # instruments, which have no exercise/settlement concept at all.
    exercise_style = db.Column(db.String(9), nullable=True)  # 'american' | 'european'
    settlement_type = db.Column(db.String(8), nullable=True)  # 'physical' | 'cash'
    contract_multiplier = db.Column(db.Integer, nullable=False, default=1)

    # A stable, human-lookupable identifier for this exact contract -- the
    # standard OCC option symbol (root + YYMMDD expiry + C/P + 8-digit
    # strike*1000) for options, or just the ticker for stock. Derived
    # entirely from the fields above (see occ_code in
    # app/services/instruments.py), so it's always unique given the
    # uq_instrument_identity constraint already on this table -- it's
    # stored rather than recomputed so it can be indexed and searched.
    #
    # 32, not the tighter 25 the worst case (10-char ticker + 6-digit
    # expiry + C/P + 8-digit strike) needs: underlying_ticker is itself
    # String(10), so that's the real ceiling, with a little headroom
    # rather than a value the very next digit would overflow. See
    # tests/test_models_column_widths.py.
    code = db.Column(db.String(32), nullable=False, unique=True, index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "underlying_ticker", "instrument_type", "strike", "expiry", name="uq_instrument_identity"
        ),
    )

    @property
    def is_option(self) -> bool:
        return self.instrument_type in ("call", "put")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "underlying_ticker": self.underlying_ticker,
            "instrument_type": self.instrument_type,
            "strike": self.strike,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "exercise_style": self.exercise_style,
            "settlement_type": self.settlement_type,
            "contract_multiplier": self.contract_multiplier,
        }


class Strategy(db.Model):
    """A book -- a named container holding one or more Legs, the same way
    a real desk books a straddle or an iron condor as one strategy made
    of several independent option legs rather than several unrelated
    trades that happen to share a ticker (see FpML's "strategy as a
    container of legs" trade model). Today's UI only ever opens a single
    leg per strategy ("Single Leg"), but the schema doesn't assume that --
    a multi-leg composer can add more Legs to an existing open Strategy
    without any structural change here.
    """

    __tablename__ = "strategies"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), nullable=False, index=True)
    name = db.Column(db.String(40), nullable=False, default="Single Leg")

    opened_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    status = db.Column(db.String(6), nullable=False, default="open")  # 'open' | 'closed'

    legs = db.relationship("Leg", backref="strategy", order_by="Leg.id", lazy="dynamic")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "opened_at": self.opened_at.isoformat(),
            "status": self.status,
            "legs": [leg.to_dict() for leg in self.legs],
        }


class Leg(db.Model):
    """One booked transaction in the shared, anonymous public trade book
    -- was a flat `Position` row; now a leg that references its Strategy
    (the book it belongs to) and its Instrument (the contract's reference
    data) instead of embedding ticker/strike/expiry itself. A single-leg
    trade (today's only UI flow) is just a Strategy with exactly one Leg.

    No user accounts -- `session_id` on Strategy is a random UUID stored
    in a cookie, used only to cap how many open strategies one visitor
    can hold at once. Every strategy (across all visitors) is visible to
    everyone.
    """

    __tablename__ = "legs"

    id = db.Column(db.Integer, primary_key=True)
    strategy_id = db.Column(db.Integer, db.ForeignKey("strategies.id"), nullable=False, index=True)
    instrument_id = db.Column(db.Integer, db.ForeignKey("instruments.id"), nullable=False, index=True)

    # 'buy' (long this leg) | 'sell' (short this leg) -- real trade
    # booking always records direction explicitly rather than folding it
    # into a signed quantity. The current open-position flow only ever
    # buys (no short-selling UI yet), so this is always "buy" for now,
    # but the field is honest about what a leg actually needs to carry.
    side = db.Column(db.String(4), nullable=False, default="buy")
    quantity = db.Column(db.Integer, nullable=False)

    entry_price = db.Column(db.Float, nullable=False)
    entry_iv = db.Column(db.Float, nullable=True)
    entry_underlying_price = db.Column(db.Float, nullable=True)

    opened_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    close_price = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(6), nullable=False, default="open")  # 'open' | 'closed'

    instrument = db.relationship("Instrument")

    # ---- passthroughs to the instrument, so routes/templates that used
    # to read position.ticker/kind/strike/expiry directly keep working
    # unchanged even though that data now lives on a separate row. ----
    @property
    def ticker(self) -> str:
        return self.instrument.underlying_ticker

    @property
    def kind(self) -> str:
        return self.instrument.instrument_type

    @property
    def strike(self) -> float | None:
        return self.instrument.strike

    @property
    def expiry(self):
        return self.instrument.expiry

    @property
    def is_option(self) -> bool:
        return self.instrument.is_option

    @property
    def multiplier(self) -> int:
        return self.instrument.contract_multiplier

    @property
    def signed_quantity(self) -> int:
        """Quantity with direction applied -- what pricing.py's PnL/Greeks
        math should actually be scaled by, once a short-selling UI exists.
        Every leg booked today is 'buy', so this equals `quantity`."""
        return self.quantity if self.side == "buy" else -self.quantity

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "instrument": self.instrument.to_dict(),
            "ticker": self.ticker,
            "kind": self.kind,
            "side": self.side,
            "quantity": self.quantity,
            "strike": self.strike,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "entry_price": self.entry_price,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "close_price": self.close_price,
            "status": self.status,
        }


class PriceCache(db.Model):
    """Short-TTL cache of the last fetched underlying price per ticker.

    Cuts down on repeat yfinance calls (free-tier rate limits) when many
    visitors are viewing/polling the same shared trade book at once.
    """

    __tablename__ = "price_cache"

    ticker = db.Column(db.String(10), primary_key=True)
    price = db.Column(db.Float, nullable=False)
    fetched_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class RiskRequest(db.Model):
    """The middle step of position -> risk request -> report/live feed:
    a real, persisted, queryable *ask* for risk on a whole position,
    rather than risk being recomputed silently and thrown away every time
    a page happens to render. "Show me every risk request run against
    position #12 today, and what each one found" is a real query against
    this table (join to RiskResult), not something to reconstruct from
    logs.

    A request targets a Strategy -- the position -- and produces one
    RiskResult per Leg inside it plus the aggregated `totals` below. All
    of those legs are priced from a single market snapshot taken once at
    the top of the run, so the report is internally consistent: a
    two-legged spread can never be marked with its legs seen seconds
    apart, which would make the net Greeks quietly wrong.

    `scenario` is None for a plain as-of-now request, or a shock spec for
    a what-if request, e.g. {"spot_shock_pct": -10, "vol_shock_pts": 5}
    meaning "spot down 10%, implied vol up 5 points". Both are in whole
    percent/points, matching the field names and the form labels, not
    fractions -- see app/services/risk_engine.py.
    """

    __tablename__ = "risk_requests"

    id = db.Column(db.Integer, primary_key=True)

    # The position this request was run against. Nullable only so the
    # migration could backfill historical rows; every new request sets it.
    strategy_id = db.Column(db.Integer, db.ForeignKey("strategies.id"), nullable=True, index=True)

    # Set when a request deliberately targets one leg inside the position
    # rather than the whole thing. NULL means "the whole position", which
    # is the normal case.
    leg_id = db.Column(db.Integer, db.ForeignKey("legs.id"), nullable=True, index=True)

    # What this request was actually run against -- 'leg' (one instrument),
    # 'position' (every leg in one Strategy), or 'book' (every open leg
    # across every position). Set explicitly by submit_risk_request rather
    # than inferred from which of strategy_id/leg_id is NULL, because a
    # book-level request has both NULL and would otherwise be
    # indistinguishable from a stale/invalid row.
    scope = db.Column(db.String(8), nullable=False, default="leg")  # 'leg' | 'position' | 'book'

    requested_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    # none_as_null=True: SQLAlchemy's JSON type otherwise stores a Python
    # `None` as the *JSON literal* 'null' rather than a real SQL NULL --
    # without this, a plain as-of-now request (scenario=None) would still
    # read back as "not NULL" to a query like `.filter(scenario.isnot(None))`,
    # making every request look like a scenario request.
    scenario = db.Column(db.JSON(none_as_null=True), nullable=True)
    status = db.Column(db.String(8), nullable=False, default="pending")  # 'pending' | 'complete' | 'failed'

    # Why a 'failed' request failed -- set by the worker job that actually
    # priced it (see risk_engine.run_risk_request_job), since that's a
    # separate process from the one that created this row and can't just
    # raise an exception back into it. "market_data: ..." is recognized by
    # submit_risk_request and re-raised as MarketDataError so callers keep
    # seeing the same exception type as before pricing moved to a worker.
    error = db.Column(db.Text, nullable=True)

    # Which registered quantitative model answered this request (see
    # app/services/risk_models). Stored per-request rather than assumed,
    # because two requests on the same position can legitimately disagree
    # when they ran different models -- a report is only meaningful if it
    # can say which one produced its numbers.
    model_key = db.Column(db.String(40), nullable=False, default="trader_granular")

    # Aggregated across every leg priced in this run: net Greeks, total PV
    # and PnL, plus the snapshot the whole run shared. Stored rather than
    # recomputed so reopening an old report gives the numbers that run
    # actually produced, not today's.
    totals = db.Column(db.JSON(none_as_null=True), nullable=True)

    strategy = db.relationship("Strategy")
    leg = db.relationship("Leg")
    results = db.relationship("RiskResult", backref="risk_request", order_by="RiskResult.id", lazy="dynamic")

    @property
    def is_position_level(self) -> bool:
        """True when this priced a whole position rather than one leg."""
        return self.scope == "position"

    @property
    def is_book_level(self) -> bool:
        """True when this priced every open leg across the whole book."""
        return self.scope == "book"

    def to_dict(self) -> dict:
        result = self.results.order_by(RiskResult.id.desc()).first()
        return {
            "id": self.id,
            "leg_id": self.leg_id,
            "scope": self.scope,
            "requested_at": self.requested_at.isoformat(),
            "scenario": self.scenario,
            "model_key": self.model_key,
            "strategy_id": self.strategy_id,
            "totals": self.totals,
            "status": self.status,
            "error": self.error,
            "result": result.to_dict() if result else None,
        }


class RiskResult(db.Model):
    """The "report" a RiskRequest produces -- what the risk actually was,
    at the market state (live or scenario-shocked) the request asked
    about, computed once and kept, not just displayed and discarded.

    Two different kinds of gamma are carried on purpose:
    - `gamma`: the closed-form Black-Scholes point-derivative (from
      pricing.black_scholes_greeks) -- the textbook second derivative of
      value with respect to spot, at a single point.
    - `scenario_gamma`: an empirical bump-and-revalue convexity -- reprice
      the position at spot+1% and spot-1% around this request's own base
      spot and measure how much the *P&L itself* actually curves between
      those two points. This is what a real scenario/stress risk run
      reports, and is the actual meaning of "scenario gamma" on a risk
      desk: the curvature the pricing model actually produces under a
      real re-price, not just its formula's tangent at one point. The
      two normally agree closely for vanilla Black-Scholes (no exotic
      kinks), which is itself a useful sanity check.

    `ir_delta` is Rho, relabeled the way a real risk book would: interest
    rate delta, sensitivity to the flat discount rate.

    `ir_vega` -- sensitivity to interest-rate *volatility* -- is real, not
    a placeholder, but it's carried under a clearly separate model from
    everything else here: pricing.py's flat RISK_FREE_RATE has no
    volatility parameter at all, so ir_vega is computed via a small,
    explicitly-labeled Hull-White stochastic-rate extension
    (pricing.black_scholes_price_stochastic_rates / pricing.ir_vega) used
    *only* for this one number -- price/PnL/every other Greek on this row
    still come from the ordinary flat-rate Black-Scholes math. Both of
    that extension's own parameters (mean reversion, rate volatility) are
    assumed illustrative constants, not calibrated to real market data --
    there's no cap/swaption vol surface in this app's data source
    (yfinance) to calibrate them against, the same situation
    RISK_FREE_RATE itself is already in.
    """

    __tablename__ = "risk_results"

    id = db.Column(db.Integer, primary_key=True)
    risk_request_id = db.Column(db.Integer, db.ForeignKey("risk_requests.id"), nullable=False, index=True)
    leg_id = db.Column(db.Integer, db.ForeignKey("legs.id"), nullable=False, index=True)

    computed_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    underlying_price_used = db.Column(db.Float, nullable=False)

    pv = db.Column(db.Float, nullable=False)  # position market value at underlying_price_used
    pnl = db.Column(db.Float, nullable=True)
    pnl_pct = db.Column(db.Float, nullable=True)

    delta = db.Column(db.Float, nullable=True)
    gamma = db.Column(db.Float, nullable=True)
    theta = db.Column(db.Float, nullable=True)
    vega = db.Column(db.Float, nullable=True)
    ir_delta = db.Column(db.Float, nullable=True)  # = rho
    scenario_gamma = db.Column(db.Float, nullable=True)
    ir_vega = db.Column(db.Float, nullable=True)  # Hull-White bump-and-revalue -- see class docstring

    # The model's own ordered measures, structural extras (a revaluation
    # ladder, for one) and any notes it wanted to attach. The fixed
    # columns above are a lowest common denominator every model can fill;
    # this is where a model reports things they were never designed to
    # hold, without a migration per model.
    report = db.Column(db.JSON(none_as_null=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "risk_request_id": self.risk_request_id,
            "leg_id": self.leg_id,
            "computed_at": self.computed_at.isoformat(),
            "underlying_price_used": self.underlying_price_used,
            "pv": self.pv,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "ir_delta": self.ir_delta,
            "scenario_gamma": self.scenario_gamma,
            "report": self.report,
            "ir_vega": self.ir_vega,
        }


# Pipeline stage names, in order -- shared by pipeline.py, analytics.py,
# and the pipeline-tracker frontend, so this is the single source of
# truth for "what stages exist and in what order." Each maps to one
# concrete concern, not an arbitrary CI-flavored label:
#   sanitize        -- input hygiene: format/length/charset. No DB reads.
#   security_scan   -- explicit scan for injection patterns (HTML/script
#                       tags, SQL metacharacters) -- belt-and-suspenders
#                       on top of sanitize's whitelist, reported as its
#                       own named check rather than folded silently in.
#   test_uniqueness -- is this name already taken.
#   test_profanity  -- does it contain a blocked word.
#   build           -- assemble the spawn payload: world position +
#                       appearance/icebreaker render data. No DB writes.
#   deploy          -- actually apply the change: write the character
#                       row as live.
#   verify          -- read the row back and confirm it landed correctly
#                       (a real read-after-write check, not assumed).
PIPELINE_STAGES = (
    "sanitize",
    "security_scan",
    "test_uniqueness",
    "test_profanity",
    "build",
    "deploy",
    "verify",
)

CHARACTER_STATUSES = (
    "pending",
    "sanitizing",
    "scanning",
    "testing_uniqueness",
    "testing_profanity",
    "building",
    "deploying",
    "live",
    "failed",
)
# No separate "verifying" status: the Deploy stage's own action *is*
# writing status="live" to the row -- that's what "deploy" means. Verify
# runs after, as a read-after-write confirmation, without the character
# passing through some other transient state first (it's already live,
# genuinely, the moment deploy commits; verify just double-checks).


class Character(db.Model):
    """A visitor-submitted character working its way through (or living in)
    Pipeline World. No accounts -- `session_id` is the same anonymous
    cookie pattern as Strategy.session_id, used only so a visitor's own
    in-flight submission can be identified back to them client-side.

    Column types are kept portable (no Postgres-only types) so the ORM
    side works against SQLite too if someone runs this app without
    Docker -- only app/services/analytics.py's raw SQL is Postgres-only
    (window functions, DATE_TRUNC), see that module's docstring.
    """

    __tablename__ = "characters"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), nullable=False, index=True)

    # 40, not the 30 sanitize_name_part enforces -- same raw-storage
    # headroom the icebreaker answer columns below explain at length:
    # these hold what the visitor actually submitted, before any stage
    # has checked it.
    first_name = db.Column(db.String(40), nullable=False)
    last_name = db.Column(db.String(40), nullable=False)

    # Character customization -- outfit color (appearance_id, the
    # original pick) plus head/body/hand type, all closed-list picks
    # validated against validators.py's HEAD_TYPE_OPTIONS/BODY_TYPE_OPTIONS/
    # HAND_TYPE_OPTIONS and rendered client-side by pipeline_town.js's
    # drawPerson. Stored server-side (not left to per-viewer randomness)
    # so every visitor sees the same look for a given character.
    appearance_id = db.Column(db.String(20), nullable=False)
    head_type_id = db.Column(db.String(20), nullable=False)
    body_type_id = db.Column(db.String(20), nullable=False)
    hand_type_id = db.Column(db.String(20), nullable=False)

    # Every visitor answers the same fixed 4 icebreaker questions -- see
    # validators.FIXED_ICEBREAKER_QUESTIONS, the single source of truth
    # for which 4 questions exist, their order, and their column name
    # (icebreaker_answer_<question id>). Not a pick-one-of-N choice: same
    # 4 questions for everyone, shown together in Production Town as a
    # speech bubble per topic ("Favorite food: tacos"). Each answer is
    # meant to be a short sentence, not a single whitelisted word, so it
    # gets its own, more permissive but still strict, sanitizer (see
    # validators.py: sanitize_icebreaker_answer) and goes through the
    # same Sanitize -> Security Scan -> Test:Profanity stages the name
    # does. See app/services/validators.py's module docstring for the
    # full reasoning.
    #
    # 120, not the 80 that sanitize_icebreaker_answer actually enforces:
    # these columns hold the visitor's *raw*, not-yet-checked answer,
    # because the row has to exist before the pipeline can run a stage
    # against it (see validators.prepare_join_submission). A column
    # exactly as wide as the limit would mean an over-long answer got
    # truncated down to a passing 80 characters on the way in, and
    # Sanitize would approve something the visitor never submitted --
    # the headroom is what lets an over-limit answer still *look*
    # over-limit when Sanitize measures it. Kept in step with
    # validators.MAX_RAW_ICEBREAKER_ANSWER_LENGTH, which is what the
    # truncation actually uses.
    icebreaker_answer_food = db.Column(db.String(120), nullable=True)
    icebreaker_answer_movie = db.Column(db.String(120), nullable=True)
    icebreaker_answer_hobby = db.Column(db.String(120), nullable=True)
    icebreaker_answer_weekend = db.Column(db.String(120), nullable=True)

    # Longest in-flight value is "testing_uniqueness" (18 chars) -- see
    # CHARACTER_STATUSES above.
    status = db.Column(db.String(24), nullable=False, default="pending", index=True)
    failure_reason = db.Column(db.Text, nullable=True)

    world_x = db.Column(db.Float, nullable=True)
    world_y = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    pipeline_runs = db.relationship(
        "PipelineRun", backref="character", order_by="PipelineRun.started_at", lazy="dynamic"
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def to_dict(self) -> dict:
        from app.services.validators import FIXED_ICEBREAKER_QUESTIONS  # local import: avoid a module-load cycle

        icebreakers = []
        for question in FIXED_ICEBREAKER_QUESTIONS:
            answer = getattr(self, question["field_name"], None)
            if answer:
                icebreakers.append(
                    {
                        "question_id": question["id"],
                        "prefix": question["prefix"],
                        "answer": answer,
                        "text": f"{question['prefix']}: {answer}",
                    }
                )

        return {
            "id": self.id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "appearance_id": self.appearance_id,
            "head_type_id": self.head_type_id,
            "body_type_id": self.body_type_id,
            "hand_type_id": self.hand_type_id,
            "icebreakers": icebreakers,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "world_x": self.world_x,
            "world_y": self.world_y,
        }


class PipelineRun(db.Model):
    """One stage attempt for one character -- validate/test/build/deploy,
    pass/fail, timestamped. This table is what app/services/analytics.py
    queries for the SQL showcase page (success rates, MTBF, slowest
    stage, rolling pass rate)."""

    __tablename__ = "pipeline_runs"

    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey("characters.id"), nullable=False, index=True)

    # 20, not 10: "test_uniqueness" (15 chars) is the longest of
    # PIPELINE_STAGES. SQLite never enforces VARCHAR length at all, so a
    # too-short length here passed every local/test run silently and only
    # surfaced as a real crash (StringDataRightTruncation) against Postgres.
    stage = db.Column(db.String(20), nullable=False)  # one of PIPELINE_STAGES
    status = db.Column(db.String(4), nullable=False)  # 'pass' | 'fail'
    detail = db.Column(db.Text, nullable=True)

    started_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    ended_at = db.Column(db.DateTime, nullable=True)

    # Whether this stage ran with the artificial demo delay disabled --
    # decided once per flow (see pipeline.py's run_pipeline), not
    # per-stage. Real-timing benchmarks (pipeline.fast_mode_benchmarks)
    # only ever average rows where this is True: a slow-mode row's
    # duration_seconds includes the artificial 1-10s sleep and would
    # make a "real processing time" benchmark meaningless if mixed in.
    fast_mode = db.Column(db.Boolean, nullable=False, default=False)

    @property
    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "character_id": self.character_id,
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": self.duration_seconds,
            "fast_mode": self.fast_mode,
        }


class TimedSquaresScore(db.Model):
    """One completed run of Timed-Squares, on a public shared leaderboard --
    the same anonymous/public pattern as the trading simulator's shared
    trade book: no login, a random per-visitor `session_id` cookie exists
    only to rate-limit/cap submissions from one visitor, and every score
    (across every visitor) is visible to everyone.

    `turns_survived` is the score -- the game is turn-based by design (see
    the Timed-Squares build spec), so "turns survived" and "time survived"
    are the same number, unlike a real-time game where they'd diverge.

    No server-side replay validation: a determined visitor could POST a
    fabricated score directly to the API. Accepted as a documented
    simplification at this scope (see `sanitize_turns_survived` in
    routes.py for the actual bound enforced) -- the same "simulation
    only" spirit as the Trading Simulator having no real money on the
    line, just applied to a leaderboard number instead.
    """

    __tablename__ = "timed_squares_scores"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), nullable=False, index=True)
    # Arcade-style short display name -- see
    # app/services/validators.py: sanitize_arcade_name. Falls back to
    # "ANON" rather than rejecting the submission outright: a malformed
    # name shouldn't cost a player a score they already earned.
    player_name = db.Column(db.String(12), nullable=False, default="ANON")
    turns_survived = db.Column(db.Integer, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "player_name": self.player_name,
            "turns_survived": self.turns_survived,
            "created_at": self.created_at.isoformat(),
        }
