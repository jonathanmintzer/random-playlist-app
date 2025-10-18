# app.py
# Flask web app version of your random Spotify playlist generator.
# Usage: set environment variables (see instructions), then run locally with: python app.py
# Or deploy to Render (instructions below).

import os
import random
import time
from datetime import date
from flask import Flask, request, redirect, session, url_for, render_template_string, flash
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ---------- Configuration (use environment variables) ----------
SPOTIPY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI")  # e.g., https://random-playlist.onrender.com/callback
SCOPE = "user-library-read playlist-modify-private playlist-modify-public"

# Secret key for Flask session cookies. Set via env var SECRET_KEY in production.
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(24))

print("🔍 Debug check:")
print("Client ID:", SPOTIPY_CLIENT_ID)
print("Redirect URI:", SPOTIPY_REDIRECT_URI)
print("Secret Key present:", bool(SPOTIPY_CLIENT_SECRET))

# ---------- Flask app setup ----------
app = Flask(__name__)
app.secret_key = SECRET_KEY

# ---------- Simple in-memory cache (for quick testing) ----------
# NOTE: This is fine for personal testing. On a hosted service the filesystem may be ephemeral.
LIKED_SONGS_CACHE = []   # list of dicts {id, name, artist_names, album_date, album_year}
CACHED_RANDOM_BATCH = None
CACHED_RANDOM_TIMESTAMP = 0
CACHE_EXPIRY_SECONDS = 60  # 1 minute

# ---------- Helper functions ----------
def create_sp_oauth():
    # cache_path stores the Spotify token on disk. For personal tests this is OK.
    # When deployed, token persistence on the server may be ephemeral (fine for testing).
    return SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPE,
        cache_path=".spotify_token_cache"
    )

def token_info_needs_refresh(token_info):
    now = int(time.time())
    return token_info.get("expires_at", 0) - now < 60

def get_token():
    token_info = session.get("token_info", None)
    if not token_info:
        return None
    if token_info_needs_refresh(token_info):
        sp_oauth = create_sp_oauth()
        refreshed = sp_oauth.refresh_access_token(token_info["refresh_token"])
        session["token_info"] = refreshed
        return refreshed
    return token_info

def ensure_spotify_client():
    token_info = get_token()
    if not token_info:
        return None
    return spotipy.Spotify(auth=token_info["access_token"])

def fetch_random_liked_songs(sp, batch_size=500):
    """
    Fetch a random sample of up to 'batch_size' liked songs.
    Caches the last random batch for 1 minute for speed.
    """
    global CACHED_RANDOM_BATCH, CACHED_RANDOM_TIMESTAMP

    now = time.time()
    if CACHED_RANDOM_BATCH and (now - CACHED_RANDOM_TIMESTAMP < CACHE_EXPIRY_SECONDS):
        print("✅ Using cached batch of random liked songs")
        return CACHED_RANDOM_BATCH

    # How many liked tracks you have in total
    meta = sp.current_user_saved_tracks(limit=1)
    total = meta.get("total", 0) or 0

    if total <= batch_size or total == 0:
        offset = 0
        limit = min(batch_size, total if total > 0 else 50)
    else:
        offset = random.randint(0, total - batch_size)
        limit = batch_size

    print(f"Fetching {limit} liked songs starting at offset {offset} of {total}")

    # Fetch in batches of 50 (Spotify’s maximum per request)
    tracks = []
    fetched = 0
    while fetched < limit:
        batch = min(50, limit - fetched)
        results = sp.current_user_saved_tracks(limit=batch, offset=offset + fetched)
        for item in results.get("items", []):
            t = item.get("track")
            if not t:
                continue
            artists = ", ".join([a.get("name", "") for a in t.get("artists", [])])
            tracks.append({
                "id": t.get("id"),
                "name": t.get("name"),
                "artists": artists
            })
        fetched += batch

    # ✅ Save to cache
    CACHED_RANDOM_BATCH = tracks
    CACHED_RANDOM_TIMESTAMP = now
    print("💾 Cached random batch for next 5 minutes")

    return tracks

    results = sp.current_user_saved_tracks(limit=50)
    all_tracks = []
    while results:
        for item in results.get('items', []):
            t = item.get('track')
            if not t: 
                continue
            artists = t.get('artists', [])
            artist_names = [a.get('name', '') for a in artists]
            album_date = t.get('album', {}).get('release_date')
            year = None
            if album_date:
                try:
                    year = int(album_date.split("-")[0])
                except Exception:
                    year = None
            all_tracks.append({
                "id": t.get('id'),
                "name": t.get('name'),
                "artist_names": artist_names,
                "album_date": album_date,
                "album_year": year
            })
        results = sp.next(results) if results.get('next') else None

    LIKED_SONGS_CACHE = all_tracks
    return LIKED_SONGS_CACHE

# ---------- Routes ----------
INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Random Spotify Playlist</title>
<style>
body {
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  margin:0;padding:0;
  background:#121212;color:#fff;text-align:center;
}
.container {
  max-width:400px;
  margin:auto;
  padding:20px;
}
button,input[type=number] {
  font-size:1.1em;
  border-radius:10px;
  padding:10px 16px;
  margin-top:12px;
  border:none;
}
button {
  background-color:#1DB954;
  color:white;
  cursor:pointer;
}
button:hover { background-color:#1ed760; }
input[type=number] {
  width:60%;
  max-width:180px;
  text-align:center;
}
a { color:#1DB954; text-decoration:none; }

/* ==== Loader styling ==== */
#loader {
  display:none;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  margin:15px 0;
}

.spinner {
  width:80px;
  height:80px;
  border:6px solid rgba(255,255,255,0.15);
  border-top:6px solid #1DB954;
  border-radius:50%;
  animation:spin 1s linear infinite;
  display:flex;
  align-items:center;
  justify-content:center;
  margin-bottom:10px;
}
@keyframes spin {
  0% { transform:rotate(0deg); }
  100% { transform:rotate(360deg); }
}
.note {
  font-size:36px;
  color:#1DB954;
  animation:bounce 1.5s ease-in-out infinite;
}
@keyframes bounce {
  0%,100% { transform:translateY(0); }
  50% { transform:translateY(-6px); }
}
</style>
</head>
<body>
<div class="container">
  <h2>🎵 Random Playlist Generator</h2>

  <!-- Musical note spinner -->
  <div id="loader">
    <div class="spinner">
      <div class="note">🎶</div>
    </div>
    <p style="margin-top:5px;color:#1DB954;font-size:0.9em;">Fetching songs...</p>
  </div>

  {% if not logged_in %}
    <p>Sign in to Spotify to begin.</p>
    <a href="{{ url_for('login') }}"><button>Sign in with Spotify</button></a>
  {% else %}
    <form id="previewForm" action="{{ url_for('preview') }}" method="post">
      <label>Number of songs:</label><br>
      <input type="number" name="size" min="1" value="10" required><br>
      <button type="submit">Create Preview</button>
    </form>
    <p style="margin-top:20px;"><a href="{{ url_for('logout') }}">Log out</a></p>
  {% endif %}
  <p style="margin-top:18px;color:#aaa;">Tip: Fetching may take a moment.</p>
</div>

<script>
document.addEventListener("DOMContentLoaded", function(){
  const form = document.getElementById("previewForm");
  const loader = document.getElementById("loader");
  if(form && loader){
    form.addEventListener("submit", function(){
      loader.style.display = "flex"; // show loader when fetching begins
    });
  }
});
</script>
</body>
</html>
"""

PREVIEW_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Preview</title>
<style>
body {
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:#121212;color:#fff;text-align:center;margin:0;padding:20px;
}
.container {max-width:400px;margin:auto;}
table {
  width:100%;border-collapse:collapse;margin-top:15px;
}
th, td {
  padding:8px 6px;border-bottom:1px solid #333;
  text-align:left;font-size:0.9em;
}
th {color:#1DB954;text-transform:uppercase;font-size:0.8em;}
button {
  display:block;width:100%;margin:10px 0;padding:12px;
  font-size:1.1em;border:none;border-radius:10px;cursor:pointer;
}
.save {background-color:#1DB954;color:white;}
.save:hover {background-color:#1ed760;}
.reshuffle {background-color:#333;color:#1DB954;border:1px solid #1DB954;}
.reshuffle:hover {background-color:#1DB954;color:white;}

/* Loader styling */
#loader {
  display:none;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  margin:15px 0;
}
.spinner {
  width:60px;height:60px;
  border:5px solid rgba(255,255,255,0.15);
  border-top:5px solid #1DB954;
  border-radius:50%;
  animation:spin 1s linear infinite;
  display:flex;
  align-items:center;
  justify-content:center;
  margin-bottom:10px;
}
@keyframes spin {
  0% { transform:rotate(0deg); }
  100% { transform:rotate(360deg); }
}
.note {
  font-size:24px;
  color:#1DB954;
  animation:bounce 1.5s ease-in-out infinite;
}
@keyframes bounce {
  0%,100% { transform:translateY(0); }
  50% { transform:translateY(-6px); }
}
</style>
</head>
<body>
<div class="container">
  <h2>🎶 Preview: {{ count }} Songs</h2>

  <!-- Loader -->
  <div id="loader">
    <div class="spinner">
      <div class="note">🎵</div>
    </div>
    <p style="margin-top:5px;color:#1DB954;font-size:0.9em;">Reshuffling songs...</p>
  </div>

  <table>
    <tr><th>Song</th><th>Artist</th></tr>
    {% for t in tracks %}
      <tr><td>{{ t.name }}</td><td>{{ t.artists }}</td></tr>
    {% endfor %}
  </table>

  <form action="{{ url_for('create_playlist') }}" method="post">
    <button type="submit" class="save">💾 Save Playlist</button>
  </form>

  <form id="reshuffleForm" action="{{ url_for('preview') }}" method="post">
    <input type="hidden" name="size" value="{{ count }}">
    <button type="submit" class="reshuffle">🔀 Reshuffle</button>
  </form>

  <p><a href="{{ url_for('index') }}" style="color:#1DB954;">⬅️ Back</a></p>
</div>

<script>
document.addEventListener("DOMContentLoaded", function(){
  const reshuffleForm = document.getElementById("reshuffleForm");
  const loader = document.getElementById("loader");
  if(reshuffleForm && loader){
    reshuffleForm.addEventListener("submit", function(){
      loader.style.display = "flex"; // show spinner while reshuffling
    });
  }
});
</script>
</body>
</html>
"""

SUCCESS_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Created</title>
<style>
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:#121212;color:#fff;text-align:center;padding:30px;margin:0;
}
.container{
  max-width: 420px;  /* keeps content narrow on phones */
  margin: 0 auto;
  padding: 0 16px;
}
a{
  color:#1DB954;text-decoration:none;
  word-wrap:break-word;         /* allow long URLs to wrap */
  overflow-wrap:break-word;     /* modern wrap property */
}
p{
  word-wrap:break-word;
  overflow-wrap:break-word;
  margin: 12px 0;
}
.link-box{
  background:#1e1e1e;
  padding:12px;
  border-radius:10px;
  text-align:left;              /* better wrapping for very long URLs */
}
button{
  background-color:#1DB954;color:white;border:none;font-size:1.1em;
  border-radius:10px;padding:12px 20px;margin-top:20px;
}
button:hover{background-color:#1ed760;}
</style>
</head>
<body>
<div class="container">
  <h2>✅ Playlist Created!</h2>
  <p>Open on Spotify:</p>
  <div class="link-box">
    <a href="{{ url }}">{{ url }}</a>
  </div>
  <a href="{{ url_for('index') }}"><button>Create Another</button></a>
</div>
</body>
</html>
"""

@app.route("/")
def index():
    logged_in = get_token() is not None
    return render_template_string(INDEX_HTML, logged_in=logged_in)

@app.route("/login")
def login():
    sp_oauth = create_sp_oauth()
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    sp_oauth = create_sp_oauth()
    code = request.args.get("code")
    error = request.args.get("error")
    if error:
        return f"Error during authentication: {error}"
    try:
        token_info = sp_oauth.get_access_token(code)
    except Exception as e:
        print("⚠️ Error during get_access_token:", e)
        return f"Spotify authentication failed: {e}"
    session["token_info"] = token_info
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.pop("token_info", None)
    # clear cached liked songs for safety if needed
    # global LIKED_SONGS_CACHE; LIKED_SONGS_CACHE = []
    return redirect(url_for("index"))

@app.route("/preview", methods=["POST"])
@app.route("/preview", methods=["POST"])
def preview():
    size_raw = request.form.get("size", "10")
    try:
        size = max(1, int(size_raw))
    except ValueError:
        flash("Please enter a valid number of songs.")
        return redirect(url_for("index"))

    token = get_token()
    if not token:
        return redirect(url_for("login"))

    sp = ensure_spotify_client()
    try:
        songs = fetch_random_liked_songs(sp, batch_size=500)
    except Exception as e:
        return f"Failed to fetch liked songs: {e}"

    if not songs:
        return "You have no Liked Songs in Spotify."

    # ✅ Prevent duplicate artists
    unique_by_artist = {}
    for s in songs:
        artist = s["artist_names"][0] if s["artist_names"] else "Unknown"
        if artist not in unique_by_artist:
            unique_by_artist[artist] = s

    unique_songs = list(unique_by_artist.values())
    if len(unique_songs) <= size:
        selection = unique_songs
    else:
        selection = random.sample(unique_songs, size)

    # Store selected track IDs in session
    session["preview_ids"] = [s["id"] for s in selection]

    # Fetch song details for preview display
    ids = session["preview_ids"]
    track_objs = sp.tracks(ids).get("tracks", [])
    tracks_for_display = []
    for t in track_objs:
        if not t:
            continue
        name = t.get("name", "Unknown")
        artists = ", ".join([a.get("name", "") for a in t.get("artists", [])])
        tracks_for_display.append({"name": name, "artists": artists})

    # ✅ Render the new preview HTML
    return render_template_string(
        PREVIEW_HTML,
        tracks=tracks_for_display,
        count=len(tracks_for_display)
    )

@app.route("/create_playlist", methods=["POST"])
def create_playlist():
    token = get_token()
    if not token:
        return redirect(url_for("login"))
    sp = ensure_spotify_client()
    ids = session.get("preview_ids", [])
    if not ids:
        return "No previewed songs found. Preview first."

    user = sp.current_user()
    user_id = user.get("id")
    from datetime import datetime
    import pytz

    local_tz = pytz.timezone("America/New_York")  # change this if you're in another timezone
    today = datetime.now(local_tz).strftime("%Y-%m-%d")

    playlist_name = f"{today} 🎲 Random Playlist"

    try:
        playlist = sp.user_playlist_create(user_id, playlist_name, public=False)
        for i in range(0, len(ids), 100):
            sp.playlist_add_items(playlist["id"], ids[i:i+100])
        playlist_url = playlist.get("external_urls", {}).get("spotify")
        return render_template_string(SUCCESS_HTML, url=playlist_url)
    except Exception as e:
        return f"Failed to create playlist: {e}"

# ---------- Run locally ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8888)))
