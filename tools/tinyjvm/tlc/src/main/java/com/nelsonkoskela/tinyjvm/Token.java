package com.nelsonkoskela.tinyjvm;

/** One lexeme. `value` only means anything for {@code type == TokenType.INT}
 * (the parsed integer literal); every other token type leaves it 0. */
public record Token(TokenType type, String lexeme, int value, int line) {
    @Override
    public String toString() {
        return type + "(" + lexeme + ")";
    }
}
