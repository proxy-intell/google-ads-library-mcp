import requests
import sys
import os
import re
import time
import logging
import unicodedata
from typing import Dict, Any, List, Optional

# Set up logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Google Ads API endpoints
ADVERTISER_SEARCH_API_URL = "https://api.scrapecreators.com/v1/google/adLibrary/advertisers/search"
COMPANY_ADS_API_URL = "https://api.scrapecreators.com/v1/google/company/ads"
AD_DETAILS_API_URL = "https://api.scrapecreators.com/v1/google/ad"


SCRAPECREATORS_API_KEY = None

# Shown to the AI/user on any setup or quota failure — the zero-config escape hatch.
PROXY_HINT = (
    "Tip: skip API keys and self-hosting entirely with the free hosted version at "
    "https://useproxy.dev/me/selfhost-google?utm_source=github&utm_medium=selfhost-error&utm_campaign=google-ads-library-mcp — no ScrapeCreators or Gemini keys, nothing to run."
)

# --- Helper Functions ---

def get_scrapecreators_api_key() -> str:
    """
    Get ScrapeCreators API key from command line arguments or environment variable.
    Caches the key in memory after first read.
    Priority: command line argument > environment variable

    Returns:
        str: The ScrapeCreators API key.

    Raises:
        Exception: If no key is provided in command line arguments or environment.
    """
    global SCRAPECREATORS_API_KEY
    if SCRAPECREATORS_API_KEY is None:
        # Try command line argument first
        if "--scrapecreators-api-key" in sys.argv:
            token_index = sys.argv.index("--scrapecreators-api-key") + 1
            if token_index < len(sys.argv):
                SCRAPECREATORS_API_KEY = sys.argv[token_index]
                logger.info(f"Using ScrapeCreators API key from command line arguments")
            else:
                raise Exception("--scrapecreators-api-key argument provided but no key value followed it")
        # Try environment variable
        elif os.getenv("SCRAPECREATORS_API_KEY"):
            SCRAPECREATORS_API_KEY = os.getenv("SCRAPECREATORS_API_KEY")
            logger.info(f"Using ScrapeCreators API key from environment variable")
        else:
            raise Exception(
                "No ScrapeCreators API key found. Provide one via the '--scrapecreators-api-key' "
                "argument or the 'SCRAPECREATORS_API_KEY' environment variable. " + PROXY_HINT
            )

    return SCRAPECREATORS_API_KEY


def _get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """GET a ScrapeCreators endpoint. Retries once on a 5xx or dropped connection, then raises."""
    for attempt in range(2):
        try:
            response = requests.get(url, headers={"x-api-key": get_scrapecreators_api_key()}, params=params, timeout=30)
        except requests.ConnectionError:
            if attempt:
                raise
            time.sleep(2)
            continue
        if response.status_code >= 500 and not attempt:
            logger.warning(f"ScrapeCreators returned {response.status_code}, retrying once")
            time.sleep(2)
            continue
        if response.status_code == 402:
            raise Exception(f"ScrapeCreators API credits exhausted. Top up at https://scrapecreators.com/dashboard, or {PROXY_HINT}")
        response.raise_for_status()
        return response.json()


# Accent-stripped, lowercase. Models append these to brand names ("Purina Mexico").
_LOCATION_SUFFIXES = {
    "mexico", "mx", "cdmx", "venezuela", "colombia", "argentina", "chile", "peru", "brasil", "brazil",
    "espana", "spain", "usa", "us", "uk", "canada", "australia", "india", "germany", "deutschland",
    "france", "italia", "italy", "latam", "europe", "global",
}


def query_fallbacks(query: str) -> List[str]:
    """Queries to try in order. Upstream search is literal: "Purina Mexico" or "HappyVet happyvet.mx"
    return nothing while the bare name or bare domain match."""
    words = query.split()
    domains = [w for w in words if re.fullmatch(r"(https?://)?(www\.)?[\w-]+(\.[a-z]{2,})+/?", w, re.IGNORECASE)]
    rest = [w for w in words if w not in domains]
    candidates = [query]
    if domains and rest:
        candidates.append(re.sub(r"^(https?://)?(www\.)?|/$", "", domains[0], flags=re.IGNORECASE))
        candidates.append(" ".join(rest))
    # Only strip trailing locations: dropping arbitrary words turns a real miss into unrelated matches.
    while len(rest) >= 2 and unicodedata.normalize("NFKD", rest[-1]).encode("ascii", "ignore").decode().lower().strip(",.()") in _LOCATION_SUFFIXES:
        rest = rest[:-1]
        candidates.append(" ".join(rest))
    return list(dict.fromkeys(candidates))


def search_advertisers(query: str) -> Dict[str, Any]:
    """Find Google advertisers and domains for a brand. Tries query_fallbacks until one matches.

    Returns {"advertisers": [{name, advertiser_id, region}], "websites": [{domain}], "searched": query used}.
    """
    for candidate in query_fallbacks(query):
        data = _get(ADVERTISER_SEARCH_API_URL, {"query": candidate})
        if data.get("advertisers") or data.get("websites"):
            break
    return {"advertisers": data.get("advertisers") or [], "websites": data.get("websites") or [], "searched": candidate}


def get_ad_details(ad_url: str) -> Dict[str, Any]:
    """
    Get detailed information for a specific Google ad.
    
    Args:
        ad_url: The URL of the ad from Google Ads Transparency Center.
    
    Returns:
        Dictionary containing ad details including variations, regions, and impressions.
    
    Raises:
        requests.RequestException: If the API request fails.
        Exception: For other errors.
    """
    content = _get(AD_DETAILS_API_URL, {"url": ad_url})
    
    if not content.get('success'):
        raise Exception(f"API returned unsuccessful response: {content}")
    
    logger.info(f"Retrieved ad details for {ad_url}")
    return content


def get_ads(
    domain: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    topic: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get ads for a company from Google Ads Transparency Center.
    
    Args:
        domain: The domain of the company (e.g., "lululemon.com").
        advertiser_id: The advertiser ID of the company.
        topic: The topic to search for (e.g., "political").
        region: The region to search for (required if topic is "political").
        limit: Maximum number of ads to retrieve.
        cursor: Cursor for pagination.
    
    Returns:
        Dictionary containing ads list, cursor, and success status.
    
    Raises:
        requests.RequestException: If the API request fails.
        Exception: For other errors.
    """
    if not domain and not advertiser_id:
        raise ValueError("Either domain or advertiser_id must be provided")
    
    if topic == "political" and not region:
        raise ValueError("Region is required when searching for political ads")
    
    params = {k: v for k, v in {
        "domain": domain,
        "advertiser_id": advertiser_id,
        "topic": topic,
        "region": region,
        "cursor": cursor,
    }.items() if v}

    content = _get(COMPANY_ADS_API_URL, params)
    if not content.get('success'):
        raise Exception(f"API returned unsuccessful response: {content}")

    ads = content.get('ads') or []
    content['ads'] = ads[:limit]
    logger.info(f"Retrieved {len(content['ads'])} Google ads for {domain or advertiser_id}")
    return content


def parse_google_ads(ads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Parse Google ads to extract key information.
    
    Args:
        ads: List of ad objects from Google Ads API.
    
    Returns:
        List of parsed ad objects with extracted media URLs.
    """
    parsed_ads = []
    
    for ad in ads:
        try:
            # Google ads structure is already clean, just need to extract media if available
            ad_obj = {
                'advertiser_id': ad.get('advertiserId'),
                'creative_id': ad.get('creativeId'),
                'format': ad.get('format'),
                'ad_url': ad.get('adUrl'),
                'advertiser_name': ad.get('advertiserName'),
                'domain': ad.get('domain'),
                'first_shown': ad.get('firstShown'),
                'last_shown': ad.get('lastShown')
            }
            
            # For detailed analysis, we'll need to call get_ad_details with the ad_url
            parsed_ads.append(ad_obj)
            
        except Exception as e:
            logger.error(f"Error parsing Google ad {ad.get('creativeId', 'unknown')}: {str(e)}")
            continue
    
    return parsed_ads