package com.nelsonkoskela.tinyjvm;

/** Every kind of token the Lexer produces. See docs/build-spec-tiny-jvm.md
 * section 4 for the language this feeds. */
public enum TokenType {
    // single-character punctuation
    LEFT_PAREN, RIGHT_PAREN, LEFT_BRACE, RIGHT_BRACE, COMMA, SEMICOLON,
    PLUS, MINUS, STAR, SLASH,

    // one- or two-character operators
    BANG, BANG_EQUAL,
    EQUAL, EQUAL_EQUAL,
    GREATER, GREATER_EQUAL,
    LESS, LESS_EQUAL,

    // literals
    IDENTIFIER, INT,

    // keywords
    LET, IF, ELSE, WHILE, FN, RETURN, TRUE, FALSE, PRINT,

    EOF,
}
