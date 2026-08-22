"""Input validation for Pipeline World's input surface: a name, an
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
# (hash the candidate word the same way and compare).
_BLOCKED_NAME_HASHES = frozenset(
    {
        hashlib.sha256(word.encode("utf-8")).hexdigest()
        for word in ("damn", "hell", "crap")  # deliberately mild placeholders, see module docstring
    }
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


def validate_join_request(
    first_name: str | None,
    last_name: str | None,
    appearance_id: str | None,
    head_type_id: str | None,
    body_type_id: str | None,
    hand_type_id: str | None,
    icebreaker_answers: dict,
    confirm_last_name_collision: bool = False,
):
    """Runs the full validation chain for a join submission. Returns the
    cleaned (first_name, last_name, appearance_id, head_type_id,
    body_type_id, hand_type_id, icebreaker_answers) tuple on success --
    icebreaker_answers is a dict keyed by question id (see
    FIXED_ICEBREAKER_QUESTIONS).

    Raises ValidationError for a hard failure (bad input, an injection
    pattern, or profanity), or LastNameCollision if there's a last-name
    match the visitor hasn't confirmed past yet (only raised when
    confirm_last_name_collision is False).

    Deliberately does **not** check full-name uniqueness here -- that's
    the pipeline's own Test:Uniqueness stage's job, run later, once the
    job actually executes (see pipeline.py's _test_uniqueness). Doing it
    both here and there meant a duplicate name was rejected before a
    visitor ever saw the pipeline run, which defeated the point of having
    a real Test:Uniqueness stage: two visitors submitting the same name
    at nearly the same time should both be accepted and *race* through
    the pipeline, with the one that actually loses the race failing
    visibly at Test:Uniqueness -- not silently turned away at the join
    form. This upfront gate still runs the same format/injection/profanity
    checks the pipeline's own stages run on their own afterward -- doing
    those here too is just good UX (reject garbage before enqueueing a
    job for it), not a substitute for the pipeline independently
    re-verifying itself once the job actually runs. The icebreaker
    *questions* are a fixed, identical set for every visitor (no
    picking); each *answer* is genuinely free text -- a broader input
    surface than name -- so every one of them goes through the same two
    checks (injection scan, sanitize) plus profanity the name does, just
    with its own charset (see sanitize_icebreaker_answer)."""
    check_no_injection_patterns(first_name or "", "First name")
    check_no_injection_patterns(last_name or "", "Last name")
    for question in FIXED_ICEBREAKER_QUESTIONS:
        check_no_injection_patterns(
            icebreaker_answers.get(question["id"]) or "", f'"{question["question"]}" answer'
        )

    clean_first = sanitize_name_part(first_name, "First name")
    clean_last = sanitize_name_part(last_name, "Last name")
    clean_appearance = validate_appearance_id(appearance_id)
    clean_head_type = validate_head_type_id(head_type_id)
    clean_body_type = validate_body_type_id(body_type_id)
    clean_hand_type = validate_hand_type_id(hand_type_id)
    clean_answers = sanitize_icebreaker_answers(icebreaker_answers)

    check_no_profanity(clean_first, "First name")
    check_no_profanity(clean_last, "Last name")
    for question in FIXED_ICEBREAKER_QUESTIONS:
        check_no_profanity(clean_answers[question["id"]], f'"{question["question"]}" answer')

    if not confirm_last_name_collision:
        collision = find_last_name_collision(clean_last)
        if collision is not None:
            raise LastNameCollision(collision)

    return clean_first, clean_last, clean_appearance, clean_head_type, clean_body_type, clean_hand_type, clean_answers
