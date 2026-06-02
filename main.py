import os
import glob
import subprocess
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import yt_dlp
import uuid
import re
import requests
import json
from proxy_config import get_ydl_proxy_opts
from yt_dlp.utils import ExtractorError

# Load environment variables from .env file (explicit path)
backend_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(backend_dir, ".env")
load_dotenv(env_path)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def cleanup_files(base_name: str):
    """Delete files starting with base_name"""
    for f in glob.glob(f"{base_name}*"):
        try:
            os.remove(f)
        except:
            pass

download_progress = {}

# Platform detection helper
def detect_platform(url: str) -> str:
    """Detect platform from URL"""
    if 'youtube.com' in url or 'youtu.be' in url:
        return 'youtube'
    elif 'tiktok.com' in url or 'vt.tiktok.com' in url:
        return 'tiktok'
    elif 'instagram.com' in url:
        return 'instagram'
    else:
        return 'unknown'

@app.post("/run")
async def run_cli(request: Request):
    body = await request.json()
    cmd = body.get("command", "").strip()
    if not cmd:
        return JSONResponse({"output": "", "error": "No command provided"}, status_code=400)

    import os
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    args = ["python", "-u", "cli.py"] + cmd.split()  # -u = unbuffered

    def stream_output():
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge stderr ke stdout
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            bufsize=1,  # line buffered
        )
        for line in process.stdout:
            yield line
        process.wait()

    return StreamingResponse(stream_output(), media_type="text/plain")

@app.get("/")
def root():
    return {"status": "Online", "message": "RaiSaver API is running"}

@app.get("/progress")
def get_progress(task_id: str):
    return download_progress.get(task_id, {"status": "starting", "progress": 0.0})

@app.get("/info")
def get_info(url: str):
    try:
        # Try multiple strategies to get all formats
        all_formats = []
        info = None
        
        # Strategy 1: Android creator client WITH proxy
        try:
            ydl_opts_android = {
                'quiet': True,
                'no_warnings': True,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android_creator'],
                    }
                },
                'geo_bypass': True,
                'geo_bypass_country': 'US',
                **get_ydl_proxy_opts(),
            }
            with yt_dlp.YoutubeDL(ydl_opts_android) as ydl:
                info = ydl.extract_info(url, download=False)
                formats = info.get('formats', [])
                all_formats.extend(formats)
                print(f"Android creator client (with proxy): {len(formats)} formats")
        except Exception as e:
            print(f"Android creator client failed: {e}")
        
        # Strategy 2: If we got less than 15 formats, try WITHOUT proxy
        if len(all_formats) < 15:
            try:
                print("Trying android_creator WITHOUT proxy...")
                ydl_opts_no_proxy = {
                    'quiet': True,
                    'no_warnings': True,
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['android_creator'],
                        }
                    },
                    'geo_bypass': True,
                    'geo_bypass_country': 'US',
                    # NO PROXY
                }
                with yt_dlp.YoutubeDL(ydl_opts_no_proxy) as ydl:
                    info_no_proxy = ydl.extract_info(url, download=False)
                    formats = info_no_proxy.get('formats', [])
                    all_formats.extend(formats)
                    if not info or len(formats) > len(info.get('formats', [])):
                        info = info_no_proxy
                    print(f"Android creator client (no proxy): {len(formats)} formats")
            except Exception as e:
                print(f"Android creator (no proxy) failed: {e}")
        
        # Strategy 3: Try android regular
        if len(all_formats) < 15:
            try:
                ydl_opts_android_regular = {
                    'quiet': True,
                    'no_warnings': True,
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['android'],
                        }
                    },
                    'geo_bypass': True,
                    'geo_bypass_country': 'US',
                    # Try without proxy
                }
                with yt_dlp.YoutubeDL(ydl_opts_android_regular) as ydl:
                    info_android = ydl.extract_info(url, download=False)
                    formats = info_android.get('formats', [])
                    all_formats.extend(formats)
                    if not info:
                        info = info_android
                    print(f"Android regular client: {len(formats)} formats")
            except Exception as e:
                print(f"Android regular client failed: {e}")
        
        # Strategy 4: iOS client
        if len(all_formats) < 15:
            try:
                ydl_opts_ios = {
                    'quiet': True,
                    'no_warnings': True,
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['ios'],
                        }
                    },
                }
                with yt_dlp.YoutubeDL(ydl_opts_ios) as ydl:
                    info_ios = ydl.extract_info(url, download=False)
                    formats = info_ios.get('formats', [])
                    all_formats.extend(formats)
                    if not info:
                        info = info_ios
                    print(f"iOS client: {len(formats)} formats")
            except Exception as e:
                print(f"iOS client failed: {e}")
        
        print(f"Total formats from all clients: {len(all_formats)}")
        
        # Debug: print semua format yang ada
        for f in all_formats:
            print(f"  format_id={f.get('format_id')} height={f.get('height')} vcodec={f.get('vcodec')} acodec={f.get('acodec')} has_url={bool(f.get('url'))}")
        
        # Filter for standard resolutions only
        standard_resolutions = {144, 240, 360, 480, 720, 1080, 1440, 2160}
        video_resolutions = {}
        seen_format_ids = set()
        
        for f in all_formats:
            format_id = f.get('format_id')
            if format_id in seen_format_ids:
                continue
            seen_format_ids.add(format_id)
            
            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            res = f.get('height')
            
            # Only include formats with standard resolutions
            if res and res in standard_resolutions:
                if vcodec != 'none' and f.get('url'):
                    if res not in video_resolutions:
                        video_resolutions[res] = []
                    
                    # Prioritize formats with audio
                    priority = 0 if acodec != 'none' else 1
                    video_resolutions[res].append((format_id, priority))
                    print(f"Added format {format_id}: {res}p (has_audio: {acodec != 'none'})")
        
        # Sort resolutions from highest to lowest
        sorted_res = sorted(video_resolutions.keys(), reverse=True)
        video_formats = []
        
        for r in sorted_res:
            # Sort by priority (formats with audio first)
            formats_for_res = sorted(video_resolutions[r], key=lambda x: x[1])
            video_formats.append({
                "resolution": f"{r}p", 
                "format_id": formats_for_res[0][0]
            })
        
        # Fallback: jika tidak ada format video yang lolos filter,
        # ambil semua format yang punya vcodec (tanpa filter resolusi)
        if not video_formats:
            print("WARNING: No standard formats found, falling back to all video formats")
            seen = set()
            for f in all_formats:
                fid = f.get('format_id')
                if fid in seen:
                    continue
                seen.add(fid)
                vcodec = f.get('vcodec', 'none')
                h = f.get('height')
                if vcodec != 'none' and h:
                    video_formats.append({
                        "resolution": f"{h}p",
                        "format_id": fid
                    })
            # Sort highest res first
            video_formats.sort(key=lambda x: int(x['resolution'].replace('p','')), reverse=True)
            # Deduplicate by resolution, keep first
            seen_res = set()
            deduped = []
            for vf in video_formats:
                if vf['resolution'] not in seen_res:
                    seen_res.add(vf['resolution'])
                    deduped.append(vf)
            video_formats = deduped
        
        print(f"Final video_formats: {video_formats}")
        
        if not info:
            raise Exception("Failed to get video info from all clients")
        
        return {
            "title": info.get('title'),
            "thumbnail": info.get('thumbnail'),
            "channel": info.get('uploader'),
            "duration": info.get('duration'),
            "video_formats": video_formats
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/tiktok/info")
def get_tiktok_info(url: str):
    """Get TikTok video information using TikWM API"""
    try:
        # Clean the URL
        if '?' in url:
            clean_url = url.split('?')[0]
        else:
            clean_url = url
            
        print(f"Fetching TikTok info via TikWM API: {clean_url}")
        
        # Call TikWM API
        api_url = "https://www.tikwm.com/api/"
        params = {
            "url": clean_url,
            "hd": 1  # Request HD quality
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.post(api_url, data=params, headers=headers, timeout=15)
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"TikWM API error: status code {response.status_code}"
            )
        
        data = response.json()
        print(f"TikWM API response: {data.get('code')}, msg: {data.get('msg')}")
        
        # Check if request was successful
        if data.get('code') != 0:
            error_msg = data.get('msg', 'Unknown error')
            raise HTTPException(
                status_code=400,
                detail=f"TikWM API error: {error_msg}"
            )
        
        # Extract video info
        video_data = data.get('data', {})
        
        title = video_data.get('title', 'TikTok Video')
        author = video_data.get('author', {})
        username = author.get('unique_id', 'Unknown')
        nickname = author.get('nickname', username)
        
        # Get video URLs
        play_url = video_data.get('play', '')  # Standard quality
        hdplay_url = video_data.get('hdplay', '')  # HD quality
        wmplay_url = video_data.get('wmplay', '')  # With watermark
        
        # Check if it's a photo/slideshow
        images = video_data.get('images', [])
        is_photo = len(images) > 0
        
        # Get thumbnail
        thumbnail = video_data.get('cover', '')
        if not thumbnail:
            thumbnail = video_data.get('origin_cover', '')
        
        # Get duration
        duration = video_data.get('duration', 0)
        
        # Get video stats
        play_count = video_data.get('play_count', 0)
        
        print(f"Berhasil fetch TikTok via TikWM: {title} by @{username}, is_photo: {is_photo}")
        
        # Build formats list
        video_formats = []
        
        if is_photo:
            # For photo/slideshow, return image URLs
            for idx, img_url in enumerate(images):
                video_formats.append({
                    "resolution": f"Image {idx + 1}",
                    "format_id": f"img_{idx}",
                    "ext": "jpg",
                    "download_url": img_url
                })
        else:
            # For video
            if hdplay_url:
                video_formats.append({
                    "resolution": "HD",
                    "format_id": "hd",
                    "ext": "mp4",
                    "download_url": hdplay_url
                })
            
            if play_url:
                video_formats.append({
                    "resolution": "SD",
                    "format_id": "sd",
                    "ext": "mp4",
                    "download_url": play_url
                })
        
        if not video_formats:
            raise HTTPException(
                status_code=400,
                detail="Tidak ada URL download yang tersedia dari TikWM API"
            )
        
        return {
            "title": title,
            "thumbnail": thumbnail,
            "channel": f"@{username} ({nickname})",
            "duration": duration,
            "description": title,
            "video_formats": video_formats,
            "platform": "tiktok",
            "play_count": play_count,
            "is_photo": is_photo
        }
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_msg = str(e)
        full_traceback = traceback.format_exc()
        print(f"Error TikTok via TikWM: {error_msg}")
        print(f"Full traceback:\n{full_traceback}")
        
        raise HTTPException(
            status_code=400,
            detail=f"Error mengambil info TikTok: {error_msg}"
        )


# --- Instagram ---
# Uses sankavollerei API for all post types (photo, carousel, video, reels)
# Falls back to yt-dlp for Reels/video if API fails

SANKAVOLLEREI_API_KEY = "planaai"
SANKAVOLLEREI_API_URL = "https://www.sankavollerei.com/download/instagram"

def _make_filename_slug(text: str, max_len: int = 50) -> str:
    """
    Convert text to a safe filename slug:
    - Remove hashtags (#word) and mentions (@word)
    - Remove/replace characters invalid in filenames
    - Collapse multiple underscores/spaces
    - Strip leading/trailing underscores
    """
    # Remove hashtags and mentions
    text = re.sub(r'[#@]\S+', '', text)
    # Remove invalid filename characters (keep letters, digits, spaces, hyphens, dots)
    text = re.sub(r'[\\/:*?"<>|]', '', text)
    # Replace whitespace/newlines with underscore
    text = re.sub(r'\s+', '_', text.strip())
    # Collapse multiple underscores
    text = re.sub(r'_+', '_', text)
    # Strip leading/trailing underscores and dots
    text = text.strip('_.')
    # Truncate
    return text[:max_len] or "download"

def _fetch_instagram_via_api(url: str) -> dict:
    """
    Fetch Instagram post info via sankavollerei API.
    Returns dict with keys: title, thumbnail, channel, duration, video_formats, platform
    Raises Exception on failure.
    """
    clean_url = url.split('?')[0] if '?' in url else url
    api_resp = requests.get(
        SANKAVOLLEREI_API_URL,
        params={"apikey": SANKAVOLLEREI_API_KEY, "url": clean_url},
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    if api_resp.status_code != 200:
        raise Exception(f"API returned HTTP {api_resp.status_code}")

    data = api_resp.json()
    if not data.get("status"):
        raise Exception(f"API error: {data.get('message', 'unknown')}")

    result = data.get("result", {})
    media_list = result.get("media") or []
    if not media_list:
        raise Exception("API returned no media")

    # API often returns null for author/caption — try yt-dlp for metadata only
    author  = result.get("author") or ""
    caption = result.get("caption") or ""
    if not author or not caption:
        try:
            # extract_flat: False needed to get uploader/description for photo posts
            ydl_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': False}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                meta = ydl.extract_info(url, download=False)
            if meta:
                if not author:
                    # yt-dlp returns None for uploader on photo carousels,
                    # but title is "Post by USERNAME" — extract from there
                    raw_author = (meta.get('uploader') or meta.get('uploader_id') or
                                  meta.get('channel') or '')
                    if not raw_author:
                        yt_title = meta.get('title') or ''
                        import re as _re
                        m = _re.match(r'^Post by (.+)$', yt_title, _re.IGNORECASE)
                        if m:
                            raw_author = m.group(1).strip()
                    author = raw_author

                if not caption:
                    # description = full caption (may start with title line)
                    desc = meta.get('description') or ''
                    caption = desc
        except Exception as e:
            print(f"yt-dlp metadata fetch failed (non-fatal): {e}")

    author  = author  or "Instagram"
    caption = caption or ""

    # Use first non-empty line of caption as title
    first_line = ""
    if caption:
        for line in caption.splitlines():
            line = line.strip()
            if line:
                first_line = line
                break
    title_display = first_line or author

    # thumbnail: first item that has a non-null thumbnail, or first image url
    thumbnail = ""
    for m in media_list:
        if m.get("thumbnail"):
            thumbnail = m["thumbnail"]
            break
    if not thumbnail:
        for m in media_list:
            if m.get("type") == "image" and m.get("url"):
                thumbnail = m["url"]
                break

    # Build format list — each media item becomes one entry
    # Use a short slug of the caption as filename prefix
    caption_slug = _make_filename_slug(first_line or author, max_len=40) or "instagram"

    video_formats = []
    photo_count = 0
    video_count = 0
    has_video = False
    
    for idx, m in enumerate(media_list):
        mtype = m.get("type", "image")
        murl  = m.get("url", "")
        if not murl:
            continue
        if mtype == "video":
            has_video = True
            video_count += 1
            ext   = "mp4"
            label = f"Video {video_count}"
            fname = f"{caption_slug}_video{video_count}"
        else:
            photo_count += 1
            ext   = "jpg"
            label = f"Foto {photo_count}"
            fname = f"{caption_slug}_foto{photo_count}"

        video_formats.append({
            "resolution": label,
            "format_id": f"api_{idx}",
            "ext": ext,
            "download_url": murl,
            "filename": fname,   # suggested filename without extension
        })

    # If it's a Reel/Video URL but API returned no video, force fallback to yt-dlp
    is_video_url = any(x in url.lower() for x in ["/reels/", "/reel/", "/tv/", "/video/"])
    if is_video_url and not has_video:
        print(f"Force fallback for suspected video URL: {url}")
        raise Exception("Suspected video URL but API returned no video")

    if not video_formats:
        raise Exception("No downloadable media found")

    return {
        "title": title_display[:200],
        "description": caption,        # full caption for display
        "thumbnail": thumbnail,
        "channel": author,
        "duration": None,
        "video_formats": video_formats,
        "platform": "instagram",
        "is_photo": not has_video,
        "is_carousel": len(media_list) > 1
    }

@app.get("/instagram/info")
def get_instagram_info(url: str):
    """
    Get Instagram post info.
    Tries sankavollerei API first (supports photos, carousels, reels, videos).
    Falls back to yt-dlp for Reels/video if API fails.
    """
    # --- Try API first ---
    try:
        result = _fetch_instagram_via_api(url)
        print(f"Instagram info via API: {len(result['video_formats'])} media items")
        
        # Simplify labels if it's a single video
        if not result.get("is_carousel") and not result.get("is_photo"):
            for fmt in result["video_formats"]:
                if "video" in fmt["resolution"].lower():
                    fmt["resolution"] = "HD (High Quality)"
                    break
        return result
    except HTTPException:
        raise
    except Exception as api_err:
        print(f"sankavollerei API failed: {api_err}, falling back to yt-dlp")

    # --- Fallback: yt-dlp (Reels / video only) ---
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        instagram_cookies = os.environ.get('INSTAGRAM_COOKIES', '')
        if instagram_cookies:
            cookies_from_browser = os.environ.get('INSTAGRAM_COOKIES_FROM_BROWSER', '')
            if cookies_from_browser:
                ydl_opts['cookiesfrombrowser'] = (cookies_from_browser,)
            else:
                import tempfile
                cookie_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
                cookie_file.write(instagram_cookies)
                cookie_file.close()
                ydl_opts['cookiefile'] = cookie_file.name

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise Exception("Failed to extract Instagram info")

        # Detect photo/carousel (yt-dlp returns playlist with 0 entries for photos)
        if info.get('_type') == 'playlist':
            entries = list(info.get('entries') or [])
            if len(entries) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="Post ini adalah foto/carousel dan API tidak tersedia saat ini. Coba lagi nanti.",
                )
            info = None
            for entry in entries:
                if isinstance(entry, dict):
                    if entry.get('url') and not entry.get('formats'):
                        try:
                            with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                                entry = ydl2.extract_info(entry['url'], download=False) or entry
                        except Exception:
                            pass
                    if entry.get('formats') or entry.get('vcodec') not in (None, 'none'):
                        info = entry
                        break
            if not info:
                raise HTTPException(status_code=400, detail="Tidak ada video yang bisa di-download dari post ini.")
        else:
            entries = info.get('entries')
            if entries:
                first = entries[0]
                if isinstance(first, dict) and first.get('url'):
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                        info = ydl2.extract_info(first['url'], download=False) or first
                elif isinstance(first, dict):
                    info = first

        title    = info.get('title') or info.get('description') or 'Instagram'
        uploader = info.get('uploader') or info.get('uploader_id') or 'Instagram'
        thumbnail = info.get('thumbnail') or ''
        if not thumbnail:
            thumbs = info.get('thumbnails') or []
            if thumbs:
                best = max(thumbs, key=lambda t: (t.get('width') or 0) * (t.get('height') or 0), default=None)
                thumbnail = (best or {}).get('url') or thumbs[-1].get('url') or ''
        duration = info.get('duration')

        formats = info.get('formats', [])
        video_formats = []
        if formats:
            height_set = set()
            for f in formats:
                h = f.get('height')
                if h and h not in height_set and f.get('vcodec') != 'none':
                    height_set.add(h)
                    video_formats.append({
                        'resolution': f'{h}p',
                        'format_id': f.get('format_id', 'best'),
                        'ext': f.get('ext', 'mp4'),
                    })
            video_formats.sort(key=lambda x: int(x['resolution'].replace('p', '') or 0), reverse=True)

        if video_formats:
            # Simplify to HD and SD for Instagram
            simplified = []
            # Best quality is HD
            best = video_formats[0]
            best['resolution'] = "HD (High Quality)"
            simplified.append(best)
            
            # If there's a significantly lower quality, call it SD
            if len(video_formats) > 1:
                worst = video_formats[-1]
                if worst['format_id'] != best['format_id']:
                    worst['resolution'] = "SD (Standard Quality)"
                    simplified.append(worst)
            
            video_formats = simplified

        if not video_formats:
            raise HTTPException(
                status_code=400,
                detail="Post ini adalah foto/carousel. Hanya Reels dan video posts yang bisa di-download.",
            )

        return {
            'title': title[:200] if title else 'Instagram',
            'thumbnail': thumbnail,
            'channel': uploader,
            'duration': duration,
            'video_formats': video_formats,
            'platform': 'instagram',
            'is_photo': False,
            'is_carousel': False
        }
    except HTTPException:
        raise
    except ExtractorError as e:
        msg = str(e)
        if 'There is no video in this post' in msg:
            raise HTTPException(status_code=400, detail="Post ini adalah foto. Hanya Reels dan video yang bisa di-download.")
        raise HTTPException(status_code=400, detail=f"Instagram extractor: {msg}")
    except Exception as e:
        import traceback
        print(f"Instagram yt-dlp fallback error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Instagram: {str(e)}")


@app.get("/proxy-image")
def proxy_image(url: str = ""):
    """Proxy image through backend to avoid Instagram hotlink / CORS issues."""
    try:
        # Return transparent 1x1 PNG if no URL provided
        if not url or not url.startswith("http"):
            import base64
            from fastapi.responses import Response
            pixel = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            )
            return Response(content=pixel, media_type="image/png")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.instagram.com/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "sec-fetch-dest": "image",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-site": "cross-site",
        }

        resp = requests.get(url, headers=headers, timeout=20, stream=True)

        if resp.status_code == 403:
            # Instagram CDN blocked — return a transparent 1x1 pixel as fallback
            # so the Flutter app doesn't crash trying to load the thumbnail
            import base64
            pixel = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            )
            from fastapi.responses import Response
            return Response(content=pixel, media_type="image/png")

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"CDN returned {resp.status_code}")

        # Read full content into memory — more reliable than streaming resp.raw
        content = resp.content
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()

        from fastapi.responses import Response
        return Response(content=content, media_type=content_type)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Image proxy error: {str(e)}")

@app.get("/instagram/download")
def download_instagram(url: str, background_tasks: BackgroundTasks, format_id: Optional[str] = "best", task_id: Optional[str] = None):
    """Download Instagram media. Uses sankavollerei API for photos/carousels, yt-dlp for Reels/video."""
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_ig_{task_id}"
    download_progress[task_id] = {"status": "starting", "progress": 0.0}

    # --- API-based download (format_id starts with "api_") ---
    if format_id and format_id.startswith("api_"):
        try:
            # Re-fetch info to get the download_url for this format
            info_resp = _fetch_instagram_via_api(url)
            formats = info_resp.get("video_formats") or []

            download_url = None
            file_ext = "jpg"
            for f in formats:
                if f.get("format_id") == format_id:
                    download_url = f.get("download_url")
                    file_ext = f.get("ext", "jpg")
                    break

            # Fallback to first item
            if not download_url and formats:
                download_url = formats[0].get("download_url")
                file_ext = formats[0].get("ext", "jpg")

            if not download_url:
                raise HTTPException(status_code=400, detail="URL media tidak ditemukan.")

            download_progress[task_id] = {"status": "downloading", "progress": 0.1}

            r = requests.get(
                download_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                stream=True,
                timeout=60,
            )
            if r.status_code != 200:
                raise Exception(f"Gagal download media: HTTP {r.status_code}")

            file_path = f"{base_name}.{file_ext}"
            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(file_path, "wb") as fh:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            download_progress[task_id] = {"status": "downloading", "progress": downloaded / total_size}

            download_progress[task_id] = {"status": "completed", "progress": 1.0}

            raw_title = formats[0].get("filename") or info_resp.get("title") or info_resp.get("channel") or "instagram"
            # Use the specific format's filename if available
            for f in formats:
                if f.get("format_id") == format_id and f.get("filename"):
                    raw_title = f["filename"]
                    break
            safe_title = re.sub(r'[\\/:*?"<>|]', '_', raw_title).strip() or f"instagram_{task_id}"

            if file_ext in ("jpg", "jpeg"):
                media_type = "image/jpeg"
            elif file_ext == "png":
                media_type = "image/png"
            else:
                media_type = "video/mp4"

            from urllib.parse import quote
            ascii_name = safe_title.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
            utf8_name  = quote(f"{safe_title}.{file_ext}", safe='')
            content_disposition = (
                f'attachment; filename="{ascii_name}.{file_ext}"; '
                f"filename*=UTF-8''{utf8_name}"
            )

            def cleanup_all():
                cleanup_files(base_name)
                download_progress.pop(task_id, None)
            background_tasks.add_task(cleanup_all)

            from fastapi.responses import Response as FastResponse
            return FileResponse(
                file_path,
                media_type=media_type,
                headers={"Content-Disposition": content_disposition},
            )
        except HTTPException:
            cleanup_files(base_name)
            download_progress.pop(task_id, None)
            raise
        except Exception as e:
            cleanup_files(base_name)
            download_progress.pop(task_id, None)
            raise HTTPException(status_code=400, detail=f"Instagram API download: {str(e)}")

    # --- yt-dlp download (Reels / video, format_id is a yt-dlp format id) ---
    def my_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                download_progress[task_id] = {"status": "downloading", "progress": downloaded / total}
        elif d['status'] == 'finished':
            download_progress[task_id] = {"status": "processing", "progress": 1.0}

    ydl_opts = {
        'format': format_id if format_id and format_id != 'best' else 'best[ext=mp4]/best',
        'outtmpl': f"{base_name}.%(ext)s",
        'quiet': True,
        'progress_hooks': [my_hook],
        'nocheckcertificate': True,
    }

    instagram_cookies = os.environ.get('INSTAGRAM_COOKIES', '')
    if instagram_cookies:
        cookies_from_browser = os.environ.get('INSTAGRAM_COOKIES_FROM_BROWSER', '')
        if cookies_from_browser:
            ydl_opts['cookiesfrombrowser'] = (cookies_from_browser,)
        else:
            import tempfile
            cookie_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
            cookie_file.write(instagram_cookies)
            cookie_file.close()
            ydl_opts['cookiefile'] = cookie_file.name

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        files = glob.glob(f"{base_name}*")
        files = [f for f in files if not f.endswith('.part') and os.path.isfile(f)]
        if not files:
            raise Exception("File not found after download")
        file_path = files[0]
        ext = file_path.split('.')[-1]
        info = None
        try:
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(url, download=False)
            if info and info.get('entries'):
                info = info['entries'][0] if isinstance(info['entries'][0], dict) else info
        except Exception:
            pass
        safe_title = 'instagram'
        if info:
            safe_title = re.sub(r'[\\/:*?"<>|]', '_', (info.get('title') or info.get('uploader') or 'instagram')).strip() or 'instagram'
        if not safe_title or safe_title == 'instagram':
            safe_title = f"instagram_{task_id}"

        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)
        background_tasks.add_task(cleanup_all)

        from urllib.parse import quote
        ascii_name = safe_title.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
        utf8_name  = quote(f"{safe_title}.{ext}", safe='')
        content_disposition = (
            f'attachment; filename="{ascii_name}.{ext}"; '
            f"filename*=UTF-8''{utf8_name}"
        )
        media_type = "video/mp4" if ext in ('mp4', 'webm') else "image/jpeg" if ext in ('jpg', 'jpeg') else "image/png"
        return FileResponse(file_path, media_type=media_type, headers={"Content-Disposition": content_disposition})
    except ExtractorError as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        msg = str(e)
        if 'There is no video in this post' in msg:
            raise HTTPException(status_code=400, detail="Post ini hanya foto. Gunakan tombol Download Photo di layar info.")
        raise HTTPException(status_code=400, detail=f"Instagram extractor: {msg}")
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        raise HTTPException(status_code=400, detail=f"Instagram download: {str(e)}")


@app.get("/instagram/download/all")
def download_instagram_all(url: str, background_tasks: BackgroundTasks, task_id: Optional[str] = None):
    """Download all photos/videos from an Instagram post as a ZIP file."""
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_ig_all_{task_id}"
    zip_path  = f"{base_name}.zip"
    download_progress[task_id] = {"status": "starting", "progress": 0.0}

    try:
        info_resp = _fetch_instagram_via_api(url)
        formats   = info_resp.get("video_formats") or []
        if not formats:
            raise HTTPException(status_code=400, detail="Tidak ada media yang bisa di-download.")

        import zipfile, time

        total = len(formats)
        downloaded_files = []

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, fmt in enumerate(formats):
                dl_url  = fmt.get("download_url")
                ext     = fmt.get("ext", "jpg")
                fname   = fmt.get("filename") or f"media_{i+1}"
                safe_fn = _make_filename_slug(fname) or f"media_{i+1}"
                arcname = f"{safe_fn}.{ext}"

                if not dl_url:
                    continue

                download_progress[task_id] = {
                    "status": "downloading",
                    "progress": i / total,
                }

                r = requests.get(
                    dl_url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    timeout=60,
                )
                if r.status_code != 200:
                    print(f"Skipping {arcname}: HTTP {r.status_code}")
                    continue

                zf.writestr(arcname, r.content)
                downloaded_files.append(arcname)
                print(f"Added to ZIP: {arcname} ({len(r.content)} bytes)")

        if not downloaded_files:
            raise Exception("Semua media gagal di-download.")

        download_progress[task_id] = {"status": "completed", "progress": 1.0}

        # ZIP filename based on caption slug
        raw_title = info_resp.get("title") or "instagram"
        safe_zip_title = _make_filename_slug(raw_title, max_len=60) or "instagram"

        from urllib.parse import quote
        ascii_name = safe_zip_title.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
        utf8_name  = quote(f"{safe_zip_title}.zip", safe='')
        content_disposition = (
            f'attachment; filename="{ascii_name}.zip"; '
            f"filename*=UTF-8''{utf8_name}"
        )

        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)
        background_tasks.add_task(cleanup_all)

        return FileResponse(
            zip_path,
            media_type="application/zip",
            headers={"Content-Disposition": content_disposition},
        )

    except HTTPException:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        raise
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        import traceback
        print(f"Instagram download all error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Download semua gagal: {str(e)}")

@app.get("/tiktok/download")
def download_tiktok(url: str, background_tasks: BackgroundTasks, format_id: Optional[str] = "hd", task_id: Optional[str] = None):
    """Download TikTok video or photo using TikWM API"""
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_{task_id}"

    download_progress[task_id] = {"status": "starting", "progress": 0.0}
    
    try:
        # Clean the URL
        if '?' in url:
            clean_url = url.split('?')[0]
        else:
            clean_url = url
        
        print(f"Downloading TikTok via TikWM: {clean_url}")
        
        # Get video/photo info first
        info_response = get_tiktok_info(clean_url)
        
        if not info_response.get('video_formats'):
            raise Exception("Tidak ada format yang tersedia")
        
        is_photo = info_response.get('is_photo', False)
        
        # Find the requested format
        download_url = None
        file_ext = "mp4"
        format_resolution = ""
        
        for fmt in info_response['video_formats']:
            if fmt['format_id'] == format_id:
                download_url = fmt['download_url']
                file_ext = fmt.get('ext', 'mp4')
                format_resolution = fmt.get('resolution', '')
                break
        
        # If format not found, use first available
        if not download_url:
            download_url = info_response['video_formats'][0]['download_url']
            file_ext = info_response['video_formats'][0].get('ext', 'mp4')
            format_resolution = info_response['video_formats'][0].get('resolution', '')
        
        if not download_url:
            raise Exception("Tidak bisa mendapatkan URL download")
        
        print(f"Download URL: {download_url[:50]}..., ext: {file_ext}")
        
        # Download file
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.tikwm.com/',
        }
        
        download_progress[task_id] = {"status": "downloading", "progress": 0.1}
        
        response = requests.get(download_url, headers=headers, stream=True, timeout=30)
        
        if response.status_code != 200:
            raise Exception(f"Gagal download: status code {response.status_code}")
        
        # Save file
        file_path = f"{base_name}.{file_ext}"
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        print(f"Downloading file, size: {total_size} bytes")
        
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = downloaded / total_size
                        download_progress[task_id] = {"status": "downloading", "progress": progress}
        
        download_progress[task_id] = {"status": "completed", "progress": 1.0}
        
        print(f"Download completed: {downloaded} bytes")
        
        # Generate simple filename: tiktok_{timestamp}_{type}.ext
        import time
        timestamp = str(int(time.time()))
        
        if is_photo:
            # For photo: tiktok_{timestamp}_img1.jpg
            img_num = format_resolution.replace('Image ', '').strip() if 'Image' in format_resolution else '1'
            safe_title = f"tiktok_{timestamp}_img{img_num}"
        else:
            # For video: tiktok_{timestamp}.mp4
            safe_title = f"tiktok_{timestamp}"

        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)

        background_tasks.add_task(cleanup_all)
        
        # Set appropriate media type
        if file_ext == "jpg" or file_ext == "jpeg":
            media_type = "image/jpeg"
        elif file_ext == "png":
            media_type = "image/png"
        else:
            media_type = "video/mp4"
        
        final_filename = f"{safe_title}.{file_ext}"
        print(f"Final filename: {final_filename}")
        
        return FileResponse(file_path, filename=final_filename, media_type=media_type)
        
    except HTTPException:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        raise
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        error_msg = str(e)
        print(f"Error download TikTok: {error_msg}")
        
        raise HTTPException(
            status_code=400,
            detail=f"Gagal download TikTok: {error_msg}"
        )


@app.get("/tiktok/download/all")
def download_tiktok_all(url: str, background_tasks: BackgroundTasks, task_id: Optional[str] = None):
    """Download all photos from a TikTok slideshow/photo post as a ZIP file."""
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_tk_all_{task_id}"
    zip_path  = f"{base_name}.zip"
    download_progress[task_id] = {"status": "starting", "progress": 0.0}

    try:
        # Clean URL
        clean_url = url.split('?')[0] if '?' in url else url

        info_resp = get_tiktok_info(clean_url)
        if not info_resp.get('is_photo'):
            raise HTTPException(status_code=400, detail="Bukan post foto/slideshow.")

        formats = [f for f in (info_resp.get('video_formats') or [])
                   if f.get('format_id', '').startswith('img_')]
        if not formats:
            raise HTTPException(status_code=400, detail="Tidak ada foto yang bisa di-download.")

        import zipfile

        title = info_resp.get('title') or 'tiktok'
        username = (info_resp.get('channel') or 'tiktok').split('(')[0].strip().lstrip('@')
        slug = _make_filename_slug(title, max_len=40) or 'tiktok'

        total = len(formats)
        headers_dl = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.tikwm.com/',
        }

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, fmt in enumerate(formats):
                dl_url = fmt.get('download_url')
                ext    = fmt.get('ext', 'jpg')
                # e.g. "tiktok_slug_foto1.jpg"
                arcname = f"{slug}_foto{i+1}.{ext}"

                download_progress[task_id] = {"status": "downloading", "progress": i / total}

                if not dl_url:
                    continue
                r = requests.get(dl_url, headers=headers_dl, timeout=30)
                if r.status_code != 200:
                    print(f"Skipping {arcname}: HTTP {r.status_code}")
                    continue
                zf.writestr(arcname, r.content)
                print(f"Added to ZIP: {arcname} ({len(r.content)} bytes)")

        download_progress[task_id] = {"status": "completed", "progress": 1.0}

        from urllib.parse import quote
        zip_title  = f"{slug}_photos"
        ascii_name = zip_title.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
        utf8_name  = quote(f"{zip_title}.zip", safe='')
        content_disposition = (
            f'attachment; filename="{ascii_name}.zip"; '
            f"filename*=UTF-8''{utf8_name}"
        )

        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)
        background_tasks.add_task(cleanup_all)

        return FileResponse(
            zip_path,
            media_type="application/zip",
            headers={"Content-Disposition": content_disposition},
        )

    except HTTPException:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        raise
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        import traceback
        print(f"TikTok download all error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Download semua TikTok gagal: {str(e)}")

@app.get("/download/video")
def download_video(url: str, format_id: str, background_tasks: BackgroundTasks, task_id: Optional[str] = None):
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_{task_id}"

    download_progress[task_id] = {"status": "starting", "progress": 0.0}

    def my_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                download_progress[task_id] = {"status": "downloading", "progress": downloaded / total}
        elif d['status'] == 'finished':
            download_progress[task_id] = {"status": "processing", "progress": 1.0}
    
    # +bestaudio/best merges the selected video format with best available audio
    ydl_opts = {
        'format': f"{format_id}+bestaudio[ext=m4a]/bestaudio/best",
        'outtmpl': f"{base_name}.%(ext)s",
        'merge_output_format': 'mp4',
        'quiet': True,
        'writethumbnail': True,
        'progress_hooks': [my_hook],
        'postprocessors': [
            {'key': 'FFmpegMetadata'},
            {'key': 'EmbedThumbnail'},
        ],
        'extractor_args': {
            'youtube': {
                'player_client': ['android_creator'],
            }
        },
        'js_runtimes': {'node': {}},
        'remote_components': {'ejs:github'},  # tambahkan ini
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'nocheckcertificate': True,
        **get_ydl_proxy_opts(),  # Add proxy support
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        # The downloaded file might end up with .mp4 or .mkv depending on what was fetched
        files = glob.glob(f"{base_name}*")
        if not files:
            raise Exception("File not found after download")
            
        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)

        file_path = files[0]
        # Clean up file after streaming response
        background_tasks.add_task(cleanup_all)
        
        info = ydl.extract_info(url, download=False)
        # Only replace invalid filename characters, keep spaces
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', info.get('title', 'video')).strip()
        if not safe_title:
            safe_title = str(uuid.uuid4())

        # Determine media type based on extension
        ext = file_path.split('.')[-1]
        media_type = "video/mp4" if ext == "mp4" else "video/x-matroska"
        download_progress[task_id] = {"status": "completed", "progress": 1.0}
        return FileResponse(file_path, filename=f"{safe_title}.{ext}", media_type=media_type)
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/download/audio")
def download_audio(url: str, background_tasks: BackgroundTasks, task_id: Optional[str] = None):
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_{task_id}"
    # Fixed output path — yt-dlp will convert to .mp3 via FFmpegExtractAudio
    output_mp3 = f"{base_name}.mp3"

    download_progress[task_id] = {"status": "starting", "progress": 0.0}

    def my_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                download_progress[task_id] = {"status": "downloading", "progress": downloaded / total}
        elif d['status'] == 'finished':
            download_progress[task_id] = {"status": "processing", "progress": 1.0}

    # Download best audio, convert to MP3, embed metadata + thumbnail
    # Postprocessor order matters:
    #   1. FFmpegExtractAudio  — convert to mp3
    #   2. FFmpegMetadata      — write title/artist/etc tags
    #   3. ThumbnailsConvertor — convert webp/png thumbnail → jpg (required for ID3 embed)
    #   4. EmbedThumbnail      — embed jpg thumbnail into mp3 ID3 tag
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'outtmpl': f"{base_name}.%(ext)s",
        'quiet': True,
        'writethumbnail': True,   # needed so EmbedThumbnail has something to embed
        'progress_hooks': [my_hook],
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            },
            {
                'key': 'FFmpegMetadata',
                'add_metadata': True,
            },
            {
                # Convert thumbnail to jpg first — EmbedThumbnail needs jpg for MP3
                'key': 'FFmpegThumbnailsConvertor',
                'format': 'jpg',
            },
            {
                'key': 'EmbedThumbnail',
            },
        ],
        'extractor_args': {
            'youtube': {
                'player_client': ['android_creator'],
            }
        },
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'nocheckcertificate': True,
        **get_ydl_proxy_opts(),
    }

    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info first (for title), then download
            info = ydl.extract_info(url, download=False)
            ydl.download([url])

        # After FFmpegExtractAudio, the file should be base_name.mp3
        if not os.path.isfile(output_mp3):
            # Fallback: search for any .mp3 with this base
            mp3_files = glob.glob(f"{base_name}*.mp3")
            if not mp3_files:
                raise Exception("MP3 file not found after conversion")
            output_mp3_actual = mp3_files[0]
        else:
            output_mp3_actual = output_mp3

        # Verify the file is a valid MP3 (starts with ID3 or 0xFF 0xFB/0xF3/0xF2)
        with open(output_mp3_actual, 'rb') as f:
            header = f.read(3)
        if header[:3] not in (b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'):
            raise Exception(f"Output file is not a valid MP3 (header: {header.hex()})")

        # Clean up any leftover thumbnail/temp files (not the mp3)
        for leftover in glob.glob(f"{base_name}*"):
            if leftover != output_mp3_actual:
                try:
                    os.remove(leftover)
                except Exception:
                    pass

        safe_title = 'audio'
        if info:
            safe_title = re.sub(r'[\\/:*?"<>|]', '_', info.get('title', 'audio')).strip()
        if not safe_title:
            safe_title = str(uuid.uuid4())

        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)

        background_tasks.add_task(cleanup_all)
        download_progress[task_id] = {"status": "completed", "progress": 1.0}

        # HTTP headers only support latin-1 — use RFC 5987 encoding for Unicode filenames
        from urllib.parse import quote
        ascii_filename = safe_title.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
        utf8_filename = quote(f"{safe_title}.mp3", safe='')
        content_disposition = (
            f'attachment; filename="{ascii_filename}.mp3"; '
            f"filename*=UTF-8''{utf8_filename}"
        )

        return FileResponse(
            output_mp3_actual,
            media_type="audio/mpeg",
            headers={"Content-Disposition": content_disposition},
        )
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        import traceback
        print(f"AUDIO ERROR: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
