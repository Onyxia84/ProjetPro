from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, join_room, leave_room
import os
from datetime import datetime, UTC
from collections import defaultdict
import threading
import time
import uuid
from chessgame import init_socketio, create_game, active_games, get_game

app = Flask(__name__)
app.config['SECRET_KEY'] = 'votre_cle_secrete_ici'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chess.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

socketio = SocketIO(app)
init_socketio(socketio)

# File d'attente pour le matchmaking
matchmaking_queue = defaultdict(list)
queue_lock = threading.Lock()

# Dictionnaire pour stocker les notifications de match
match_notifications = defaultdict(dict)

user_sockets = {}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    games_played = db.Column(db.Integer, default=0)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    draws = db.Column(db.Integer, default=0)
    elo_rating = db.Column(db.Integer, default=1000)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_uuid = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4()))
    white_player_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    black_player_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    game_type = db.Column(db.String(20), nullable=False, default='casual')
    status = db.Column(db.String(20), nullable=False, default='waiting')
    name = db.Column(db.String(100), nullable=False, default='Partie d\'échecs')
    description = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    
    white_player = db.relationship('User', foreign_keys=[white_player_id], backref='white_games')
    black_player = db.relationship('User', foreign_keys=[black_player_id], backref='black_games')
    
    def to_dict(self):
        return {
            'id': self.id,
            'game_uuid': self.game_uuid,
            'white_player': self.white_player.username if self.white_player else None,
            'black_player': self.black_player.username if self.black_player else None,
            'game_type': self.game_type,
            'status': self.status,
            'name': self.name,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html')

@app.route('/games')
@login_required
def games():
    return render_template('games.html')

@app.route('/api/games/create', methods=['POST'])
@login_required
def create_game_api():
    try:
        data = request.get_json()
        game_type = data.get('game_type', 'casual')
        name = data.get('name', 'Partie d\'échecs')
        description = data.get('description', '')
        
        # Vérifier si l'utilisateur a déjà une partie en cours
        active_game = Game.query.filter(
            ((Game.white_player_id == current_user.id) | (Game.black_player_id == current_user.id)) &
            (Game.status.in_(['waiting', 'active']))
        ).first()
        
        if active_game:
            return jsonify({
                'error': 'Vous avez déjà une partie en cours',
                'game_uuid': active_game.game_uuid
            }), 400
        
        # Créer une nouvelle partie
        game = Game(
            white_player_id=current_user.id,
            game_type=game_type,
            status='waiting',
            name=name,
            description=description
        )
        db.session.add(game)
        db.session.commit()
        
        # Notifier le créateur de la partie
        match_notifications[current_user.id] = {
            'match_found': True,
            'game_uuid': game.game_uuid,
            'opponent': None,
            'color': 'white',
            'name': game.name,
            'description': game.description
        }
        
        return jsonify({
            'success': True,
            'game_id': game.id,
            'game_uuid': game.game_uuid,
            'name': game.name,
            'description': game.description
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/games/waiting')
@login_required
def get_waiting_games():
    games = Game.query.filter_by(status='waiting').all()
    return jsonify([{
        'id': game.id,
        'name': game.name,
        'description': game.description,
        'white_player': game.white_player.username,
        'game_type': game.game_type,
        'created_at': game.created_at.isoformat()
    } for game in games])

@app.route('/api/games/join/<int:game_id>', methods=['POST'])
@login_required
def join_game(game_id):
    game = Game.query.get_or_404(game_id)
    
    if game.status != 'waiting':
        return jsonify({'error': 'Cette partie n\'est plus disponible'}), 400
    
    if game.white_player_id == current_user.id:
        return jsonify({'error': 'Vous ne pouvez pas rejoindre votre propre partie'}), 400
    
    game.black_player_id = current_user.id
    game.status = 'active'
    db.session.commit()
    
    # Notifier les deux joueurs
    match_notifications[game.white_player_id] = {
        'match_found': True,
        'game_uuid': game.game_uuid,
        'opponent': current_user.username,
        'color': 'white',
        'name': game.name,
        'description': game.description
    }
    match_notifications[game.black_player_id] = {
        'match_found': True,
        'game_uuid': game.game_uuid,
        'opponent': game.white_player.username,
        'color': 'black',
        'name': game.name,
        'description': game.description
    }
    
    return jsonify({
        'success': True,
        'game_uuid': game.game_uuid,
        'opponent': game.white_player.username,
        'color': 'black',
        'name': game.name,
        'description': game.description
    })

@app.route('/api/games/matchmaking', methods=['POST'])
@login_required
def matchmaking():
    data = request.get_json()
    game_type = data.get('game_type', 'casual')
    
    with queue_lock:
        # Vérifier si l'utilisateur est déjà dans la file d'attente
        if current_user.id in [user['id'] for user in matchmaking_queue[game_type]]:
            return jsonify({'error': 'Vous êtes déjà dans la file d\'attente'}), 400
        
        # Ajouter l'utilisateur à la file d'attente
        matchmaking_queue[game_type].append({
            'id': current_user.id,
            'username': current_user.username,
            'elo': current_user.elo_rating,
            'timestamp': datetime.now(UTC),
            'name': data.get('name', 'Partie d\'échecs'),
            'description': data.get('description', '')
        })
        
        # Vérifier s'il y a un match possible
        if len(matchmaking_queue[game_type]) >= 2:
            if game_type == 'ranked':
                # Trier par ELO pour le mode classé
                matchmaking_queue[game_type].sort(key=lambda x: x['elo'])
                # Prendre les deux joueurs les plus proches en ELO
                player1 = matchmaking_queue[game_type][0]
                player2 = matchmaking_queue[game_type][1]
                if abs(player1['elo'] - player2['elo']) <= 200:
                    matchmaking_queue[game_type].remove(player1)
                    matchmaking_queue[game_type].remove(player2)
                    
                    # Créer la partie
                    game = Game(
                        game_uuid=str(uuid.uuid4()),
                        white_player_id=player1['id'],
                        black_player_id=player2['id'],
                        game_type=game_type,
                        status='in_progress',
                        name=data.get('name', 'Partie d\'échecs'),
                        description=data.get('description', '')
                    )
                    db.session.add(game)
                    db.session.commit()
                    
                    # Notifier les deux joueurs immédiatement
                    match_notifications[player1['id']] = {
                        'game_id': game.id,
                        'game_uuid': game.game_uuid,
                        'opponent': player2['username'],
                        'color': 'white',
                        'match_found': True,
                        'name': game.name,
                        'description': game.description
                    }
                    match_notifications[player2['id']] = {
                        'game_id': game.id,
                        'game_uuid': game.game_uuid,
                        'opponent': player1['username'],
                        'color': 'black',
                        'match_found': True,
                        'name': game.name,
                        'description': game.description
                    }
                    
                    return jsonify({
                        'message': 'Match trouvé !',
                        'game_id': game.id,
                        'game_uuid': game.game_uuid,
                        'opponent': player2['username'] if current_user.id == player1['id'] else player1['username'],
                        'color': 'white' if current_user.id == player1['id'] else 'black',
                        'match_found': True,
                        'name': game.name,
                        'description': game.description
                    }), 200
            else:
                # Mode casual : prendre les deux premiers joueurs
                player1 = matchmaking_queue[game_type][0]
                player2 = matchmaking_queue[game_type][1]
                matchmaking_queue[game_type].remove(player1)
                matchmaking_queue[game_type].remove(player2)
                
                # Créer la partie
                game = Game(
                    game_uuid=str(uuid.uuid4()),
                    white_player_id=player1['id'],
                    black_player_id=player2['id'],
                    game_type=game_type,
                    status='in_progress',
                    name=data.get('name', 'Partie d\'échecs'),
                    description=data.get('description', '')
                )
                db.session.add(game)
                db.session.commit()
                
                # Notifier les deux joueurs immédiatement
                match_notifications[player1['id']] = {
                    'game_id': game.id,
                    'game_uuid': game.game_uuid,
                    'opponent': player2['username'],
                    'color': 'white',
                    'match_found': True,
                    'name': game.name,
                    'description': game.description
                }
                match_notifications[player2['id']] = {
                    'game_id': game.id,
                    'game_uuid': game.game_uuid,
                    'opponent': player1['username'],
                    'color': 'black',
                    'match_found': True,
                    'name': game.name,
                    'description': game.description
                }
                
                return jsonify({
                    'message': 'Match trouvé !',
                    'game_id': game.id,
                    'game_uuid': game.game_uuid,
                    'opponent': player2['username'] if current_user.id == player1['id'] else player1['username'],
                    'color': 'white' if current_user.id == player1['id'] else 'black',
                    'match_found': True,
                    'name': game.name,
                    'description': game.description
                }), 200
    
    return jsonify({
        'message': 'Recherche d\'adversaire en cours...',
        'queue_position': len(matchmaking_queue[game_type]),
        'match_found': False
    }), 200

@app.route('/api/games/matchmaking/status')
@login_required
def matchmaking_status():
    game_type = request.args.get('game_type', 'casual')
    
    with queue_lock:
        queue_position = next((i for i, user in enumerate(matchmaking_queue[game_type]) if user['id'] == current_user.id), None)
        
        if queue_position is not None:
            return jsonify({
                'in_queue': True,
                'queue_position': queue_position + 1,
                'total_players': len(matchmaking_queue[game_type])
            })
        
        return jsonify({'in_queue': False})

@app.route('/api/games/matchmaking/cancel', methods=['POST'])
@login_required
def cancel_matchmaking():
    game_type = request.args.get('game_type', 'casual')
    
    with queue_lock:
        matchmaking_queue[game_type] = [user for user in matchmaking_queue[game_type] if user['id'] != current_user.id]
    
    return jsonify({'message': 'Recherche d\'adversaire annulée'}), 200

@app.route('/api/games/matchmaking/check')
@login_required
def check_matchmaking():
    """Vérifie si un match a été trouvé pour l'utilisateur"""
    if current_user.id in match_notifications:
        notification = match_notifications.pop(current_user.id)
        return jsonify({
            'match_found': True,
            'game_id': notification['game_id'],
            'game_uuid': notification['game_uuid'],
            'opponent': notification['opponent'],
            'color': notification['color'],
            'name': notification['name'],
            'description': notification['description']
        })
    return jsonify({'match_found': False})

@app.route('/chessgame/<string:game_uuid>')
@login_required
def game_page(game_uuid):
    game = Game.query.filter_by(game_uuid=game_uuid).first_or_404()
    if game.white_player_id != current_user.id and game.black_player_id != current_user.id:
        return redirect(url_for('games'))
    
    color = 'white' if game.white_player_id == current_user.id else 'black'
    opponent = game.black_player if color == 'white' else game.white_player
    
    return render_template('chessgame.html', game=game, color=color, opponent=opponent)

@app.route('/api/user/status')
def user_status():
    if current_user.is_authenticated:
        return jsonify({
            'is_authenticated': True,
            'username': current_user.username,
            'email': current_user.email,
            'stats': {
                "games_played": current_user.games_played,
                "wins": current_user.wins,
                "losses": current_user.losses,
                "draws": current_user.draws,
                "elo_rating": current_user.elo_rating
            }
        })
    return jsonify({'is_authenticated': False})

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    
    if User.query.filter_by(username=data['username']).first():
        return jsonify({'error': 'Nom d\'utilisateur déjà pris'}), 400
    
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email déjà utilisé'}), 400
    
    user = User(username=data['username'], email=data['email'])
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    return jsonify({'message': 'Inscription réussie'}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    user = User.query.filter_by(username=data['username']).first()
    
    if user and user.check_password(data['password']):
        login_user(user)
        return jsonify({
            'message': 'Connexion réussie',
            'user': {
                'username': user.username,
                'email': user.email
            }
        }), 200
    
    return jsonify({'error': 'Identifiants invalides'}), 401

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return jsonify({'message': 'Déconnexion réussie'}), 200

@socketio.on('connect')
def handle_connect():
    print("[SOCKET] Connexion établie (mais sans user_id tant que join_game n'est pas appelé)")


@socketio.on('disconnect')
def handle_disconnect():
    print('Client déconnecté')

@socketio.on('join_game')
def handle_join_game(data):
    game_uuid = data['game_uuid']
    game = Game.query.filter_by(game_uuid=game_uuid).first()

    if game and current_user.id in [game.white_player_id, game.black_player_id]:
        join_room(game_uuid)

        # 🧠 Ajoute ces lignes ici
        user_sockets[current_user.id] = request.sid
        if request.sid in socketio.server.environ:
            socketio.server.environ[request.sid]['user_id'] = current_user.id

        print(f"[SOCKET] {current_user.username} a rejoint la room {game_uuid}")

        # Initialiser la partie si elle n'existe pas déjà
        if game_uuid not in active_games:
            chess_game = create_game(game_uuid, game.white_player_id, game.black_player_id)
            chess_game.set_socketio(socketio)
            active_games[game_uuid] = chess_game

        return {'status': 'success'}

    return {'status': 'error', 'message': 'Partie non trouvée ou accès non autorisé'}



@socketio.on('make_move')
def handle_make_move(data):
    print("[DEBUG] Socket reçu : make_move", data)
    game_uuid = data['game_uuid']
    move_uci = data['move']
    
    if game_uuid in active_games:
        game = active_games[game_uuid]
        success, message = game.make_move(current_user.id, move_uci)
        if success:
            # Émettre l'état du jeu à tous les joueurs dans la salle
            game.emit_game_state()
        return {'status': 'success' if success else 'error', 'message': message}
    return {'status': 'error', 'message': 'Partie non trouvée'}

@socketio.on('resign_game')
def handle_resign_game(data):
    game_uuid = data.get('game_uuid')
    player_id = current_user.id
    game = get_game(game_uuid)

    if game:
        game.game_over = True

        if player_id == game.white_player_id:
            game.winner = 'black'
            winner_color = 'Noirs'
        else:
            game.winner = 'white'
            winner_color = 'Blancs'

        db_game = Game.query.filter_by(game_uuid=game_uuid).first()
        if db_game:
            db_game.status = 'finished'
            db.session.commit()

        socketio.emit('game_over', {
            'result': f'Victoire des {winner_color} (abandon)',
            'winner': game.winner,
            'game_uuid': game_uuid
        }, room=game_uuid)



@socketio.on('offer_draw')
def handle_offer_draw(data):
    print(f"[SOCKET] Offre de nulle reçue pour {data['game_uuid']} par {current_user.username}")
    game_uuid = data.get('game_uuid')
    game = get_game(game_uuid)

    if game:
        if current_user.id == game.white_player_id:
            opponent_id = game.black_player_id
        else:
            opponent_id = game.white_player_id

        opponent_sid = user_sockets.get(opponent_id)
        if opponent_sid:
            socketio.emit('draw_offered', {'game_uuid': game_uuid}, room=opponent_sid)
        else:
            print(f"[ERREUR] SID non trouvé pour l'adversaire {opponent_id}")


@socketio.on('accept_draw')
def handle_accept_draw(data):
    game = get_game(data['game_uuid'])
    if game:
        game.game_over = True
        game.winner = None
        db_game = Game.query.filter_by(game_uuid=game.game_uuid).first()
        if db_game:
            db_game.status = 'finished'
            db.session.commit()

        socketio.emit('game_over', {
            'result': 'Partie nulle',
            'game_uuid': game.game_uuid
        }, room=game.game_uuid)

@socketio.on('decline_draw')
def handle_decline_draw(data):
    game = get_game(data['game_uuid'])
    if game:
        game.decline_draw()




@socketio.on('send_greeting')
def handle_greeting(data):
    print("Greeting reçu :", data)
    game_uuid = data['game_uuid']
    message = data['message']
    # Envoyer à tous les membres de la room (y compris l'expéditeur si souhaité)
    socketio.emit('receive_greeting', {'message': message}, room=game_uuid)


# Créer la base de données si elle n'existe pas
with app.app_context():
    if not os.path.exists('chess.db'):
        db.create_all()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True) 