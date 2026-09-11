"""
Static-page/link integrity checks for the Jantar AI public pricing page
(Phase 3 -- frontend/pricing.html) and its link from frontend/index.html.

frontend/*.html are plain, no-build-step static files with no backend
dependency (same convention as frontend/privacy-policy.html) -- these
checks read the files directly rather than spinning up a server, since
what's being verified is the file's own content and cross-links, not
application behavior. No payment provider, checkout, or subscription
enforcement is implemented anywhere in this codebase yet; several
assertions below exist specifically to keep this honest.
"""

from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

# Every feature bullet on the pricing page that does not yet exist
# anywhere in this codebase (no analytics, no lead-capture, no custom
# knowledge upload, no forced multi-language behavior, no voice/speech
# pipeline, no workflow engine) -- each MUST carry a "Coming soon" tag.
UNIMPLEMENTED_FEATURES = [
    "Advanced conversation history",
    "Customer/lead capture",
    "Custom restaurant knowledge",
    "Multi-language responses",
    "Basic analytics",
    "AI voice receptionist",
    "Advanced analytics",
    "Custom workflows",
    "Future integrations",
]


def _read(name: str) -> str:
    return (FRONTEND_DIR / name).read_text()


def test_pricing_page_exists_with_jantar_ai_branding():
    content = _read("pricing.html")
    assert "<title>" in content
    assert "Jantar AI" in content


def test_pricing_page_lists_all_three_approved_plans_and_prices():
    content = _read("pricing.html")
    for plan, price in [("Starter", "$39"), ("Growth", "$79"), ("Pro", "$149")]:
        assert plan in content
        assert price in content


def test_growth_plan_is_marked_most_popular():
    assert "MOST POPULAR" in _read("pricing.html")


def test_pricing_page_has_no_payment_provider_or_fake_checkout():
    """
    No payment-provider names or card-collection language anywhere.
    "checkout" itself is deliberately NOT banned here -- the page
    honestly says "No automated checkout yet" in its CTA note, which is
    exactly the kind of honesty this task asked for; the absence of an
    actual checkout FLOW is what matters, covered separately by the
    no-<form>/mailto-only CTA check below.
    """
    content = _read("pricing.html").lower()
    for forbidden in (
        "stripe", "paddle", "lemon squeezy", "lemonsqueezy",
        "card number", "credit card",
    ):
        assert forbidden not in content


def test_pricing_page_ctas_use_mailto_not_a_form():
    content = _read("pricing.html")
    # One "Get Started" mailto CTA per plan (Starter, Growth, Pro).
    assert content.count('href="mailto:rankoutreachhub@gmail.com') >= 3
    assert "<form" not in content


def test_every_unimplemented_feature_is_labeled_coming_soon():
    content = _read("pricing.html")
    for feature in UNIMPLEMENTED_FEATURES:
        assert feature in content, f"expected feature bullet {feature!r} on the pricing page"
    # Each listed feature above must carry its own "Coming soon" tag --
    # not just appear somewhere in the page's boilerplate text once.
    assert content.count("Coming soon") >= len(UNIMPLEMENTED_FEATURES)


def test_real_features_are_not_tagged_coming_soon():
    """A sanity check the other way: features that DO exist in this
    codebase today (chat, booking, widget, WhatsApp, admin dashboard,
    multi-admin-user support) must not be presented as unavailable."""
    content = _read("pricing.html")
    real_features = [
        "AI website chat", "Table booking", "Booking confirmation email",
        "Admin dashboard", "AI chat widget", "WhatsApp channel",
        "Multiple staff/admin users",
    ]
    for feature in real_features:
        assert feature in content
    for feature in real_features:
        # None of these should be immediately followed by a "Coming soon" badge.
        idx = content.index(feature)
        nearby = content[idx: idx + len(feature) + 60]
        assert "Coming soon" not in nearby, f"{feature!r} incorrectly tagged Coming soon"


def test_pricing_page_has_no_fabricated_social_proof():
    content = _read("pricing.html").lower()
    for forbidden in (
        "testimonial", "trusted by", "customers love", "5 stars",
        "rated #1", "as seen on", "★★★★★",
    ):
        assert forbidden not in content


def test_pricing_page_links_back_to_privacy_policy():
    assert 'href="privacy-policy.html"' in _read("pricing.html")


def test_index_page_links_to_pricing_and_still_links_to_privacy_policy():
    """The existing Privacy Policy link must be preserved, not replaced,
    when the new Pricing link is added."""
    content = _read("index.html")
    assert 'href="pricing.html"' in content
    assert 'href="privacy-policy.html"' in content
