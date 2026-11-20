"""Analysis playbooks: step-by-step methods for common ad research jobs.

Served two ways: as MCP prompts (the host's prompt menu) and through the get_playbook tool,
so the model can load one itself. Adapted from hosted Proxy's playbooks to this server's tools.
"""

OTHER_PLATFORMS = (
    "This server covers Google only. For Meta, LinkedIn or TikTok, add the sibling self-hosted servers "
    "(github.com/proxy-intell/facebook-ads-library-mcp, linkedin-ads-library-mcp, tiktok-ads-library-mcp) "
    "or hosted Proxy, which has all four plus competitor discovery: "
    "https://useproxy.dev/me/selfhost-google?utm_source=github&utm_medium=playbook&utm_campaign=google-ads-library-mcp"
)

RULES = """\
Rules:
- Ask at most one short question, only if a missing input blocks the work; otherwise state assumptions and go.
- Cite each ad with the adUrl its tool output carries. Never build ad URLs.
- Run time (firstShown to lastShown) is a proxy for performance, not proof. Say "long-running", not "winning". Mark inference ("likely", "suggests").
- A failed lookup is not "no ads": report the error the tool returned.
- End with the deliverables, ready to use. No recap."""

TRIGGERS = {
    "competitor_teardown": "competitors' Google Search, Display and YouTube ads: hooks, offers, angles and positioning",
    "who_is_advertising": "which businesses or prospects run Google ads; prospect lists for agencies and sellers",
    "competitor_watch": "tracking competitors' Google ads, what's new since last time, weekly updates",
}

_BODIES = {
    "competitor_teardown": """\
PLAYBOOK: competitor_teardown. How competitors sell on Google: angles, offers, formats, gaps.
Inputs: competitors (names or domains), the user's own brand if any, region if named.

Method:
1. get_google_platform_id(brand_name=["A", "B", "C"]) in one call. Prefer the advertiser whose region matches the user's market; note both advertiser_id and domain.
2. Per brand: get_google_ads(domain="<domain>", region="<XX>", limit=30). If a domain returns nothing, retry with advertiser_id="<advertiser_id>".
3. Run time per ad = firstShown to lastShown. Pick the 3-5 longest-running per brand and call get_google_ad_details(ad_url="<adUrl>") for headlines, descriptions, images and regions.
4. Tag every detailed ad: angle (pain, aspiration, social proof, us-vs-them, mechanism, price, urgency, authority), offer (discount, trial, free shipping, guarantee, none), format (text, image, video).
5. Image variations: analyze_ad_image(media_url="<imageUrl>"). Video variations carry a youtubeUrl: cite it; analyze_ad_video needs a direct video file, not a YouTube page.

Look for: headlines a brand keeps running for months versus ones it tests and kills; one angle across several brands (proven but crowded); offers only in new ads; claims nobody makes; regions only one brand targets.

Output:
- Angle x brand table: ad count and longest run in days.
- Long-running ads: brand, headline, angle, offer, format, run time, link.
- Format mix per brand.
- Gaps: angles, offers, formats nobody runs.
- 5 angles to test, one line each, tied to an ad or a gap.""",

    "who_is_advertising": """\
PLAYBOOK: who_is_advertising. Which businesses on a list run Google ads, and an opener for each.
Inputs: names or domains. Domains match better than names.

Method:
1. get_google_platform_id(brand_name=["biz1.com", "biz2.com"]) in one call. Pick the advertiser that matches the city or domain; say when none does.
2. get_google_ads(domain="<domain>", limit=20) per business found. An advertiser match is not proof of current ads: "yes" needs an ad with lastShown in the past week.
3. Per advertiser: format mix, newest ad (latest firstShown), oldest still running.
4. Read 1-2 ads per advertiser with get_google_ad_details(ad_url="<adUrl>") for one factual opener.

Status per business: yes / no (lookup worked, nothing recent) / not found (no advertiser matched) / error (retry once, then report). Not found and error are never "no".

Output:
- Table: business | Google status | ads fetched | newest ad | format mix | pitch opener with ad link.
- Top 5 to contact first, one line why each.""",

    "competitor_watch": """\
PLAYBOOK: competitor_watch. What competitors launched, dropped and kept on Google since last time.
How the diff works: get_google_ads remembers each advertiser's last fetch (same domain or advertiser_id, region, topic and limit). A re-fetch 24h+ later returns changes.new_since_last_fetch (creative ids) and changes.dropped_since_last_fetch; changes.launched_last_7d lists ads first shown in the last 7 days, even on a first run. The first run is the baseline: say so and report the last-7-day launches.

Method:
1. Per brand: get_google_ads(domain="<domain>") with the same region and limit as last time.
2. Per brand: new (ids), dropped (count only), still running (the rest; note long-runners that survived). "Dropped" can also mean an ad fell outside the limit: say so when the fetch hit it.
3. get_google_ad_details(ad_url="<adUrl>") on the new ads only, for headlines, landing pages and regions.

Look for: a new angle or offer; a burst of launches (refresh, scaling, a promo); a mass drop (strategy shift, season end); new regions or landing pages (new product or market); the same move from several brands at once.

Then offer a weekly scheduled task in the user's assistant with a prompt like "Run the competitor_watch playbook for A, B and C with the same settings."

Output per brand: launched (count, links), dropped (count), still running (count, longest-runner), what it suggests (inference). Close with 3 lines: what to respond to and what to ignore.""",
}

PLAYBOOKS = {job: f"{body}\n\n{RULES}\n\n{OTHER_PLATFORMS}" for job, body in _BODIES.items()}
