"""
Static-page/link integrity checks for the Jantar AI public pricing page
(Phase 3 -- frontend/pricing.html) and the Terms of Service page
(frontend/terms-of-service.html), and their links from
frontend/index.html and frontend/pricing.html.

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


# --- Terms of Service (frontend/terms-of-service.html) ---

TOS_REQUIRED_SECTIONS = [
    "Service overview",
    "Restaurant responsibilities",
    "Customer responsibilities",
    "Account and admin access",
    "Bookings and restaurant-provided information",
    "Acceptable use",
    "Third-party services",
    "Fees and payment",
    "Availability and service limitations",
    "Intellectual property",
    "Suspension and termination",
    "Privacy",
    "Contact",
]


def test_terms_of_service_exists_with_jantar_ai_branding():
    content = _read("terms-of-service.html")
    assert "<title>" in content
    assert "Jantar AI" in content
    assert "Terms of Service" in content


def test_terms_of_service_includes_all_required_sections():
    content = _read("terms-of-service.html")
    for section in TOS_REQUIRED_SECTIONS:
        assert section in content, f"expected a section covering {section!r}"


def test_terms_of_service_uses_the_existing_contact_email():
    content = _read("terms-of-service.html")
    assert 'href="mailto:rankoutreachhub@gmail.com"' in content


def test_terms_of_service_links_to_privacy_policy():
    assert 'href="privacy-policy.html"' in _read("terms-of-service.html")


def test_terms_of_service_does_not_invent_legal_identity_details():
    """No fabricated company registration number, physical address, or
    DPO -- only the same honest "not currently published" wording
    established for the Privacy Policy."""
    content = _read("terms-of-service.html")
    assert "Not currently published" in content
    for forbidden in ("Companies House", "Company No.", "Registration No.", "DPO", "Data Protection Officer"):
        assert forbidden not in content


def test_terms_of_service_does_not_claim_payment_processing_or_refunds():
    """
    No payment-provider names, card-collection language, or an actual
    invented refund policy. The page DOES honestly say it does not
    state a refund policy (since no payment processing exists yet) --
    that denial is exactly the honesty this task asked for, so it's not
    banned here; only a concrete, invented refund TERM (a time window or
    "money back") would be a real problem.
    """
    content = _read("terms-of-service.html").lower()
    for forbidden in (
        "stripe", "paddle", "lemon squeezy", "lemonsqueezy",
        "card number", "credit card", "30-day refund", "money back",
    ):
        assert forbidden not in content
    # It must instead say plainly that automated payment isn't implemented yet.
    assert "not implemented yet" in _read("terms-of-service.html").lower() or \
           "not implemented" in _read("terms-of-service.html").lower()


def test_terms_of_service_does_not_present_coming_soon_features_as_available():
    """The ToS must not claim any of the pricing page's not-yet-built
    features (voice, analytics, workflows, etc.) as already available."""
    content = _read("terms-of-service.html")
    for feature in UNIMPLEMENTED_FEATURES:
        assert feature not in content


def test_index_and_pricing_pages_link_to_terms_of_service():
    for page in ("index.html", "pricing.html"):
        content = _read(page)
        assert 'href="terms-of-service.html"' in content, f"{page} should link to terms-of-service.html"
        # Both existing links must still be present alongside the new one.
        assert 'href="privacy-policy.html"' in content
