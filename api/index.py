import os
import requests
import json
from flask import Flask, request, jsonify, Response, redirect, url_for
from pymongo import MongoClient
from datetime import datetime
from urllib.parse import quote

# =================================================================
# ১. কনফিগারেশন এবং এনভায়রনমেন্ট ভেরিয়েবলস
# =================================================================

# Vercel বা লোকাল পরিবেশ থেকে লোড হবে
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/eonmovies')
TMDB_API_KEY = os.environ.get('TMDB_API_KEY', 'YOUR_TMDB_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')

TMDB_IMAGE_BASE_URL = 'https://image.tmdb.org/t/p/w500'

# =================================================================
# ২. ডাটাবেস সংযোগ
# =================================================================

try:
    client = MongoClient(MONGO_URI)
    db = client.get_database('eonmovies_db')
    movies_collection = db.get_collection('movies')
    movies_collection.create_index([('slug', 1)], unique=True)
except Exception as e:
    print(f"Database connection error: {e}")
    # Local fallback for testing if MongoDB is not running

# =================================================================
# ৩. সাহায্যকারী ফাংশন ও HTML টেমপ্লেট
# =================================================================

def create_slug(title, tmdb_id):
    """মুভির টাইটেল থেকে URL-বান্ধব স্লগ তৈরি করে।"""
    slug = title.lower().replace(' ', '-')
    slug = ''.join(c for c in slug if c.isalnum() or c == '-')
    slug = slug.replace('--', '-')
    return f"{slug}-{tmdb_id}"

def get_download_link(file_id):
    """টেলিগ্রাম ফাইল ডাউনলোড লিঙ্ক তৈরি করে।"""
    # Note: For security and simplicity, we generally redirect to the telegram post
    # or use a direct file access link if the bot grants it.
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"


# --- আধুনিক UI/UX এর জন্য CSS ---
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

        /* Detail Page Styling */
        .detail-flex { display: flex; gap: 30px; margin-top: 30px; }
        .detail-poster { width: 300px; height: auto; border-radius: 8px; flex-shrink: 0; box-shadow: 0 0 15px rgba(0, 188, 212, 0.5); }
        .detail-info { flex-grow: 1; }
        .detail-title { color: #00bcd4; margin-bottom: 10px; }
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
    <title>Eon Movies Serverless Clone</title>
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
    try:
        update = request.get_json()
        
        if not update or not update.get('channel_post') or not update['channel_post'].get('document'):
            return jsonify({'status': 'ok', 'message': 'Not a relevant post'}), 200

        caption = update['channel_post'].get('caption', '')
        file_id = update['channel_post']['document']['file_id']
        
        # ১. টাইটেল এক্সট্রাকশন
        # আমরা ক্যাপশন থেকে মুভি টাইটেল এবং টাইপ (#MOVIE/#SERIES) বের করার চেষ্টা করব
        search_title = caption.strip().split('\n')[0]
        content_type = 'Movie' # Default type
        
        if "#SERIES" in caption.upper():
            content_type = 'Web Series'
        elif "#MOVIE" in caption.upper():
            content_type = 'Movie'
            
        if not search_title:
            return jsonify({'status': 'error', 'message': 'No title in caption'}), 200

        # ২. TMDB সার্চ (Movie/TV উভয় সার্চ করা হচ্ছে)
        # যেহেতু আমরা TMDB-তে সরাসরি মুভি নাকি সিরিজ আপলোড হয়েছে তা জানতে পারছি না,
        # তাই আমরা একটি ফ্লেক্সিবল সার্চ করব বা প্রথমে মুভি সার্চ করব।
        
        is_tv = content_type == 'Web Series'
        tmdb_path = 'tv' if is_tv else 'movie'
        
        tmdb_url = f"https://api.themoviedb.org/3/search/{tmdb_path}?api_key={TMDB_API_KEY}&query={quote(search_title)}"
        tmdb_response = requests.get(tmdb_url)
        
        if tmdb_response.status_code != 200 or not tmdb_response.json().get('results'):
            # যদি প্রথম সার্চে না পাওয়া যায়, তবে অপর টাইপে সার্চ করে দেখা যেতে পারে (ফলব্যাক)
            tmdb_path = 'movie' if is_tv else 'tv'
            tmdb_url = f"https://api.themoviedb.org/3/search/{tmdb_path}?api_key={TMDB_API_KEY}&query={quote(search_title)}"
            tmdb_response = requests.get(tmdb_url)
            content_type = 'Movie' if tmdb_path == 'movie' else 'Web Series'

        tmdb_data = tmdb_response.json().get('results')
        if not tmdb_data:
            return jsonify({'status': 'error', 'message': 'Content not found on TMDB'}), 200
        
        movie_data = tmdb_data[0]
        
        # ৩. স্লগ তৈরি এবং ডেটাবেসে সংরক্ষণ
        title = movie_data.get('title') or movie_data.get('name')
        slug = create_slug(title, movie_data.get('id'))
        
        movie_doc = {
            'tmdb_id': movie_data.get('id'),
            'title': title,
            'content_type': content_type,
            'overview': movie_data.get('overview'),
            'poster_path': movie_data.get('poster_path'),
            'release_date': movie_data.get('release_date') or movie_data.get('first_air_date'),
            'telegram_file_id': file_id,
            'slug': slug,
            'uploaded_at': datetime.now()
        }
        
        movies_collection.update_one(
            {'slug': slug}, 
            {'$set': movie_doc}, 
            upsert=True
        )

        # সাধারণত Vercel/ISR সিস্টেমে এখানে রিভ্যালিডেশন হয়, ফ্লাস্ক স্ট্যাটিক নয়, তাই ইনস্ট্যান্ট আপডেটের জন্য ব্রাউজার রিফ্রেশ দরকার হবে।
        return jsonify({'status': 'success', 'movie': title, 'type': content_type, 'slug': slug}), 200

    except Exception as e:
        print(f"Webhook Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# =================================================================
# ৬. ফ্রন্টএন্ড রুটস
# =================================================================

@app.route('/')
def homepage():
    """হোমপেজ রেন্ডার করে (EonMovies স্টাইলে গ্রিড ভিউ)।"""
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
        return Response(HEADER_HTML + f"<h1>Error</h1><p>{e}</p>" + FOOTER_HTML, mimetype='text/html'), 500


@app.route('/t/<slug>')
def movie_detail(slug):
    """মুভি ডিটেইল পেজ রেন্ডার করে।"""
    try:
        movie = movies_collection.find_one({'slug': slug})
        
        if not movie:
            content = "<h2>404 - Content Not Found</h2>"
            return Response(HEADER_HTML + content + FOOTER_HTML, mimetype='text/html'), 404

        poster_url = f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else 'https://via.placeholder.com/500x750?text=No+Image'
        
        # টেলিগ্রামের getFile endpoint কল না করে, সরাসরি ব্যবহারকারীকে ডাউনলোড করার জন্য তৈরি করা লিঙ্ক
        download_link = get_download_link(movie.get('telegram_file_id'))
        
        content = f"""
            <h1 class="detail-title">{movie['title']}</h1>
            
            <div class="detail-flex">
                <img src="{poster_url}" class="detail-poster" alt="{movie['title']}">
                
                <div class="detail-info">
                    <h2>{movie['title']}</h2>
                    <p style="color: #999;">Type: {movie['content_type']}</p>
                    <p style="color: #999;">Release: {movie.get('release_date', 'N/A')}</p>
                    
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
        return Response(HEADER_HTML + f"<h1>Error</h1><p>{e}</p>" + FOOTER_HTML, mimetype='text/html'), 500


# =================================================================
# ৭. অ্যাপ্লিকেশন রান ব্লক
# =================================================================

if __name__ == '__main__':
    # লোকাল ডেভেলপমেন্টের জন্য
    app.run(debug=True, port=5000)

# Vercel ডিপ্লয়মেন্টের জন্য (Vercel-এ পাইথন অ্যাপ ডিপ্লয় করলে এটি এন্ট্রি পয়েন্ট হয়)
# from index import app
