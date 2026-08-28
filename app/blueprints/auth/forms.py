from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length, Regexp, ValidationError

from app.models import User

# Username charset: letters, digits, dash, underscore. No spaces, no '@'
# (so it never looks like a half-typed email), no dots.
USERNAME_PATTERN = r"^[A-Za-z0-9_-]+$"


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=User.USERNAME_MAX)])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Log in")


class RegisterForm(FlaskForm):
    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            Length(min=User.USERNAME_MIN, max=User.USERNAME_MAX),
            Regexp(USERNAME_PATTERN, message="Letters, numbers, dashes and underscores only."),
        ],
    )
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(),
            Length(min=User.PASSWORD_MIN, message=f"At least {User.PASSWORD_MIN} characters."),
        ],
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords do not match.")],
    )
    submit = SubmitField("Create account")

    def validate_username(self, field):
        """Case-insensitive uniqueness check. The DB unique constraint on
        username_ci is the real guard (and a race is caught as an
        IntegrityError in the route); this just turns the common case into
        a friendly field error instead of a 409."""
        if User.query.filter_by(username_ci=User.normalize_username(field.data)).first():
            raise ValidationError("That username is taken.")
