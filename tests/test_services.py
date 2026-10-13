from services.cart_service import config_key
from services.catalog_service import money
from services.order_service import ALLOWED_TRANSITIONS


def test_money_never_uses_float_formatting() -> None:
    assert money(123456) == "1 234.56 RUB"
    assert money(5) == "0.05 RUB"


def test_cart_configuration_has_stable_key() -> None:
    assert config_key({"player_id": "42", "zone": "eu"}) == config_key({"zone": "eu", "player_id": "42"})
    assert config_key(None) == ""


def test_only_expected_order_transitions_are_allowed() -> None:
    assert ALLOWED_TRANSITIONS["pending_payment"] == {"cancelled"}
    assert not ALLOWED_TRANSITIONS["paid_test"]
