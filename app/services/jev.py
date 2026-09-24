"""Jev (TypeSafe AI's "System One" classifier) as Job Discovery's first
evaluation pass -- the "JEv" in a Discover run.

Jev doesn't generate text. One POST to /v1/systemone sends a `state`
plus typed questions (Choice / Score / Noul) and gets back calibrated
probabilities for each, answered in parallel, priced on input tokens only
(~$0.04 per million, so a ~3k-token listing costs about $0.0001). That
makes it the right tool for "is this worth a real look", which is what the
old keyword pre-score was a blunt stand-in for, and it spends none of the
Groq budget, whose 8k-tokens/minute cap is what actually bounds a run.

The questions follow MadsLorentzen/ai-job-search's evaluation framework
(04-job-evaluation.md: technical skills + experience rubrics), and the
grade is computed *here*, in code, from the answers -- including the
calibration caps from job_discovery._SCORING_GUIDANCE (an Engineering
Manager posting landed ~32, a PM ~45, a staff-level bar ~28-37, ...). So
the same answers always produce the same grade, and tuning the caps is a
code change, not a prompt tweak.

Called with plain `requests` against the documented REST endpoint rather
than through langchain-typesafe (0.0.1a3 at the time, and it would add
httpx2 as a dependency for this one call); the wire shape below is taken
from that package's own source.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests
from flask import current_app

from app.services.net_monitor import log_outbound

_PATH = "/v1/systemone"
_DESCRIPTION_CHARS = 3000


class JevUnavailable(Exception):
    """No key configured, or the request/response failed."""


def is_configured() -> bool:
    return bool(current_app.config.get("TYPESAFE_API_KEY"))


def classify(state: Any, questions: dict[str, dict]) -> dict[str, Any]:
    """One Jev request. Returns the response's `answers` mapping."""
    config = current_app.config
    api_key = config.get("TYPESAFE_API_KEY")
    if not api_key:
        raise JevUnavailable("TYPESAFE_API_KEY is not configured")
    url = f"{(config.get('TYPESAFE_BASE_URL') or 'https://api.typesafe.ai').rstrip('/')}{_PATH}"
    payload = {"model": config.get("JEV_MODEL") or "jev-latest", "state": state, "questions": questions}

    start = time.time()
    status = 200
    try:
        resp = requests.post(
            url, json=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
        )
        status = resp.status_code
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - every failure means "fall back to keyword triage"
        status = status if isinstance(status, int) and status != 200 else 599
        raise JevUnavailable(f"Jev request failed: {exc}") from exc
    finally:
        log_outbound("typesafe", "POST", url, status, (time.time() - start) * 1000)

    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        raise JevUnavailable("Jev response had no answers")
    return answers


# --------------------------------------------------------------------------
# the job evaluation
# --------------------------------------------------------------------------

# Role families -> (tier score 0-100, grade cap). Mirrors
# job_discovery._SCORING_GUIDANCE's three tiers; the caps are that
# guidance's own calibration numbers. Keep the two in step.
_ROLE_FAMILIES: dict[str, tuple[str, int, int]] = {
    "backend_swe": ("Software Engineer, backend or Python-leaning general product engineering.", 100, 100),
    "ai_engineer": (
        "AI, Applied AI, Agentic AI, AI Automation or AI Platform engineer building LLM or agent "
        "systems in production.", 100, 100,
    ),
    "analytics_engineer": ("Analytics Engineer: dbt, dimensional modeling, SQL transformation.", 100, 100),
    "data_engineer": (
        "Data Engineer on the backend/Python track: pipelines, warehouses, APIs. Not centred on "
        "big-data infrastructure.", 90, 100,
    ),
    "full_stack": ("Full Stack Engineer, Python plus React, without deep frontend specialization.", 75, 100),
    "big_data_infra": (
        "Data engineering centred on Spark, Kafka, Airflow or Snowflake at production depth.", 60, 70,
    ),
    "ml_research": (
        "ML Engineer or Research Scientist requiring PyTorch/TensorFlow model-training depth.", 25, 45,
    ),
    "frontend_specialist": ("Frontend role requiring deep Vue, Angular or React specialization.", 25, 35),
    "platform_infra": (
        "Platform, Infrastructure, DevOps or SRE role requiring Kubernetes, Terraform, Go or Rust at depth.",
        25, 46,
    ),
    "eng_manager": ("Engineering Manager or any role with formal people management.", 10, 32),
    "product_manager": ("Product Manager, including technical PM.", 10, 45),
    "other": ("Anything else: sales or solutions engineering, support, QA, non-software roles.", 10, 30),
}

# Seniority -> grade cap. The target band is 2-6 years; both sides of it
# are scored down equally hard (see _SCORING_GUIDANCE).
_SENIORITY: dict[str, tuple[str, int]] = {
    "entry": ("Entry level, new grad, junior, or explicitly under 2 years of experience.", 40),
    "mid": ("Mid-level: roughly 2 to 6 years of experience.", 100),
    "senior": ("Senior title but an experience bar of about 5-6 years or unstated.", 85),
    "staff_plus": ("Staff, principal, lead, or an explicit 7+ years of experience requirement.", 37),
}

# From upstream's 04-job-evaluation.md rubrics, lowest level first.
_SKILLS_RUBRIC = [
    "Fundamental mismatch: the core required skills are ones the candidate lacks.",
    "Partial match: significant upskilling needed on core requirements.",
    "Most requirements match, with one or two learnable gaps.",
    "The core requirements are the candidate's primary, demonstrated skills.",
]
_EXPERIENCE_RUBRIC = [
    "Unrelated experience.",
    "Adjacent experience; the candidate would need to make the case.",
    "Related experience with clearly transferable work.",
    "Direct experience in the same kind of role and domain.",
]

_WEIGHTS = {"skills": 0.35, "experience": 0.30, "role": 0.35}
_FINTECH_BONUS = 5


def _questions() -> dict[str, dict]:
    return {
        "role_family": {
            "type": "choice",
            "instructions": "Which kind of role is the job posting actually for, judged by its "
            "responsibilities and requirements rather than its title alone?",
            "criteria": {key: desc for key, (desc, _, _) in _ROLE_FAMILIES.items()},
        },
        "seniority": {
            "type": "choice",
            "instructions": "What level of experience does the job posting require?",
            "criteria": {key: desc for key, (desc, _) in _SENIORITY.items()},
        },
        "skills": {
            "type": "score",
            "instructions": "How well do the candidate's demonstrated skills (resume and projects) "
            "cover the job posting's required and preferred skills?",
            "criteria": _SKILLS_RUBRIC,
        },
        "experience": {
            "type": "score",
            "instructions": "How closely does the candidate's work history match the kind of work "
            "this role does? Match on the nature of the work, not the literal job title.",
            "criteria": _EXPERIENCE_RUBRIC,
        },
        "fintech": {
            "type": "noul",
            "instructions": "Is this role in financial services: fintech, banking, trading, "
            "payments, insurance, or financial/market data?",
        },
        "clearance": {
            "type": "noul",
            "instructions": "Does the job posting require an active security clearance?",
        },
    }


@dataclass
class Evaluation:
    grade: int
    notes: str
    role_family: str
    seniority: str


def _rubric_fraction(answer: dict, levels: int) -> float:
    return max(0.0, min(1.0, float(answer["score"]) / (levels - 1)))


def grade_from_answers(answers: dict[str, Any]) -> Evaluation:
    """Deterministic grade from Jev's answers. Caps are applied as an
    expectation over each Choice's probability distribution rather than
    to the single top label, so an ambiguous 'maybe a manager role' is
    pulled down in proportion to how likely it is, not all-or-nothing."""
    role = answers["role_family"]
    seniority = answers["seniority"]
    role_probs: dict[str, float] = role["probabilities"]
    seniority_probs: dict[str, float] = seniority["probabilities"]

    skills = _rubric_fraction(answers["skills"], len(_SKILLS_RUBRIC))
    experience = _rubric_fraction(answers["experience"], len(_EXPERIENCE_RUBRIC))
    role_tier = sum(p * _ROLE_FAMILIES.get(k, _ROLE_FAMILIES["other"])[1] for k, p in role_probs.items())

    raw = (
        _WEIGHTS["skills"] * skills * 100
        + _WEIGHTS["experience"] * experience * 100
        + _WEIGHTS["role"] * role_tier
    )
    fintech = float(answers.get("fintech", {}).get("noul", 0.0))
    if fintech >= 0.5:
        raw += _FINTECH_BONUS
    raw = min(100.0, raw)

    capped = sum(p * min(raw, _ROLE_FAMILIES.get(k, _ROLE_FAMILIES["other"])[2]) for k, p in role_probs.items())
    capped = sum(p * min(capped, _SENIORITY.get(k, ("", 100))[1]) for k, p in seniority_probs.items())
    grade = max(0, min(100, round(capped)))

    top_role, top_level = role["choice"], seniority["choice"]
    note = (
        f"Jev: {top_role.replace('_', ' ')} ({role_probs.get(top_role, 0):.0%}), "
        f"{top_level.replace('_', ' ')} level ({seniority_probs.get(top_level, 0):.0%}); "
        f"skills {skills:.0%}, experience {experience:.0%}"
    )
    if fintech >= 0.5:
        note += ", fintech"
    if float(answers.get("clearance", {}).get("noul", 0.0)) >= 0.5:
        note += ". Likely requires a security clearance"
    return Evaluation(grade=grade, notes=note + ".", role_family=top_role, seniority=top_level)


def evaluate_listing(profile_text: str, listing: dict[str, Any]) -> Evaluation:
    """One Jev call for one listing. Raises JevUnavailable on any failure,
    including an answer set missing a question -- the caller falls back
    to keyword triage for that listing."""
    state = {
        "candidate": profile_text,
        "job_posting": {
            "title": listing["role_title"],
            "company": listing["company_name"],
            "location": listing.get("location") or "unspecified",
            "description": (listing.get("description") or "")[:_DESCRIPTION_CHARS],
        },
    }
    answers = classify(state, _questions())
    try:
        return grade_from_answers(answers)
    except (KeyError, TypeError, ValueError) as exc:
        raise JevUnavailable(f"Jev answers were incomplete: {exc}") from exc
