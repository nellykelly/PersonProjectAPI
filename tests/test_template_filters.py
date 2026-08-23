import pytest

from app.template_filters import format_money


@pytest.mark.parametrize(
    "value,expected",
    [
        (52.10, "$52.10"),
        (-6.44, "$-6.44"),
        (0.0, "$0.00"),
        (1_234.56, "$1,235"),
        (-143_436.43, "$-143.4K"),
        (100_000.00, "$100.0K"),
        (99_999.00, "$99,999"),
        (3_756_570.57, "$3.76M"),
        (1_000_000.00, "$1.00M"),
        (None, "n/a"),
    ],
)
def test_format_money(value, expected):
    assert format_money(value) == expected


def test_format_money_rounds_before_deciding_whether_to_abbreviate_to_millions():
    # 999999.99 rounds to 1,000,000 under the thousands-format branch --
    # without checking the *rounded* magnitude first, this would render
    # as the misleadingly round-looking "$1,000,000" instead of
    # abbreviating like every other 7-figure value does.
    assert format_money(999_999.99) == "$1.00M"


def test_format_money_rounds_before_deciding_whether_to_abbreviate_to_thousands():
    # Same boundary bug one tier down: 999,999.00 divided into millions
    # is 0.999999, which rounds to 1.00 at 2dp -- it must abbreviate as
    # "$1.00M" like its neighbor above, not fall through to the K tier
    # and render the confusing "$1,000.0K".
    assert format_money(999_999.00) == "$1.00M"


def test_money_filter_registered_on_the_app(app):
    assert "money" in app.jinja_env.filters
    assert app.jinja_env.filters["money"](1_000_000.0) == "$1.00M"
