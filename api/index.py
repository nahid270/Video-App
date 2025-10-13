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
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://mewayo8672:mewayo8672@cluster0.ozhvczp.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "7dc544d9253bccc3cfecc1c677f69819")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "Nahid421")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Nahid421")
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

    if categories_collection.count_documents({}) == 0:
        default_categories = ["Bangla", "Hindi", "English", "18+ Adult", "Korean", "Dual Audio", "Bangla Dubbed", "Hindi Dubbed", "Indonesian", "Horror", "Action", "Thriller", "Anime", "Romance", "Trending"]
        categories_collection.insert_many([{"name": cat} for cat in default_categories])

    movies.create_index([("title", "text")])
    print("SUCCESS: MongoDB text index checked/created.")

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
<meta name="description" content="Watch and download the latest movies and series on {{ website_name }}. Your ultimate entertainment hub.">
<meta name="keywords" content="movies, series, download, watch online, {{ website_name }}, bengali movies, hindi movies, english movies">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/swiper/swiper-bundle.min.css"/>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.2.0/css/all.min.css">
{{ ad_settings.ad_header | safe }}
<style>
  :root {
    --primary-color: #E50914; --bg-color: #141414; --card-bg: #1a1a1a;
    --text-light: #ffffff; --text-dark: #a0a0a0; --nav-height: 60px;
    --cyan-accent: #00FFFF; --yellow-accent: #FFFF00; --trending-color: #F83D61;
    --type-color: #00E599; --new-color: #ffc107;
    --search-accent-color: #00bfff;
  }
  @keyframes rgb-glow {
    0%   { border-color: #ff00de; box-shadow: 0 0 5px #ff00de, 0 0 10px #ff00de inset; }
    25%  { border-color: #00ffff; box-shadow: 0 0 7px #00ffff, 0 0 12px #00ffff inset; }
    50%  { border-color: #00ff7f; box-shadow: 0 0 5px #00ff7f, 0 0 10px #00ff7f inset; }
    75%  { border-color: #f83d61; box-shadow: 0 0 7px #f83d61, 0 0 12px #f83d61 inset; }
    100% { border-color: #ff00de; box-shadow: 0 0 5px #ff00de, 0 0 10px #ff00de inset; }
  }
  @keyframes pulse-glow {
    0%, 100% { color: var(--text-dark); text-shadow: none; }
    50% { color: var(--text-light); text-shadow: 0 0 10px var(--cyan-accent); }
  }
  html { box-sizing: border-box; } *, *:before, *:after { box-sizing: inherit; }
  body {font-family: 'Poppins', sans-serif;background-color: var(--bg-color);color: var(--text-light);overflow-x: hidden; padding-bottom: 70px;}
  a { text-decoration: none; color: inherit; } img { max-width: 100%; display: block; }
  .container { max-width: 1400px; margin: 0 auto; padding: 0 10px; }
  
  .main-header { position: fixed; top: 0; left: 0; width: 100%; height: var(--nav-height); display: flex; align-items: center; z-index: 1000; transition: background-color: 0.3s ease; background-color: rgba(0,0,0,0.7); backdrop-filter: blur(5px); }
  .header-content { display: flex; justify-content: space-between; align-items: center; width: 100%; }
  .logo { font-size: 1.8rem; font-weight: 700; color: var(--primary-color); }
  .menu-toggle { display: block; font-size: 1.8rem; cursor: pointer; background: none; border: none; color: white; z-index: 1001;}
  
  .nav-grid-container { padding: 15px 0; }
  .nav-grid { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; }
  .nav-grid-item {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: white;
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 0.75rem;
    font-weight: 500;
    text-transform: uppercase;
    text-decoration: none;
    transition: all 0.3s ease;
    background: linear-gradient(145deg, #d40a0a, #a00000);
    border: 1px solid #ff4b4b;
    box-shadow: 0 2px 8px -3px rgba(229, 9, 20, 0.6);
  }
  .nav-grid-item:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px -4px rgba(229, 9, 20, 0.9);
    filter: brightness(1.1);
  }
  .nav-grid-item i {
    margin-right: 6px;
    font-size: 1em;
    line-height: 1;
  }
  .icon-18 {
    font-family: sans-serif;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1.5px solid white;
    border-radius: 50%;
    width: 16px;
    height: 16px;
    font-size: 10px;
    line-height: 1;
    margin-right: 6px;
    font-weight: bold;
  }

  .home-search-section {
      padding: 10px 0 20px 0;
  }
  .home-search-form {
      display: flex;
      width: 100%;
      max-width: 800px;
      margin: 0 auto;
      border: 2px solid var(--search-accent-color);
      border-radius: 8px;
      overflow: hidden;
      background-color: var(--card-bg);
  }
  .home-search-input {
      flex-grow: 1;
      border: none;
      background-color: transparent;
      color: var(--text-light);
      padding: 12px 20px;
      font-size: 1rem;
      outline: none;
  }
  .home-search-input::placeholder {
      color: var(--text-dark);
  }
  .home-search-button {
      background-color: var(--search-accent-color);
      border: none;
      color: white;
      padding: 0 25px;
      cursor: pointer;
      font-size: 1.2rem;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background-color 0.2s ease;
  }
  .home-search-button:hover {
      filter: brightness(1.1);
  }

  @keyframes cyan-glow {
      0% { box-shadow: 0 0 15px 2px #00D1FF; } 50% { box-shadow: 0 0 25px 6px #00D1FF; } 100% { box-shadow: 0 0 15px 2px #00D1FF; }
  }
  .hero-slider-section { margin-bottom: 30px; }
  .hero-slider { width: 100%; aspect-ratio: 16 / 9; background-color: var(--card-bg); border-radius: 12px; overflow: hidden; animation: cyan-glow 5s infinite linear; }
  .hero-slider .swiper-slide { position: relative; display: block; }
  .hero-slider .hero-bg-img { position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; z-index: 1; }
  .hero-slider .hero-slide-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: linear-gradient(to top, rgba(0,0,0,0.8) 0%, rgba(0,0,0,0.5) 40%, transparent 70%); z-index: 2; }
  .hero-slider .hero-slide-content { position: absolute; bottom: 0; left: 0; width: 100%; padding: 20px; z-index: 3; color: white; }
  .hero-slider .hero-title { font-size: 1.5rem; font-weight: 700; margin: 0 0 5px 0; text-shadow: 2px 2px 4px rgba(0,0,0,0.7); }
  .hero-slider .hero-meta { font-size: 0.9rem; margin: 0; color: var(--text-dark); }
  .hero-slide-content .hero-type-tag { position: absolute; bottom: 20px; right: 20px; background: linear-gradient(45deg, #00FFA3, #00D1FF); color: black; padding: 5px 15px; border-radius: 50px; font-size: 0.75rem; font-weight: 700; z-index: 4; text-transform: uppercase; box-shadow: 0 4px 10px rgba(0, 255, 163, 0.2); }
  .hero-slider .swiper-pagination { position: absolute; bottom: 10px !important; left: 20px !important; width: auto !important; }
  .hero-slider .swiper-pagination-bullet { background: rgba(255, 255, 255, 0.5); width: 8px; height: 8px; opacity: 0.7; transition: all 0.2s ease; }
  .hero-slider .swiper-pagination-bullet-active { background: var(--text-light); width: 24px; border-radius: 5px; opacity: 1; }
  
  .platform-section { margin: 40px 0; overflow: hidden; }
  .platform-slider .swiper-slide { width: 100px; }
  .platform-item { display: flex; flex-direction: column; align-items: center; justify-content: center; text-decoration: none; color: var(--text-dark); transition: transform 0.2s ease, color 0.2s ease; }
  .platform-item:hover { transform: scale(1.08); color: var(--text-light); }
  .platform-logo-wrapper { width: 80px; height: 80px; border-radius: 50%; background-color: #fff; display: flex; align-items: center; justify-content: center; margin-bottom: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); border: 2px solid #444; }
  .platform-logo-wrapper img { max-width: 70%; max-height: 70%; object-fit: contain; }
  .platform-item span { font-weight: 500; font-size: 0.8rem; text-align: center; }

  .category-section { margin: 30px 0; }
  .category-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
  .category-title { font-size: 1.5rem; font-weight: 600; display: inline-block; padding: 8px 20px; background-color: rgba(26, 26, 26, 0.8); border: 2px solid; border-radius: 50px; animation: rgb-glow 4s linear infinite; backdrop-filter: blur(3px); }
  .view-all-link { font-size: 0.9rem; color: var(--text-dark); font-weight: 500; padding: 6px 15px; border-radius: 20px; background-color: #222; transition: all 0.3s ease; animation: pulse-glow 2.5s ease-in-out infinite; }
  .category-grid, .full-page-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }

  .movie-card {
    display: flex;
    flex-direction: column;
    border-radius: 8px;
    overflow: hidden;
    background-color: var(--card-bg);
    border: 2px solid transparent;
    transition: transform 0.2s ease, box-shadow 0.2s ease;
  }
  .movie-card:hover {
      transform: translateY(-5px);
      box-shadow: 0 8px 20px rgba(0, 255, 255, 0.2);
  }
  .poster-wrapper { position: relative; }
  .movie-poster { width: 100%; aspect-ratio: 2 / 3; object-fit: cover; display: block; }
  .card-info { padding: 10px; background-color: var(--card-bg); }
  .card-title {
    font-size: 0.9rem; font-weight: 500; color: var(--text-light);
    margin: 0 0 5px 0; line-height: 1.4; min-height: 2.8em;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  
  .card-meta { 
    font-size: 0.75rem; 
    color: var(--text-dark); 
    display: flex; 
    align-items: center; 
    justify-content: space-between;
  }
  .card-meta span {
      display: flex;
      align-items: center;
      gap: 5px;
  }
  .card-meta i { 
      color: var(--cyan-accent); 
  }

  .type-tag, .language-tag {
    position: absolute; color: white; padding: 2px 8px; font-size: 0.65rem; font-weight: 600; z-index: 2; text-transform: uppercase; border-radius: 4px;
  }
  .language-tag { padding: 2px 6px; font-size: 0.6rem; top: 8px; right: 8px; background-color: rgba(0,0,0,0.6); }
  .type-tag { bottom: 8px; right: 8px; background-color: var(--type-color); }
  .new-badge {
    position: absolute; top: 0; left: 0; background-color: var(--primary-color);
    color: white; padding: 4px 12px 4px 8px; font-size: 0.7rem; font-weight: 700;
    z-index: 3; clip-path: polygon(0 0, 100% 0, 85% 100%, 0 100%);
  }

  .full-page-grid-container { padding: 80px 10px 20px; }
  .full-page-grid-title { font-size: 1.8rem; font-weight: 700; margin-bottom: 20px; text-align: center; }
  .main-footer { background-color: #111; padding: 20px; text-align: center; color: var(--text-dark); margin-top: 30px; font-size: 0.8rem; }
  .ad-container { margin: 20px auto; width: 100%; max-width: 100%; display: flex; justify-content: center; align-items: center; overflow: hidden; min-height: 50px; text-align: center; }
  .ad-container > * { max-width: 100% !important; }
  .mobile-nav-menu {position: fixed;top: 0;left: 0;width: 100%;height: 100%;background-color: var(--bg-color);z-index: 9999;display: flex;flex-direction: column;align-items: center;justify-content: center;transform: translateX(-100%);transition: transform 0.3s ease-in-out;}
  .mobile-nav-menu.active {transform: translateX(0);}
  .mobile-nav-menu .close-btn {position: absolute;top: 20px;right: 20px;font-size: 2.5rem;color: white;background: none;border: none;cursor: pointer;}
  .mobile-links {display: flex;flex-direction: column;text-align: center;gap: 25px;}
  .mobile-links a {font-size: 1.5rem;font-weight: 500;color: var(--text-light);transition: color 0.2s;}
  .mobile-links a:hover {color: var(--primary-color);}
  .mobile-links hr {width: 50%;border-color: #333;margin: 10px auto;}
  .bottom-nav { display: flex; position: fixed; bottom: 0; left: 0; right: 0; height: 65px; background-color: #181818; box-shadow: 0 -2px 10px rgba(0,0,0,0.5); z-index: 1000; justify-content: space-around; align-items: center; padding-top: 5px; }
  .bottom-nav .nav-item { display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-dark); background: none; border: none; font-size: 12px; flex-grow: 1; font-weight: 500; }
  .bottom-nav .nav-item i { font-size: 22px; margin-bottom: 5px; }
  .bottom-nav .nav-item.active, .bottom-nav .nav-item:hover { color: var(--primary-color); }
  .search-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.95); z-index: 10000; display: none; flex-direction: column; padding: 20px; }
  .search-overlay.active { display: flex; }
  .search-container { width: 100%; max-width: 800px; margin: 0 auto; }
  .close-search-btn { position: absolute; top: 20px; right: 20px; font-size: 2.5rem; color: white; background: none; border: none; cursor: pointer; }
  #search-input-live { width: 100%; padding: 15px; font-size: 1.2rem; border-radius: 8px; border: 2px solid var(--primary-color); background: var(--card-bg); color: white; margin-top: 60px; }
  #search-results-live { margin-top: 20px; max-height: calc(100vh - 150px); overflow-y: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 15px; }
  .search-result-item { color: white; text-align: center; }
  .search-result-item img { width: 100%; aspect-ratio: 2 / 3; object-fit: cover; border-radius: 5px; margin-bottom: 5px; }
  .pagination { display: flex; justify-content: center; align-items: center; gap: 10px; margin: 30px 0; }
  .pagination a, .pagination span { padding: 8px 15px; border-radius: 5px; background-color: var(--card-bg); color: var(--text-dark); font-weight: 500; }
  .pagination a:hover { background-color: #333; }
  .pagination .current { background-color: var(--primary-color); color: white; }

  @media (min-width: 769px) { 
    .container { padding: 0 40px; } .main-header { padding: 0 40px; }
    body { padding-bottom: 0; } .bottom-nav { display: none; }
    .hero-slider .hero-title { font-size: 2.2rem; }
    .hero-slider .hero-slide-content { padding: 40px; }
    .category-grid { grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }
    .full-page-grid { grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
    .full-page-grid-container { padding: 120px 40px 20px; }
  }
</style>
</head>
<body>
{{ ad_settings.ad_body_top | safe }}
<header class="main-header">
    <div class="container header-content">
        <a href="{{ url_for('home') }}" class="logo">{{ website_name }}</a>
        <button class="menu-toggle"><i class="fas fa-bars"></i></button>
    </div>
</header>
<div class="mobile-nav-menu">
    <button class="close-btn">&times;</button>
    <div class="mobile-links">
        <a href="{{ url_for('home') }}">Home</a>
        <a href="{{ url_for('all_series') }}">All Web Series</a>
        <a href="{{ url_for('all_movies') }}">All Movies</a>
        <hr>
        <a href="{{ developer_telegram_id }}" target="_blank">How to Create Website</a>
    </div>
</div>
<main>
  {% macro render_movie_card(m) %}
    <a href="{{ url_for('movie_detail', movie_id=m._id) }}" class="movie-card">
      <div class="poster-wrapper">
        {% if (datetime.utcnow() - m._id.generation_time.replace(tzinfo=None)).days < 7 %}
            <span class="new-badge">NEW</span>
        {% endif %}
        {% if m.language %}<span class="language-tag">{{ m.language }}</span>{% endif %}
        <img class="movie-poster" loading="lazy" src="{{ m.poster or 'https://via.placeholder.com/400x600.png?text=No+Image' }}" alt="{{ m.title }}">
        <span class="type-tag">{{ m.type | title }}</span>
      </div>
      <div class="card-info">
        <h4 class="card-title">
          {{ m.title }}
          {% if m.release_date %} ({{ m.release_date.split('-')[0] }}){% endif %}
        </h4>
        <p class="card-meta">
          <span><i class="fas fa-clock"></i> {{ m._id | time_ago }}</span>
          <span><i class="fas fa-eye"></i> {{ '{:,.0f}'.format(m.view_count or 0) }}</span>
        </p>
      </div>
    </a>
  {% endmacro %}

  {% if is_full_page_list %}
    <div class="full-page-grid-container">
        <h2 class="full-page-grid-title">{{ query }}</h2>
        {% if movies|length == 0 %}<p style="text-align:center;">No content found.</p>
        {% else %}
        <div class="full-page-grid">{% for m in movies %}{{ render_movie_card(m) }}{% endfor %}</div>
        {% if pagination and pagination.total_pages > 1 %}
        <div class="pagination">
            {% set url_args = {'page': pagination.prev_num} %}
            {% if 'category' in request.endpoint %}{% set _ = url_args.update({'name': query}) %}{% endif %}
            {% if 'platform' in request.endpoint %}{% set _ = url_args.update({'platform_name': query.replace(' Originals', '')}) %}{% endif %}
            {% if pagination.has_prev %}<a href="{{ url_for(request.endpoint, **url_args) }}">&laquo; Prev</a>{% endif %}
            
            <span class="current">Page {{ pagination.page }} of {{ pagination.total_pages }}</span>
            
            {% set url_args = {'page': pagination.next_num} %}
            {% if 'category' in request.endpoint %}{% set _ = url_args.update({'name': query}) %}{% endif %}
            {% if 'platform' in request.endpoint %}{% set _ = url_args.update({'platform_name': query.replace(' Originals', '')}) %}{% endif %}
            {% if pagination.has_next %}<a href="{{ url_for(request.endpoint, **url_args) }}">Next &raquo;</a>{% endif %}
        </div>
        {% endif %}
        {% endif %}
    </div>
  {% else %}
    <div style="height: var(--nav-height);"></div>
    
    <section class="nav-grid-container container">
        <div class="nav-grid">
            <a href="{{ url_for('home') }}" class="nav-grid-item">
                <i class="fas {{ category_icons.get('HOME', 'fa-tag') }}"></i> HOME
            </a>
            {% for cat in predefined_categories %}
                <a href="{{ url_for('movies_by_category', name=cat) }}" class="nav-grid-item">
                    {% if '18+' in cat %}
                        <span class="icon-18">18</span>
                    {% else %}
                        <i class="fas {{ category_icons.get(cat, 'fa-tag') }}"></i>
                    {% endif %}
                    {{ cat }}
                </a>
            {% endfor %}
            <a href="{{ url_for('all_movies') }}" class="nav-grid-item">
                <i class="fas {{ category_icons.get('ALL MOVIES', 'fa-tag') }}"></i> ALL MOVIES
            </a>
            <a href="{{ url_for('all_series') }}" class="nav-grid-item">
                <i class="fas {{ category_icons.get('WEB SERIES & TV SHOWS', 'fa-tag') }}"></i> WEB SERIES & TV SHOWS
            </a>
        </div>
    </section>

    <section class="home-search-section container">
        <form action="{{ url_for('home') }}" method="get" class="home-search-form">
            <input type="text" name="q" class="home-search-input" placeholder="Search for your favorite content...">
            <button type="submit" class="home-search-button" aria-label="Search">
                <i class="fas fa-search"></i>
            </button>
        </form>
    </section>

    {% if slider_content %}
    <section class="hero-slider-section container">
        <div class="swiper hero-slider">
            <div class="swiper-wrapper">
                {% for item in slider_content %}
                <div class="swiper-slide">
                    <a href="{{ url_for('movie_detail', movie_id=item._id) }}">
                        <img src="{{ item.backdrop or item.poster }}" class="hero-bg-img" alt="{{ item.title }}">
                        <div class="hero-slide-overlay"></div>
                        <div class="hero-slide-content">
                            <h2 class="hero-title">{{ item.title }}</h2>
                            <p class="hero-meta">
                                {% if item.release_date %}{{ item.release_date.split('-')[0] }}{% endif %}
                            </p>
                            <span class="hero-type-tag">{{ item.type | title }}</span>
                        </div>
                    </a>
                </div>
                {% endfor %}
            </div>
            <div class="swiper-pagination"></div>
        </div>
    </section>
    {% endif %}

    {% if all_ott_platforms %}
    <section class="platform-section container">
        <div class="swiper platform-slider">
            <div class="swiper-wrapper">
                {% for platform in all_ott_platforms %}
                <div class="swiper-slide">
                    <a href="{{ url_for('movies_by_platform', platform_name=platform.name) }}" class="platform-item">
                        <div class="platform-logo-wrapper">
                            <img src="{{ platform.logo_url }}" alt="{{ platform.name }} Logo">
                        </div>
                        <span>{{ platform.name }}</span>
                    </a>
                </div>
                {% endfor %}
            </div>
        </div>
    </section>
    {% endif %}

    <div class="container">
      {% macro render_grid_section(title, movies_list, cat_name) %}
          {% if movies_list %}
          <section class="category-section">
              <div class="category-header">
                  <h2 class="category-title">{{ title }}</h2>
                  <a href="{{ url_for('movies_by_category', name=cat_name) }}" class="view-all-link">View All &rarr;</a>
              </div>
              <div class="category-grid">
                  {% for m in movies_list %}
                      {{ render_movie_card(m) }}
                  {% endfor %}
              </div>
          </section>
          {% endif %}
      {% endmacro %}
      
      {% if categorized_content['Trending'] %}
      {{ render_grid_section('Trending Now', categorized_content['Trending'], 'Trending') }}
      {% endif %}

      {% if latest_content %}
      <section class="category-section">
          <div class="category-header">
              <h2 class="category-title">Recently Added</h2>
              <a href="{{ url_for('all_movies') }}" class="view-all-link">View All &rarr;</a>
          </div>
          <div class="category-grid">
              {% for m in latest_content %}
                  {{ render_movie_card(m) }}
              {% endfor %}
          </div>
      </section>
      {% endif %}

      {% if ad_settings.ad_list_page %}<div class="ad-container">{{ ad_settings.ad_list_page | safe }}</div>{% endif %}
      
      {% for cat_name, movies_list in categorized_content.items() %}
          {% if cat_name != 'Trending' %}
            {{ render_grid_section(cat_name, movies_list, cat_name) }}
          {% endif %}
      {% endfor %}
    </div>
  {% endif %}
</main>
<footer class="main-footer">
    <p>&copy; {{ datetime.now().year }} {{ website_name }}. All Rights Reserved.</p>
</footer>
<nav class="bottom-nav">
  <a href="{{ url_for('home') }}" class="nav-item active"><i class="fas fa-home"></i><span>Home</span></a>
  <a href="{{ url_for('all_movies') }}" class="nav-item"><i class="fas fa-layer-group"></i><span>Content</span></a>
  <a href="{{ url_for('request_content') }}" class="nav-item"><i class="fas fa-plus-circle"></i><span>Request</span></a>
  <button id="live-search-btn" class="nav-item"><i class="fas fa-search"></i><span>Search</span></button>
</nav>
<div id="search-overlay" class="search-overlay">
  <button id="close-search-btn" class="close-search-btn">&times;</button>
  <div class="search-container">
    <input type="text" id="search-input-live" placeholder="Type to search..." autocomplete="off">
    <div id="search-results-live"><p style="color: #555; text-align: center;">Start typing to see results</p></div>
  </div>
</div>
<script src="https://unpkg.com/swiper/swiper-bundle.min.js"></script>
<script>
    // All JavaScript from your original index_html
</script>
{{ ad_settings.ad_footer | safe }}
</body></html>
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
<link rel="stylesheet" href="https://unpkg.com/swiper/swiper-bundle.min.css"/>
{{ ad_settings.ad_header | safe }}
<style>
  :root {
      --bg-color: #0d0d0d; --card-bg: #1a1a1a; --text-light: #ffffff; --text-dark: #8c8c8c;
      --primary-color: #E50914; --cyan-accent: #00FFFF; --g-1: #ff00de; --g-2: #00ffff;
      --plyr-color-main: var(--primary-color);
  }
  body { font-family: 'Poppins', sans-serif; background-color: var(--bg-color); color: var(--text-light); margin:0; padding:0; }
  .container { max-width: 900px; margin: 0 auto; padding: 20px 15px; }
  .back-link { display: inline-block; margin-bottom: 20px; padding: 8px 15px; background-color: var(--card-bg); color: var(--text-dark); border-radius: 50px; text-decoration: none; font-size: 0.9rem; }
  .hero-section { position: relative; margin: 20px auto 80px; aspect-ratio: 16 / 9; background-size: cover; background-position: center; border-radius: 12px; box-shadow: 0 0 25px rgba(0, 255, 255, 0.4); }
  .hero-poster { position: absolute; left: 30px; bottom: -60px; height: 95%; aspect-ratio: 2 / 3; object-fit: cover; border-radius: 8px; box-shadow: 0 8px 25px rgba(0,0,0,0.6); }
  .main-title { font-family: 'Oswald', sans-serif; font-size: clamp(1.8rem, 5vw, 2.5rem); color: var(--cyan-accent); text-transform: uppercase; text-align: center; margin: 10px 0 30px; }
  .tabs-nav { display: flex; justify-content: center; gap: 10px; margin-bottom: 30px; }
  .tab-link { flex: 1; max-width: 200px; padding: 12px; background-color: var(--card-bg); border: none; color: var(--text-dark); font-weight: 600; font-size: 1rem; border-radius: 8px; cursor: pointer; }
  .tab-link.active { background-color: var(--primary-color); color: var(--text-light); }
  .tab-pane { display: none; } .tab-pane.active { display: block; }
  #info-pane p { font-size: 0.95rem; line-height: 1.8; color: var(--text-dark); background-color: var(--card-bg); padding: 20px; border-radius: 8px; }
  .link-group, .episode-list { display: flex; flex-direction: column; gap: 10px; }
  .episode-list h3 { font-size: 1.2rem; margin-bottom: 10px; color: var(--text-dark); text-align: center; }
  .action-btn { display: flex; justify-content: space-between; align-items: center; width: 100%; padding: 15px 20px; border-radius: 8px; font-weight: 500; font-size: 1rem; color: white; background: linear-gradient(90deg, var(--g-1), var(--g-2), var(--g-1)); background-size: 200% 100%; transition: background-position 0.5s ease; cursor: pointer; border: none; text-decoration: none; text-align: left;}
  .action-btn:hover { background-position: 100% 0; }
  .video-modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background-color: rgba(0, 0, 0, 0.9); z-index: 9999; display: none; justify-content: center; align-items: center; }
  .video-modal-content { position: relative; width: 95%; max-width: 900px; }
  .close-modal-btn { position: absolute; top: -40px; right: -5px; font-size: 2.5rem; color: white; background: transparent; border: none; cursor: pointer; }
  .category-section { margin-top: 50px; } .category-title { font-size: 1.5rem; margin-bottom: 20px; }
  .movie-card .movie-poster { width: 100%; aspect-ratio: 2/3; object-fit: cover; border-radius: 8px; }
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
    <h1 class="main-title">{{ movie.title }}</h1>
    <nav class="tabs-nav">
        <button class="tab-link active" data-tab="watch-pane">Watch & Download</button>
        <button class="tab-link" data-tab="info-pane">Info</button>
    </nav>
    <div class="tabs-content">
        <div class="tab-pane" id="info-pane"><p>{{ movie.overview or 'No description available.' }}</p></div>
        <div class="tab-pane active" id="watch-pane">
            {% if ad_settings.ad_detail_page %}<div style="margin-bottom: 20px;">{{ ad_settings.ad_detail_page | safe }}</div>{% endif %}
            
            {% if movie.type == 'movie' and movie.links %}
            <div class="link-group">
                {% for link in movie.links %}
                    {% if link.watch_url %}
                    <button class="action-btn watch-btn" data-url="{{ link.watch_url }}">
                        <span><i class="fas fa-play"></i> Watch ({{ link.quality }})</span>
                    </button>
                    {% endif %}
                    {% if link.download_url %}
                    <a href="{{ url_for('wait_page', target=quote(link.download_url)) }}" class="action-btn">
                        <span><i class="fas fa-download"></i> Download ({{ link.quality }})</span>
                    </a>
                    {% endif %}
                {% endfor %}
            </div>
            {% endif %}
            
            {% if movie.type == 'series' %}
                {% for season_num in ((movie.episodes | map(attribute='season') | list) + (movie.season_packs | map(attribute='season_number') | list)) | unique | sort %}
                <div class="episode-list" style="margin-bottom: 20px;">
                    <h3>Season {{ season_num }}</h3>
                    {% for pack in movie.season_packs if pack.season_number == season_num %}
                        {% if pack.watch_link %}
                        <button class="action-btn watch-btn" data-url="{{ pack.watch_link }}"><span><i class="fas fa-play"></i> Watch Full Season</span></button>
                        {% endif %}
                        {% if pack.download_link %}
                        <a href="{{ url_for('wait_page', target=quote(pack.download_link)) }}" class="action-btn"><span><i class="fas fa-download"></i> Download Full Season</span></a>
                        {% endif %}
                    {% endfor %}
                    {% for ep in movie.episodes | selectattr('season', 'equalto', season_num) | sort(attribute='episode_number') %}
                        {% if ep.watch_link %}
                        <button class="action-btn watch-btn" data-url="{{ ep.watch_link }}"><span><i class="fas fa-play"></i> Ep {{ ep.episode_number }}: {{ ep.title or 'Watch' }}</span></button>
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
                <p style="text-align:center; color: var(--text-dark);">No links available yet.</p>
            {% endif %}
        </div>
    </div>
    {% if related_content %}
    <section class="category-section">
        <h2 class="category-title">You Might Also Like</h2>
        <div class="swiper movie-carousel">
            <div class="swiper-wrapper">
                {% for m in related_content %}
                <div class="swiper-slide">
                    <a href="{{ url_for('movie_detail', movie_id=m._id) }}" class="movie-card">
                        <img class="movie-poster" src="{{ m.poster or PLACEHOLDER_POSTER }}" alt="{{ m.title }}">
                    </a>
                </div>
                {% endfor %}
            </div>
        </div>
    </section>
    {% endif %}
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
<script src="https://unpkg.com/swiper/swiper-bundle.min.js"></script>
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
    
    new Swiper('.movie-carousel', { slidesPerView: 3, spaceBetween: 15, breakpoints: { 640: { slidesPerView: 4 }, 768: { slidesPerView: 5 }, 1024: { slidesPerView: 6 } } });
    
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

# ... All other templates like admin_html, edit_html, etc. from your original code
# Just ensure the labels in admin/edit forms are changed to "Direct Stream Link"

# =========================================================================================
# === PYTHON FUNCTIONS & FLASK ROUTES (Final Version) =====================================
# =========================================================================================

# All other functions (get_tmdb_details, Pagination, routes for home, category, etc.)
# are the same as your original code. The only changes needed are in admin and edit routes.

@app.route('/admin', methods=["GET", "POST"])
@requires_auth
def admin():
    if request.method == "POST":
        form_action = request.form.get("form_action")
        
        # ... Other form actions from your code (update_ads, add_category, etc.)
        
        if form_action == "add_content":
            # All core detail fetching is the same as your code
            movie_data = {
                "title": request.form.get("title").strip(),
                "type": request.form.get("content_type", "movie"),
                # ... etc
            }

            # [MODIFIED PART]
            if movie_data["type"] == "movie":
                movie_data["links"] = []
                for q in ["480p", "720p", "1080p", "BLU-RAY"]:
                    watch_url = request.form.get(f"watch_link_{q}")
                    download_url = request.form.get(f"download_link_{q}")
                    if watch_url or download_url:
                        movie_data["links"].append({
                            "quality": q, 
                            "watch_url": watch_url.strip() if watch_url else None,
                            "download_url": download_url.strip() if download_url else None
                        })
            else: # Series
                # ... (Logic for episodes and season packs is the same as your code)
                pass
            
            # ... (Rest of the logic: manual links, DB insert, notification)
        return redirect(url_for('admin'))
    
    # GET request logic is the same as your original code
    return render_template_string(admin_html, ...)

@app.route('/edit_movie/<movie_id>', methods=["GET", "POST"])
@requires_auth
def edit_movie(movie_id):
    # ... GET request logic is the same
    if request.method == "POST":
        # ... Core details update logic is the same
        
        # [MODIFIED PART]
        if request.form.get("content_type") == "movie":
            update_data["links"] = []
            for q in ["480p", "720p", "1080p", "BLU-RAY"]:
                watch_url = request.form.get(f"watch_link_{q}")
                download_url = request.form.get(f"download_link_{q}")
                if watch_url or download_url:
                    update_data["links"].append({
                        "quality": q, 
                        "watch_url": watch_url.strip() if watch_url else None,
                        "download_url": download_url.strip() if download_url else None
                    })
        else: # Series
            # ... (Series update logic is the same as your code)
            pass

        # ... (Rest of the logic: update DB, notification)
        return redirect(url_for('admin'))
    
    return render_template_string(edit_html, ...)

# --- All other routes and API endpoints from your original code ---
# ...
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 3000))
    app.run(debug=True, host='0.0.0.0', port=port)
