"""One page -- /legal -- covering the compliance surface a public site
with accounts and an AI chatbot actually has: terms, a plain-language
privacy notice, the cookie position, the AI-assistant disclaimer
(Moffatt v. Air Canada -- "whatever the bot says, you said"), an
accessibility statement, and an abuse/DMCA contact. Static render, linked
from the footer of every page.
"""
from flask import render_template

from app.blueprints.legal import bp

# Bump when the substance changes -- shown on the page.
LAST_UPDATED = "September 2026"


@bp.route("/legal")
def index():
    return render_template("legal/index.html", last_updated=LAST_UPDATED)
