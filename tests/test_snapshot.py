from decimal import Decimal

from bot.handlers.store import validate_field
from services.snapshot_service import Pricing, image_url, parse_premium_plans, product_name, region_label, region_order


def test_pricing_rounds_up_to_whole_roubles():
    assert Pricing(Decimal("95"), Decimal("15")).rub_minor("1.50") == 16400
    assert Pricing(Decimal("100"), Decimal("0")).rub_minor("1.00") == 10000


def test_premium_plans_shapes():
    assert parse_premium_plans({"data": [{"months": 12, "price_usd": "30"}, {"months": 3, "price_usd": "12.5"}]}) == [(3, "12.5"), (12, "30")]
    assert parse_premium_plans({"prices": {"3": "12", "6": "17"}}) == [(3, "12"), (6, "17")]


def test_region_labels():
    assert region_label("us") == "🇺🇸 US"
    assert region_label(None, "Auto") == "🌍 Global · авто"
    assert region_label("CIS") == "🌐 СНГ"


def test_recipient_field_validation():
    assert validate_field("telegram_username", "durov_bot") == ("@durov_bot", "")
    assert validate_field("telegram_username", "@ab")[0] is None
    assert validate_field("player_id", "5123456789") == ("5123456789", "")
    assert validate_field("player_id", "a\nb")[0] is None


def test_russian_plurals():
    from bot.handlers.store import offers_count
    assert [offers_count(n) for n in (1, 2, 5, 11, 21, 104)] == ["1 предложение", "2 предложения", "5 предложений", "11 предложений", "21 предложение", "104 предложения"]


def test_real_supplier_shapes():
    """Shapes taken from a --probe run against the live API (2026-10-14)."""
    assert parse_premium_plans({"plans": [{"months": 3, "price_usd": "12.1698"}, {"months": 12, "price_usd": "29.4248"}]}) == [(3, "12.1698"), (12, "29.4248")]
    assert image_url("/api/media/x.webp") == "https://resell.codes/api/media/x.webp"
    assert image_url("https://cdn/x.png") == "https://cdn/x.png"
    assert region_label("UK") == "🇬🇧 GB"
    assert region_label("GLOBAL", "Fast") == "🌍 Global · быстро"
    assert region_order("US") < region_order("AE") < region_order("BH")
    assert product_name("PlayStation", "70 USD") == "PlayStation 70 USD"
    assert product_name("PUBG Mobile", "PUBG Mobile 60 UC") == "PUBG Mobile 60 UC"
