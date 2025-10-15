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

def fetch_all_liked_songs(sp):
    """Fetch liked songs and fill LIKED_SONGS_CACHE (simple, single-user)."""
    global LIKED_SONGS_CACHE
    if LIKED_SONGS_CACHE:
        return LIKED_SONGS_CACHE

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
<title>Random Spotify Playlist</title>
<h2>Random Spotify Playlist</h2>
{% if not logged_in %}
  <p><b>Not signed in</b> — you must sign in to Spotify to use the app.</p>
  <a href="{{ url_for('login') }}">Sign in with Spotify</a>
{% else %}
  <form action="{{ url_for('preview') }}" method="post">
    <label>Number of songs:
      <input type="number" name="size" min="1" value="10" required>
    </label>
    <button type="submit">Preview</button>
  </form>

  <p style="margin-top:12px;">
    <a href="{{ url_for('logout') }}">Log out</a>
  </p>
{% endif %}
<p style="margin-top:18px;color:gray">Tip: First run may take a bit while your Liked Songs are fetched.</p>
"""

PREVIEW_HTML = """
<!doctype html>
<title>Preview</title>
<h2>Preview: {{ count }} songs</h2>
<ol>
  {% for t in tracks %}
    <li>{{ t.name }} — {{ t.artists }}</li>
  {% endfor %}
</ol>
<form action="{{ url_for('create_playlist') }}" method="post">
  <button type="submit">Create Playlist on Spotify</button>
</form>
<p><a href="{{ url_for('index') }}">Back</a></p>
"""

SUCCESS_HTML = """
<!doctype html>
<title>Created</title>
<h2>Playlist Created!</h2>
<p>Your playlist: <a href="{{ url }}">{{ url }}</a></p>
<p><a href="{{ url_for('index') }}">Create another</a></p>
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
    token_info = sp_oauth.get_access_token(code)
    session["token_info"] = token_info
    return redirect(url_for("index"))

@app.route("/logout")
def logout():
    session.pop("token_info", None)
    # clear cached liked songs for safety if needed
    # global LIKED_SONGS_CACHE; LIKED_SONGS_CACHE = []
    return redirect(url_for("index"))

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
        songs = fetch_all_liked_songs(sp)
    except Exception as e:
        return f"Failed to fetch liked songs: {e}"

    if not songs:
        return "You have no Liked Songs in Spotify."

    if len(songs) < size:
        selection = songs[:]   # all of them
    else:
        selection = random.sample(songs, size)

    # store selected track ids in session (small footprint)
    session["preview_ids"] = [s["id"] for s in selection]
    # For display, we'll fetch track details
    ids = session["preview_ids"]
    track_objs = sp.tracks(ids).get("tracks", [])
    tracks_for_display = []
    for t in track_objs:
        if not t: continue
        name = t.get("name", "Unknown")
        artists = ", ".join([a.get("name", "") for a in t.get("artists", [])])
        tracks_for_display.append({"name": name, "artists": artists})
    return render_template_string(PREVIEW_HTML, tracks=tracks_for_display, count=len(tracks_for_display))

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
    today = date.today().strftime("%Y-%m-%d")
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
