import os
import random
import json # For AJAX responses AND file storage
import uuid # For generating unique IDs for preview files
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import datetime 
from urllib.parse import urlparse # Added for robust URL parsing

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

# Spotify API credentials and settings
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
SCOPE = "playlist-modify-public playlist-modify-private user-read-private user-read-email user-library-read"

# Directory for temporary preview files
TEMP_PREVIEW_DIR = 'temp_previews'
if not os.path.exists(TEMP_PREVIEW_DIR):
    os.makedirs(TEMP_PREVIEW_DIR)

# --- Context Processor ---
@app.context_processor
def inject_current_year():
    return {'current_year': datetime.datetime.now().year}

# --- Helper Functions ---
def get_spotify_oauth():
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPE,
        cache_path=".spotifycache" 
    )

def get_spotify_client():
    token_info = session.get("token_info", None)
    if not token_info:
        return None
    sp_oauth = get_spotify_oauth()
    if sp_oauth.is_token_expired(token_info):
        try:
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            session["token_info"] = token_info
        except spotipy.SpotifyOauthError as e:
            session.clear(); return None
    return spotipy.Spotify(auth=token_info['access_token'])

def get_artist_details_with_search(sp, artist_name_query):
    """Searches for an artist by name and returns details."""
    try:
        search_results = sp.search(q=f"artist:{artist_name_query}", type="artist", limit=1)
        if search_results and search_results['artists']['items']:
            artist_item = search_results['artists']['items'][0]
            image_url = artist_item['images'][0]['url'] if artist_item.get('images') else None
            return {"id": artist_item['id'], "name": artist_item['name'], "image_url": image_url}
    except Exception as e:
        print(f"DEBUG: get_artist_details_with_search - Error for '{artist_name_query}': {e}")
    return None

def get_artist_details_by_id(sp, artist_id):
    """Fetches artist details directly by their Spotify ID."""
    try:
        artist_item = sp.artist(artist_id)
        if artist_item:
            image_url = artist_item['images'][0]['url'] if artist_item.get('images') else None
            return {"id": artist_item['id'], "name": artist_item['name'], "image_url": image_url}
    except Exception as e:
        print(f"DEBUG: get_artist_details_by_id - Error for ID '{artist_id}': {e}")
    return None


def get_all_artist_tracks_with_details(sp, artist_id, artist_name_for_log, limit_per_album=50, max_albums_to_scan=10, max_tracks_to_return=150):
    tracks_info = []; seen_track_uris = set()
    try:
        album_types = ['album', 'single']; all_album_items_from_api = []
        for album_type in album_types:
            offset = 0; albums_fetched_this_type = 0
            while albums_fetched_this_type < max_albums_to_scan:
                limit = min(50, max_albums_to_scan - albums_fetched_this_type)
                if limit <=0: break
                album_results = sp.artist_albums(artist_id, album_type=album_type, limit=limit, offset=offset)
                if not album_results or not album_results['items']: break
                all_album_items_from_api.extend(album_results['items'])
                albums_fetched_this_type += len(album_results['items']); offset += len(album_results['items'])
                if not album_results['next']: break
        unique_album_ids = list({album['id'] for album in all_album_items_from_api}); random.shuffle(unique_album_ids)
        for i, album_id_iter in enumerate(unique_album_ids): 
            if i >= max_albums_to_scan or len(tracks_info) >= max_tracks_to_return: break
            album_tracks_results = sp.album_tracks(album_id_iter, limit=limit_per_album) 
            if album_tracks_results:
                album_image_for_track = None
                current_album_details = next((alb for alb in all_album_items_from_api if alb['id'] == album_id_iter), None) 
                if current_album_details and current_album_details['images']: album_image_for_track = current_album_details['images'][0]['url']
                for track in album_tracks_results['items']:
                    if track['uri'] not in seen_track_uris:
                        tracks_info.append({'uri': track['uri'], 'name': track['name'], 
                                            'artists_str': ", ".join([a['name'] for a in track['artists']]), 
                                            'image_url': album_image_for_track })
                        seen_track_uris.add(track['uri'])
                        if len(tracks_info) >= max_tracks_to_return: break
        if len(tracks_info) < max_tracks_to_return:
            top_tracks_results = sp.artist_top_tracks(artist_id)
            if top_tracks_results:
                for track in top_tracks_results['tracks']:
                    if len(tracks_info) >= max_tracks_to_return: break
                    if track['uri'] not in seen_track_uris:
                        album_image = track['album']['images'][0]['url'] if track['album']['images'] else None
                        tracks_info.append({'uri': track['uri'], 'name': track['name'], 
                                            'artists_str': ", ".join([a['name'] for a in track['artists']]), 
                                            'image_url': album_image})
                        seen_track_uris.add(track['uri'])
    except Exception as e: 
        pass
    return tracks_info[:max_tracks_to_return]

# --- Flask Routes ---
@app.route("/")
def index():
    sp = get_spotify_client()
    user_info = session.get('user_info')
    error_message = request.args.get('error_message') 
    if sp and not user_info:
        try:
            user_profile = sp.current_user()
            user_info = {"name": user_profile.get('display_name', user_profile.get('id')),
                         "image": user_profile['images'][0]['url'] if user_profile.get('images') else None}
            session['user_info'] = user_info 
        except Exception as e:
            if "token" in str(e).lower(): session.clear(); return redirect(url_for("login")) 
            sp = None
    return render_template("index.html", user_logged_in=bool(sp), user_info=user_info, error_message=error_message)

@app.route("/login")
def login():
    auth_url = get_spotify_oauth().get_authorize_url()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    sp_oauth = get_spotify_oauth()
    code = request.args.get('code'); error = request.args.get('error')
    if error: 
        return redirect(url_for("index", error_message=f"Spotify auth failed: {error}"))
    if not code: 
        return redirect(url_for("index", error_message="Spotify auth failed: No code"))
    try:
        token_info = sp_oauth.get_access_token(code, check_cache=False) 
        session["token_info"] = token_info
        sp_temp = spotipy.Spotify(auth=token_info['access_token'])
        user_profile = sp_temp.current_user()
        session['user_info'] = {"name": user_profile.get('display_name', user_profile.get('id')),
                                "image": user_profile['images'][0]['url'] if user_profile.get('images') else None}
        return redirect(url_for("index"))
    except Exception as e:
        return redirect(url_for("index", error_message=f"Auth error: {e}"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# MODIFIED FUNCTION STARTS HERE
@app.route("/suggest_artists")
def suggest_artists():
    sp = get_spotify_client()
    if not sp: 
        return jsonify({"error": "User not authenticated"}), 401

    query = request.args.get("query", "").strip()
    if not query: 
        return jsonify([])

    final_suggestions = []
    processed_artist_ids = set()

    def format_artist_item(artist_item_data):
        if artist_item_data and artist_item_data.get('id') and artist_item_data['id'] not in processed_artist_ids:
            processed_artist_ids.add(artist_item_data['id'])
            return {
                "id": artist_item_data['id'],
                "name": artist_item_data.get('name', 'Unknown Artist'),
                "image_url": artist_item_data['images'][0]['url'] if artist_item_data.get('images') else None
            }
        return None

    # Attempt to parse query as a URL
    try:
        parsed_url = urlparse(query)
        if parsed_url.scheme in ['http', 'https'] and \
           parsed_url.netloc == 'open.spotify.com' and \
           '/artist/' in parsed_url.path:
            
            path_segments = parsed_url.path.strip('/').split('/')
            artist_id_from_url = None
            if 'artist' in path_segments:
                try:
                    artist_index = path_segments.index('artist')
                    if artist_index + 1 < len(path_segments):
                        artist_id_from_url = path_segments[artist_index + 1]
                except ValueError:
                    pass # 'artist' not in path_segments as expected

            if artist_id_from_url:
                # print(f"DEBUG: suggest_artists - URL detected. Artist ID: {artist_id_from_url}")
                try:
                    artist_data = sp.artist(artist_id_from_url)
                    suggestion = format_artist_item(artist_data)
                    if suggestion:
                        final_suggestions.append(suggestion)
                    
                    # Fetch related artists if primary artist found and need to pad to 5
                    if artist_data and len(final_suggestions) < 5:
                        related_artists_data = sp.artist_related_artists(artist_id_from_url)
                        if related_artists_data and related_artists_data.get('artists'):
                            for rel_artist in related_artists_data['artists']:
                                if len(final_suggestions) >= 5: break
                                rel_suggestion = format_artist_item(rel_artist)
                                if rel_suggestion:
                                    final_suggestions.append(rel_suggestion)
                except spotipy.SpotifyException as se: # More specific exception for Spotify API errors
                    print(f"DEBUG: suggest_artists - Spotify API error fetching artist by ID {artist_id_from_url}: {se}")
                except Exception as e_url:
                    print(f"DEBUG: suggest_artists - Generic error processing artist URL for ID {artist_id_from_url}: {e_url}")
                
                # If a URL was processed (even if it failed to find an artist), return its results.
                return jsonify(final_suggestions[:5]) 
            else:
                print(f"DEBUG: suggest_artists - Could not extract valid artist ID from URL: {query}")
                # It looked like a URL but ID extraction failed, so treat as bad URL, don't fall to text search
                return jsonify([])


    except ValueError: # Malformed URL for urlparse
        # print(f"DEBUG: suggest_artists - Query '{query}' not a valid URL structure. Proceeding to text search.")
        pass # Fall through to text search

    # If not a valid & processed Spotify URL, proceed with text search logic
    is_quoted_search = query.startswith('"') and query.endswith('"') and len(query) >= 3 # e.g., "A"
    
    search_term_for_filter = "" 
    spotify_api_query_term = query

    if is_quoted_search:
        search_term_for_filter = query[1:-1] 
        if not search_term_for_filter: return jsonify([]) # Empty quotes ""
        spotify_api_query_term = f'"{search_term_for_filter}"' # Use Spotify's exact phrase search
        api_limit = 25 
    else:
        search_term_for_filter = query 
        api_limit = 10 # Fetch a reasonable number for non-quoted for filtering

    try:
        spotify_q_param = f'artist:{spotify_api_query_term}'
        # print(f"DEBUG: suggest_artists - Text search. Query: {spotify_q_param}, Limit: {api_limit}")
        results = sp.search(q=spotify_q_param, type="artist", limit=api_limit) 
        
        raw_spotify_items = results['artists']['items'] if results and results['artists']['items'] else []

        for item in raw_spotify_items:
            if len(final_suggestions) >= 5: break
            
            suggestion_to_add = None
            if is_quoted_search:
                if search_term_for_filter.lower() in item.get('name', '').lower():
                    suggestion_to_add = format_artist_item(item)
            else: 
                suggestion_to_add = format_artist_item(item)
            
            if suggestion_to_add:
                final_suggestions.append(suggestion_to_add)
        
    except Exception as e:
        print(f"DEBUG: suggest_artists - Error during text search for '{query}': {e}")
        # Do not return error here if some results were already found (e.g. from a failed URL path that didn't return early)
        # If final_suggestions is empty, then it's a true failure.
        if not final_suggestions:
            return jsonify({"error": "Could not fetch suggestions"}), 500
    
    # print(f"DEBUG: suggest_artists - Returning suggestions: {final_suggestions[:5]}")
    return jsonify(final_suggestions[:5])
# MODIFIED FUNCTION ENDS HERE

@app.route("/liked_songs")
def liked_songs():
    sp = get_spotify_client()
    if not sp:
        return jsonify({"error": "User not authenticated"}), 401

    songs = []
    try:
        limit = 50
        offset = 0
        while True:
            results = sp.current_user_saved_tracks(limit=limit, offset=offset)
            items = results.get('items', [])
            for item in items:
                track = item['track']
                songs.append({
                    "id": track['id'],
                    "name": track['name'],
                    "artists": [artist['name'] for artist in track['artists']],
                    "uri": track['uri'],
                    "album": track['album']['name'],
                    "image_url": track['album']['images'][0]['url'] if track['album']['images'] else None
                })
            if len(items) < limit:
                break
            offset += limit
    except Exception as e:
        return jsonify({"error": f"Could not fetch liked songs: {e}"}), 500

    return jsonify(songs)


@app.route("/liked_artists")
def liked_artists():
    sp = get_spotify_client()
    if not sp:
        return jsonify({"error": "User not authenticated"}), 401

    artist_map = {}
    try:
        limit = 50
        offset = 0
        while True:
            results = sp.current_user_saved_tracks(limit=limit, offset=offset)
            items = results.get('items', [])
            for item in items:
                for artist in item['track']['artists']:
                    if artist['id'] not in artist_map:
                        artist_map[artist['id']] = {
                            "id": artist['id'],
                            "name": artist['name'],
                            "image_url": None  # This will be populated below.
                        }
            if len(items) < limit:
                break
            offset += limit

        # Optionally, fetch artist images in batches (Spotify API allows up to 50 IDs per call)
        artist_ids = list(artist_map.keys())
        for i in range(0, len(artist_ids), 50):
            batch_ids = artist_ids[i:i+50]
            batch_response = sp.artists(batch_ids)
            for artist in batch_response.get('artists', []):
                if artist and artist.get('id') in artist_map:
                    artist_map[artist['id']]['image_url'] = artist['images'][0]['url'] if artist.get('images') else None
    except Exception as e:
        return jsonify({"error": f"Could not fetch liked artists: {e}"}), 500

    return jsonify(list(artist_map.values()))

@app.route("/generate_preview", methods=["POST"])
def generate_preview_route():
    sp = get_spotify_client()
    if not sp: return redirect(url_for("login"))
    
    user_info = session.get('user_info')
    playlist_name = request.form.get("playlist_name", "My Artimix Playlist")
    
    artists_form_data = []
    i = 1
    while True:
        artist_query_name = request.form.get(f"artist_{i}") 
        percentage_str = request.form.get(f"percentage_{i}")
        confirmed_artist_id = request.form.get(f"artist_id_{i}")
        
        if not (artist_query_name and percentage_str): break
        try:
            percentage = int(percentage_str)
            if not (0 < percentage <= 100):
                return render_template("index.html", user_logged_in=True, user_info=user_info, error_message="Percentages must be 1-100.")
            
            artist_details = None
            if confirmed_artist_id:
                # print(f"DEBUG: generate_preview - Attempting to fetch artist by CONFIRMED ID: {confirmed_artist_id}")
                artist_details = get_artist_details_by_id(sp, confirmed_artist_id)
                if not artist_details:
                    # print(f"DEBUG: generate_preview - FAILED to fetch by ID {confirmed_artist_id}. Falling back to search by name: {artist_query_name}")
                    artist_details = get_artist_details_with_search(sp, artist_query_name) # Fallback
            else:
                # print(f"DEBUG: generate_preview - No confirmed ID. Searching by name: {artist_query_name}")
                artist_details = get_artist_details_with_search(sp, artist_query_name)

            if not artist_details: 
                # print(f"DEBUG: generate_preview - FINAL: Could not verify artist: {artist_query_name} (ID: {confirmed_artist_id if confirmed_artist_id else 'N/A'})")
                i += 1; continue 

            # print(f"DEBUG: generate_preview - Successfully got details for artist: {artist_details['name']} (ID: {artist_details['id']})")
            artists_form_data.append({
                "query_name": artist_query_name, 
                "spotify_name": artist_details['name'], 
                "id": artist_details['id'],
                "image_url": artist_details['image_url'], 
                "percentage": percentage
            })
        except ValueError:
            return render_template("index.html", user_logged_in=True, user_info=user_info, error_message="Invalid percentage.")
        i += 1
    
    if not artists_form_data:
        return render_template("index.html", user_logged_in=True, user_info=user_info, error_message="Add at least one artist.")

    all_prospective_playlist_tracks_details = [] 
    artist_contributions_summary = []
    MAX_TRACKS_PER_ARTIST_SAMPLE = 150

    for artist_entry in artists_form_data:
        artist_tracks_with_details = get_all_artist_tracks_with_details(sp, artist_entry["id"], artist_entry["spotify_name"], max_tracks_to_return=MAX_TRACKS_PER_ARTIST_SAMPLE)
        count_for_artist = 0
        if artist_tracks_with_details:
            num_songs_to_pick = int(len(artist_tracks_with_details) * (artist_entry["percentage"] / 100.0))
            if num_songs_to_pick == 0 and len(artist_tracks_with_details) > 0 and artist_entry["percentage"] > 0: num_songs_to_pick = 1
            actual_songs_to_pick = min(num_songs_to_pick, len(artist_tracks_with_details))
            if actual_songs_to_pick > 0 :
                 selected_tracks_details = random.sample(artist_tracks_with_details, actual_songs_to_pick)
                 all_prospective_playlist_tracks_details.extend(selected_tracks_details)
                 count_for_artist = len(selected_tracks_details)
        artist_contributions_summary.append({"name": artist_entry["spotify_name"], "image_url": artist_entry["image_url"], 
                                             "count": count_for_artist, "requested_percentage": artist_entry["percentage"]})

    if not all_prospective_playlist_tracks_details:
        return render_template("index.html", user_logged_in=True, user_info=user_info, error_message="No tracks selected. Try different artists/percentages.")

    final_unique_tracks_map = {track['uri']: track for track in all_prospective_playlist_tracks_details}
    final_track_list_full_details = list(final_unique_tracks_map.values()); random.shuffle(final_track_list_full_details) 
    MAX_TRACKS_FOR_PREVIEW_DETAILS = 30 
    tracks_for_preview_display = final_track_list_full_details[:MAX_TRACKS_FOR_PREVIEW_DETAILS]

    preview_id = str(uuid.uuid4()) 
    preview_data_to_store = {
        'playlist_name': playlist_name,
        'track_uris': [track['uri'] for track in final_track_list_full_details],
        'artist_contributions': artist_contributions_summary, 
        'tracks_for_preview_display': tracks_for_preview_display, 
        'total_songs_in_playlist': len(final_track_list_full_details)
    }
    
    filepath = os.path.join(TEMP_PREVIEW_DIR, f"{preview_id}.json")
    try:
        with open(filepath, 'w') as f:
            json.dump(preview_data_to_store, f, indent=4)
    except IOError as e:
        return render_template("index.html", user_logged_in=True, user_info=user_info, error_message="Server error: Could not save preview data.")

    return redirect(url_for('show_playlist_preview', preview_id=preview_id))

@app.route("/preview")
def show_playlist_preview():
    sp = get_spotify_client(); user_info = session.get('user_info')
    if not sp: return redirect(url_for("login"))
        
    preview_id_from_url = request.args.get('preview_id')
    if not preview_id_from_url:
        return redirect(url_for('index', error_message="Preview link is invalid or expired."))

    filepath = os.path.join(TEMP_PREVIEW_DIR, f"{preview_id_from_url}.json")
    try:
        with open(filepath, 'r') as f:
            preview_data = json.load(f)
    except FileNotFoundError:
        return redirect(url_for('index', error_message="Preview data not found. It may have expired or been removed."))
    except IOError as e:
        return redirect(url_for('index', error_message="Server error: Could not load preview data."))

    return render_template(
        "preview_playlist.html",
        preview_id=preview_id_from_url, 
        playlist_name=preview_data['playlist_name'],
        total_songs=preview_data['total_songs_in_playlist'],
        artist_contributions=preview_data['artist_contributions'],
        tracks_sample_for_display=preview_data['tracks_for_preview_display'], 
        user_logged_in=True, 
        user_info=user_info
    )

@app.route("/confirm_add_to_spotify", methods=["POST"])
def confirm_add_to_spotify_route():
    sp = get_spotify_client(); user_info = session.get('user_info')
    if not sp: return redirect(url_for("login"))

    preview_id_from_form = request.form.get('preview_id') 
    if not preview_id_from_form:
        return redirect(url_for("index", error_message="Missing preview identifier. Please try again."))

    filepath = os.path.join(TEMP_PREVIEW_DIR, f"{preview_id_from_form}.json")
    preview_data = None
    try:
        with open(filepath, 'r') as f:
            preview_data = json.load(f)
    except FileNotFoundError:
        return redirect(url_for("index", error_message="Playlist data to create not found. It may have expired."))
    except IOError as e:
        return redirect(url_for("index", error_message="Server error: Could not load data for playlist creation."))

    if not preview_data or 'track_uris' not in preview_data:
        if os.path.exists(filepath):
            try: os.remove(filepath)
            except OSError as e_del: print(f"DEBUG: confirm_add_to_spotify - Error deleting malformed file {filepath}: {e_del}")
        return redirect(url_for("index", error_message="Playlist data is incomplete. Please try again."))

    playlist_name = preview_data['playlist_name']
    track_uris = preview_data['track_uris']

    if not track_uris:
         return render_template("playlist_created.html", error_message="No tracks were selected.", user_logged_in=True, user_info=user_info)

    try:
        user_profile = sp.current_user(); user_id = user_profile['id']
    except Exception as e:
        return render_template("playlist_created.html", error_message=f"Could not get user info: {e}", user_logged_in=True, user_info=user_info)

    try:
        new_playlist = sp.user_playlist_create(user_id, playlist_name, public=False, description="Created with Artimix!")
        playlist_id = new_playlist['id']; playlist_url = new_playlist['external_urls']['spotify']
        for i in range(0, len(track_uris), 100):
            sp.playlist_add_items(playlist_id, track_uris[i:i + 100])
        
        if os.path.exists(filepath):
            try: os.remove(filepath)
            except OSError as e_del: print(f"DEBUG: confirm_add_to_spotify - Error deleting preview file {filepath} after success: {e_del}")
        
        return render_template("playlist_created.html", playlist_name=playlist_name, playlist_url=playlist_url, user_logged_in=True, user_info=user_info) 

    except Exception as e:
        return render_template("playlist_created.html", error_message=f"Playlist creation error: {e}", user_logged_in=True, user_info=user_info)

if __name__ == "__main__":
    if not all([SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI, app.secret_key]):
        print("ERROR: Missing critical environment variables.")
    else:
        app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", port=8888)
