# app.py
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from collections import deque
import os
import time
from werkzeug.utils import secure_filename
import threading

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB limit

# Initialize SocketIO with eventlet (or gevent)
socketio = SocketIO(app, async_mode='eventlet')

# Ensure the uploads folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Global variables
song_queue = deque()       # Queue for upcoming songs
current_song = None        # Currently playing song (dict)
current_song_start_time = None  # When the current song started
skip_votes = set()         # Set of socket IDs who have voted to skip

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/upload', methods=['POST'])
def upload():
    if 'song' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['song']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if not file.filename.lower().endswith('.mp3'):
        return jsonify({'error': 'Invalid file format; only MP3 allowed.'}), 400
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    metadata = {'title': filename, 'artist': 'Unknown'}
    song = {
        'filepath': filepath,
        'metadata': metadata,
        'uploaded_at': time.time()
    }
    song_queue.append(song)
    
    # If no song is playing, start the new one immediately.
    if current_song is None:
        play_next_song()
    
    send_queue_update()
    return jsonify({'success': True, 'message': 'Song uploaded and queued.'}), 200

@socketio.on('connect')
def handle_connect():
    # Update listener count and send queue to new user.
    update_listener_count()
    send_queue_update()
    
    if current_song and current_song_start_time:
        elapsed = time.time() - current_song_start_time
        filename = os.path.basename(current_song['filepath'])
        emit('play_song', {
            'url': f"/uploads/{filename}",
            'metadata': current_song['metadata'],
            'offset': elapsed
        })

@socketio.on('disconnect')
def handle_disconnect():
    update_listener_count()
    sid = request.sid
    if sid in skip_votes:
        skip_votes.discard(sid)
        # Update skip votes for everyone after removal.
        participants = list(socketio.server.manager.get_participants('/', None))
        socketio.emit('skip_votes_update', {
            'votes': len(skip_votes),
            'total': len(participants)
        })

@socketio.on('song_finished')
def song_finished():
    play_next_song()

@socketio.on('chat_message')
def chat_message(data):
    # Broadcast anonymous chat message to all clients.
    emit('chat_message', data, broadcast=True)

@socketio.on('vote_skip')
def vote_skip():
    global skip_votes
    sid = request.sid
    skip_votes.add(sid)
    participants = list(socketio.server.manager.get_participants('/', None))
    total = len(participants)
    socketio.emit('skip_votes_update', {
        'votes': len(skip_votes),
        'total': total
    })
    # If more than 50% of listeners voted to skip, skip the song.
    if total > 0 and (len(skip_votes) / total) > 0.5:
        skip_votes.clear()
        play_next_song()

def send_queue_update():
    """Emit the current song queue to all connected clients."""
    queue_data = [song['metadata'] for song in song_queue]
    socketio.emit('queue_update', {'queue': queue_data})

def update_listener_count():
    participants = list(socketio.server.manager.get_participants('/', None))
    socketio.emit('listener_count', {'count': len(participants)})

def play_next_song():
    global current_song, current_song_start_time, skip_votes
    skip_votes.clear()  # Reset skip votes for new song.
    if song_queue:
        current_song = song_queue.popleft()
        current_song_start_time = time.time()
        filename = os.path.basename(current_song['filepath'])
        socketio.emit('play_song', {
            'url': f"/uploads/{filename}",
            'metadata': current_song['metadata'],
            'offset': 0
        })
        send_queue_update()
        # Reset skip votes display
        participants = list(socketio.server.manager.get_participants('/', None))
        socketio.emit('skip_votes_update', {'votes': 0, 'total': len(participants)})
    else:
        current_song = None
        current_song_start_time = None

def cleanup_old_files():
    """Background thread that deletes files older than 6 hours."""
    while True:
        now = time.time()
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            # Skip the file if it is currently playing.
            if current_song and os.path.basename(current_song['filepath']) == filename:
                continue
            if os.stat(filepath).st_mtime < now - 21600:  # 6 hours = 21600 seconds
                try:
                    os.remove(filepath)
                    print(f"Removed old file: {filepath}")
                except Exception as e:
                    print(f"Error removing file {filepath}: {e}")
        time.sleep(3600)  # Check every hour

# Start the background cleanup thread.
threading.Thread(target=cleanup_old_files, daemon=True).start()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

