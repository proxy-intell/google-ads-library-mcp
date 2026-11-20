from dotenv import load_dotenv

# Load .env before the services read their settings (GEMINI_MODEL, API keys).
load_dotenv()

from src.telemetry import TrackedFastMCP
from src.services.scrapecreators_service import get_ads, get_ad_details, get_scrapecreators_api_key, search_advertisers
from src.services.media_cache_service import media_cache, image_cache  # Keep image_cache for backward compatibility
from src.services.gemini_service import GEMINI_MODEL, configure_gemini, upload_video_to_gemini, analyze_video_with_gemini, cleanup_gemini_file
from src import ad_history
from src.playbooks import PLAYBOOKS, TRIGGERS
from mcp.server.fastmcp import Image
from typing import Dict, Any, List, Optional, Union
import json
import requests
import os


INSTRUCTIONS = """
This server provides access to Google's Ads Transparency Center (Search, Display, YouTube) through the ScrapeCreators API.

Workflow:
1. get_google_platform_id finds a brand's advertiser IDs and domains (pass the bare name or bare domain).
2. get_google_ads returns an advertiser's ads by domain or advertiser_id.
3. get_google_ad_details on an adUrl gives headlines, descriptions, image URLs and regions.
4. analyze_ad_image / analyze_ad_video for creative analysis.

Re-running get_google_ads for an advertiser 24h+ later reports which ads are new and how many
dropped out since the last fetch. For a research job (competitor teardown, prospect lists,
weekly competitor tracking) call get_playbook first and follow it.
"""

IMAGE_ANALYSIS_PROMPT = """Analyze this ad image and extract:
- Overall: what is shown, the story or message, emotional tone.
- Text: transcribe ALL text; tag each as Headline Hook, Value Proposition, CTA, Disclaimer, Brand Name or Other.
- People: age range, gender, appearance, pose, expression, setting.
- Brand: logos (position), products shown, brand colors.
- Composition: layout, visual hierarchy (first, second, third), text overlay vs separate text.
- Style: dominant colors, background, photography style (studio, UGC, lifestyle), filters.
- Audience cues: who this targets and why.
Report facts first; mark inference as such."""


mcp = TrackedFastMCP(
   name="Google Ads Library",
   instructions=INSTRUCTIONS
)


@mcp.tool(
  description="Find a brand's Google advertiser IDs and domains in the Google Ads Transparency Center: the first step before get_google_ads. Pass the bare brand name (\"Purina\") or the bare domain (\"purina.com.mx\"), never both and never a country or city. Results span all countries; pick the advertiser whose region matches the user's market. Several brands: pass one list.",
  annotations={
    "title": "Search Google Advertisers",
    "readOnlyHint": True,
    "openWorldHint": True
  }
)
def get_google_platform_id(brand_name: Union[str, List[str]]) -> Dict[str, Any]:
    """Search Google Ads Transparency Center for advertisers matching a brand name or domain.

    Args:
        brand_name: Bare brand name or bare domain, or a list of them.

    Returns:
        success, message, results ({brand: {advertisers: [{name, advertiser_id, region}], websites: [{domain}], searched}}),
        total_results, error. Call get_google_ads with advertiser_id and/or domain: they can return different accounts.
    """
    names = list(dict.fromkeys(n.strip() for n in ([brand_name] if isinstance(brand_name, str) else brand_name or []) if n and str(n).strip()))
    if not names:
        return {"success": False, "message": "brand_name must be a non-empty name or domain, or a list of them.", "results": {}, "total_results": 0, "error": "Missing brand name"}

    results, notes, total = {}, [], 0
    for name in names:
        try:
            found = search_advertisers(name)
        except Exception as e:
            # Keep the error visible: an empty result would read as "not on Google".
            results[name] = {"error": str(e)}
            notes.append(f"'{name}': lookup failed (not confirmed absent): {e}")
            continue
        n = len(found["advertisers"]) + len(found["websites"])
        total += n
        results[name] = found
        if n == 0:
            notes.append(f"'{name}': no advertisers or domains matched. Retry with the bare brand name or bare domain.")
        elif found["searched"] != name:
            notes.append(f"'{name}': no exact match; results are for '{found['searched']}'. Check they are the intended brand.")
    failed = sum("error" in r for r in results.values())
    return {
        "success": failed < len(names),
        "message": f"Found {total} advertiser(s)/domain(s) for {len(names)} brand(s)." + (" " + " ".join(notes) if notes else ""),
        "results": results,
        "total_results": total,
        "ad_transparency_url": "https://adstransparency.google.com/",
        "error": None if failed < len(names) else "All lookups failed",
    }


@mcp.tool(
  description="Retrieve currently running ads for a company from Google Ads Transparency Center. Use this tool to get ads for a company using their domain (e.g., 'nike.com') or advertiser ID. You can filter by topic (including political ads) and region. The result's changes block lists ads first shown in the last 7 days and, when the same advertiser was fetched with the same filters 24h+ ago, which ads are new since then and how many dropped out. For complete analysis of visual elements, colors, design, or image content, you MUST also use analyze_ad_image on the imageUrl from each ad's details.",
  annotations={
    "title": "Get Google Ads for Company",
    "readOnlyHint": True,
    "openWorldHint": True
  }
)
def get_google_ads(
    domain: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    topic: Optional[str] = None,
    region: Optional[str] = None,
    limit: Optional[int] = 50,
    cursor: Optional[str] = None
) -> Dict[str, Any]:
    """Retrieve currently running ads for a company from Google Ads Transparency Center.
    
    This endpoint fetches active advertisements from Google Ads Transparency Center for the specified company.
    You must provide either a domain or advertiser_id. It supports pagination and filtering by topic and region.
    
    Args:
        domain: The domain of the company (e.g., "lululemon.com", "nike.com").
        advertiser_id: The Google advertiser ID (e.g., "AR01614014350098432001").
        topic: Optional topic filter (e.g., "political"). If "political", region is required.
        region: Optional region code (e.g., "US", "CA", "GB"). Required for political ads.
        limit: Maximum number of ads to retrieve (default: 50).
        cursor: Pagination cursor from previous response.
    
    Returns:
        A dictionary containing:
        - success: Boolean indicating if the ads were retrieved successfully
        - message: Status message describing the result
        - ads: List of ad objects with advertiserId, creativeId, format, adUrl, etc.
        - cursor: Pagination cursor for retrieving additional ads
        - statusCode: HTTP status code from the API
        - error: Error details if the retrieval failed
    """
    if not domain and not advertiser_id:
        return {
            "success": False,
            "message": "Either domain or advertiser_id must be provided.",
            "ads": [],
            "cursor": None,
            "statusCode": 400,
            "error": "Missing required parameter: domain or advertiser_id"
        }
    
    if topic == "political" and not region:
        return {
            "success": False,
            "message": "Region is required when searching for political ads.",
            "ads": [],
            "cursor": None,
            "statusCode": 400,
            "error": "Missing required parameter: region (required for political topic)"
        }
    
    try:
        # Get API key first
        get_scrapecreators_api_key()
        
        # Fetch ads
        result = get_ads(
            domain=domain,
            advertiser_id=advertiser_id,
            topic=topic,
            region=region,
            limit=limit or 50,
            cursor=cursor
        )
        
        ads = result.get('ads', [])
        # Pagination pages are not a new fetch: only first pages update the history.
        changes = None if cursor else ad_history.changes(
            f"google:{domain}:{advertiser_id}:{topic}:{region}:{limit}",
            [{"ad_id": a.get("creativeId") or a.get("adUrl"), "start_date": a.get("firstShown")} for a in ads],
        )
        
        if not ads:
            identifier = domain or advertiser_id
            return {
                "success": True,
                "message": f"No current ads found for '{identifier}' in Google Ads Transparency Center.",
                "ads": [],
                "changes": changes,
                "cursor": None,
                "statusCode": result.get('statusCode', 200),
                "error": None
            }
        
        identifier = domain or advertiser_id
        return {
            "success": True,
            "message": f"Successfully retrieved {len(ads)} ads for '{identifier}' from Google Ads Transparency Center.",
            "ads": ads,
            "changes": changes,
            "cursor": result.get('cursor'),
            "statusCode": result.get('statusCode', 200),
            "ad_transparency_url": "https://adstransparency.google.com/",
            "source_citation": f"[Google Ads Transparency Center - {identifier}](https://adstransparency.google.com/)",
            "error": None
        }
        
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "message": f"Network error while retrieving ads: {str(e)}",
            "ads": [],
            "cursor": None,
            "statusCode": 500,
            "error": f"Network error: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to retrieve ads: {str(e)}",
            "ads": [],
            "cursor": None,
            "statusCode": 500,
            "error": str(e)
        }


@mcp.tool(
  description="Get detailed information about a specific Google ad including variations, regional stats, and impressions. Use this tool with the adUrl from get_google_ads to retrieve full ad details including all text variations, image URLs, headlines, and descriptions. Essential for analyzing ad content and extracting media URLs for visual analysis.",
  annotations={
    "title": "Get Google Ad Details",
    "readOnlyHint": True,
    "openWorldHint": True
  }
)
def get_google_ad_details(ad_url: str) -> Dict[str, Any]:
    """Get detailed information about a specific Google ad.
    
    This endpoint retrieves comprehensive details about a specific ad from Google Ads Transparency Center,
    including all variations, regional statistics, and impression data. The ad_url should be obtained
    from the get_google_ads tool.
    
    Args:
        ad_url: The URL of the ad from Google Ads Transparency Center.
                Example: "https://adstransparency.google.com/advertiser/AR.../creative/CR..."
    
    Returns:
        A dictionary containing:
        - success: Boolean indicating if the details were retrieved successfully
        - advertiserId: The advertiser's ID
        - creativeId: The creative's ID
        - format: Ad format (text, image, video)
        - firstShown: When the ad was first shown
        - lastShown: When the ad was last shown
        - variations: List of ad variations with headlines, descriptions, and image URLs
        - creativeRegions: List of regions where the ad is shown
        - regionStats: Detailed statistics by region
        - error: Error details if the retrieval failed
    """
    if not ad_url or not ad_url.strip():
        return {
            "success": False,
            "message": "Ad URL must be provided and cannot be empty.",
            "error": "Missing or empty ad URL"
        }
    
    try:
        # Get API key first
        get_scrapecreators_api_key()
        
        # Fetch ad details
        details = get_ad_details(ad_url.strip())
        
        # Extract key information for easier access
        result = {
            "success": details.get('success', False),
            "advertiserId": details.get('advertiserId'),
            "creativeId": details.get('creativeId'),
            "format": details.get('format'),
            "firstShown": details.get('firstShown'),
            "lastShown": details.get('lastShown'),
            "overallImpressions": details.get('overallImpressions'),
            "creativeRegions": details.get('creativeRegions', []),
            "regionStats": details.get('regionStats', []),
            "variations": details.get('variations', []),
            "ad_url": ad_url,
            "ad_transparency_url": "https://adstransparency.google.com/",
            "source_citation": f"[Google Ad Details]({ad_url})",
            "error": None
        }
        
        # Add message based on variations found
        variations_count = len(result.get('variations', []))
        if variations_count > 0:
            result["message"] = f"Successfully retrieved ad details with {variations_count} variation(s)."
        else:
            result["message"] = "Ad details retrieved but no variations found."
        
        return result
        
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "message": f"Network error while retrieving ad details: {str(e)}",
            "error": f"Network error: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to retrieve ad details: {str(e)}",
            "error": str(e)
        }


@mcp.tool(
  description="Analyze a Google ad image: returns the image itself so you can see it, plus a checklist (text, hooks, people, brand elements, composition, colors). Pass one imageUrl from a get_google_ad_details variation. Downloads are cached locally.",
  annotations={
    "title": "Analyze Ad Image Content",
    "readOnlyHint": True,
    "openWorldHint": True
  }
)
def analyze_ad_image(media_url: str, brand_name: Optional[str] = None, ad_id: Optional[str] = None) -> Any:
    """Download a Google ad image and hand it to the model for visual analysis.

    The image comes back as an image content block, so the model sees it with its own vision,
    plus a short checklist of what to extract. Downloads are cached locally.

    Args:
        media_url: An imageUrl from a get_google_ad_details variation.
        brand_name: Optional brand name for cache organization.
        ad_id: Optional creative ID for tracking.
    """
    if not media_url or not media_url.strip():
        return {"success": False, "message": "media_url must be provided and cannot be empty.", "error": "Missing media URL"}
    media_url = media_url.strip()
    try:
        cached = image_cache.get_cached_image(media_url)
        image_bytes, content_type = None, None
        if cached:
            try:
                with open(cached['file_path'], 'rb') as f:
                    image_bytes = f.read()
                content_type = cached['content_type']
            except OSError:
                cached = None  # cache file gone, download again
        if not cached:
            response = requests.get(media_url, timeout=30)
            response.raise_for_status()
            content_type = response.headers.get('content-type', '').lower()
            if not content_type.startswith('image/'):
                return {"success": False, "message": f"URL does not point to an image (content type: {content_type}).", "error": f"Invalid content type: {content_type}"}
            image_cache.cache_image(url=media_url, image_data=response.content, content_type=content_type, brand_name=brand_name, ad_id=ad_id)
            image_bytes = response.content

        info = {
            "success": True,
            "media_url": media_url,
            "brand_name": brand_name,
            "ad_id": ad_id,
            "ad_transparency_url": "https://adstransparency.google.com/",
            "cache_status": "Used cached image" if cached else "Downloaded and cached new image",
            "analysis_instructions": IMAGE_ANALYSIS_PROMPT,
        }
        fmt = content_type.split('/')[-1].split(';')[0].replace('jpg', 'jpeg')
        return [json.dumps(info), Image(data=image_bytes, format=fmt)]
    except requests.exceptions.RequestException as e:
        return {"success": False, "message": f"Failed to download image from {media_url}: {str(e)}", "error": f"Network error: {str(e)}"}
    except Exception as e:
        return {"success": False, "message": f"Failed to process image from {media_url}: {str(e)}", "error": str(e)}


@mcp.tool(
  description="REQUIRED for checking media cache status and storage usage. Use this tool when users ask about cache statistics, storage space used by cached media (images and videos), or how many files have been analyzed and cached. Essential for cache management and monitoring.",
  annotations={
    "title": "Get Media Cache Statistics",
    "readOnlyHint": True,
    "openWorldHint": False
  }
)
def get_cache_stats() -> Dict[str, Any]:
    """Get comprehensive statistics about the media cache (images and videos).
    
    Returns:
        A dictionary containing:
        - success: Boolean indicating if stats were retrieved successfully
        - message: Status message
        - stats: Cache statistics including:
            * total_files: Total number of cached files
            * total_images: Number of cached images
            * total_videos: Number of cached videos
            * total_size_mb/gb: Storage space used
            * analyzed_files: Number of files with cached analysis
            * unique_brands: Number of different brands cached
        - error: Error details if retrieval failed
    """
    try:
        stats = media_cache.get_cache_stats()
        
        total_files = stats.get('total_files', 0)
        total_images = stats.get('total_images', 0)
        total_videos = stats.get('total_videos', 0)
        
        return {
            "success": True,
            "message": f"Cache contains {total_files} files ({total_images} images, {total_videos} videos) using {stats.get('total_size_gb', 0)}GB storage",
            "stats": stats,
            "error": None
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to retrieve cache statistics: {str(e)}",
            "stats": {},
            "error": str(e)
        }


@mcp.tool(
  description="REQUIRED for finding previously analyzed ad media (images and videos) in cache. Use this tool when users want to search for cached media by brand name, find media with people, search by colors, or filter by media type. Essential for retrieving past analysis results without re-downloading media.",
  annotations={
    "title": "Search Cached Media",
    "readOnlyHint": True,
    "openWorldHint": True
  }
)
def search_cached_media(
    brand_name: Optional[str] = None,
    has_people: Optional[bool] = None,
    color_contains: Optional[str] = None,
    media_type: Optional[str] = None,
    limit: Optional[int] = 20
) -> Dict[str, Any]:
    """Search cached media (images and videos) by various criteria.
    
    Args:
        brand_name: Filter by exact brand name match
        has_people: Filter by presence of people in media (True/False)
        color_contains: Filter by dominant color (partial match, e.g., "red", "blue")
        media_type: Filter by media type ('image' or 'video')
        limit: Maximum number of results to return (default: 20)
    
    Returns:
        A dictionary containing:
        - success: Boolean indicating if search was successful
        - message: Status message
        - results: List of matching cached media with metadata
        - count: Number of results returned
        - error: Error details if search failed
    """
    try:
        results = media_cache.search_cached_media(
            brand_name=brand_name,
            has_people=has_people,
            color_contains=color_contains,
            media_type=media_type
        )
        
        # Limit results
        if limit and len(results) > limit:
            results = results[:limit]
        
        # Remove large base64 data from results for cleaner output
        clean_results = []
        for result in results:
            clean_result = result.copy()
            if 'analysis_results' in clean_result and clean_result['analysis_results']:
                # Keep analysis but remove any base64 image data if present
                analysis = clean_result['analysis_results'].copy()
                if 'image_data_base64' in analysis:
                    analysis['image_data_base64'] = "[Image data available]"
                clean_result['analysis_results'] = analysis
            clean_results.append(clean_result)
        
        search_criteria = []
        if brand_name:
            search_criteria.append(f"brand: {brand_name}")
        if has_people is not None:
            search_criteria.append(f"has_people: {has_people}")
        if color_contains:
            search_criteria.append(f"color: {color_contains}")
        if media_type:
            search_criteria.append(f"media_type: {media_type}")
        
        criteria_str = ", ".join(search_criteria) if search_criteria else "no filters"
        
        return {
            "success": True,
            "message": f"Found {len(clean_results)} cached media files matching criteria: {criteria_str}",
            "results": clean_results,
            "count": len(clean_results),
            "error": None
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to search cached images: {str(e)}",
            "results": [],
            "count": 0,
            "error": str(e)
        }


@mcp.tool(
  description="REQUIRED for cleaning up old cached media files (images and videos) and freeing disk space. Use this tool when users want to remove old cached media, clean up storage space, or when cache becomes too large. Essential for cache maintenance and storage management.",
  annotations={
    "title": "Cleanup Media Cache",
    "readOnlyHint": False,
    "openWorldHint": False
  }
)
def cleanup_media_cache(max_age_days: Optional[int] = 30) -> Dict[str, Any]:
    """Clean up old cached media files (images and videos) and database entries.
    
    Args:
        max_age_days: Maximum age in days before media files are deleted (default: 30)
    
    Returns:
        A dictionary containing:
        - success: Boolean indicating if cleanup was successful
        - message: Status message with cleanup results
        - cleanup_stats: Statistics about what was cleaned up
        - error: Error details if cleanup failed
    """
    try:
        # Get stats before cleanup
        stats_before = media_cache.get_cache_stats()
        
        # Perform cleanup
        media_cache.cleanup_old_cache(max_age_days=max_age_days or 30)
        
        # Get stats after cleanup
        stats_after = media_cache.get_cache_stats()
        
        files_removed = stats_before.get('total_files', 0) - stats_after.get('total_files', 0)
        images_removed = stats_before.get('total_images', 0) - stats_after.get('total_images', 0)
        videos_removed = stats_before.get('total_videos', 0) - stats_after.get('total_videos', 0)
        space_freed_mb = stats_before.get('total_size_mb', 0) - stats_after.get('total_size_mb', 0)
        
        return {
            "success": True,
            "message": f"Cleanup completed: removed {files_removed} files ({images_removed} images, {videos_removed} videos), freed {space_freed_mb:.2f}MB",
            "cleanup_stats": {
                "total_files_removed": files_removed,
                "images_removed": images_removed,
                "videos_removed": videos_removed,
                "space_freed_mb": round(space_freed_mb, 2),
                "max_age_days": max_age_days or 30,
                "files_remaining": stats_after.get('total_files', 0),
                "images_remaining": stats_after.get('total_images', 0),
                "videos_remaining": stats_after.get('total_videos', 0),
                "space_remaining_mb": stats_after.get('total_size_mb', 0)
            },
            "error": None
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to cleanup cache: {str(e)}",
            "cleanup_stats": {},
            "error": str(e)
        }


# Backward compatibility aliases
def search_cached_images(brand_name: Optional[str] = None, has_people: Optional[bool] = None, 
                        color_contains: Optional[str] = None, limit: Optional[int] = 20) -> Dict[str, Any]:
    """Search cached images by criteria (backward compatibility)."""
    return search_cached_media(brand_name, has_people, color_contains, 'image', limit)

cleanup_image_cache = cleanup_media_cache


@mcp.tool(
  description="REQUIRED for analyzing video ads from Google. Download and analyze ad videos using Gemini's advanced video understanding capabilities. Extracts visual storytelling, audio elements, pacing, scene transitions, brand messaging, and marketing strategy insights. Uses intelligent caching for efficiency and includes comprehensive video analysis.",
  annotations={
    "title": "Analyze Ad Video Content",
    "readOnlyHint": True,
    "openWorldHint": True
  }
)
def analyze_ad_video(media_url: str, brand_name: Optional[str] = None, ad_id: Optional[str] = None) -> Dict[str, Any]:
    """Download Google ad videos and analyze them using Gemini's video understanding capabilities.
    
    This tool downloads videos from Google Ads Transparency Center URLs and provides comprehensive analysis
    using Google's Gemini AI model. Videos are cached locally to avoid re-downloading, and 
    analysis results are cached to improve performance and reduce API costs.
    
    Args:
        media_url: The direct URL to the Google ad video to analyze.
        brand_name: Optional brand name for cache organization.
        ad_id: Optional ad ID for tracking purposes.
    
    Returns:
        A dictionary containing:
        - success: Boolean indicating if analysis was successful
        - message: Status message
        - cached: Boolean indicating if video was retrieved from cache
        - analysis: Comprehensive video analysis results
        - media_url: Original video URL
        - brand_name: Brand name if provided
        - ad_id: Ad ID if provided
        - cache_status: Information about cache usage
        - error: Error details if analysis failed
    """
    if not media_url or not media_url.strip():
        return {
            "success": False,
            "message": "Media URL must be provided and cannot be empty.",
            "cached": False,
            "analysis": {},
            "cache_info": {},
            "error": "Missing or empty media URL"
        }
    
    try:
        # Check cache first
        cached_data = media_cache.get_cached_media(media_url.strip(), media_type='video')
        
        if cached_data and cached_data.get('analysis_results'):
            # Return cached analysis results
            return {
                "success": True,
                "message": f"Retrieved cached video analysis for {media_url}",
                "cached": True,
                "analysis": cached_data['analysis_results'],
                "cache_info": {
                    "cached_at": cached_data.get('downloaded_at'),
                    "analysis_cached_at": cached_data.get('analysis_cached_at'),
                    "file_size": cached_data.get('file_size'),
                    "brand_name": cached_data.get('brand_name'),
                    "ad_id": cached_data.get('ad_id'),
                    "duration_seconds": cached_data.get('duration_seconds')
                },
                "ad_transparency_url": "https://adstransparency.google.com/",
                "source_citation": f"[Google Ads Transparency Center - {brand_name if brand_name else 'Ad'} #{ad_id if ad_id else 'Unknown'}]({media_url})",
                "error": None
            }
        
        # Download video if not cached or no analysis available
        video_path = None
        file_size = None
        duration_seconds = None
        
        if cached_data:
            # Video is cached but no analysis results yet
            video_path = cached_data['file_path']
            file_size = cached_data['file_size']
            duration_seconds = cached_data.get('duration_seconds')
        else:
            # Download the video
            response = requests.get(media_url.strip(), timeout=60)  # Longer timeout for videos
            response.raise_for_status()
            
            # Check if it's a video
            content_type = response.headers.get('content-type', '').lower()
            if not any(vid_type in content_type for vid_type in ['video/', 'mp4', 'mov', 'webm', 'avi']):
                return {
                    "success": False,
                    "message": f"URL does not point to a valid video. Content type: {content_type}",
                    "cached": False,
                    "analysis": {},
                    "cache_info": {},
                    "error": f"Invalid content type: {content_type}"
                }
            
            # Cache the downloaded video
            file_path = media_cache.cache_media(
                url=media_url.strip(),
                media_data=response.content,
                content_type=content_type,
                media_type='video',
                brand_name=brand_name,
                ad_id=ad_id
            )
            
            video_path = file_path
            file_size = len(response.content)
        
        # Configure Gemini API
        try:
            model = configure_gemini()
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to configure Gemini API: {str(e)}. Ensure --gemini-api-key is provided or GEMINI_API_KEY environment variable is set.",
                "cached": bool(cached_data),
                "analysis": {},
                "cache_info": {},
                "error": f"Gemini configuration error: {str(e)}"
            }
        
        # Create structured video analysis prompt based on user requirements
        analysis_prompt = """
Analyze this Google ad video and provide a comprehensive, structured breakdown following this exact format:

**SCENE ANALYSIS:**
Analyze the video at a scene-by-scene level. For each identified scene, provide:

Scene [Number]: [Brief scene title]
1. Visual Description:
   - Detailed description of key visuals within the scene
   - Appearance and demographics of featured individuals (age, gender, notable characteristics)
   - Specific camera angles and movements used

2. Text Elements:
   - Document ALL text elements appearing in the scene
   - Categorize each text element as:
     * "Text Hook" (introductory text designed to grab attention)
     * "CTA (middle)" (call-to-action appearing mid-video)
     * "CTA (end)" (final call-to-action)

3. Brand Elements:
   - Note any visible brand logos or product placements
   - Provide brief descriptions and specific timing within the scene

4. Audio Analysis:
   - Transcription or detailed summary of any voiceover present
   - Describe voiceover characteristics: tone, pitch, conveyed emotions
   - Identify and briefly describe notable sound effects

5. Music Analysis:
   - Music present: [true/false]
   - If true: Brief description or identification of music style/track

6. Scene Transition:
   - Describe the style and pacing of transition to next scene (quick cuts, fades, dynamic transitions, etc.)

**OVERALL VIDEO ANALYSIS:**

**Ad Format:**
- Identify the specific ad format (single video, carousel, story, etc.)
- Aspect ratio and orientation
- Duration and pacing style

**Notable Angles:**
- List all significant camera angles used throughout the video
- Comment on their effectiveness and purpose

**Overall Messaging:**
- Primary message or value proposition
- Secondary messages or supporting points
- Target audience indicators

**Hook Analysis:**
- Primary hook type: Text, Visual, or VoiceOver
- Description of the hook and its placement
- Effectiveness assessment of attention-grabbing elements

Provide detailed, factual observations that would help understand the video's marketing strategy and effectiveness. Focus on specific, actionable insights.
"""
        
        # Upload video to Gemini and analyze
        gemini_file = None
        try:
            # Upload video to Gemini File API
            gemini_file = upload_video_to_gemini(model, video_path)
            
            # Analyze video with Gemini
            analysis_text = analyze_video_with_gemini(model, gemini_file, analysis_prompt)
            
            # Structure the analysis results
            analysis_results = {
                "raw_analysis": analysis_text,
                "analysis_timestamp": media_cache._generate_url_hash(str(hash(analysis_text))),
                "model_used": GEMINI_MODEL,
                "video_metadata": {
                    "file_size_mb": round(file_size / (1024 * 1024), 2) if file_size else None,
                    "duration_seconds": duration_seconds,
                    "content_type": cached_data.get('content_type') if cached_data else response.headers.get('content-type')
                }
            }
            
            # Cache analysis results
            media_cache.update_analysis_results(media_url.strip(), analysis_results)
            
            # Cleanup Gemini file to save storage
            if gemini_file:
                cleanup_gemini_file(model, gemini_file.name)
            
            return {
                "success": True,
                "message": f"Video analysis completed successfully",
                "cached": bool(cached_data),
                "analysis": analysis_results,
                "media_url": media_url,
                "brand_name": brand_name,
                "ad_id": ad_id,
                "cache_status": "Used cached video" if cached_data else "Downloaded and cached new video",
                "ad_transparency_url": "https://adstransparency.google.com/",
                "source_citation": f"[Google Ads Transparency Center - {brand_name if brand_name else 'Ad'} #{ad_id if ad_id else 'Unknown'}]({media_url})",
                "error": None
            }
            
        except Exception as e:
            # Cleanup Gemini file in case of error
            if gemini_file:
                try:
                    cleanup_gemini_file(model, gemini_file.name)
                except:
                    pass
            raise e
        
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "message": f"Failed to download video from {media_url}: {str(e)}",
            "cached": False,
            "analysis": {},
            "cache_info": {},
            "error": f"Network error: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to analyze video from {media_url}: {str(e)}",
            "cached": bool(cached_data) if 'cached_data' in locals() else False,
            "analysis": {},
            "cache_info": {},
            "error": str(e)
        }


@mcp.tool(
  description="Load a playbook: the step-by-step method for an ad research job (which tool calls, what to look for, output format). Call it BEFORE starting when the request matches a job, then follow it. Jobs:\n" + "\n".join(f"- {job}: {trigger}" for job, trigger in TRIGGERS.items()),
  annotations={
    "title": "Get Analysis Playbook",
    "readOnlyHint": True,
    "openWorldHint": False
  }
)
def get_playbook(job: str) -> str:
    """Return the playbook text for a job: competitor_teardown, who_is_advertising, competitor_watch."""
    key = job.strip().lower().replace(" ", "_").replace("-", "_")
    if key not in PLAYBOOKS:
        return f"Unknown job '{job}'. Valid jobs: {', '.join(PLAYBOOKS)}."
    return PLAYBOOKS[key]


def _playbook_prompt(job: str):
    def prompt(brief: str = "") -> str:
        return f"Run this playbook.{f' My brief: {brief}' if brief else ''}\n\n{PLAYBOOKS[job]}"
    return prompt


# The same playbooks in the host's prompt menu (Claude Desktop, Claude Code, Cursor).
for _job, _trigger in TRIGGERS.items():
    mcp.prompt(name=_job, title=_job.replace("_", " ").capitalize(), description=f"Playbook for {_trigger}.")(_playbook_prompt(_job))


if __name__ == "__main__":
   if os.getenv("MCP_TRANSPORT") == "http":
      from src.http_server import run_http
      run_http(mcp)
   else:
      mcp.run()
