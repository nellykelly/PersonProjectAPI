"""Bearer-token JSON API over the private job tracker, at /api/job-tracker.

For Nelson's local Claude Code: it finds a posting, then creates the card
and fills in everything it learned (stack, salary, match notes, ...)
without a browser session. Same data and same rules as the gated
/job-tracker blueprint -- every read and write goes through
app/services/job_tracker.py, and every write lands in the audit log with
source="api".

Access control:
- `Authorization: Bearer <token>`. Only the token's SHA-256 lives in the
  environment (JOB_TRACKER_API_TOKEN_SHA256), never the token itself;
  `flask job-tracker api-token` mints a pair. The token is 32 random
  bytes, so a fast hash is enough -- there is nothing to brute-force.
- **Fails closed**: no hash configured means every request is a 503.
- csrf-exempt, deliberately and safely: auth is the header, not a cookie,
  so a cross-site form can't ride an existing session into this API.
- No delete endpoint. Removing a card stays a human action on the board.
- noindex + no-store on every response, and per-IP rate limited as one
  bucket that failed-auth attempts count against too.
- Input is treated as untrusted even with a valid token: the client is an
  LLM that reads arbitrary job postings, so a prompt-injected page can
  steer what it sends. Hence strict types, per-field and body size caps,
  no NUL bytes, unknown keys rejected, and http(s)-only posting URLs
  (enforced in the service, where it covers every writer).
"""
import hashlib
import hmac
import re
from functools import wraps
from typing import Any

from flask import current_app, jsonify, request, url_for

from app.blueprints.job_tracker_api import bp
from app.extensions import limiter
from app.models import utcnow
from app.services import job_tracker

_rate_limit = limiter.shared_limit(
    lambda: current_app.config["JOB_TRACKER_API_RATE_LIMIT"],
    scope="job_tracker_api",
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# Well above any real posting or note, well below "fill the database".
_MAX_BODY_BYTES = 64 * 1024
_MAX_FIELD_CHARS = 10_000
_MAX_NOTES_CHARS = 50_000
_MAX_TECH_TAGS = 50
_MAX_QUERY_CHARS = 200

# Extra keys the API understands on top of the service's editable fields.
_CREATE_EXTRAS = {"allow_duplicate"}
_UPDATE_EXTRAS = {"append_notes"}


def _error(message: str, status: int, **extra: Any):
    return jsonify({"error": message, **extra}), status


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _require_token(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = (current_app.config.get("JOB_TRACKER_API_TOKEN_SHA256") or "").strip().lower()
        if not _SHA256_HEX.match(expected):
            # Fail closed: unconfigured (or a malformed value pasted into
            # the env) means nobody gets in, not everybody.
            return _error("The job tracker API is not configured.", 503)
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            return _error("Missing bearer token.", 401)
        if not hmac.compare_digest(token_hash(token), expected):
            return _error("Invalid token.", 401)
        return view(*args, **kwargs)

    return wrapped


@bp.after_request
def _noindex(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------


@bp.route("/api/job-tracker", methods=["GET"])
@_rate_limit
@_require_token
def describe():
    """Self-description, so a client can discover the contract instead of
    guessing: the fields it may send, the status vocabulary, the routes."""
    return jsonify(
        {
            "fields": {
                "company_name": "string, required on create",
                "role_title": "string, required on create",
                "job_posting_url": "string",
                "date_applied": "YYYY-MM-DD",
                "status": f"one of {list(job_tracker.STATUSES)} (default 'Applied')",
                "source": "string: where the job was found (LinkedIn, Otta, Company site, ...)",
                "resume_version": "string",
                "cover_letter_used": "boolean",
                "company_industry": "string",
                "company_size_stage": "string, e.g. 'Series B, ~200'",
                "location_remote_policy": "string, e.g. 'Remote (US)' or 'Hybrid, Austin'",
                "tech_stack": "list of strings, or one comma-separated string",
                "salary_range": "string",
                "posting_summary": "string, max 3000 chars: a condensed summary of the "
                "posting (role, responsibilities, must-have requirements, seniority/years, "
                "stack, location/remote, salary). The hourly grader reads this, so "
                "send it on every card. Changing it re-grades an automatic grade.",
                "match_grade": "integer 0-100. Usually leave it out: the hourly grader "
                "sets it from posting_summary using the site's calibrated rubric. A value "
                "you send is kept as a manual grade and never overwritten; send null to "
                "hand a card back to the grader.",
                "match_notes": "string: why the grade (the grader writes its own)",
                "notes": "string (replaces existing notes)",
            },
            "create_only": {
                "allow_duplicate": "boolean: create even if the same posting URL or "
                "company+role is already on the board (otherwise 409)",
            },
            "update_only": {
                "append_notes": "string: added under a dated line instead of "
                "replacing the notes",
            },
            "statuses": list(job_tracker.STATUSES),
            "endpoints": {
                "GET /api/job-tracker/applications": "list; ?status= ?q= ?sort=",
                "POST /api/job-tracker/applications": "create",
                "GET /api/job-tracker/applications/<id>": "read one",
                "PATCH /api/job-tracker/applications/<id>": "partial update",
            },
        }
    )


@bp.route("/api/job-tracker/applications", methods=["GET"])
@_rate_limit
@_require_token
def list_applications():
    if len(request.args.get("q") or "") > _MAX_QUERY_CHARS:
        return _error("q is too long.", 400)
    try:
        rows = job_tracker.list_applications(
            status=request.args.get("status") or None,
            search=request.args.get("q") or None,
            sort=request.args.get("sort") or "-date_applied",
        )
    except job_tracker.JobTrackerError as exc:
        return _error(str(exc), 400)
    return jsonify({"applications": [job_tracker.application_as_dict(r) for r in rows]})


@bp.route("/api/job-tracker/applications/<int:app_id>", methods=["GET"])
@_rate_limit
@_require_token
def get_application(app_id: int):
    try:
        row = job_tracker.get_application(app_id)
    except job_tracker.JobTrackerError as exc:
        return _error(str(exc), 404)
    return jsonify({"application": _with_link(row)})


@bp.route("/api/job-tracker/applications", methods=["POST"])
@_rate_limit
@_require_token
def create_application():
    data, problem = _json_body(set(job_tracker.EDITABLE_FIELDS) | _CREATE_EXTRAS)
    if problem:
        return problem
    allow_duplicate = data.pop("allow_duplicate", False) is True

    if not allow_duplicate:
        existing = job_tracker.find_duplicate(
            _str_or_none(data.get("company_name")),
            _str_or_none(data.get("role_title")),
            _str_or_none(data.get("job_posting_url")),
        )
        if existing is not None:
            return _error(
                f"Already on the board as id {existing.id}. PATCH it instead, "
                "or send allow_duplicate: true to add a second card.",
                409,
                application=_with_link(existing),
            )

    try:
        row = job_tracker.create_application(data, source="api")
    except job_tracker.JobTrackerError as exc:
        return _error(str(exc), 400)
    return jsonify({"application": _with_link(row)}), 201


@bp.route("/api/job-tracker/applications/<int:app_id>", methods=["PATCH"])
@_rate_limit
@_require_token
def update_application(app_id: int):
    data, problem = _json_body(set(job_tracker.EDITABLE_FIELDS) | _UPDATE_EXTRAS)
    if problem:
        return problem
    try:
        row = job_tracker.get_application(app_id)
    except job_tracker.JobTrackerError as exc:
        return _error(str(exc), 404)

    append = _str_or_none(data.pop("append_notes", None))
    if append:
        if "notes" in data:
            return _error("Send notes or append_notes, not both.", 400)
        stamp = utcnow().date().isoformat()
        base = (row.notes or "").rstrip()
        data["notes"] = f"{base}\n\n[{stamp}] {append}" if base else f"[{stamp}] {append}"
        if len(data["notes"]) > _MAX_NOTES_CHARS:
            return _error(f"notes would exceed {_MAX_NOTES_CHARS} characters.", 400)

    if not data:
        return _error("Nothing to update.", 400)
    try:
        row = job_tracker.update_application(app_id, data, source="api")
    except job_tracker.JobTrackerError as exc:
        return _error(str(exc), 400)
    return jsonify({"application": _with_link(row)})


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _json_body(allowed: set[str]):
    """(data, None) for a JSON object whose keys are all in `allowed`,
    else (None, error response). Unknown keys are rejected rather than
    ignored, so a misspelt field comes back as an error instead of
    quietly never reaching the board. Values must be JSON scalars (plus a
    list of strings for tech_stack) -- a nested object would otherwise be
    str()'d into the column by the service."""
    # Checked before parsing. get_data() reads at most MAX_CONTENT_LENGTH
    # (1 MB, app-wide) even for a chunked body with no Content-Length.
    if (request.content_length or 0) > _MAX_BODY_BYTES:
        return None, _error(f"Body over {_MAX_BODY_BYTES} bytes.", 413)
    if len(request.get_data(cache=True)) > _MAX_BODY_BYTES:
        return None, _error(f"Body over {_MAX_BODY_BYTES} bytes.", 413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, _error(
            "Body must be a JSON object (Content-Type: application/json).", 400
        )
    unknown = sorted(set(data) - allowed)
    if unknown:
        return None, _error(
            f"Unknown field(s): {', '.join(unknown)}.", 400, allowed=sorted(allowed)
        )

    tags = data.get("tech_stack")
    if isinstance(tags, list):
        if len(tags) > _MAX_TECH_TAGS or not all(isinstance(t, str) for t in tags):
            return None, _error(f"tech_stack must be at most {_MAX_TECH_TAGS} strings.", 400)
        data["tech_stack"] = ", ".join(tags)

    for key, value in data.items():
        if value is not None and not isinstance(value, (str, int, float, bool)):
            return None, _error(f"{key} must be a string, number, boolean or null.", 400)
        if isinstance(value, str):
            limit = _MAX_NOTES_CHARS if key in ("notes", "append_notes") else _MAX_FIELD_CHARS
            if len(value) > limit:
                return None, _error(f"{key} is over {limit} characters.", 400)
            if "\x00" in value:
                return None, _error(f"{key} contains a NUL byte.", 400)
    return data, None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _with_link(row) -> dict:
    d = job_tracker.application_as_dict(row)
    d["posting_summary"] = row.posting_summary
    d["board_url"] = url_for("job_tracker.edit_application", app_id=row.id, _external=True)
    return d
