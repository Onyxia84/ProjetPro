from datetime import datetime
from database import db

class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_uuid = db.Column(db.String(36), unique=True, nullable=False)
    white_player_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    black_player_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    game_type = db.Column(db.String(20))  # 'ranked' ou 'casual'
    status = db.Column(db.String(20))  # 'waiting', 'in_progress', 'completed'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    winner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    white_player = db.relationship('User', foreign_keys=[white_player_id])
    black_player = db.relationship('User', foreign_keys=[black_player_id])
    winner = db.relationship('User', foreign_keys=[winner_id]) 