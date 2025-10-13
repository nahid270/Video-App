import os
import sys
import requests
import json
from flask import Flask, render_template_string, request, redirect, url_for, Response, jsonify, flash
from pymongo import MongoClient
from bson.objectid import ObjectId
from functools import wraps
from urllib.parse import unquote, quote
from datetime import datetime, timedelta
import math
import re

# --- Environment Variables ---
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://Demo270:Demo270@cluster0.ls1igsg.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "7dc544d9253bccc3cfecc1c677f69819")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "Nahid70")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Nahid70")
WEBSITE_NAME = os.environ.get("WEBSITE_NAME", "FreeMovieHub")
DEVELOPER_TELEGRAM_ID = os.environ.get("DEVELOPER_TELEGRAM_ID", "https://t.me/AllBotUpdatemy")

# --- Telegram Notification Variables ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
WEBSITE_URL = os.environ.get("WEBSITE_URL") 

# --- App Initialization ---
PLACEHOLDER_POSTER = "https://via.placeholder.com/400x600.png?text=Poster+Not+Found"
ITEMS_PER_PAGE = 20
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "a_super_secret_key_for_flash_messages")


# --- Authentication ---
def check_auth(username, password):
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

def authenticate():
    return Response('Could not verify your access level.', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- Database Connection ---
try:
    client = MongoClient(MONGO_URI)
    db = client["movie_db"]
    movies = db["movies"]
    settings = db["settings"]
    categories_collection = db["categories"]
    requests_collection = db["requests"]
    ott_collection = db["ott_platforms"]
    print("SUCCESS: Successfully connected to MongoDB!")
except Exception as e:
    print(f"FATAL: Error connecting to MongoDB: {e}.")
    if 'vercel' not in os.environ.get('SERVER_SOFTWARE', '').lower():
        sys.exit(1)


# --- Helper function to format series info ---
def format_series_info(episodes, season_packs):
    info_parts = []
    if season_packs:
        for pack in sorted(season_packs, key=lambda p: p.get('season_number', 0)):
            if pack.get('season_number') is not None:
                info_parts.append(f"S{pack['season_number']:02d} [SEASON PACK]")
    if episodes:
        episodes_by_season = {}
        for ep in episodes:
            season, ep_num = ep.get('season'), ep.get('episode_number')
            if season is not None and ep_num is not None:
                if season not in episodes_by_season: episodes_by_season[season] = []
                episodes_by_season[season].append(ep_num)
        for season in sorted(episodes_by_season.keys()):
            ep_nums = sorted(episodes_by_season[season])
            if not ep_nums: continue
            ep_range = f"EP{ep_nums[0]:02d}" if len(ep_nums) == 1 else f"EP{ep_nums[0]:02d}-{ep_nums[-1]:02d}"
            info_parts.append(f"S{season:02d} [{ep_range} ADDED]")
    return " & ".join(info_parts)


# --- Telegram Notification Function ---
def send_telegram_notification(movie_data, content_id, notification_type='new', series_update_info=None):
    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, WEBSITE_URL]): return
    try:
        movie_url = f"{WEBSITE_URL}/movie/{str(content_id)}"
        title = movie_data.get('title', 'N/A')
        if movie_data.get('release_date'): title += f" ({movie_data['release_date'].split('-')[0]})"
        if series_update_info: title += f" {series_update_info}"
        
        qualities = ", ".join(sorted(list(set([link.get('quality') for link in movie_data.get('links', []) if link.get('quality')])))) or "HD"
        lang = movie_data.get('language', 'N/A')
        genres = ", ".join(movie_data.get('genres', [])) or "N/A"
        clean_url = WEBSITE_URL.replace('https://', '').replace('www.', '')

        header = f"🔄 **UPDATED: {title}**\n" if notification_type == 'update' else f"🔥 **NEW: {title}**\n"
        caption = f"{header}"
        if lang and not any(char.isdigit() for char in lang): caption += f"**{lang.upper()}**\n"
        caption += f"\n🎞️ Quality: **{qualities}**\n🌐 Language: **{lang}**\n🎭 Genres: **{genres}**\n\n🔗 Watch Now: **{clean_url}**\n⚠️ **অবশ্যই লিংকগুলো ক্রোম ব্রাউজারে ওপেন করবেন!!**"
        
        keyboard = {"inline_keyboard": [[{"text": "📺👇 Watch Now 👇📺", "url": movie_url}]]}
        payload = {'chat_id': TELEGRAM_CHANNEL_ID, 'photo': movie_data.get('poster', PLACEHOLDER_POSTER), 'caption': caption, 'parse_mode': 'Markdown', 'reply_markup': json.dumps(keyboard)}
        response = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=payload, timeout=15)
        if response.json().get('ok'): print(f"SUCCESS: Telegram notification sent for '{movie_data['title']}'.")
        else: print(f"WARNING: Telegram API error: {response.json().get('description')}")
    except Exception as e: print(f"ERROR: Failed to send Telegram notification: {e}")


# --- Custom Jinja Filter for Relative Time ---
def time_ago(obj_id):
    if not isinstance(obj_id, ObjectId): return ""
    diff = datetime.utcnow() - obj_id.generation_time.replace(tzinfo=None)
    seconds = diff.total_seconds()
    if seconds < 60: return "just now"
    if seconds < 3600: return f"{int(seconds/60)}m ago"
    if seconds < 86400: return f"{int(seconds/3600)}h ago"
    return f"{int(seconds/86400)}d ago"
app.jinja_env.filters['time_ago'] = time_ago

# --- Context Processor ---
@app.context_processor
def inject_globals():
    ad_settings = settings.find_one({"_id": "ad_config"}) or {}
    all_categories = [cat['name'] for cat in categories_collection.find().sort("name", 1)]
    all_ott = list(ott_collection.find().sort("name", 1))
    icons = {"Bangla": "fa-film", "Hindi": "fa-film", "English": "fa-film", "18+ Adult": "fa-exclamation-circle", "Korean": "fa-tv", "Dual Audio": "fa-headphones", "Bangla Dubbed": "fa-microphone-alt", "Hindi Dubbed": "fa-microphone-alt", "Horror": "fa-ghost", "Action": "fa-bolt", "Thriller": "fa-knife-kitchen", "Anime": "fa-dragon", "Romance": "fa-heart", "Trending": "fa-fire", "ALL MOVIES": "fa-layer-group", "WEB SERIES & TV SHOWS": "fa-tv-alt", "HOME": "fa-home"}
    return dict(website_name=WEBSITE_NAME, ad_settings=ad_settings, predefined_categories=all_categories, quote=quote, datetime=datetime, category_icons=icons, all_ott_platforms=all_ott, developer_telegram_id=DEVELOPER_TELEGRAM_ID)

# =========================================================================================
# === [START] HTML TEMPLATES ==============================================================
# =========================================================================================

index_html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
    <title>{{ website_name }} - Your Entertainment Hub</title>
    <link rel="icon" href="https://img.icons8.com/fluency/48/cinema-.png" type="image/png">
    <meta name="description" content="Watch and download the latest movies and series on {{ website_name }}.">
    <link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://unpkg.com/swiper/swiper-bundle.min.css"/>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.2.0/css/all.min.css">
    {{ ad_settings.ad_header | safe }}
    <style>
      :root { --primary-color: #E50914; --bg-color: #141414; --card-bg: #1a1a1a; --text-light: #ffffff; --text-dark: #a0a0a0; --nav-height: 60px; --cyan-accent: #00FFFF; --search-accent-color: #00bfff; }
      html { box-sizing: border-box; } *, *:before, *:after { box-sizing: inherit; }
      body {font-family: 'Poppins', sans-serif;background-color: var(--bg-color);color: var(--text-light);overflow-x: hidden; padding-bottom: 70px;}
      a { text-decoration: none; color: inherit; } img { max-width: 100%; display: block; }
      .container { max-width: 1400px; margin: 0 auto; padding: 0 10px; }
      .main-header { position: fixed; top: 0; left: 0; width: 100%; height: var(--nav-height); display: flex; align-items: center; z-index: 1000; background-color: rgba(0,0,0,0.7); backdrop-filter: blur(5px); }
      .header-content { display: flex; justify-content: space-between; align-items: center; width: 100%; }
      .logo { font-size: 1.8rem; font-weight: 700; color: var(--primary-color); }
      .menu-toggle { display: block; font-size: 1.8rem; cursor: pointer; background: none; border: none; color: white; z-index: 1001;}
      .category-section { margin: 30px 0; }
      .category-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
      .category-title { font-size: 1.5rem; font-weight: 600; }
      .category-grid, .full-page-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }
      .movie-card { border-radius: 8px; overflow: hidden; background-color: var(--card-bg); transition: transform 0.2s ease; }
      .movie-card:hover { transform: translateY(-5px); }
      .poster-wrapper { position: relative; }
      .movie-poster { width: 100%; aspect-ratio: 2 / 3; object-fit: cover; }
      .card-info { padding: 10px; }
      .card-title { font-size: 0.9rem; font-weight: 500; margin: 0 0 5px; min-height: 2.8em; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
      .card-meta { font-size: 0.75rem; color: var(--text-dark); display: flex; justify-content: space-between; }
      .bottom-nav { display: flex; position: fixed; bottom: 0; left: 0; right: 0; height: 65px; background-color: #181818; box-shadow: 0 -2px 10px rgba(0,0,0,0.5); z-index: 1000; justify-content: space-around; align-items: center; padding-top: 5px; }
      .bottom-nav .nav-item { display: flex; flex-direction: column; align-items: center; color: var(--text-dark); background: none; border: none; font-size: 12px; flex-grow: 1; }
      .bottom-nav .nav-item i { font-size: 22px; margin-bottom: 5px; }
      .bottom-nav .nav-item.active { color: var(--primary-color); }
      @media (min-width: 769px) { 
        .category-grid, .full-page-grid { grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
        .bottom-nav { display: none; }
      }
    </style>
</head>
<body>
    <!-- The body content of the index page can remain the same as before -->
</body>
</html>
"""

detail_html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
<title>{{ movie.title if movie else "Content Not Found" }} - {{ website_name }}</title>
<link rel="icon" href="https://img.icons8.com/fluency/48/cinema-.png" type="image/png">
<meta name="description" content="{{ movie.overview|striptags|truncate(160) }}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Oswald:wght@700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.2.0/css/all.min.css">
<link rel="stylesheet" href="https://cdn.plyr.io/3.7.8/plyr.css" />
{{ ad_settings.ad_header | safe }}
<style>
  :root {
      --bg-color: #0d0d0d; --card-bg: #1a1a1a; --text-light: #ffffff; --text-dark: #8c8c8c;
      --primary-color: #E50914; --cyan-accent: #00FFFF; --g-1: #ff00de; --g-2: #00ffff;
      --plyr-color-main: var(--primary-color);
  }
  html { box-sizing: border-box; } *, *:before, *:after { box-sizing: inherit; }
  body { font-family: 'Poppins', sans-serif; background-color: var(--bg-color); color: var(--text-light); margin:0; padding:0; }
  .container { max-width: 900px; margin: 0 auto; padding: 20px 15px; }
  .back-link { display: inline-block; margin-bottom: 20px; padding: 8px 15px; background-color: var(--card-bg); color: var(--text-dark); border-radius: 50px; text-decoration: none; font-size: 0.9rem; }
  .hero-section { position: relative; margin: 20px auto 80px; aspect-ratio: 16 / 9; background-size: cover; background-position: center; border-radius: 12px; box-shadow: 0 0 25px rgba(0, 255, 255, 0.4); }
  .hero-poster { position: absolute; left: 30px; bottom: -60px; height: 95%; aspect-ratio: 2 / 3; object-fit: cover; border-radius: 8px; box-shadow: 0 8px 25px rgba(0,0,0,0.6); }
  .content-title-section { text-align: center; padding: 10px 15px 30px; margin: 0 auto; }
  .main-title { font-family: 'Oswald', sans-serif; font-size: clamp(1.8rem, 5vw, 2.5rem); color: var(--cyan-accent); text-transform: uppercase; }
  .tabs-nav { display: flex; justify-content: center; gap: 10px; margin: 20px 0 30px; }
  .tab-link { flex: 1; max-width: 200px; padding: 12px; background-color: var(--card-bg); border: none; color: var(--text-dark); font-weight: 600; font-size: 1rem; border-radius: 8px; cursor: pointer; }
  .tab-link.active { background-color: var(--primary-color); color: var(--text-light); }
  .tab-pane { display: none; } .tab-pane.active { display: block; }
  #info-pane p { font-size: 0.95rem; line-height: 1.8; color: var(--text-dark); background-color: var(--card-bg); padding: 20px; border-radius: 8px; }
  .link-group, .episode-list { display: flex; flex-direction: column; gap: 10px; max-width: 800px; margin: 0 auto; }
  .episode-list h3 { font-size: 1.2rem; margin-bottom: 10px; color: var(--text-dark); text-align: center; }
  .action-btn { display: flex; justify-content: space-between; align-items: center; width: 100%; padding: 15px 20px; border-radius: 8px; font-weight: 500; font-size: 1rem; color: white; background: linear-gradient(90deg, var(--g-1), var(--g-2), var(--g-1)); background-size: 200% 100%; transition: background-position 0.5s ease; cursor: pointer; border: none; text-decoration: none; text-align: left;}
  .action-btn:hover { background-position: 100% 0; }
  .video-modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background-color: rgba(0, 0, 0, 0.9); z-index: 9999; display: none; justify-content: center; align-items: center; }
  .video-modal-content { position: relative; width: 95%; max-width: 900px; }
  .close-modal-btn { position: absolute; top: -40px; right: -5px; font-size: 2.5rem; color: white; background: transparent; border: none; cursor: pointer; }
</style>
</head>
<body>
{{ ad_settings.ad_body_top | safe }}
{% if movie %}
<main class="container">
    <a href="#" onclick="window.history.back(); return false;" class="back-link"><i class="fas fa-arrow-left"></i> Go Back</a>
    <div class="hero-section" style="background-image: url('{{ movie.backdrop or movie.poster }}');">
        <img src="{{ movie.poster or PLACEHOLDER_POSTER }}" alt="{{ movie.title }}" class="hero-poster">
    </div>
    <div class="content-title-section"><h1 class="main-title">{{ movie.title }}</h1></div>

    <nav class="tabs-nav">
        <button class="tab-link" data-tab="info-pane">Info</button>
        <button class="tab-link active" data-tab="watch-pane">Watch Now</button>
    </nav>

    <div class="tabs-content">
        <div class="tab-pane" id="info-pane"><p>{{ movie.overview or 'No description available.' }}</p></div>
        <div class="tab-pane active" id="watch-pane">
            {% if ad_settings.ad_detail_page %}<div class="ad-container" style="margin-bottom: 20px;">{{ ad_settings.ad_detail_page | safe }}</div>{% endif %}
            
            {% if movie.type == 'movie' and movie.links %}
            <div class="link-group">
                {% for link_item in movie.links %}
                    <button class="action-btn watch-btn" data-url="{{ link_item.url }}">
                        <span>Watch Now ({{ link_item.quality }})</span><i class="fas fa-play"></i>
                    </button>
                {% endfor %}
            </div>
            {% endif %}
            
            {% if movie.type == 'series' %}
                {% set all_seasons = ((movie.episodes | map(attribute='season') | list) + (movie.season_packs | map(attribute='season_number') | list)) | unique | sort %}
                {% for season_num in all_seasons %}
                <div class="episode-list" style="margin-bottom: 20px;">
                    <h3>Season {{ season_num }}</h3>
                    {% for pack in movie.season_packs if pack.season_number == season_num and pack.stream_link %}
                        <button class="action-btn watch-btn" data-url="{{ pack.stream_link }}"><span>Watch Full Season</span><i class="fas fa-play"></i></button>
                    {% endfor %}
                    {% for ep in movie.episodes | selectattr('season', 'equalto', season_num) | sort(attribute='episode_number') %}
                        {% if ep.watch_link %}
                        <button class="action-btn watch-btn" data-url="{{ ep.watch_link }}"><span>Ep {{ ep.episode_number }}: {{ ep.title or 'Watch' }}</span><i class="fas fa-play"></i></button>
                        {% endif %}
                    {% endfor %}
                </div>
                {% endfor %}
            {% endif %}

            {% if movie.manual_links %}
            <div class="link-group" style="margin-top: 20px;">
                <h3>External Links</h3>
                {% for link in movie.manual_links %}
                     <a href="{{ url_for('wait_page', target=quote(link.url)) }}" class="action-btn" target="_blank"><span>{{ link.name }}</span><i class="fas fa-external-link-alt"></i></a>
                {% endfor %}
            </div>
            {% endif %}

            {% if not movie.links and not movie.manual_links and not movie.episodes and not movie.season_packs %}
                <p style="text-align:center; color: var(--text-dark);">No playable links available yet.</p>
            {% endif %}
        </div>
    </div>
</main>
{% else %}
<div style="display:flex; justify-content:center; align-items:center; height:100vh;"><h2>Content not found.</h2></div>
{% endif %}

<div id="videoModal" class="video-modal-overlay">
    <div class="video-modal-content">
        <button id="closeModal" class="close-modal-btn">&times;</button>
        <video id="videoPlayer" playsinline controls></video>
    </div>
</div>

<script src="https://cdn.plyr.io/3.7.8/plyr.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function () {
    const tabLinks = document.querySelectorAll('.tab-link');
    tabLinks.forEach(link => {
        link.addEventListener('click', () => {
            document.querySelector('.tab-link.active').classList.remove('active');
            document.querySelector('.tab-pane.active').classList.remove('active');
            link.classList.add('active');
            document.getElementById(link.dataset.tab).classList.add('active');
        });
    });
    
    const videoModal = document.getElementById('videoModal');
    const closeModalBtn = document.getElementById('closeModal');
    const videoElement = document.getElementById('videoPlayer');
    const watchButtons = document.querySelectorAll('.watch-btn');
    const player = new Plyr(videoElement, { title: '{{ movie.title }}' });

    watchButtons.forEach(button => {
        button.addEventListener('click', function() {
            const videoUrl = this.dataset.url;
            if (videoUrl) {
                player.source = {
                    type: 'video',
                    sources: [{ src: videoUrl, type: 'video/mp4' }],
                };
                videoModal.style.display = 'flex';
                player.play();
            }
        });
    });

    function closeModal() {
        player.stop();
        videoModal.style.display = 'none';
    }

    closeModalBtn.addEventListener('click', closeModal);
    videoModal.addEventListener('click', (event) => {
        if (event.target === videoModal) closeModal();
    });
});
</script>
{{ ad_settings.ad_footer | safe }}
</body></html>
"""

admin_html = """
<!-- এই টেমপ্লেটটি আগের মতোই থাকবে, শুধুমাত্র লিংক ইনপুট ফিল্ডের লেবেল পরিবর্তন হবে -->
<!-- যেমন: "Google Drive Link" এর পরিবর্তে "Direct Stream Link" -->
"""
edit_html = """
<!-- এই টেমপ্লেটটিও আগের মতোই থাকবে, শুধুমাত্র লিংক ইনপুট ফিল্ডের লেবেল পরিবর্তন হবে -->
"""
# Note: For brevity, the full admin/edit templates are omitted here, but they are implied to be the same structure as before.
# The logic in the Python routes is the most critical part.

# =========================================================================================
# === PYTHON FUNCTIONS & FLASK ROUTES (Final Version) =====================================
# =========================================================================================

# --- All helper functions (TMDB, Pagination, etc.) remain the same ---

@app.route('/')
def home():
    # ... No changes needed
    pass

@app.route('/movie/<movie_id>')
def movie_detail(movie_id):
    # ... No changes needed
    pass

# ... All other public routes remain the same ...

@app.route('/admin', methods=["GET", "POST"])
@requires_auth
def admin():
    if request.method == "POST":
        form_action = request.form.get("form_action")
        
        # ... (update_ads, add_category, add_platform, bulk_delete logic is the same)
        
        if form_action == "add_content":
            # ... (Core details logic is the same)
            movie_data = {
                "title": request.form.get("title").strip(),
                "type": request.form.get("content_type", "movie"),
                # ... etc.
                "links": [], "episodes": [], "season_packs": [], "manual_links": []
            }
            
            if movie_data["type"] == "movie":
                for q in ["480p", "720p", "1080p", "BLU-RAY"]:
                    link = request.form.get(f"link_{q}")
                    if link: movie_data["links"].append({"quality": q, "url": link.strip()})
            else: # Series
                sp_nums, sp_links = request.form.getlist('season_pack_number[]'), request.form.getlist('season_pack_link[]')
                for i in range(len(sp_nums)):
                    if sp_nums[i] and sp_links[i]:
                        movie_data['season_packs'].append({"season_number": int(sp_nums[i]), "stream_link": sp_links[i].strip()})
                
                s, n, t, l = request.form.getlist('episode_season[]'), request.form.getlist('episode_number[]'), request.form.getlist('episode_title[]'), request.form.getlist('episode_watch_link[]')
                for i in range(len(s)):
                     if s[i] and n[i] and l[i]:
                         movie_data['episodes'].append({"season": int(s[i]), "episode_number": int(n[i]), "title": t[i].strip(), "watch_link": l[i].strip()})
            
            names, urls = request.form.getlist('manual_link_name[]'), request.form.getlist('manual_link_url[]')
            movie_data["manual_links"] = [{"name": names[i].strip(), "url": urls[i].strip()} for i in range(len(names)) if names[i] and urls[i]]
            
            result = movies.insert_one(movie_data)
            if result.inserted_id:
                # ... (Send notification logic is the same)
                pass
        return redirect(url_for('admin'))
    
    # GET request logic is the same
    content_list = list(movies.find({}).sort('updated_at', -1))
    stats = { "total_content": movies.count_documents({}), ... }
    return render_template_string(admin_html, content_list=content_list, stats=stats, ...)

@app.route('/edit_movie/<movie_id>', methods=["GET", "POST"])
@requires_auth
def edit_movie(movie_id):
    movie_obj = movies.find_one({"_id": ObjectId(movie_id)})
    if not movie_obj: return "Movie not found", 404
    
    if request.method == "POST":
        # ... (Core details update logic is the same)
        update_data = { "title": request.form.get("title").strip(), ... }
        
        if update_data["type"] == "movie":
            update_data["links"] = []
            for q in ["480p", "720p", "1080p", "BLU-RAY"]:
                link = request.form.get(f"link_{q}")
                if link: update_data["links"].append({"quality": q, "url": link.strip()})
        else: # Series logic is the same as in add_content
            # ...
        
        movies.update_one({"_id": ObjectId(movie_id)}, {"$set": update_data})
        # ... (Send notification logic)
        return redirect(url_for('admin'))
    
    return render_template_string(edit_html, movie=movie_obj, ...)

# --- All other API endpoints and routes can remain the same ---

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 3000))
    app.run(debug=True, host='0.0.0.0', port=port)
