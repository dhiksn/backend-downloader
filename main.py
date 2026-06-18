import os
import glob
import subprocess
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Dict, Any
import yt_dlp
import uuid
import re
import requests
import json
from proxy_config import get_ydl_proxy_opts
from yt_dlp.utils import ExtractorError
from bs4 import BeautifulSoup

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
# Guard: task_id yang sedang aktif di-download, cegah request duplikat
_active_tasks: set = set()

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
        all_formats = []
        info = None

        base_opts = {
            'quiet': True,
            'no_warnings': True,
            'geo_bypass': True,
            'geo_bypass_country': 'US',
        }

        # Strategy 1: tv_embedded — still works without PO token
        try:
            opts = {
                **base_opts,
                'extractor_args': {'youtube': {'player_client': ['tv_embedded']}},
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                formats = info.get('formats', [])
                all_formats.extend(formats)
                print(f"tv_embedded: {len(formats)} formats")
        except Exception as e:
            print(f"tv_embedded failed: {e}")

        # Strategy 2: web_creator
        if len(all_formats) < 10:
            try:
                opts = {
                    **base_opts,
                    'extractor_args': {'youtube': {'player_client': ['web_creator']}},
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info2 = ydl.extract_info(url, download=False)
                    formats = info2.get('formats', [])
                    all_formats.extend(formats)
                    if not info:
                        info = info2
                    print(f"web_creator: {len(formats)} formats")
            except Exception as e:
                print(f"web_creator failed: {e}")

        # Strategy 3: mweb
        if len(all_formats) < 10:
            try:
                opts = {
                    **base_opts,
                    'extractor_args': {'youtube': {'player_client': ['mweb']}},
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info3 = ydl.extract_info(url, download=False)
                    formats = info3.get('formats', [])
                    all_formats.extend(formats)
                    if not info:
                        info = info3
                    print(f"mweb: {len(formats)} formats")
            except Exception as e:
                print(f"mweb failed: {e}")

        # Strategy 4: ios
        if len(all_formats) < 10:
            try:
                opts = {
                    **base_opts,
                    'extractor_args': {'youtube': {'player_client': ['ios']}},
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info4 = ydl.extract_info(url, download=False)
                    formats = info4.get('formats', [])
                    all_formats.extend(formats)
                    if not info:
                        info = info4
                    print(f"ios: {len(formats)} formats")
            except Exception as e:
                print(f"ios failed: {e}")

        # Strategy 5: default (no player_client specified)
        if not info:
            try:
                with yt_dlp.YoutubeDL(base_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    formats = info.get('formats', [])
                    all_formats.extend(formats)
                    print(f"default: {len(formats)} formats")
            except Exception as e:
                print(f"default failed: {e}")

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
        
        # Get thumbnail — try cover, origin_cover, then first image for photo posts
        thumbnail = video_data.get('cover', '') or video_data.get('origin_cover', '')
        if not thumbnail and is_photo and images:
            # For photo/slideshow, use the first image as thumbnail
            thumbnail = images[0] if isinstance(images[0], str) else images[0].get('url', '')
        
        # Fallback: fetch thumbnail via yt-dlp if still empty
        if not thumbnail:
            try:
                ydl_opts_thumb = {'quiet': True, 'no_warnings': True}
                with yt_dlp.YoutubeDL(ydl_opts_thumb) as ydl:
                    meta = ydl.extract_info(url, download=False)
                    if meta:
                        thumbnail = meta.get('thumbnail', '')
                        if not thumbnail:
                            thumbs = meta.get('thumbnails') or []
                            if thumbs:
                                thumbnail = thumbs[-1].get('url', '')
                print(f"yt-dlp thumbnail fallback: {thumbnail[:60] if thumbnail else 'none'}")
            except Exception as e:
                print(f"yt-dlp thumbnail fallback failed: {e}")
        
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
            # Untuk video: play dan hdplay sudah termasuk audio asli
            # Prioritaskan hdplay terlebih dahulu jika tersedia
            if hdplay_url:
                video_formats.append({
                    "resolution": "HD Quality",
                    "format_id": "hd",
                    "ext": "mp4",
                    "download_url": hdplay_url
                })
            
            if play_url and play_url != hdplay_url:
                video_formats.append({
                    "resolution": "Standard Quality",
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
# Uses SnapSave for all post types (photo, carousel, video, reels)

SNAPSAVE_API_URL = "https://snapsave.app/action.php?lang=en"

def _decrypt_snapsave(html: str) -> str:
    """Use Node.js script to decrypt SnapSave response"""
    import subprocess
    import sys
    import os
    # Get the directory of main.py
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Try to find node in PATH
    try:
        result = subprocess.run(
            ["node", "decrypt_snapsave.js"],
            input=html,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=script_dir
        )
        if result.returncode != 0:
            print("Node.js decrypt error:", result.stderr)
            raise Exception("Node.js decrypt failed")
        return result.stdout
    except Exception as e:
        print(f"Error decrypting with Node.js: {e}")
        raise e

def _fetch_snapsave(url: str) -> List[Dict[str, Any]]:
    """Fetch media from SnapSave for Instagram"""
    clean_url = url.split('?')[0] if '?' in url else url
    print(f"Fetching Instagram via SnapSave: {clean_url}")
    
    form_data = {"url": clean_url}
    headers = {
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://snapsave.app",
        "referer": "https://snapsave.app/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0"
    }
    
    response = requests.post(SNAPSAVE_API_URL, data=form_data, headers=headers, timeout=30)
    if response.status_code != 200:
        raise Exception(f"SnapSave HTTP {response.status_code}")
    
    decoded_html = _decrypt_snapsave(response.text)
    soup = BeautifulSoup(decoded_html, 'html.parser')
    
    media_list = []
    
    # Try table layout (for videos with multiple qualities)
    table = soup.find('table', class_='table')
    if table:
        for row in table.find('tbody').find_all('tr'):
            cols = row.find_all('td')
            if len(cols) >= 3:
                resolution = cols[0].text.strip()
                link = cols[2].find('a')
                if link:
                    media_url = link.get('href', '')
                else:
                    btn = cols[2].find('button')
                    if btn:
                        onclick = btn.get('onclick', '')
                        if 'get_progressApi' in onclick:
                            match = re.search(r"get_progressApi\('(.*?)'\)", onclick)
                            if match:
                                media_url = "https://snapsave.app" + match.group(1)
                            else:
                                media_url = ""
                        else:
                            media_url = ""
                if media_url:
                    media_list.append({
                        "resolution": resolution,
                        "type": "video",
                        "url": media_url
                    })
    
    # Try download-items layout
    download_items = soup.find_all('div', class_='download-items')
    if download_items and not media_list:
        for item in download_items:
            thumb = item.find('div', class_='download-items__thumb')
            btn = item.find('div', class_='download-items__btn')
            if btn:
                a_tag = btn.find('a')
                if a_tag:
                    media_url = a_tag.get('href', '')
                    span_text = btn.find('span').text.strip() if btn.find('span') else ""
                    media_type = "image" if "Photo" in span_text else "video"
                    thumbnail = ""
                    if thumb:
                        img = thumb.find('img')
                        if img:
                            thumbnail = img.get('src', '')
                    if media_url:
                        media_list.append({
                            "type": media_type,
                            "url": media_url,
                            "thumbnail": thumbnail
                        })
    
    # Try card layout
    cards = soup.find_all('div', class_='card')
    if cards and not media_list:
        for card in cards:
            card_body = card.find('div', class_='card-body')
            if card_body:
                a_tag = card_body.find('a')
                if a_tag:
                    media_url = a_tag.get('href', '')
                    a_text = a_tag.text.strip()
                    media_type = "image" if "Photo" in a_text else "video"
                    if media_url:
                        media_list.append({
                            "type": media_type,
                            "url": media_url
                        })
    
    # Try simple layout
    if not media_list:
        a_tag = soup.find('a')
        btn = soup.find('button')
        if a_tag:
            media_url = a_tag.get('href', '')
            a_text = a_tag.text.strip()
            media_type = "image" if "Photo" in a_text else "video"
            if media_url:
                media_list.append({
                    "type": media_type,
                    "url": media_url
                })
        elif btn:
            onclick = btn.get('onclick', '')
            if 'get_progressApi' in onclick:
                match = re.search(r"get_progressApi\('(.*?)'\)", onclick)
                if match:
                    media_url = "https://snapsave.app" + match.group(1)
                    media_list.append({
                        "type": "video",
                        "url": media_url
                    })
    
    if not media_list:
        raise Exception("SnapSave: No media found")
    
    return media_list

def _fetch_instagram_via_snapsave(url: str) -> Dict[str, Any]:
    """Fetch Instagram info using SnapSave"""
    media_list = _fetch_snapsave(url)
    
    # Try to get title/thumbnail using yt-dlp for metadata
    title = "Instagram Post"
    description = ""
    thumbnail = ""
    channel = "Instagram"
    is_photo = False
    is_carousel = len(media_list) > 1
    
    try:
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            meta = ydl.extract_info(url, download=False)
            if meta:
                raw_title = meta.get('title', title)
                description = meta.get('description', description)
                
                # Extract channel/uploader
                if meta.get('uploader'):
                    channel = f"@{meta.get('uploader')}"
                elif meta.get('channel'):
                    channel = f"@{meta.get('channel')}"
                elif raw_title.startswith("Post by "):
                    channel = f"@{raw_title[8:]}"
                elif raw_title.startswith("Video by "):
                    channel = f"@{raw_title[9:]}"
                elif raw_title.startswith("Photo by "):
                    channel = f"@{raw_title[9:]}"

                title = raw_title

                if not thumbnail:
                    thumbnail = meta.get('thumbnail', '')
    except Exception as e:
        print(f"yt-dlp metadata fetch for SnapSave failed (non-fatal): {e}")
    
    # Build video_formats
    video_formats = []
    has_video = False
    for idx, media in enumerate(media_list):
        media_type = media.get('type', 'image')
        media_url = media.get('url', '')
        if not media_url:
            continue
        
        if media_type == 'video':
            has_video = True
            ext = 'mp4'
            resolution = media.get('resolution', f'Video {idx+1}')
        else:
            ext = 'jpg'
            resolution = f'Foto {idx+1}'
        
        video_formats.append({
            "resolution": resolution,
            "format_id": f"snapsave_{idx}",
            "ext": ext,
            "download_url": media_url
        })
    
    is_photo = not has_video
    
    return {
        "title": title[:200],
        "description": description,
        "thumbnail": thumbnail,
        "channel": channel,
        "duration": None,
        "video_formats": video_formats,
        "platform": "instagram",
        "is_photo": is_photo,
        "is_carousel": is_carousel
    }

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

@app.get("/instagram/info")
def get_instagram_info(url: str):
    """
    Get Instagram post info using SnapSave.
    """
    try:
        result = _fetch_instagram_via_snapsave(url)
        print(f"Instagram info via SnapSave: {len(result['video_formats'])} media items")
        
        # Simplify labels if it's a single video
        if not result.get('is_carousel') and not result.get('is_photo'):
            for fmt in result["video_formats"]:
                if "Video" in fmt["resolution"]:
                    fmt["resolution"] = "HD (High Quality)"
                    break
        return result
    except Exception as snapsave_err:
        print(f"SnapSave failed: {snapsave_err}")
        raise HTTPException(
            status_code=400,
            detail=f"Gagal mengambil info Instagram. Pastikan link valid dan post bersifat publik. ({snapsave_err})"
        )


@app.get("/proxy-image")
def proxy_image(url: str = ""):
    """Proxy image through backend to avoid hotlink / CORS issues."""
    try:
        if not url or not url.startswith("http"):
            import base64
            from fastapi.responses import Response
            pixel = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            )
            return Response(content=pixel, media_type="image/png")

        # Pick Referer based on domain
        if 'tiktok' in url or 'tiktokcdn' in url or 'muscdn' in url:
            referer = 'https://www.tiktok.com/'
        elif 'instagram' in url or 'cdninstagram' in url or 'fbcdn' in url:
            referer = 'https://www.instagram.com/'
        else:
            referer = 'https://www.google.com/'

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": referer,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        resp = requests.get(url, headers=headers, timeout=20)

        if resp.status_code in (403, 404):
            import base64
            from fastapi.responses import Response
            pixel = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            )
            return Response(content=pixel, media_type="image/png")

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"CDN returned {resp.status_code}")

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
    """Download Instagram media using SnapSave."""
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_ig_{task_id}"
    download_progress[task_id] = {"status": "starting", "progress": 0.0}

    try:
        # Re-fetch info to get the download_url for this format
        info_resp = _fetch_instagram_via_snapsave(url)
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

        raw_title = info_resp.get("title") or info_resp.get("channel") or "instagram"
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
        info_resp = _fetch_instagram_via_snapsave(url)
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
                fname   = f"media_{i+1}"
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
    """Download TikTok video or photo using TikWM API, merge audio if needed."""
    # Jika format_id=mp3, redirect ke endpoint download_tiktok_mp3
    if format_id == "mp3":
        return download_tiktok_mp3(url, background_tasks, task_id)
    
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_{task_id}"

    # ── Dedup: kalau task_id ini sudah selesai, langsung return file yang ada ──
    if task_id in download_progress:
        existing = download_progress[task_id]
        # Kalau sudah completed dan file masih ada, serve ulang
        if existing.get("status") == "completed":
            final_path = existing.get("final_path", "")
            if final_path and os.path.isfile(final_path):
                from urllib.parse import quote
                fname = os.path.basename(final_path)
                ascii_name = fname.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
                utf8_name  = quote(fname, safe='')
                return FileResponse(
                    final_path,
                    media_type="video/mp4",
                    headers={"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'},
                )

    # ── Cegah request duplikat yang sedang berjalan ───────────────────────────
    if task_id in _active_tasks:
        # Return progress saja, download sedang berjalan di request lain
        import time
        # Poll sampai selesai (max 15 menit)
        for _ in range(900):
            prog = download_progress.get(task_id, {})
            if prog.get("status") == "completed":
                final_path = prog.get("final_path", "")
                if final_path and os.path.isfile(final_path):
                    from urllib.parse import quote
                    fname = os.path.basename(final_path)
                    ascii_name = fname.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
                    utf8_name  = quote(fname, safe='')
                    return FileResponse(
                        final_path,
                        media_type="video/mp4",
                        headers={"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'},
                    )
            if prog.get("status") == "error":
                raise HTTPException(status_code=400, detail=prog.get("error", "Download gagal"))
            time.sleep(1)
        raise HTTPException(status_code=408, detail="Timeout menunggu download selesai")

    _active_tasks.add(task_id)
    download_progress[task_id] = {"status": "starting", "progress": 0.0}

    def _dl(dl_url: str, dest: str, prog_start: float = 0.1, prog_end: float = 0.9):
        """Download URL ke file, update progress."""
        hdrs = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Referer': 'https://www.tiktok.com/',
            'Accept': '*/*',
            'Accept-Encoding': 'identity',
        }
        r = requests.get(dl_url, headers=hdrs, stream=True, timeout=60, allow_redirects=True)
        if r.status_code not in (200, 206):
            raise Exception(f"HTTP {r.status_code} saat download")
        total = int(r.headers.get('content-length', 0))
        got = 0
        with open(dest, 'wb') as fh:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)
                    got += len(chunk)
                    if total > 0:
                        pct = prog_start + (got / total) * (prog_end - prog_start)
                        download_progress[task_id] = {
                            "status": "downloading",
                            "progress": pct,
                            "total": f"{total / 1048576:.1f}MB",
                        }
        if got < 10240:
            raise Exception(f"File terlalu kecil ({got} bytes), URL mungkin expired.")
        return got

    try:
        clean_url = url.split('?')[0] if '?' in url else url
        print(f"Downloading TikTok: {clean_url}")

        # ── Panggil TikWM langsung untuk dapat music_url juga ─────────────
        api_resp = requests.post(
            "https://www.tikwm.com/api/",
            data={"url": clean_url, "hd": 1},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        raw = api_resp.json()
        if raw.get('code') != 0:
            raise Exception(f"TikWM: {raw.get('msg', 'unknown error')}")

        vdata       = raw['data']
        is_photo    = bool(vdata.get('images'))
        play_url    = vdata.get('play', '')
        hdplay_url  = vdata.get('hdplay', '')
        music_url   = vdata.get('music', '')   # audio terpisah
        hd_size     = vdata.get('hd_size', 0)
        sd_size     = vdata.get('size', 0)
        title       = vdata.get('title', 'tiktok')

        # ── Foto / slideshow ──────────────────────────────────────────────
        if is_photo:
            images = vdata.get('images', [])
            # format_id: "img_0", "img_1", ...
            idx = 0
            try:
                idx = int(format_id.replace('img_', ''))
            except Exception:
                pass
            img_url = images[idx] if idx < len(images) else images[0]
            img_path = f"{base_name}_img{idx}.jpg"
            _dl(img_url, img_path)
            download_progress[task_id] = {"status": "completed", "progress": 1.0}

            import time
            safe = f"tiktok_{int(time.time())}_img{idx + 1}"

            def cleanup_all():
                cleanup_files(base_name)
                download_progress.pop(task_id, None)
            background_tasks.add_task(cleanup_all)

            from urllib.parse import quote
            utf8 = quote(f"{safe}.jpg", safe='')
            return FileResponse(
                img_path, media_type="image/jpeg",
                headers={"Content-Disposition": f'attachment; filename="{safe}.jpg"; filename*=UTF-8\'\'{utf8}'},
            )

        # ── Video ─────────────────────────────────────────────────────────
        # Pilih video URL
        if format_id == 'sd' and play_url:
            video_url = play_url
        else:
            video_url = hdplay_url or play_url

        if not video_url:
            raise Exception("Tidak ada URL video tersedia")

        video_path = f"{base_name}_video.mp4"
        download_progress[task_id] = {"status": "downloading", "progress": 0.05}

        # Download video asli (sudah termasuk audio asli)
        _dl(video_url, video_path, prog_start=0.05, prog_end=1.0)

        # Pakai video langsung tanpa merge apapun
        final_path = video_path

        download_progress[task_id] = {"status": "completed", "progress": 1.0}

        import time
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:60] or f"tiktok_{int(time.time())}"

        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)
            _active_tasks.discard(task_id)
        background_tasks.add_task(cleanup_all)

        from urllib.parse import quote
        ascii_name = safe_title.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
        utf8_name  = quote(f"{safe_title}.mp4", safe='')

        # Simpan final_path supaya request duplikat bisa serve ulang
        download_progress[task_id]["final_path"] = final_path

        return FileResponse(
            final_path,
            media_type="video/mp4",
            headers={"Content-Disposition": f'attachment; filename="{ascii_name}.mp4"; filename*=UTF-8\'\'{utf8_name}'},
        )

    except HTTPException:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        _active_tasks.discard(task_id)
        raise
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        _active_tasks.discard(task_id)
        print(f"Error download TikTok: {e}")
        raise HTTPException(status_code=400, detail=f"Gagal download TikTok: {e}")


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

@app.get("/tiktok/download/mp3")
def download_tiktok_mp3(url: str, background_tasks: BackgroundTasks, task_id: Optional[str] = None):
    """Download TikTok sebagai MP3 audio."""
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_mp3_{task_id}"
    download_progress[task_id] = {"status": "starting", "progress": 0.0}
    
    def _dl(dl_url: str, dest: str, prog_start: float = 0.1, prog_end: float = 0.7):
        """Download URL ke file, update progress."""
        hdrs = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Referer': 'https://www.tiktok.com/',
            'Accept': '*/*',
            'Accept-Encoding': 'identity',
        }
        r = requests.get(dl_url, headers=hdrs, stream=True, timeout=60, allow_redirects=True)
        if r.status_code not in (200, 206):
            raise Exception(f"HTTP {r.status_code} saat download")
        total = int(r.headers.get('content-length', 0))
        got = 0
        with open(dest, 'wb') as fh:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)
                    got += len(chunk)
                    if total > 0:
                        pct = prog_start + (got / total) * (prog_end - prog_start)
                        download_progress[task_id] = {
                            "status": "downloading",
                            "progress": pct,
                            "total": f"{total / 1048576:.1f}MB",
                        }
        if got < 1024:
            raise Exception(f"File terlalu kecil ({got} bytes), URL mungkin expired.")
        return got

    try:
        clean_url = url.split('?')[0] if '?' in url else url
        print(f"Downloading TikTok MP3: {clean_url}")

        # Panggil TikWM API
        api_resp = requests.post(
            "https://www.tikwm.com/api/",
            data={"url": clean_url, "hd": 1},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        raw = api_resp.json()
        if raw.get('code') != 0:
            raise Exception(f"TikWM: {raw.get('msg', 'unknown error')}")

        vdata = raw['data']
        title = vdata.get('title', 'tiktok')
        
        # Prioritaskan: download video lalu extract audio (untuk audio asli)
        # Fallback: gunakan music_url jika tersedia
        play_url = vdata.get('play', '')
        hdplay_url = vdata.get('hdplay', '')
        music_url = vdata.get('music', '')
        
        video_url = hdplay_url or play_url
        
        if not video_url and not music_url:
            raise Exception("Tidak ada media yang bisa di-download")

        # Download video (atau audio)
        if video_url:
            video_path = f"{base_name}_video.mp4"
            download_progress[task_id] = {"status": "downloading", "progress": 0.05}
            _dl(video_url, video_path, prog_start=0.05, prog_end=0.65)
            
            # Extract audio menjadi MP3
            mp3_path = f"{base_name}.mp3"
            download_progress[task_id] = {"status": "processing", "progress": 0.70}
            
            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-i', video_path,
                '-vn',  # tanpa video
                '-acodec', 'libmp3lame',
                '-ab', '320k',  # bitrate tinggi
                '-ar', '44100',
                mp3_path
            ]
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0 or not os.path.isfile(mp3_path) or os.path.getsize(mp3_path) < 1024:
                raise Exception(f"Gagal extract audio: {result.stderr}")
        else:
            # Fallback: download music_url langsung
            audio_path = f"{base_name}_audio"
            download_progress[task_id] = {"status": "downloading", "progress": 0.05}
            _dl(music_url, audio_path, prog_start=0.05, prog_end=0.90)
            
            # Convert ke MP3 jika bukan MP3
            mp3_path = f"{base_name}.mp3"
            download_progress[task_id] = {"status": "processing", "progress": 0.92}
            
            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-i', audio_path,
                '-acodec', 'libmp3lame',
                '-ab', '320k',
                mp3_path
            ]
            subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=120)
            if not os.path.isfile(mp3_path) or os.path.getsize(mp3_path) < 1024:
                mp3_path = audio_path

        download_progress[task_id] = {"status": "completed", "progress": 1.0}

        import time
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:60] or f"tiktok_{int(time.time())}"

        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)
        background_tasks.add_task(cleanup_all)

        from urllib.parse import quote
        ascii_name = safe_title.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
        utf8_name  = quote(f"{safe_title}.mp3", safe='')

        return FileResponse(
            mp3_path,
            media_type="audio/mpeg",
            headers={"Content-Disposition": f'attachment; filename="{ascii_name}.mp3"; filename*=UTF-8\'\'{utf8_name}'},
        )

    except HTTPException:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        raise
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        print(f"Error download TikTok MP3: {e}")
        raise HTTPException(status_code=400, detail=f"Gagal download TikTok MP3: {e}")

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
            speed_bps = d.get('speed') or 0
            progress = downloaded / total if total > 0 else 0

            # Format total size
            total_mb = total / (1024 * 1024)
            total_str = f"{total_mb:.2f}MiB" if total_mb < 1024 else f"{total_mb/1024:.2f}GiB"

            # Format speed
            speed_str = ""
            if speed_bps:
                speed_mb = speed_bps / (1024 * 1024)
                speed_str = f"{speed_mb:.2f}MiB/s" if speed_mb < 1024 else f"{speed_mb/1024:.2f}GiB/s"

            download_progress[task_id] = {
                "status": "downloading",
                "progress": progress,
                "total": total_str,
                "speed": speed_str,
            }
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
                'player_client': ['ios', 'default'],
            }
        },
        'concurrent_fragment_downloads': 8,
        'http_chunk_size': 10485760,
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
    output_m4a = f"{base_name}.m4a"

    download_progress[task_id] = {"status": "starting", "progress": 0.0}

    def my_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            speed_bps = d.get('speed') or 0
            progress = downloaded / total if total > 0 else 0

            # Format total size
            total_mb = total / (1024 * 1024)
            total_str = f"{total_mb:.2f}MiB" if total_mb < 1024 else f"{total_mb/1024:.2f}GiB"

            # Format speed
            speed_str = ""
            if speed_bps:
                speed_mb = speed_bps / (1024 * 1024)
                speed_str = f"{speed_mb:.2f}MiB/s" if speed_mb < 1024 else f"{speed_mb/1024:.2f}GiB/s"

            download_progress[task_id] = {
                "status": "downloading",
                "progress": progress,
                "total": total_str,
                "speed": speed_str,
            }
        elif d['status'] == 'finished':
            download_progress[task_id] = {"status": "processing", "progress": 1.0}

    ydl_opts = {
        'format': '140/m4a/bestaudio',
        'outtmpl': f"{base_name}.%(ext)s",
        'quiet': True,
        'writethumbnail': True,
        'progress_hooks': [my_hook],
        'postprocessors': [
            {'key': 'FFmpegMetadata'},
            {'key': 'EmbedThumbnail'},
        ],
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'default'],
            }
        },
        'concurrent_fragment_downloads': 8,
        'http_chunk_size': 10485760,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'nocheckcertificate': True,
        **get_ydl_proxy_opts(),
    }

    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            ydl.download([url])

        # After downloading, the file should be base_name.m4a
        if not os.path.isfile(output_m4a):
            # Fallback: search for any file with this base
            files = glob.glob(f"{base_name}*")
            if not files:
                raise Exception("Audio file not found after download")
            output_actual = files[0]
        else:
            output_actual = output_m4a

        # Clean up any leftover temp files
        for leftover in glob.glob(f"{base_name}*"):
            if leftover != output_actual:
                try:
                    os.remove(leftover)
                except Exception:
                    pass

        safe_title = 'audio'
        if info:
            safe_title = re.sub(r'[\\/:*?"<>|]', '_', info.get('title', 'audio')).strip()
        if not safe_title:
            safe_title = str(uuid.uuid4())


        # HTTP headers only support latin-1 — use RFC 5987 encoding for Unicode filenames
        from urllib.parse import quote
        ascii_filename = safe_title.encode('ascii', errors='replace').decode('ascii').replace('?', '_')
        utf8_filename = quote(f"{safe_title}.m4a", safe='')
        content_disposition = (
            f'attachment; filename="{ascii_filename}.m4a"; '
            f"filename*=UTF-8''{utf8_filename}"
        )

        ext = output_actual.split('.')[-1]
        
        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)

        background_tasks.add_task(cleanup_all)
        download_progress[task_id] = {"status": "completed", "progress": 1.0}

        return FileResponse(
            output_actual,
            media_type=f"audio/{ext}",
            headers={"Content-Disposition": content_disposition},
        )
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        import traceback
        print(f"AUDIO ERROR: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=400, detail=str(e))

# --- Spotify Endpoints ---

def _fetch_spotify_info(track_url: str) -> Dict[str, Any]:
    """Call musicfab.io API to get Spotify track metadata and download URL."""
    import json as _json

    payload = _json.dumps({"url": track_url}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(payload)),
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    resp = requests.post(
        "https://musicfab.io/api/spotify",
        data=payload,
        headers=headers,
        timeout=30,
    )

    if resp.status_code != 200:
        raise Exception(f"API returned HTTP {resp.status_code}")

    data = resp.json()
    metadata = (data.get("data") or {}).get("metadata")
    if not metadata or not metadata.get("download"):
        raise Exception("Download URL not found in API response")

    raw_duration = metadata.get("duration")
    print(f"[Spotify] raw duration from API: {raw_duration!r} (type: {type(raw_duration).__name__})")

    return {
        "title": metadata.get("name") or "Unknown",
        "artist": metadata.get("artist") or "Unknown",
        "album": metadata.get("album") or "",
        "duration": raw_duration or 0,
        "thumbnail": metadata.get("image") or "",
        "download_url": metadata["download"],
    }


@app.get("/spotify/info")
def get_spotify_info(url: str):
    """Get Spotify track metadata and download URL."""
    try:
        if not url:
            raise HTTPException(status_code=400, detail="URL required")
        if "open.spotify.com/track" not in url:
            raise HTTPException(
                status_code=400,
                detail="Only Spotify track URLs are supported (open.spotify.com/track/...)",
            )

        info = _fetch_spotify_info(url)
        return {
            "title": info["title"],
            "artist": info["artist"],
            "album": info["album"],
            "duration": info["duration"],
            "thumbnail": info["thumbnail"],
            "download_url": info["download_url"],
            "platform": "spotify",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gagal mengambil info Spotify: {str(e)}")


@app.get("/spotify/download")
def download_spotify(url: str, background_tasks: BackgroundTasks, task_id: Optional[str] = None):
    """Download a Spotify track as MP3."""
    if not task_id:
        task_id = str(uuid.uuid4())
    base_name = f"temp_spotify_{task_id}"
    dest_path = f"{base_name}.mp3"

    download_progress[task_id] = {"status": "starting", "progress": 0.0}

    try:
        if not url:
            raise HTTPException(status_code=400, detail="URL required")
        if "open.spotify.com/track" not in url:
            raise HTTPException(
                status_code=400,
                detail="Only Spotify track URLs are supported (open.spotify.com/track/...)",
            )

        download_progress[task_id] = {"status": "downloading", "progress": 0.1, "total": "Fetching info...", "speed": ""}

        info = _fetch_spotify_info(url)

        download_progress[task_id] = {"status": "downloading", "progress": 0.2, "total": "Downloading...", "speed": ""}

        r = requests.get(
            info["download_url"],
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            stream=True,
            timeout=60,
        )
        if r.status_code != 200:
            raise Exception(f"Gagal download audio: HTTP {r.status_code}")

        total_size = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = 0.2 + (downloaded / total_size) * 0.8
                        total_mb = total_size / (1024 * 1024)
                        total_str = f"{total_mb:.1f}MB"
                        download_progress[task_id] = {
                            "status": "downloading",
                            "progress": progress,
                            "total": total_str,
                            "speed": "",
                        }

        download_progress[task_id] = {"status": "completed", "progress": 1.0}

        safe_title = _make_filename_slug(f"{info['artist']} - {info['title']}", max_len=60) or f"spotify_{task_id}"

        from urllib.parse import quote
        ascii_name = safe_title.encode("ascii", errors="replace").decode("ascii").replace("?", "_")
        utf8_name = quote(f"{safe_title}.mp3", safe="")
        content_disposition = (
            f'attachment; filename="{ascii_name}.mp3"; '
            f"filename*=UTF-8''{utf8_name}"
        )

        def cleanup_all():
            cleanup_files(base_name)
            download_progress.pop(task_id, None)
        background_tasks.add_task(cleanup_all)

        return FileResponse(
            dest_path,
            media_type="audio/mpeg",
            headers={"Content-Disposition": content_disposition},
        )

    except HTTPException:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        raise
    except Exception as e:
        cleanup_files(base_name)
        download_progress.pop(task_id, None)
        raise HTTPException(status_code=400, detail=f"Spotify download gagal: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("SERVER_PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
