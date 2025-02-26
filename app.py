from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from collections import deque
import os
import time
from werkzeug.utils import secure_filename
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = 'uploads/'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB file size limit

socketio = SocketIO(app, async_mode='eventlet')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Global variables for song queue, current song, and skip votes.
song_queue = deque()
current_song = None
current_song_start_time = None
skip_votes = set()

@app.route('/')
def index():
    return render_template('index.html')

# Serve favicon if available.
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

# Serve uploaded MP3 files.
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Upload endpoint.
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
    if current_song is None:
        play_next_song()
    # Update all clients with the new queue.
    socketio.emit('queue_update', {'queue': [s['metadata'] for s in song_queue]})
    return jsonify({'success': True, 'message': 'Song uploaded and queued.'}), 200

@socketio.on('connect')
def handle_connect(auth):
    # Send current song info to new client.
    if current_song and current_song_start_time:
        elapsed = time.time() - current_song_start_time
        filename = os.path.basename(current_song['filepath'])
        emit('play_song', {
            'url': f"/uploads/{filename}",
            'metadata': current_song['metadata'],
            'offset': elapsed
        })
    # Also send the current upcoming songs queue.
    emit('queue_update', {'queue': [s['metadata'] for s in song_queue]})
    update_listener_count()

@socketio.on('disconnect')
def handle_disconnect():
    update_listener_count()
    sid = request.sid
    if sid in skip_votes:
        skip_votes.discard(sid)
        # Update skip votes after removal.
        participants = list(socketio.server.manager.get_participants('/', None))
        total = len(participants)
        socketio.emit('skip_votes_update', {'votes': len(skip_votes), 'total': total})

def update_listener_count():
    participants = list(socketio.server.manager.get_participants('/', None))
    count = len(participants)
    socketio.emit('listener_count', {'count': count})

def play_next_song():
    global current_song, current_song_start_time, skip_votes
    skip_votes.clear()  # Reset skip votes for the new song.
    if song_queue:
        current_song = song_queue.popleft()
        current_song_start_time = time.time()
        filename = os.path.basename(current_song['filepath'])
        socketio.emit('play_song', {
            'url': f"/uploads/{filename}",
            'metadata': current_song['metadata'],
            'offset': 0
        })
        socketio.emit('queue_update', {'queue': [s['metadata'] for s in song_queue]})
        socketio.emit('skip_votes_update', {'votes': 0, 'total': len(list(socketio.server.manager.get_participants('/', None)))})
    else:
        current_song = None
        current_song_start_time = None

@socketio.on('song_finished')
def song_finished():
    play_next_song()

# Chat event – broadcast anonymous messages.
@socketio.on('chat_message')
def chat_message(data):
    emit('chat_message', data, broadcast=True)

# Voting Skip – record vote, update skip votes, and check threshold.
@socketio.on('vote_skip')
def vote_skip():
    global skip_votes
    sid = request.sid
    skip_votes.add(sid)
    participants = list(socketio.server.manager.get_participants('/', None))
    total = len(participants)
    votes = len(skip_votes)
    # Emit update to all clients about current skip votes.
    socketio.emit('skip_votes_update', {'votes': votes, 'total': total})
    if total > 0 and votes / total > 0.5:
        skip_votes.clear()
        play_next_song()

# Background cleanup: Delete files older than 6 hours (21600 seconds).
def cleanup_old_files():
    while True:
        now = time.time()
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            # Skip if currently playing.
            if current_song and os.path.basename(current_song['filepath']) == filename:
                continue
            if os.stat(filepath).st_mtime < now - 21600:
                try:
                    os.remove(filepath)
                    print(f"Removed old file: {filepath}")
                except Exception as e:
                    print(f"Error removing file {filepath}: {e}")
        time.sleep(3600)  # Check every hour.

threading.Thread(target=cleanup_old_files, daemon=True).start()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

