"""Service layer for the private /family suite.

One module per data type owns all of its writes -- calendar.py, grocery.py,
memory.py -- so the blueprint and the Hera chatbot both go through the same
door and every write is attributed. Nothing here does access control; the
blueprint's password gate is the boundary.
"""


class FamilyError(ValueError):
    """A family service rejected the input: unknown id, bad date, missing
    required field, unknown status. The blueprint turns it into a 400/404;
    the chatbot's tool dispatcher turns it into a short string for the
    model. Mirrors job_tracker.JobTrackerError."""
