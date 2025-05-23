import chess
import json
import time
from database import db, User
from datetime import datetime
from flask_socketio import emit

class ChessGame:
    def __init__(self, game_uuid, white_id, black_id, total_time_seconds=600):
        self.game_uuid = game_uuid
        self.white_player_id = white_id
        self.black_player_id = black_id
        self.board = chess.Board()
        self.current_turn = 'white'
        self.game_over = False
        self.winner = None
        self.socketio = None
        self.clock = {
            'white': total_time_seconds,
            'black': total_time_seconds,
            'last_update': time.time(),
            'running': 'white'
        }

    def set_socketio(self, socketio_instance):
        self.socketio = socketio_instance

    def get_winner(self):
        if not self.board.is_game_over():
            return None
        outcome = self.board.outcome()
        if outcome.winner is None:
            return 'draw'
        return 'white' if outcome.winner else 'black'

    def make_move(self, player_id, move_uci):
        print("[INFO] Coup reçu :", move_uci, "de joueur ID", player_id)
        if self.game_over:
            print("[INFO] La partie est déjà terminée")
            return False, "La partie est terminée"

        try:
            move = chess.Move.from_uci(move_uci)
            if move in self.board.legal_moves:
                self.board.push(move)
                now = time.time()
                elapsed = now - self.clock['last_update']
                if self.board.turn == chess.WHITE:
                    self.clock['black'] -= elapsed
                    self.clock['running'] = 'white'
                else:
                    self.clock['white'] -= elapsed
                    self.clock['running'] = 'black'
                self.clock['last_update'] = now
                self.current_turn = 'black' if self.current_turn == 'white' else 'white'

                if self.board.is_game_over():
                    result = self.board.result()
                    self.game_over = True
                    if result == '1-0':
                        self.winner = 'blancs'
                        self.update_player_stats(winner_id=self.white_player_id, loser_id=self.black_player_id)
                    elif result == '0-1':
                        self.winner = 'noirs'
                        self.update_player_stats(winner_id=self.black_player_id, loser_id=self.white_player_id)
                    else:
                        self.winner = 'draw'
                        self.update_player_stats(draw_ids=[self.white_player_id, self.black_player_id])
                    time.sleep(0.3)
                    self.socketio.emit('game_over', {
                        'result': f'Victoire des {self.winner} !' if self.winner != 'draw' else 'Nulle',
                        'winner': self.winner,
                        'game_uuid': self.game_uuid
                    }, room=self.game_uuid)

                self.emit_game_state()
                return True, "Coup accepté"
            else:
                print("[INFO] Coup illégal selon python-chess")
                return False, "Coup illégal"
        except Exception as e:
            print("[ERREUR] Problème dans make_move :", str(e))
            return False, str(e)

    def update_player_stats(self, winner_id=None, loser_id=None, draw_ids=[]):
        if winner_id:
            winner = User.query.get(winner_id)
            if winner:
                winner.wins += 1
                winner.games_played += 1
        if loser_id:
            loser = User.query.get(loser_id)
            if loser:
                loser.losses += 1
                loser.games_played += 1
        for player_id in draw_ids:
            player = User.query.get(player_id)
            if player:
                player.draws += 1
                player.games_played += 1
        db.session.commit()

    def is_player_turn(self, player_id):
        return (player_id == self.white_player_id if self.current_turn == 'white' else player_id == self.black_player_id)

    def emit_game_state(self):
        self.socketio.emit('game_state', {
            'board_fen': self.board.fen(),
            'turn': 'white' if self.board.turn == chess.WHITE else 'black',
            'is_game_over': self.game_over,
            'winner': self.winner,
            'white_time': self.clock['white'],
            'black_time': self.clock['black'],
            'running': self.clock['running']
        }, room=self.game_uuid)

    def resign(self, player_id):
        if not self.game_over:
            self.game_over = True
            self.winner = 'black' if player_id == self.white_player_id else 'white'
            winner_id = self.black_player_id if player_id == self.white_player_id else self.white_player_id
            self.update_player_stats(winner_id=winner_id, loser_id=player_id)
            self.socketio.emit('game_over', {
                'result': 'Abandon',
                'winner': self.winner
            }, room=self.game_uuid)

    def offer_draw(self):
        if not self.game_over:
            self.socketio.emit('draw_offered', room=self.game_uuid)

    def accept_draw(self):
        if not self.game_over:
            self.game_over = True
            self.update_player_stats(draw_ids=[self.white_player_id, self.black_player_id])
            self.socketio.emit('game_over', {
                'result': 'Nulle',
                'winner': 'draw'
            }, room=self.game_uuid)

    def decline_draw(self):
        if not self.game_over:
            self.socketio.emit('draw_declined', room=self.game_uuid)

    def handle_disconnect(self, player_id):
        if not self.game_over:
            self.game_over = True
            self.winner = 'black' if player_id == self.white_player_id else 'white'
            winner_id = self.black_player_id if player_id == self.white_player_id else self.white_player_id
            self.update_player_stats(winner_id=winner_id, loser_id=player_id)
            if self.socketio:
                self.socketio.emit('game_over', {
                    'result': 'Abandon',
                    'winner': self.winner
                }, room=self.game_uuid)

# Dictionnaire pour stocker les parties actives
active_games = {}

def create_game(game_uuid, white_player_id, black_player_id):
    game = ChessGame(game_uuid, white_player_id, black_player_id)
    active_games[game_uuid] = game
    return game

def get_game(game_uuid):
    return active_games.get(game_uuid)

def remove_game(game_uuid):
    if game_uuid in active_games:
        del active_games[game_uuid]

def init_socketio(socketio):
    @socketio.on('join_game')
    def handle_join_game(data):
        game_uuid = data['game_uuid']
        game = get_game(game_uuid)
        if game:
            game.emit_game_state()
            return {'status': 'success'}
        return {'status': 'error', 'message': 'Partie non trouvée'}

    @socketio.on('make_move')
    def handle_make_move(data):
        game_uuid = data['game_uuid']
        move_uci = data['move']
        game = get_game(game_uuid)
        if game:
            success, message = game.make_move(data['player_id'], move_uci)
            return {'status': 'success' if success else 'error', 'message': message}
        return {'status': 'error', 'message': 'Partie non trouvée'}
