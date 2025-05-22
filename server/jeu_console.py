import chess

def afficher_plateau(board):
    print(board)
    print(f"Tour de {'Blancs' if board.turn == chess.WHITE else 'Noirs'}")

def main():
    board = chess.Board()
    
    while not board.is_game_over():
        afficher_plateau(board)
        move_input = input("Entrez votre coup (en notation UCI, ex: e2e4) : ")
        
        try:
            move = chess.Move.from_uci(move_input)
            if move in board.legal_moves:
                board.push(move)
            else:
                print("Coup illégal. Essayez encore.")
        except Exception as e:
            print(f"Erreur : {e}")
    
    print("Partie terminée.")
    print("Résultat :", board.result())

if __name__ == "__main__":
    main()
