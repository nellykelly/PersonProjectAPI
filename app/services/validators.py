"""Input validation, shared across the site's public-write surfaces.

Most of this module is Pipeline World's input surface: a name, an
appearance pick, and free-text answers to 4 fixed icebreaker questions.
Nothing here is ever executed as code -- see docs/build-spec
(project-3-4) "Input Model" for the original reasoning (name + a fixed
appearance list only). The icebreaker answers are a deliberate, later
addition of genuinely open text fields, handled with their own
sanitizer/charset and run through the same pipeline stages the name gets
(see pipeline.py) -- not a loophole in the "no code, ever" model, a
second, wider input surface defended the same way the first one is:
parametrized queries as the real defense, a strict whitelist and a
profanity check as defense-in-depth on top of that.

Every visitor answers the same fixed 4 questions (FIXED_ICEBREAKER_QUESTIONS
below) -- there's no picking, so every character has the same 4 topics,
which is what lets Production Town have two nearby characters "converse"
by matching one's answer to a topic against the other's answer to the
same topic (see pipeline_town.js).

Every check here is real and enforced server-side regardless of what a
frontend does -- the join form's client-side checks are UX convenience,
not the actual boundary.
"""
from __future__ import annotations

import hashlib
import re

from app.extensions import db

MAX_NAME_PART_LENGTH = 30

# ---------- raw storage limits (deliberately NOT validation) ----------
#
# A join submission is persisted *before* any content check runs, because
# the pipeline is what does the checking and there has to be a row for a
# pipeline run to be about (see prepare_join_submission). So these
# columns hold genuinely unvalidated visitor text, and each limit below
# has to sit *above* the length the matching sanitize_* function
# enforces: truncating an over-long submission down to exactly the
# allowed length would quietly turn a value Sanitize should reject into
# one it accepts, and the stage would pass something nobody submitted.
# Kept in step with the column widths in models.py: Character.
MAX_RAW_NAME_PART_LENGTH = 40  # vs. MAX_NAME_PART_LENGTH (30)
MAX_RAW_ICEBREAKER_ANSWER_LENGTH = 120  # vs. MAX_ICEBREAKER_ANSWER_LENGTH (80)
# The customization ids are matched against fixed option lists, so
# there's no length rule to leave headroom over -- anything that isn't
# an exact known id fails validate_*_type_id no matter how long it is.
# This is purely "make it fit the column instead of raising a database
# error before the pipeline can run."
MAX_RAW_TYPE_ID_LENGTH = 20


def truncate_for_storage(raw: str | None, limit: int) -> str:
    """Fits an unchecked submission into its column without rejecting it.

    Not a validation step: it never raises, and (given the limits above)
    it can never turn an invalid value into a valid one. `None` becomes
    `""` so a missing field arrives at the Sanitize stage as an empty
    value that stage can fail on, rather than as a NOT NULL database
    error that would 500 before any pipeline run existed."""
    return (raw or "").strip()[:limit]

# Letters, spaces, hyphens, apostrophes only -- this whitelist alone
# already rejects HTML/script tags and most SQL metacharacters (<, >,
# ;, --, etc.). Apostrophe and hyphen are allowed because they're
# legitimate in real names (O'Brien, Smith-Jones); they're not a SQL
# injection vector here because every query touching these values is
# parametrized (SQLAlchemy bound params) -- the real defense is
# parametrization, this whitelist is defense-in-depth on top of it.
NAME_PART_PATTERN = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,29}$")

# Hashed rather than a plaintext wordlist -- keeps the literal words out
# of a public repo's source while still being a real, functioning check
# (hash the candidate word the same way and compare). This list needs to
# actually catch the profanity a public visitor might type, not just
# stand in for one -- a live character named "Fuck Fuck" made it through
# to Production Town because an earlier version of this list only had
# three deliberately-mild placeholder words ("damn", "hell", "crap") in
# it, which is a demo of the *mechanism* working, not the filter itself.
_BLOCKED_WORDS = (
    "fuck", "shit", "ass", "asshole", "bitch", "bastard", "cunt", "dick",
    "cock", "pussy", "whore", "slut", "damn", "hell", "crap", "piss",
    "twat", "wanker", "bollocks", "prick", "douche", "fag", "faggot",
    "retard", "nigger", "nigga", "chink", "spic", "kike", "gook",
    "tranny", "rape", "nazi", "hitler",
)
_BLOCKED_NAME_HASHES = frozenset(
    hashlib.sha256(word.encode("utf-8")).hexdigest() for word in _BLOCKED_WORDS
)

APPEARANCE_OPTIONS = [
    {"id": "crimson", "label": "Crimson", "color": "#f87171"},
    {"id": "amber", "label": "Amber", "color": "#fbbf24"},
    {"id": "emerald", "label": "Emerald", "color": "#4ade80"},
    {"id": "teal", "label": "Teal", "color": "#2dd4bf"},
    {"id": "sky", "label": "Sky", "color": "#3aa0ff"},
    {"id": "indigo", "label": "Indigo", "color": "#818cf8"},
    {"id": "violet", "label": "Violet", "color": "#c084fc"},
    {"id": "rose", "label": "Rose", "color": "#f472b6"},
]
APPEARANCE_IDS = frozenset(opt["id"] for opt in APPEARANCE_OPTIONS)
APPEARANCE_COLOR_BY_ID = {opt["id"]: opt["color"] for opt in APPEARANCE_OPTIONS}

# Character customization: outfit color is APPEARANCE_OPTIONS above
# (already existed); these three are the newer head/body/hand picks,
# same closed-list pattern as appearance -- rendered client-side by
# pipeline_town.js's drawPerson (see that module for how each id maps
# to an actual drawn shape/color), not free text, so validation here is
# just a membership check, no sanitizer/profanity/injection-scan needed.
HEAD_TYPE_OPTIONS = [
    {"id": "round_tan", "label": "Round · Tan", "shape": "round", "skin": "#e8b98c", "hair": "#3b2a1e"},
    {"id": "round_deep", "label": "Round · Deep", "shape": "round", "skin": "#8d5a3c", "hair": "#1c1410"},
    {"id": "square_fair", "label": "Square · Fair", "shape": "square", "skin": "#f0c9a0", "hair": "#5a3d28"},
    {"id": "square_olive", "label": "Square · Olive", "shape": "square", "skin": "#c98a52", "hair": "#2b2016"},
    {"id": "oval_pale", "label": "Oval · Pale", "shape": "oval", "skin": "#f3d9c4", "hair": "#6b4423"},
    {"id": "oval_deep", "label": "Oval · Deep", "shape": "oval", "skin": "#a9704a", "hair": "#160f0a"},
]
HEAD_TYPE_IDS = frozenset(opt["id"] for opt in HEAD_TYPE_OPTIONS)

BODY_TYPE_OPTIONS = [
    {"id": "slim", "label": "Slim", "width": 11, "height": 15},
    {"id": "regular", "label": "Regular", "width": 13, "height": 13},
    {"id": "stocky", "label": "Stocky", "width": 16, "height": 11},
]
BODY_TYPE_IDS = frozenset(opt["id"] for opt in BODY_TYPE_OPTIONS)

HAND_TYPE_OPTIONS = [
    {"id": "bare", "label": "Bare hands"},
    {"id": "gloves_dark", "label": "Dark gloves"},
    {"id": "gloves_matching", "label": "Matching gloves"},
]
HAND_TYPE_IDS = frozenset(opt["id"] for opt in HAND_TYPE_OPTIONS)


def validate_head_type_id(head_type_id: str | None) -> str:
    if head_type_id not in HEAD_TYPE_IDS:
        raise ValidationError("Please pick a valid head type.")
    return head_type_id


def validate_body_type_id(body_type_id: str | None) -> str:
    if body_type_id not in BODY_TYPE_IDS:
        raise ValidationError("Please pick a valid body type.")
    return body_type_id


def validate_hand_type_id(hand_type_id: str | None) -> str:
    if hand_type_id not in HAND_TYPE_IDS:
        raise ValidationError("Please pick a valid hand type.")
    return hand_type_id

# Speech-bubble icebreakers for Production Town: every visitor answers
# the *same* fixed 4 business-friendly questions, in genuinely free text
# -- not a pick-one-of-N choice, and not a picker for the answers
# themselves. Fixed and identical per person on purpose: it's what lets
# two nearby characters "match" on a topic and converse (see
# pipeline_town.js). Each answer is a real open text field, deliberately
# a *harder* input surface than first/last name since it needs to allow
# a whole sentence (spaces, basic punctuation, digits), not just a
# single whitelisted word -- so it gets its own sanitizer with a
# broader charset, and because it's broader, goes through the same
# Sanitize -> Security Scan -> Test:Profanity pipeline stages the name
# does (see pipeline.py), not waved through as "just flavor text."
#
# `field_name` is the Character column each answer is stored in
# (icebreaker_answer_<id>) -- named here once so every other module
# (models.py, pipeline.py, routes.py) derives it the same way instead of
# re-deriving the string.
FIXED_ICEBREAKER_QUESTIONS = [
    {
        "id": "food",
        "question": "What's your favorite food?",
        "prefix": "Favorite food",
        "example": "Tacos, a good bowl of ramen, or my grandmother's lasagna",
        "field_name": "icebreaker_answer_food",
    },
    {
        "id": "movie",
        "question": "What's your favorite movie?",
        "prefix": "Favorite movie",
        "example": "The Matrix, Inception, or anything Studio Ghibli",
        "field_name": "icebreaker_answer_movie",
    },
    {
        "id": "hobby",
        "question": "What's a hobby you enjoy?",
        "prefix": "Hobby",
        "example": "Rock climbing, playing guitar, or woodworking",
        "field_name": "icebreaker_answer_hobby",
    },
    {
        "id": "weekend",
        "question": "What's your ideal weekend?",
        "prefix": "Ideal weekend",
        "example": "A long hike followed by a good meal with friends",
        "field_name": "icebreaker_answer_weekend",
    },
]
FIXED_ICEBREAKER_QUESTION_IDS = tuple(q["id"] for q in FIXED_ICEBREAKER_QUESTIONS)

MAX_ICEBREAKER_ANSWER_LENGTH = 80

# Letters, digits, spaces, and a small set of punctuation that a normal
# sentence actually needs -- still a strict whitelist (no <, >, ;, /,
# backslash, backtick, quotes that could break out of an HTML attribute
# or a SQL string literal), just wider than the name pattern. Rejects
# HTML/script tags and SQL metacharacters by construction, same as
# NAME_PART_PATTERN -- parametrized queries are still the actual
# injection defense; this is defense-in-depth on top of that.
ICEBREAKER_ANSWER_PATTERN = re.compile(r"^[A-Za-z0-9 .,!?'\-()&:]{1,80}$")


def sanitize_icebreaker_answer(raw: str | None, label: str = "Icebreaker answer") -> str:
    """The free-text counterpart to sanitize_name_part -- same kind of
    check (strip, length cap, charset whitelist), tuned for a short
    sentence instead of a single name token. Also part of the pipeline's
    **Sanitize** stage."""
    if raw is None:
        raise ValidationError(f"{label} is required.")
    cleaned = raw.strip()
    if not cleaned:
        raise ValidationError(f"{label} is required.")
    if len(cleaned) > MAX_ICEBREAKER_ANSWER_LENGTH:
        raise ValidationError(f"{label} must be {MAX_ICEBREAKER_ANSWER_LENGTH} characters or fewer.")
    if not ICEBREAKER_ANSWER_PATTERN.match(cleaned):
        raise ValidationError(
            f"{label} may only contain letters, numbers, spaces, and basic punctuation (. , ! ? ' - ( ) & :)."
        )
    return cleaned


def sanitize_icebreaker_answers(raw_answers: dict) -> dict:
    """Sanitizes all 4 fixed icebreaker answers at once. `raw_answers` is
    keyed by question id (e.g. {"food": "Tacos", "movie": "...", ...});
    every one of the 4 fixed questions is required -- there's no picking,
    so every character answers the same complete set. Returns a dict
    keyed by question id -> cleaned answer, or raises ValidationError
    naming the specific question that failed."""
    cleaned = {}
    for question in FIXED_ICEBREAKER_QUESTIONS:
        qid = question["id"]
        cleaned[qid] = sanitize_icebreaker_answer(raw_answers.get(qid), f'"{question["question"]}" answer')
    return cleaned


class ValidationError(Exception):
    """A hard validation failure -- the caller should reject the submission
    and show `str(exc)` to the visitor."""


class LastNameCollision(Exception):
    """Not a hard failure -- raised to signal the "confirm anyway?" warn
    step. Carries the existing character so the caller can render its name."""

    def __init__(self, existing_character):
        self.existing_character = existing_character
        super().__init__(f"A {existing_character.last_name} already exists.")


def _contains_blocked_word(cleaned: str) -> bool:
    lowered = cleaned.lower()
    return any(
        hashlib.sha256(token.encode("utf-8")).hexdigest() in _BLOCKED_NAME_HASHES
        for token in re.findall(r"[a-z]+", lowered)
    )


def sanitize_name_part(raw: str | None, label: str) -> str:
    """The pipeline's **Sanitize** stage: input hygiene only -- strip
    whitespace, enforce a length cap, and whitelist to letters/spaces/
    hyphens/apostrophes (which alone already rejects HTML/script tags
    and most SQL metacharacters -- see the whitelist's own comment for
    why apostrophe/hyphen are still allowed). Deliberately does **not**
    check uniqueness or profanity, or scan for injection patterns --
    those are the Security Scan and Test stages' job (`check_no_injection_patterns`,
    `check_full_name_collision`, `check_no_profanity`), so each stage
    reports exactly one concern instead of a mixed pass/fail blob.
    Raises ValidationError with a message safe to show the visitor."""
    if raw is None:
        raise ValidationError(f"{label} is required.")
    cleaned = raw.strip()
    if not cleaned:
        raise ValidationError(f"{label} is required.")
    if len(cleaned) > MAX_NAME_PART_LENGTH:
        raise ValidationError(f"{label} must be {MAX_NAME_PART_LENGTH} characters or fewer.")
    if not NAME_PART_PATTERN.match(cleaned):
        raise ValidationError(f"{label} may only contain letters, spaces, hyphens, and apostrophes.")
    return cleaned


def check_no_profanity(cleaned: str, label: str) -> None:
    """The **Test: Profanity** stage. Takes an already-*sanitized* name
    part and checks it against the hashed blocklist."""
    if _contains_blocked_word(cleaned):
        raise ValidationError(f"{label} contains a disallowed word.")


# Explicit injection-pattern markers -- HTML/script tags and common SQL
# metacharacters/keywords. Redundant with sanitize_name_part's charset
# whitelist by the time a name reaches this stage (a name that passed
# Sanitize literally cannot contain '<' or ';'), which is the honest
# point of naming it separately: this is a second, independent layer
# that would still catch something if the whitelist were ever loosened
# or bypassed elsewhere, not the only thing standing between a visitor
# and the database (that's parametrized queries, always).
_INJECTION_PATTERN = re.compile(
    r"<[a-z]|</|script|drop\s+table|;\s*--|union\s+select|\bselect\b.*\bfrom\b",
    re.IGNORECASE,
)


def check_no_injection_patterns(raw: str, label: str) -> None:
    """The **Security Scan** stage. Unlike the other checks, this
    intentionally runs against the *raw*, pre-Sanitize input, so it's
    checking what the visitor actually sent, not what already survived
    the whitelist."""
    if _INJECTION_PATTERN.search(raw or ""):
        raise ValidationError(f"{label} contains a disallowed pattern.")


def validate_appearance_id(appearance_id: str | None) -> str:
    if appearance_id not in APPEARANCE_IDS:
        raise ValidationError("Please pick a valid appearance.")
    return appearance_id


def check_full_name_collision(first_name: str, last_name: str, exclude_character_id: int | None = None) -> None:
    """Hard block -- raises ValidationError if a *different* character
    with this exact (case-insensitive) first + last name already exists.

    `exclude_character_id` matters for the pipeline's own Test:Uniqueness
    stage (see pipeline.py), which re-checks this rule against an already
    *persisted* character -- without excluding its own id, a character
    would always "collide" with itself the moment it's inserted."""
    from app.models import Character

    query = Character.query.filter(
        db.func.lower(Character.first_name) == first_name.lower(),
        db.func.lower(Character.last_name) == last_name.lower(),
    ).filter(Character.status != "failed")

    if exclude_character_id is not None:
        query = query.filter(Character.id != exclude_character_id)

    if query.first() is not None:
        raise ValidationError(f"{first_name} {last_name} already exists.")


def find_last_name_collision(last_name: str):
    """Returns an existing (non-failed) character sharing this last name,
    if any -- used to drive the "A Koskela already exists -- continue
    anyway?" confirm step. Returns None if there's no collision."""
    from app.models import Character

    return (
        Character.query.filter(db.func.lower(Character.last_name) == last_name.lower())
        .filter(Character.status != "failed")
        .first()
    )


def prepare_join_submission(
    first_name: str | None,
    last_name: str | None,
    appearance_id: str | None,
    head_type_id: str | None,
    body_type_id: str | None,
    hand_type_id: str | None,
    icebreaker_answers: dict,
    confirm_last_name_collision: bool = False,
):
    """Prepares a join submission for **storage**, not for validation.

    Returns the (first_name, last_name, appearance_id, head_type_id,
    body_type_id, hand_type_id, icebreaker_answers) tuple to persist --
    every value only stripped and truncated to its column width (see
    truncate_for_storage), never content-checked. icebreaker_answers is
    a dict keyed by question id (see FIXED_ICEBREAKER_QUESTIONS).

    **Nothing here rejects a submission for its content, on purpose.**
    This function used to do exactly that: it ran the same charset,
    length, injection and profanity checks the pipeline's own stages
    run, as an upfront "don't enqueue a job for obvious garbage" gate.
    The problem was what that did to the actual product. A visitor who
    typed a blocked word got a red error under the form and *no pipeline
    run at all* -- the submission was cancelled before the thing this
    entire project exists to show could start. The pipeline is the
    point: a real submission that violates a real rule should be
    watchable failing the stage that owns that rule, in the live build
    log and the run table, exactly the way a uniqueness collision
    already was. Checking first meant the most interesting case was the
    one nobody could ever see happen.

    So every content check now runs in exactly one place -- inside its
    own stage (see pipeline.py) -- and a stage that fails ends the run
    right there: no later stage runs, the character is marked `failed`,
    and it never reaches Production Town.

    The one thing still resolved here is the last-name collision confirm
    (raises LastNameCollision when unconfirmed). That is not a rejection
    and never cancels a run: it's a warn-and-continue prompt that needs
    a human yes/no *before* there's anything to enqueue, and answering
    yes proceeds normally. It has no pass/fail stage of its own
    precisely because it isn't a pass/fail question -- see
    find_last_name_collision."""
    stored_first = truncate_for_storage(first_name, MAX_RAW_NAME_PART_LENGTH)
    stored_last = truncate_for_storage(last_name, MAX_RAW_NAME_PART_LENGTH)
    stored_appearance = truncate_for_storage(appearance_id, MAX_RAW_TYPE_ID_LENGTH)
    stored_head_type = truncate_for_storage(head_type_id, MAX_RAW_TYPE_ID_LENGTH)
    stored_body_type = truncate_for_storage(body_type_id, MAX_RAW_TYPE_ID_LENGTH)
    stored_hand_type = truncate_for_storage(hand_type_id, MAX_RAW_TYPE_ID_LENGTH)
    stored_answers = {
        question["id"]: truncate_for_storage(
            icebreaker_answers.get(question["id"]), MAX_RAW_ICEBREAKER_ANSWER_LENGTH
        )
        for question in FIXED_ICEBREAKER_QUESTIONS
    }

    if not confirm_last_name_collision:
        collision = find_last_name_collision(stored_last)
        if collision is not None:
            raise LastNameCollision(collision)

    return (
        stored_first,
        stored_last,
        stored_appearance,
        stored_head_type,
        stored_body_type,
        stored_hand_type,
        stored_answers,
    )


# ---------- Timed-Squares: a short public leaderboard display name ----------
#
# A different shape of "name" than the character-join fields above (which
# is why it isn't just sanitize_name_part with different constants): an
# arcade high-score initials field allows digits ("P1AYER") and, on
# anything that fails validation, silently falls back to "ANON" instead
# of rejecting the submission -- the score itself was earned by actually
# playing the game, and a malformed name shouldn't cost the player that,
# the way a malformed field should block a *form* submission before
# anything of value has happened yet.

MAX_ARCADE_NAME_LENGTH = 12
_ARCADE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{0,11}$")


def sanitize_arcade_name(raw: str | None) -> str:
    if not raw:
        return "ANON"
    cleaned = raw.strip()[:MAX_ARCADE_NAME_LENGTH]
    if not cleaned or not _ARCADE_NAME_PATTERN.match(cleaned):
        return "ANON"
    cleaned = cleaned.upper()
    if _contains_blocked_word(cleaned):
        return "ANON"
    return cleaned
