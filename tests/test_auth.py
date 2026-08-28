from app.models import User


# ---------------------------------------------------------------- registration

def test_register_creates_account_and_logs_in(client, db):
    resp = client.post(
        "/auth/register",
        data={"username": "NewGuy", "password": "hunter2hunter", "confirm": "hunter2hunter"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/leetcode-150")

    user = User.query.filter_by(username_ci="newguy").one()
    assert user.username == "NewGuy"          # stored as typed
    assert user.password_hash and user.password_hash != "hunter2hunter"
    assert user.last_login_at is not None

    # session is authenticated -> the gated page renders
    assert client.get("/leetcode-150").status_code == 200


def test_register_rejects_duplicate_username_case_insensitively(client, make_user, db):
    make_user("Taken")
    resp = client.post(
        "/auth/register",
        data={"username": "taken", "password": "longenough1", "confirm": "longenough1"},
    )
    assert resp.status_code == 200  # re-rendered form, not a redirect
    assert b"taken" in resp.data.lower()
    assert User.query.filter(User.username_ci == "taken").count() == 1


def test_register_rejects_short_password(client, db):
    resp = client.post(
        "/auth/register",
        data={"username": "shortpw", "password": "abc", "confirm": "abc"},
    )
    assert resp.status_code == 200
    assert User.query.count() == 0


def test_register_rejects_mismatched_confirmation(client, db):
    resp = client.post(
        "/auth/register",
        data={"username": "mismatch", "password": "properlength1", "confirm": "different12345"},
    )
    assert resp.status_code == 200
    assert User.query.count() == 0


def test_register_rejects_bad_username_chars(client, db):
    resp = client.post(
        "/auth/register",
        data={"username": "has spaces", "password": "properlength1", "confirm": "properlength1"},
    )
    assert resp.status_code == 200
    assert User.query.count() == 0


def test_registration_can_be_disabled(client, app, db):
    app.config["REGISTRATION_ENABLED"] = False
    assert client.get("/auth/register").status_code == 403
    resp = client.post(
        "/auth/register",
        data={"username": "late", "password": "properlength1", "confirm": "properlength1"},
    )
    assert resp.status_code == 403
    assert User.query.count() == 0


# ---------------------------------------------------------------- login / logout

def test_login_succeeds_with_correct_credentials(client, make_user):
    make_user("loginok", "correct-horse")
    resp = client.post("/auth/login", data={"username": "loginok", "password": "correct-horse"})
    assert resp.status_code == 302
    assert client.get("/leetcode-150").status_code == 200


def test_login_is_case_insensitive_on_username(client, make_user):
    make_user("MixedCase", "correct-horse")
    resp = client.post("/auth/login", data={"username": "mixedcase", "password": "correct-horse"})
    assert resp.status_code == 302


def test_login_fails_with_wrong_password(client, make_user):
    make_user("wrongpw", "the-real-one")
    resp = client.post("/auth/login", data={"username": "wrongpw", "password": "not-it"})
    assert resp.status_code == 401
    assert client.get("/leetcode-150").status_code == 302  # still anonymous


def test_login_gives_one_message_for_unknown_user(client):
    resp = client.post("/auth/login", data={"username": "ghost", "password": "whatever12"})
    assert resp.status_code == 401
    assert b"Wrong username or password" in resp.data


def test_logout_ends_the_session(auth_client):
    assert auth_client.get("/leetcode-150").status_code == 200
    resp = auth_client.post("/auth/logout")
    assert resp.status_code == 302
    assert auth_client.get("/leetcode-150").status_code == 302


def test_login_next_only_allows_same_site_paths(client, make_user):
    make_user("redir", "correct-horse")
    resp = client.post(
        "/auth/login?next=https://evil.example/phish",
        data={"username": "redir", "password": "correct-horse"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/leetcode-150")  # external next ignored


def test_login_next_honours_a_relative_path(client, make_user):
    make_user("redir2", "correct-horse")
    resp = client.post(
        "/auth/login?next=/leetcode-150",
        data={"username": "redir2", "password": "correct-horse"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/leetcode-150")


# ---------------------------------------------------------------- CLI

def test_create_user_and_set_password_cli(app, db):
    runner = app.test_cli_runner()

    r = runner.invoke(args=["create-user", "cliuser", "--password", "cli-password-1"])
    assert r.exit_code == 0, r.output
    user = User.query.filter_by(username_ci="cliuser").one()
    assert user.check_password("cli-password-1")

    r = runner.invoke(args=["create-user", "CLIUSER", "--password", "another-pass-1"])
    assert r.exit_code != 0  # duplicate (case-insensitive)

    r = runner.invoke(args=["set-password", "cliuser", "--password", "brand-new-pass-2"])
    assert r.exit_code == 0, r.output
    db.session.expire_all()
    assert User.query.filter_by(username_ci="cliuser").one().check_password("brand-new-pass-2")
