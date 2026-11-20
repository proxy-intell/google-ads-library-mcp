# Changelog

v1.1.0 · Nov 20 · Google

## Look up any brand, and watch YouTube ads

You used to need the advertiser ID before you could see a single ad. Now a brand name is enough, and video ads finally get analysed.

### new
- **Find a brand by name:** `get_google_platform_id` turns "Purina" or "purina.com.mx" into advertiser IDs and domains. Pass a list to look up several at once. It also tidies queries like "Purina Mexico" for you.
- **See what's new since your last pull:** fetch a brand again a day or more later and the result lists its new ads and how many stopped. Ads first shown in the last 7 days show up on the first pull too.
- **Playbooks:** competitor teardown, prospect lists and a weekly competitor watch, in your client's prompt menu or via `get_playbook`.

### fixed
- **Video ads can be analysed:** Google video ads only come with a YouTube link, which the tool couldn't download. Gemini now reads the YouTube URL directly.
- **Video analysis on a current model:** moved off Google's deprecated SDK, and off a retired model. `GEMINI_MODEL` in `.env` switches it.
- **Image analysis your AI can see:** `analyze_ad_image` now returns the image, not base64 text.
- **Fewer flaky lookups:** server errors get one retry, and a failed lookup says it failed instead of returning nothing.

Upgrade: `git pull && ./venv/bin/pip install -r requirements.txt`, then restart your MCP client.

Also self-hosted: [Facebook](https://github.com/proxy-intell/facebook-ads-library-mcp), [LinkedIn](https://github.com/proxy-intell/linkedin-ads-library-mcp), [TikTok](https://github.com/proxy-intell/tiktok-ads-library-mcp). Or all four in one connection, no keys: [Proxy](https://useproxy.dev/me/selfhost-google?utm_source=github&utm_medium=release&utm_campaign=google-ads-library-mcp).
