"""
Static-page/link integrity checks for the Jantar AI public pricing page
(Phase 3 -- frontend/pricing.html), the Terms of Service page
(frontend/terms-of-service.html), the Jantar AI public homepage
(frontend/index.html), and the live chat demo page
(frontend/demo.html) it links to, plus the cross-links between all of
these.

frontend/*.html are plain, no-build-step static files with no backend
dependency (same convention as frontend/privacy-policy.html) -- these
checks read the files directly rather than spinning up a server, since
what's being verified is the file's own content and cross-links, not
application behavior. No payment provider, checkout, or subscription
enforcement is implemented anywhere in this codebase yet; several
assertions below exist specifically to keep this honest.
"""

import json
import re
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


# --- Jantar AI public homepage (frontend/index.html) ---

HOMEPAGE_REQUIRED_SECTIONS = [
    "How it works",
    "Features",
    "See it in action",
    "Pricing preview",
    "Coming soon",
    "Get started",
]

HOMEPAGE_REAL_FEATURES = [
    "AI website chat",
    "Menu, hours &amp; FAQ answering",
    "Embeddable chat widget",
    "Table booking with capacity checks",
    "Booking confirmation email",
    "WhatsApp channel",
    "Admin dashboard",
    "Conversation history",
]

HOMEPAGE_COMING_SOON_FEATURES = [
    "AI voice receptionist",
    "Analytics",
    "Lead capture",
    "Custom restaurant knowledge",
    "Multi-language responses",
    "Custom workflows",
    "Future integrations",
]


def test_homepage_has_jantar_ai_branding_and_correct_title():
    content = _read("index.html")
    assert "<title>Jantar AI — AI Chat &amp; Table Booking for Restaurants</title>" in content
    assert "Jantar AI" in content


def test_homepage_has_exactly_one_h1_with_the_approved_wording():
    content = _read("index.html")
    assert content.count("<h1>") == 1
    assert "AI chat and table bookings for your restaurant" in content


def test_homepage_has_the_approved_meta_description():
    content = _read("index.html")
    assert (
        '<meta name="description" content="Jantar AI gives restaurants an AI chat assistant '
        'for their website and WhatsApp, with automatic table booking and an admin dashboard. '
        'See plans and pricing.">'
    ) in content


def test_homepage_is_indexable():
    assert '<meta name="robots" content="index, follow">' in _read("index.html")


def test_homepage_has_the_correct_canonical_and_og_url():
    """jantarai.com is now verified and live (final domain SEO update) --
    both must point at the real root URL, not a placeholder or the old
    temporary hosting domain."""
    content = _read("index.html")
    assert '<link rel="canonical" href="https://jantarai.com/">' in content
    assert '<meta property="og:url" content="https://jantarai.com/">' in content


def test_homepage_has_open_graph_text_fields_but_no_invented_image():
    content = _read("index.html")
    assert 'property="og:title"' in content
    assert 'property="og:description"' in content
    assert 'property="og:type"' in content
    assert "og:image" not in content


def test_homepage_has_valid_organization_json_ld_with_only_verified_facts():
    content = _read("index.html")
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', content, re.S)
    assert match, "expected a JSON-LD script block on the homepage"
    data = json.loads(match.group(1))
    assert data["@type"] == "Organization"
    assert data["name"] == "Jantar AI"
    assert data["email"] == "rankoutreachhub@gmail.com"
    # No invented address, phone, logo, social profiles, or founding date.
    for forbidden_key in ("address", "telephone", "logo", "sameAs", "foundingDate", "url"):
        assert forbidden_key not in data


def test_homepage_includes_all_required_sections_in_order():
    content = _read("index.html")
    positions = []
    for section in HOMEPAGE_REQUIRED_SECTIONS:
        assert section in content, f"expected a section covering {section!r}"
        positions.append(content.index(section))
    assert positions == sorted(positions), "homepage sections are out of the approved order"


def test_homepage_lists_only_real_features_without_coming_soon_tags():
    content = _read("index.html")
    for feature in HOMEPAGE_REAL_FEATURES:
        assert feature in content, f"expected real feature {feature!r} on the homepage"
        idx = content.index(feature)
        nearby = content[idx: idx + len(feature) + 80]
        assert "Coming soon" not in nearby, f"{feature!r} incorrectly tagged Coming soon"


def test_homepage_coming_soon_section_tags_every_unavailable_feature():
    content = _read("index.html")
    for feature in HOMEPAGE_COMING_SOON_FEATURES:
        assert feature in content, f"expected {feature!r} in the homepage's Coming soon section"
    assert content.count("Coming soon") >= len(HOMEPAGE_COMING_SOON_FEATURES)


def test_homepage_has_no_fabricated_claims_or_social_proof():
    content = _read("index.html").lower()
    for forbidden in (
        "testimonial", "trusted by", "customers love", "5 stars",
        "rated #1", "as seen on", "★★★★★", "free trial", "sign up free",
        "pos integration", "point of sale", "money back", "guarantee",
    ):
        assert forbidden not in content


def test_homepage_has_no_payment_provider_or_fake_checkout():
    content = _read("index.html").lower()
    for forbidden in ("stripe", "paddle", "lemon squeezy", "lemonsqueezy", "card number", "credit card"):
        assert forbidden not in content
    assert "<form" not in content


def test_homepage_ctas_and_links_are_correct():
    content = _read("index.html")
    for href in (
        'href="pricing.html"', 'href="demo.html"',
        'href="terms-of-service.html"', 'href="privacy-policy.html"',
    ):
        assert href in content
    assert content.count('href="mailto:rankoutreachhub@gmail.com') >= 1


# --- Live chat demo page (frontend/demo.html) ---

def test_demo_page_exists_and_preserves_chat_mechanics():
    """The move from index.html to demo.html must not touch the working
    chat mechanics: message list, input, send button, header, typing
    indicator, live API connection, and restaurant_id."""
    content = _read("demo.html")
    for expected in (
        'id="messages"', 'id="user-input"', 'id="send-btn"', 'id="chat-header"',
        "The Kings Arms", "Typing...",
        'const API_URL = "https://restaurant-ai-agent-production-834f.up.railway.app/chat"',
        "restaurant_id: 1",
    ):
        assert expected in content, f"expected {expected!r} to survive the move to demo.html"


def test_demo_page_has_the_honest_demo_banner():
    content = _read("demo.html")
    assert (
        "This is a live demo of the Jantar AI chat assistant, "
        "shown here for an example restaurant."
    ) in content


def test_demo_page_links_back_to_homepage_and_other_pages():
    content = _read("demo.html")
    for href in ('href="index.html"', 'href="pricing.html"', 'href="privacy-policy.html"', 'href="terms-of-service.html"'):
        assert href in content


# --- SEO foundation (jantarai.com not connected yet -- no canonical/og:url/
# sitemap/robots.txt in this pass; see the approved read-only SEO audit) ---

PUBLIC_SEO_PAGES = {
    "index.html": {
        "title": "Jantar AI — AI Chat &amp; Table Booking for Restaurants",
        "description": (
            "Jantar AI gives restaurants an AI chat assistant for their website and "
            "WhatsApp, with automatic table booking and an admin dashboard. See plans and pricing."
        ),
        "og_title": "Jantar AI — AI Chat & Table Booking for Restaurants",
        "robots": "index, follow",
        "canonical": "https://jantarai.com/",
    },
    "pricing.html": {
        "title": "Pricing — Jantar AI",
        "description": (
            "Jantar AI plans for restaurants: AI website chat, table booking, and "
            "WhatsApp messaging. Starter from $39/month. See features and pricing."
        ),
        "og_title": "Pricing — Jantar AI",
        "robots": "index, follow",
        "canonical": "https://jantarai.com/pricing.html",
    },
    "terms-of-service.html": {
        "title": "Terms of Service — Jantar AI",
        "description": (
            "Terms of Service for Jantar AI, the AI chat and booking platform for "
            "restaurants. Read what the current early-stage MVP covers."
        ),
        "og_title": "Terms of Service — Jantar AI",
        "robots": "index, follow",
        "canonical": "https://jantarai.com/terms-of-service.html",
    },
    "privacy-policy.html": {
        "title": "Privacy Policy — Jantar AI",
        "description": (
            "Privacy Policy for Jantar AI, describing what data is collected through "
            "AI chat, WhatsApp, and table bookings, and how it's used."
        ),
        "og_title": "Privacy Policy — Jantar AI",
        "robots": "index, follow",
        "canonical": "https://jantarai.com/privacy-policy.html",
    },
    "demo.html": {
        "title": "Live Demo Chat — Jantar AI",
        "description": "See a live example of the Jantar AI restaurant chat assistant in action.",
        "og_title": "Live Demo Chat — Jantar AI",
        "robots": "noindex, follow",
        "canonical": "https://jantarai.com/demo.html",
    },
}

PUBLIC_INDEXABLE_PAGES = list(PUBLIC_SEO_PAGES.keys())

ALL_SIX_PAGES = list(PUBLIC_SEO_PAGES.keys()) + ["admin.html"]


def test_meta_descriptions_are_present_and_unique_across_public_pages():
    descriptions = []
    for page, expected in PUBLIC_SEO_PAGES.items():
        content = _read(page)
        tag = f'<meta name="description" content="{expected["description"]}">'
        assert tag in content, f"{page} missing its approved meta description"
        descriptions.append(expected["description"])
    assert len(descriptions) == len(set(descriptions)), "meta descriptions must be unique per page"


def test_robots_directives_are_correct_per_page():
    for page, expected in PUBLIC_SEO_PAGES.items():
        content = _read(page)
        assert f'<meta name="robots" content="{expected["robots"]}">' in content, \
            f"{page} should have robots={expected['robots']!r}"


def test_admin_page_is_noindex_nofollow_and_has_no_marketing_metadata():
    """
    admin.html legitimately contains name="description" as an unrelated
    menu-item form field (<textarea name="description">) -- checked for
    specifically as <meta name="description" so that pre-existing,
    unrelated admin UI isn't a false positive here.
    """
    content = _read("admin.html")
    assert '<meta name="robots" content="noindex, nofollow">' in content
    assert '<meta name="description"' not in content
    assert "og:title" not in content
    assert "og:description" not in content
    assert "twitter:" not in content


def test_demo_page_is_noindex_follow():
    """noindex keeps the example-restaurant demo out of search results;
    follow still lets crawlers discover the links it points to (Pricing,
    Terms, Privacy, Home)."""
    assert '<meta name="robots" content="noindex, follow">' in _read("demo.html")


def test_open_graph_title_description_type_correct_on_every_public_page():
    for page, expected in PUBLIC_SEO_PAGES.items():
        content = _read(page)
        assert f'property="og:title" content="{expected["og_title"]}"' in content, page
        assert 'property="og:type" content="website"' in content, page
        # og:description must match the same text as the meta description.
        assert f'property="og:description" content="{expected["description"]}"' in content, page


def test_twitter_card_title_description_correct_on_every_public_page():
    for page, expected in PUBLIC_SEO_PAGES.items():
        content = _read(page)
        assert 'name="twitter:card" content="summary"' in content, page
        assert f'name="twitter:title" content="{expected["og_title"]}"' in content, page
        assert f'name="twitter:description" content="{expected["description"]}"' in content, page


def test_admin_page_has_no_twitter_card_tags():
    assert "twitter:" not in _read("admin.html")


def test_every_public_page_has_the_correct_canonical_and_og_url():
    """jantarai.com is verified and live -- every public page's
    canonical and og:url must point at https://jantarai.com/<path>,
    consistently, matching PUBLIC_SEO_PAGES exactly."""
    for page, expected in PUBLIC_SEO_PAGES.items():
        content = _read(page)
        assert f'<link rel="canonical" href="{expected["canonical"]}">' in content, page
        assert f'<meta property="og:url" content="{expected["canonical"]}">' in content, page


def test_admin_page_still_has_no_canonical_or_og_url():
    """admin.html is noindex/nofollow and not a public page -- it must
    never gain a canonical or og:url, unlike the 5 public pages above."""
    content = _read("admin.html")
    assert 'rel="canonical"' not in content
    assert "og:url" not in content


def test_no_invented_og_image_anywhere():
    """No image asset exists in this repo -- og:image must not be
    invented or pointed at a placeholder/remote URL."""
    for page in ALL_SIX_PAGES:
        assert "og:image" not in _read(page), page
        assert "twitter:image" not in _read(page), page


# --- robots.txt (safe-now SEO cleanup) ---

def test_robots_txt_exists_and_applies_to_every_crawler():
    content = _read("robots.txt")
    assert "User-agent: *" in content


def test_robots_txt_disallows_admin_html():
    assert "Disallow: /admin.html" in _read("robots.txt")


def test_robots_txt_does_not_block_public_pages():
    """Only admin.html is disallowed -- the homepage, pricing, demo,
    Terms, and Privacy pages (and the site root) must remain crawlable,
    matching each page's own <meta name="robots"> directive tested
    above (all "index, follow" or "noindex, follow", never blocked at
    the robots.txt level)."""
    content = _read("robots.txt")
    disallowed_paths = [
        line.split(":", 1)[1].strip()
        for line in content.splitlines()
        if line.strip().lower().startswith("disallow:")
    ]
    assert disallowed_paths == ["/admin.html"]
    for public_path in ("/", "/index.html", "/pricing.html", "/demo.html",
                         "/terms-of-service.html", "/privacy-policy.html"):
        assert public_path not in disallowed_paths


def test_robots_txt_references_the_live_sitemap():
    """jantarai.com is verified and live -- robots.txt must now point
    crawlers at the real sitemap, while the pre-existing admin disallow
    stays exactly as it was."""
    content = _read("robots.txt")
    assert "Sitemap: https://jantarai.com/sitemap.xml" in content
    assert "Disallow: /admin.html" in content


# --- sitemap.xml (final domain SEO update) ---

def _read_sitemap() -> str:
    return (FRONTEND_DIR / "sitemap.xml").read_text()


def test_sitemap_exists_and_is_well_formed_xml():
    import xml.etree.ElementTree as ET
    root = ET.fromstring(_read_sitemap())
    assert root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset"


def test_sitemap_contains_exactly_the_five_public_pages_and_nothing_else():
    import xml.etree.ElementTree as ET
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.fromstring(_read_sitemap())
    locs = {el.text for el in root.findall("sm:url/sm:loc", ns)}
    assert locs == {
        "https://jantarai.com/",
        "https://jantarai.com/pricing.html",
        "https://jantarai.com/demo.html",
        "https://jantarai.com/privacy-policy.html",
        "https://jantarai.com/terms-of-service.html",
    }


def test_sitemap_never_includes_admin_html():
    content = _read_sitemap()
    assert "admin" not in content.lower()


def test_sitemap_never_invents_a_different_domain():
    """Every <loc> must use the one verified production domain -- no
    Railway/Vercel temporary host, no http://, no trailing differences."""
    content = _read_sitemap()
    assert "railway.app" not in content
    assert "vercel.app" not in content
    assert "http://jantarai.com" not in content


def test_privacy_policy_footer_links_are_fixed():
    """Regression test for the SEO audit's biggest internal-linking
    gap: privacy-policy.html previously linked to nothing at all."""
    content = _read("privacy-policy.html")
    for href in ('href="index.html"', 'href="pricing.html"', 'href="terms-of-service.html"',
                 'href="mailto:rankoutreachhub@gmail.com"'):
        assert href in content


def test_privacy_policy_is_fully_rebranded_to_jantar_ai():
    """This task explicitly approved renaming the remaining "Restaurant
    AI Agent" occurrences in privacy-policy.html to "Jantar AI" -- none
    should be left behind."""
    assert "Restaurant AI Agent" not in _read("privacy-policy.html")


FAVICON_RASTER_ASSETS = [
    "favicon-16x16.png", "favicon-32x32.png", "favicon-48x48.png",
    "favicon-512x512.png", "apple-touch-icon.png",
]


def test_favicon_assets_exist_locally_and_are_not_remote():
    """
    No external/remote favicon URL -- real local SVG + PNG files,
    referenced consistently from all six pages. The SVG's own
    xmlns="http://www.w3.org/2000/svg" namespace declaration is
    standard, required SVG boilerplate (never fetched over the
    network) and is deliberately not flagged here -- only an actual
    external resource reference (<image>, xlink:href, a src/href
    pointing off-repo) would be a real problem.
    """
    favicon_path = FRONTEND_DIR / "favicon.svg"
    assert favicon_path.exists(), "expected frontend/favicon.svg to exist"
    svg_content = favicon_path.read_text()
    assert "<svg" in svg_content
    assert "<image" not in svg_content
    assert "xlink:href" not in svg_content
    assert "https://" not in svg_content

    for asset in FAVICON_RASTER_ASSETS:
        asset_path = FRONTEND_DIR / asset
        assert asset_path.exists(), f"expected frontend/{asset} to exist"
        assert asset_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{asset} should be a real PNG file"


def test_favicon_design_matches_the_approved_artwork():
    """The approved design: maroon #7a2e2e background, an 8-point gold
    sunburst (#F0A500 rays, #FFD65C core) as the J's dot, and the same
    white J glyph as before -- unchanged, not redesigned."""
    svg_content = (FRONTEND_DIR / "favicon.svg").read_text()
    assert 'fill="#7a2e2e"' in svg_content
    assert 'fill="#F0A500"' in svg_content
    assert 'fill="#FFD65C"' in svg_content
    assert ">J<" in svg_content


def test_favicon_referenced_consistently_from_every_page():
    for page in ALL_SIX_PAGES:
        content = _read(page)
        assert '<link rel="icon" type="image/svg+xml" href="favicon.svg">' in content, \
            f"{page} should reference the local SVG favicon"
        assert '<link rel="icon" type="image/png" sizes="16x16" href="favicon-16x16.png">' in content, page
        assert '<link rel="icon" type="image/png" sizes="32x32" href="favicon-32x32.png">' in content, page
        assert '<link rel="icon" type="image/png" sizes="48x48" href="favicon-48x48.png">' in content, page
        assert '<link rel="icon" type="image/png" sizes="512x512" href="favicon-512x512.png">' in content, page
        assert '<link rel="apple-touch-icon" sizes="180x180" href="apple-touch-icon.png">' in content, page


def test_demo_page_title_and_description_reflect_its_demo_nature():
    content = _read("demo.html")
    assert "<title>Live Demo Chat — Jantar AI</title>" in content
    assert "See a live example of the Jantar AI restaurant chat assistant in action." in content


# --- Final QA fixes A1-A4 (frontend/admin.html) ---

def test_admin_page_is_fully_rebranded_to_jantar_ai():
    """A1: no remaining "Restaurant AI Agent" branding anywhere in
    admin.html -- the exact same regression-test pattern already
    established for privacy-policy.html above."""
    content = _read("admin.html")
    assert "Restaurant AI Agent" not in content
    assert "<title>Jantar AI — Admin</title>" in content
    assert "<h1>Jantar AI</h1>" in content
    assert "<h1>Jantar AI — Admin</h1>" in content


def test_admin_page_links_to_terms_of_service_alongside_privacy_policy():
    """A4: both the login-screen footer and the signed-in app footer
    must link to Terms of Service now, without losing the existing
    Privacy Policy link in either."""
    content = _read("admin.html")
    assert content.count('href="privacy-policy.html"') == 2, "expected the pre-existing Privacy Policy link in both footers"
    assert content.count('href="terms-of-service.html"') == 2, "expected a new Terms of Service link in both footers"
    login_footer = content[content.index('id="login-footer"'): content.index('id="login-footer"') + 200]
    assert 'href="privacy-policy.html"' in login_footer
    assert 'href="terms-of-service.html"' in login_footer
    app_footer = content[content.index('id="app-footer"'): content.index('id="app-footer"') + 200]
    assert 'href="privacy-policy.html"' in app_footer
    assert 'href="terms-of-service.html"' in app_footer


def test_admin_page_tables_are_wrapped_for_horizontal_scroll():
    """A2: every <table> in admin.html (all 8, rendered from JS template
    literals) is wrapped in a .table-scroll container, so a wide table
    scrolls within its own box on a narrow screen instead of forcing the
    whole page to scroll sideways."""
    content = _read("admin.html")
    assert ".table-scroll" in content, "expected a .table-scroll CSS rule (overflow-x: auto)"
    table_count = content.count("<table>")
    wrapped_count = content.count('<div class="table-scroll"><table>')
    assert table_count == 8, f"expected exactly 8 <table> elements in admin.html, found {table_count}"
    assert wrapped_count == table_count, (
        f"expected every <table> to be immediately preceded by the .table-scroll wrapper, "
        f"but only {wrapped_count} of {table_count} were"
    )


def test_admin_page_form_inputs_have_accessible_labels():
    """A3: every important interactive form input that previously relied
    on placeholder-only text now has an aria-label (or, for the 4 fields
    that already had a real <label for>, was deliberately left alone
    rather than given a redundant aria-label -- see the module docstring
    on why: booking_enabled/is_active checkboxes and the
    welcome-message/accent-color-picker fields)."""
    content = _read("admin.html")
    labeled_fields = [
        'id="restaurant-select"',
        'name="name" placeholder="Name" aria-label="Restaurant name"',
        'name="phone" placeholder="Phone" aria-label="Phone" value="${escapeHtml(r.phone)}"',
        'name="address" placeholder="Address" aria-label="Address"',
        'name="email" placeholder="Email" aria-label="Email" value="${escapeHtml(r.email)}"',
        'name="map_link" placeholder="Map link" aria-label="Map link"',
        'name="seating_capacity" type="number" min="1" placeholder="Seating capacity" aria-label="Seating capacity" value="${r.seating_capacity}"',
        'name="parking_notes" placeholder="Parking notes" aria-label="Parking notes"',
        'name="category" placeholder="Category" aria-label="Category"',
        'name="name" placeholder="Name" aria-label="Menu item name"',
        'name="price" type="number" step="0.01" min="0" placeholder="Price" aria-label="Price"',
        'name="dietary_tags" placeholder="Dietary tags (optional)" aria-label="Dietary tags"',
        'name="description" placeholder="Description (optional)" aria-label="Description"',
        'name="question" placeholder="Question" aria-label="Question"',
        'name="answer" placeholder="Answer" aria-label="Answer"',
        'name="customer_name" placeholder="Customer name" aria-label="Customer name"',
        'name="booking_date" type="date" aria-label="Booking date"',
        'name="booking_time" type="time" aria-label="Booking time"',
        'name="party_size" type="number" min="1" max="20" placeholder="Party size" aria-label="Party size"',
        'name="notes" placeholder="Notes (optional)" aria-label="Notes"',
        'name="primary_language" placeholder="Primary language (e.g. en-GB)" aria-label="Primary language"',
        'name="logo_url" type="url" placeholder="Logo URL (https://...)" aria-label="Logo URL"',
        'id="accent-color-text" placeholder="#7a2e2e" aria-label="Accent color hex code"',
        'name="origin" placeholder="https://www.your-restaurant.com" aria-label="Allowed origin URL"',
        'name="label" placeholder="Label (e.g. restaurant name)" aria-label="Label"',
        'name="restaurant_ids" multiple size="4" aria-label="Restaurants"',
    ]
    for fragment in labeled_fields:
        assert fragment in content, f"expected accessible-label fragment {fragment!r} in admin.html"

    # login-key specifically has both a real aria-label and its
    # pre-existing placeholder (the placeholder alone isn't an
    # accessible name).
    login_key_line = content[content.index('id="login-key"') - 60: content.index('id="login-key"') + 100]
    assert 'aria-label="Admin API key"' in login_key_line

    # The 4 fields that already had a real <label for="..."> must NOT
    # have gained a redundant aria-label alongside it.
    assert 'id="widget-welcome-message"' in content
    welcome_line = content[content.index('id="widget-welcome-message"'): content.index('id="widget-welcome-message"') + 200]
    assert "aria-label" not in welcome_line
    accent_picker_line = content[content.index('id="accent-color-picker"'): content.index('id="accent-color-picker"') + 100]
    assert "aria-label" not in accent_picker_line
