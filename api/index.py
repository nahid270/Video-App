import os
import requests
import json
import re # রেগুলার এক্সপ্রেশন ব্যবহারের জন্য
from flask import Flask, request, jsonify, Response, redirect, url_for
from pymongo import MongoClient
from datetime import datetime
from urllib.parse import quote

# =================================================================
# ১. কনফিগারেশন: আপনার তথ্য এখানে বসান
# =================================================================

# --- আপনার গোপন তথ্য (হার্ডকোডেড) ---
MONGO_URI = 'mongodb+srv://mewayo8672:mewayo8672@cluster0.ozhvczp.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0' 
TMDB_API_KEY = '7dc544d9253bccc3cfecc1c677f69819' 
TELEGRAM_BOT_TOKEN = '7769138300:AAE0qSFNOoQQxXsD7qtWuumHMgTkmAon3X8'

# --- আপনার অ্যাডমিন চ্যানেলের ID ---
# এখানে আপনার সংখ্যাভিত্তিক প্রাইভেট চ্যানেল আইডি (-100...) বসান
TELEGRAM_CHANNEL_ID = -1002878014870 # <-- CHANGE THIS to your actual private channel ID
# -------------------------------------------------------------

TMDB_IMAGE_BASE_URL = 'https://image.tmdb.org/t/p/w500'

# =================================================================
# ২. ডাটাবেস সংযোগ (ত্রুটি হ্যান্ডলিং সহ)
# =================================================================

client = None
movies_collection = None
try:
    client = MongoClient(MONGO_URI)
    db = client.get_database('eonmovies_db')
    movies_collection = db.get_collection('movies')
    movies_collection.create_index([('slug', 1)], unique=True)
except Exception as e:
    print(f"FATAL: Database connection error. Check MONGO_URI. Error: {e}")

# =================================================================
# ৩. সাহায্যকারী ফাংশন ও HTML টেমপ্লেট
# =================================================================

def create_slug(title, tmdb_id):
    slug = title.lower().replace(' ', '-')
    slug = ''.join(c for c in slug if c.isalnum() or c == '-')
    slug = slug.replace('--', '-')
    return f"{slug}-{tmdb_id}"

def get_download_link(file_id):
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"

# --- UI/UX স্টাইল (অপরিবর্তিত) ---
GLOBAL_STYLE = """
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0d0d0d; color: #FFFFFF; margin: 0; padding: 0; }
        .container { max-width: 1400px; margin: 20px auto; padding: 0 15px; }
        h1 { color: #00bcd4; border-bottom: 2px solid #00bcd4; padding-bottom: 10px; }
        .movie-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 25px; }
        .movie-card { background-color: #1a1a1a; border-radius: 8px; overflow: hidden; position: relative; box-shadow: 0 4px 10px rgba(0, 0, 0, 0.6); transition: transform 0.3s; }
        .movie-card:hover { transform: translateY(-8px); box-shadow: 0 8px 20px rgba(0, 0, 0, 0.8); }
        .movie-card a { text-decoration: none; color: inherit; }
        .movie-card img { width: 100%; height: 300px; object-fit: cover; }
        .tag { position: absolute; top: 10px; left: 10px; background-color: #ff9800; color: #1a1a1a; padding: 4px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }
        .series-tag { background-color: #e91e63; }
        .movie-card-info { padding: 10px; text-align: center; }
        .movie-card-info h3 { font-size: 1em; margin: 5px 0 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .detail-flex { display: flex; gap: 30px; margin-top: 30px; }
        .detail-poster { width: 300px; height: auto; border-radius: 8px; flex-shrink: 0; box-shadow: 0 0 15px rgba(0, 188, 212, 0.5); }
        .detail-info { flex-grow: 1; }
        .detail-title { color: #00bcd4; margin-bottom: 10px; }
        .detail-meta p { margin: 5px 0; color: #ccc; }
        .download-btn { display: inline-block; padding: 12px 30px; background-color: #4CAF50; color: white; text-decoration: none; border-radius: 6px; font-size: 1.1em; font-weight: bold; margin-top: 20px; transition: background-color 0.3s; }
        .download-btn:hover { background-color: #45a049; }
        .overview { line-height: 1.6; color: #ccc; }
    </style>
"""
HEADER_HTML = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Eon Movies Clone</title>
    {GLOBAL_STYLE}
</head>
<body>
<div class="container">
"""

FOOTER_HTML = """
</div>
</body>
</html>
"""

# =================================================================
# ৪. ফ্লাস্ক অ্যাপ্লিকেশন সেটআপ
# =================================================================

app = Flask(__name__)

# =================================================================
# ৫. ওয়েববুক/অটোমেশন রুট (POST API)
# =================================================================

@app.route('/api/telegram-webhook', methods=['POST'])
def telegram_webhook():
    """টেলিগ্রাম থেকে নতুন মুভি/সিরিজ পোস্ট ডেটা প্রক্রিয়া করে।"""
    if movies_collection is None:
        return jsonify({'status': 'error', 'message': 'Database not connected'}), 500
        
    try:
        update = request.get_json()
        
        if not update or not update.get('channel_post'):
            return jsonify({'status': 'ok', 'message': 'Not a channel post'}), 200

        post = update['channel_post']
        
        # --- ১. চ্যানেল আইডি চেক (নিখুঁত ফিল্টার) ---
        chat_id_from_post = post['chat']['id']

        # ID গুলি পূর্ণসংখ্যা (Integer) হিসেবে তুলনামূলকভাবে সুরক্ষিত
        if int(chat_id_from_post) != int(TELEGRAM_CHANNEL_ID):
             return jsonify({'status': 'ignored', 'message': f'Post from unauthorized channel: {chat_id_from_post}'}), 200

        # ডকুমেন্ট এবং ক্যাপশন আছে কিনা চেক (যেহেতু Document হিসেবে আপলোড করার কথা)
        if not post.get('document') or not post.get('caption'):
            # Vercel লগসে দেখা যাবে এই রিকোয়েস্টটি এসেছে, কিন্তু ইগনোর হয়েছে
            print("Webhook received, but missing file or caption.")
            return jsonify({'status': 'ok', 'message': 'Post is not a file or missing caption'}), 200
        
        caption = post.get('caption', '')
        file_id = post['document']['file_id']
        
        # ২. টাইটেল এক্সট্রাকশন ও টাইপ সনাক্তকরণ (উন্নত লজিক)
        
        # ক্যাপশনের প্রথম অংশ থেকে অপ্রয়োজনীয় শব্দ (যেমন 720p, Dual Audio) বাদ দিয়ে শুধু মুভির নাম বের করার চেষ্টা
        raw_title = caption.strip().split('\n')[0].strip()
        # টাইটেল থেকে বছর, রেজোলিউশন বা ট্যাগগুলো বাদ দেওয়া
        search_title_match = re.search(r'([\w\s\'\.\-&]+)\s*(\d{4})?', raw_title, re.IGNORECASE)
        search_title = (search_title_match.group(1).strip() if search_title_match else raw_title).replace('.', ' ')
        
        content_type = 'Movie' 
        
        if "#SERIES" in caption.upper():
            content_type = 'Web Series'
        elif "#MOVIE" in caption.upper():
            content_type = 'Movie'
            
        if not search_title or len(search_title) < 2:
            return jsonify({'status': 'error', 'message': 'No robust title found for search'}), 200

        # ৩. TMDB সার্চ লজিক
        tmdb_path = 'tv' if content_type == 'Web Series' else 'movie'
        tmdb_url = f"https://api.themoviedb.org/3/search/{tmdb_path}?api_key={TMDB_API_KEY}&query={quote(search_title)}"
        tmdb_response = requests.get(tmdb_url)
        tmdb_data = tmdb_response.json().get('results')
        
        # ফলব্যাক সার্চ (যদি প্রথম সার্চে না পাওয়া যায়)
        if not tmdb_data:
             tmdb_path_fallback = 'movie' if tmdb_path == 'tv' else 'tv'
             tmdb_url_fallback = f"https://api.themoviedb.org/3/search/{tmdb_path_fallback}?api_key={TMDB_API_KEY}&query={quote(search_title)}"
             tmdb_response_fallback = requests.get(tmdb_url_fallback)
             tmdb_data = tmdb_response_fallback.json().get('results')
             if tmdb_data: content_type = 'Movie' if tmdb_path_fallback == 'movie' else 'Web Series'
        
        if not tmdb_data:
            return jsonify({'status': 'error', 'message': f'Content "{search_title}" not found on TMDB'}), 200
        
        movie_data = tmdb_data[0]
        
        # ৪. ডেটাবেস সংরক্ষণের জন্য উন্নত ডেটা
        title = movie_data.get('title') or movie_data.get('name')
        slug = create_slug(title, movie_data.get('id'))
        release_date = movie_data.get('release_date') or movie_data.get('first_air_date')
        
        movie_doc = {
            'tmdb_id': movie_data.get('id'),
            'title': title,
            'content_type': content_type,
            'overview': movie_data.get('overview'),
            'poster_path': movie_data.get('poster_path'),
            'release_year': release_date.split('-')[0] if release_date else 'N/A',
            'original_language': movie_data.get('original_language', 'N/A').upper(), # ভাষা বড় হাতের অক্ষরে
            'telegram_file_id': file_id,
            'slug': slug,
            'uploaded_at': datetime.now()
        }
        
        movies_collection.update_one(
            {'slug': slug}, 
            {'$set': movie_doc}, 
            upsert=True
        )
        
        print(f"SUCCESS: Movie saved - {title}")
        return jsonify({'status': 'success', 'movie': title, 'type': content_type, 'slug': slug}), 200

    except Exception as e:
        print(f"CRITICAL WEBHOOK ERROR: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# =================================================================
# ৬. ফ্রন্টএন্ড রুটস (অপরিবর্তিত)
# =================================================================

@app.route('/')
def homepage():
    if movies_collection is None:
         return Response(HEADER_HTML + "<h1>Error</h1><p>Database connection failed.</p>" + FOOTER_HTML, mimetype='text/html'), 500
         
    try:
        movies = list(movies_collection.find().sort('uploaded_at', -1).limit(40))
        content = "<h1>🎬 Latest Uploads</h1>"
        content += '<div class="movie-grid">'
        
        if not movies:
            content += "<p>No content uploaded yet.</p>"
        else:
            for movie in movies:
                poster_url = f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else 'https://via.placeholder.com/500x750?text=No+Image'
                tag_class = 'series-tag' if movie.get('content_type') == 'Web Series' else ''
                
                card = f"""
                <div class="movie-card">
                    <a href="/t/{movie['slug']}">
                        <img src="{poster_url}" alt="{movie['title']}">
                        <span class="tag {tag_class}">{movie['content_type']}</span>
                        <div class="movie-card-info">
                            <h3>{movie['title']}</h3>
                        </div>
                    </a>
                </div>
                """
                content += card
        
        content += '</div>'
        return Response(HEADER_HTML + content + FOOTER_HTML, mimetype='text/html')

    except Exception as e:
        return Response(HEADER_HTML + f"<h1>Error</h1><p>Could not load content: {e}</p>" + FOOTER_HTML, mimetype='text/html'), 500


@app.route('/t/<slug>')
def movie_detail(slug):
    if movies_collection is None:
        return Response(HEADER_HTML + "<h1>Error</h1><p>Database connection failed.</p>" + FOOTER_HTML, mimetype='text/html'), 500
        
    try:
        movie = movies_collection.find_one({'slug': slug})
        
        if not movie:
            content = "<h2>404 - Content Not Found</h2>"
            return Response(HEADER_HTML + content + FOOTER_HTML, mimetype='text/html'), 404

        poster_url = f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else 'https://via.placeholder.com/500x750?text=No+Image'
        download_link = get_download_link(movie.get('telegram_file_id'))
        
        content = f"""
            <h1 class="detail-title">{movie['title']}</h1>
            
            <div class="detail-flex">
                <img src="{poster_url}" class="detail-poster" alt="{movie['title']}">
                
                <div class="detail-info">
                    <h2>{movie['title']}</h2>
                    
                    <div class="detail-meta">
                        <p><strong>Type:</strong> {movie['content_type']}</p>
                        <p><strong>Release Year:</strong> {movie.get('release_year', 'N/A')}</p>
                        <p><strong>Original Language:</strong> {movie.get('original_language', 'N/A')}</p>
                    </div>
                    
                    <h3>Overview:</h3>
                    <p class="overview">{movie['overview']}</p>
                    
                    <a href="{download_link}" class="download-btn" target="_blank">
                        ⬇️ Get File (Telegram)
                    </a>
                </div>
            </div>
        """
        
        return Response(HEADER_HTML + content + FOOTER_HTML, mimetype='text/html')

    except Exception as e:
        return Response(HEADER_HTML + f"<h1>Error</h1><p>Detail page error: {e}</p>" + FOOTER_HTML, mimetype='text/html'), 500


# =================================================================
# ৭. অ্যাপ্লিকেশন রান ব্লক
# =================================================================

if __name__ == '__main__':
    app.run(debug=True, port=5000)
