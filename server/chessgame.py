import chess
import json
from datetime import datetime
from flask_socketio import emit

class ChessGame:
    def __init__(self, game_uuid, white_player_id, black_player_id):
        self.game_uuid = game_uuid
        self.white_player_id = white_player_id
        self.black_player_id = black_player_id
        self.board = chess.Board()
        self.moves = []
        self.game_over = False
        self.winner = None
        self.socketio = None
        self.last_move_time = datetime.now()
        self.current_turn = 'white'

    def set_socketio(self, socketio):
        self.socketio = socketio

    def make_move(self, player_id, move_uci):
        if self.game_over:
            return False, "La partie est terminée"

        if not self.is_player_turn(player_id):
            return False, "Ce n'est pas votre tour"

        try:
            move = chess.Move.from_uci(move_uci)
            if move in self.board.legal_moves:
                self.board.push(move)
                self.moves.append(move_uci)
                self.last_move_time = datetime.now()
                self.current_turn = 'black' if self.current_turn == 'white' else 'white'
                
                # Vérifier l'état de la partie
                if self.board.is_checkmate():
                    self.game_over = True
                    self.winner = 'white' if self.board.turn == chess.BLACK else 'black'
                elif self.board.is_stalemate() or self.board.is_insufficient_material():
                    self.game_over = True
                    self.winner = None

                # Émettre l'état du jeu à tous les joueurs
                self.emit_game_state()
                return True, "Mouvement effectué"
            else:
                return False, "Mouvement illégal"
        except Exception as e:
            return False, str(e)

    def is_player_turn(self, player_id):
        if self.current_turn == 'white':
            return player_id == self.white_player_id
        else:
            return player_id == self.black_player_id

    def emit_game_state(self):
        if self.socketio:
            game_state = {
                'board_fen': self.board.fen(),
                'current_turn': self.current_turn,
                'is_game_over': self.game_over,
                'winner': self.winner,
                'last_move': self.moves[-1] if self.moves else None,
                'legal_moves': [move.uci() for move in self.board.legal_moves]
            }
            self.socketio.emit('game_state', game_state, room=self.game_uuid)

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