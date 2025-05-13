from flask import Blueprint, request, jsonify, render_template, redirect, url_for
from flask_login import login_required, current_user
from models.game import Game
from models.user import User
from database import db
import uuid
from datetime import datetime
from collections import defaultdict
import threading

game_bp = Blueprint('game', __name__)

# File d'attente pour le matchmaking
matchmaking_queue = defaultdict(list)
queue_lock = threading.Lock()

# Dictionnaire pour stocker les notifications de match
match_notifications = defaultdict(dict)

@game_bp.route('/games')
@login_required
def games():
    return render_template('games.html')

@game_bp.route('/api/games/create', methods=['POST'])
@login_required
def create_game():
    data = request.get_json()
    game_type = data.get('game_type', 'casual')
    
    game = Game(
        game_uuid=str(uuid.uuid4()),
        white_player_id=current_user.id,
        game_type=game_type,
        status='waiting'
    )
    
    db.session.add(game)
    db.session.commit()
    
    return jsonify({
        'message': 'Partie créée avec succès',
        'game_id': game.id,
        'game_uuid': game.game_uuid
    }), 201

@game_bp.route('/api/games/waiting')
@login_required
def get_waiting_games():
    games = Game.query.filter_by(status='waiting').all()
    return jsonify([{
        'id': game.id,
        'white_player': game.white_player.username,
        'game_type': game.game_type,
        'created_at': game.created_at.isoformat()
    } for game in games])

@game_bp.route('/api/games/join/<int:game_id>', methods=['POST'])
@login_required
def join_game(game_id):
    game = Game.query.get_or_404(game_id)
    
    if game.status != 'waiting':
        return jsonify({'error': 'Cette partie n\'est plus disponible'}), 400
    
    if game.white_player_id == current_user.id:
        return jsonify({'error': 'Vous ne pouvez pas rejoindre votre propre partie'}), 400
    
    game.black_player_id = current_user.id
    game.status = 'in_progress'
    db.session.commit()
    
    return jsonify({
        'message': 'Vous avez rejoint la partie',
        'game_uuid': game.game_uuid
    }), 200

@game_bp.route('/api/games/matchmaking', methods=['POST'])
@login_required
def matchmaking():
    data = request.get_json()
    game_type = data.get('game_type', 'casual')
    
    with queue_lock:
        if current_user.id in [user['id'] for user in matchmaking_queue[game_type]]:
            return jsonify({'error': 'Vous êtes déjà dans la file d\'attente'}), 400
        
        matchmaking_queue[game_type].append({
            'id': current_user.id,
            'username': current_user.username,
            'elo': current_user.elo_rating,
            'timestamp': datetime.utcnow()
        })
        
        if len(matchmaking_queue[game_type]) >= 2:
            if game_type == 'ranked':
                matchmaking_queue[game_type].sort(key=lambda x: x['elo'])
                player1 = matchmaking_queue[game_type][0]
                player2 = matchmaking_queue[game_type][1]
                if abs(player1['elo'] - player2['elo']) <= 200:
                    matchmaking_queue[game_type].remove(player1)
                    matchmaking_queue[game_type].remove(player2)
                    
                    game = Game(
                        game_uuid=str(uuid.uuid4()),
                        white_player_id=player1['id'],
                        black_player_id=player2['id'],
                        game_type=game_type,
                        status='in_progress'
                    )
                    db.session.add(game)
                    db.session.commit()
                    
                    match_notifications[player1['id']] = {
                        'game_id': game.id,
                        'game_uuid': game.game_uuid,
                        'opponent': player2['username'],
                        'color': 'white',
                        'match_found': True
                    }
                    match_notifications[player2['id']] = {
                        'game_id': game.id,
                        'game_uuid': game.game_uuid,
                        'opponent': player1['username'],
                        'color': 'black',
                        'match_found': True
                    }
                    
                    return jsonify({
                        'message': 'Match trouvé !',
                        'game_id': game.id,
                        'game_uuid': game.game_uuid,
                        'opponent': player2['username'] if current_user.id == player1['id'] else player1['username'],
                        'color': 'white' if current_user.id == player1['id'] else 'black',
                        'match_found': True
                    }), 200
            else:
                player1 = matchmaking_queue[game_type][0]
                player2 = matchmaking_queue[game_type][1]
                matchmaking_queue[game_type].remove(player1)
                matchmaking_queue[game_type].remove(player2)
                
                game = Game(
                    game_uuid=str(uuid.uuid4()),
                    white_player_id=player1['id'],
                    black_player_id=player2['id'],
                    game_type=game_type,
                    status='in_progress'
                )
                db.session.add(game)
                db.session.commit()
                
                match_notifications[player1['id']] = {
                    'game_id': game.id,
                    'game_uuid': game.game_uuid,
                    'opponent': player2['username'],
                    'color': 'white',
                    'match_found': True
                }
                match_notifications[player2['id']] = {
                    'game_id': game.id,
                    'game_uuid': game.game_uuid,
                    'opponent': player1['username'],
                    'color': 'black',
                    'match_found': True
                }
                
                return jsonify({
                    'message': 'Match trouvé !',
                    'game_id': game.id,
                    'game_uuid': game.game_uuid,
                    'opponent': player2['username'] if current_user.id == player1['id'] else player1['username'],
                    'color': 'white' if current_user.id == player1['id'] else 'black',
                    'match_found': True
                }), 200
    
    return jsonify({
        'message': 'Recherche d\'adversaire en cours...',
        'queue_position': len(matchmaking_queue[game_type]),
        'match_found': False
    }), 200

@game_bp.route('/api/games/matchmaking/status')
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

@game_bp.route('/api/games/matchmaking/cancel', methods=['POST'])
@login_required
def cancel_matchmaking():
    game_type = request.args.get('game_type', 'casual')
    
    with queue_lock:
        matchmaking_queue[game_type] = [user for user in matchmaking_queue[game_type] if user['id'] != current_user.id]
    
    return jsonify({'message': 'Recherche d\'adversaire annulée'}), 200

@game_bp.route('/api/games/matchmaking/check')
@login_required
def check_matchmaking():
    if current_user.id in match_notifications:
        notification = match_notifications.pop(current_user.id)
        return jsonify({
            'match_found': True,
            'game_id': notification['game_id'],
            'game_uuid': notification['game_uuid'],
            'opponent': notification['opponent'],
            'color': notification['color']
        })
    return jsonify({'match_found': False})

@game_bp.route('/chessgame/<string:game_uuid>')
@login_required
def game_page(game_uuid):
    game = Game.query.filter_by(game_uuid=game_uuid).first_or_404()
    if game.white_player_id != current_user.id and game.black_player_id != current_user.id:
        return redirect(url_for('game.games'))
    
    color = 'white' if game.white_player_id == current_user.id else 'black'
    opponent = game.black_player if color == 'white' else game.white_player
    
    return render_template('chessgame.html', game=game, color=color, opponent=opponent) 