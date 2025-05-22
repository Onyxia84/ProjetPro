import chess
import json
from datetime import datetime
from flask_socketio import emit

class ChessGame:
    def __init__(self, game_uuid, white_id, black_id):
        self.game_uuid = game_uuid
        self.white_player_id = white_id
        self.black_player_id = black_id
        self.board = chess.Board()
        self.current_turn = 'white'
        self.game_over = False
        self.socketio = None  # ✅ Important

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
                self.current_turn = 'black' if self.current_turn == 'white' else 'white'
                print("[INFO] Coup appliqué. Nouveau FEN :", self.board.fen())
                self.emit_game_state()
                return True, "Coup accepté"
            else:
                print("[INFO] Coup illégal selon python-chess")
                return False, "Coup illégal"
        except Exception as e:
            print("[ERREUR] Problème dans make_move :", str(e))
            return False, str(e)



    def is_player_turn(self, player_id):
        if self.current_turn == 'white':
            return player_id == self.white_player_id
        else:
            return player_id == self.black_player_id

    def emit_game_state(self):
           self.socketio.emit('game_state', {
        'board_fen': self.board.fen(),
        'turn': 'white' if self.board.turn else 'black',
        'is_game_over': self.board.is_game_over(),
        'winner': 'white' if self.board.result() == '1-0' else 'black' if self.board.result() == '0-1' else 'draw',
    }, room=self.game_uuid)



    def handle_disconnect(self, player_id):
        if not self.game_over:
            self.game_over = True
            self.winner = 'black' if player_id == self.white_player_id else 'white'
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