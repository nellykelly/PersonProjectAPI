package com.nelsonkoskela.tinyjvm;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Hand-written single-pass scanner, one character of lookahead. Not a
 * generator (ANTLR/JFlex): the token set is ~20 entries, small enough
 * that a generator would add a build dependency and a grammar file to
 * explain for no real benefit (build spec section 5.1). */
public final class Lexer {
    private static final Map<String, TokenType> KEYWORDS = Map.ofEntries(
            Map.entry("let", TokenType.LET),
            Map.entry("if", TokenType.IF),
            Map.entry("else", TokenType.ELSE),
            Map.entry("while", TokenType.WHILE),
            Map.entry("fn", TokenType.FN),
            Map.entry("return", TokenType.RETURN),
            Map.entry("true", TokenType.TRUE),
            Map.entry("false", TokenType.FALSE),
            Map.entry("print", TokenType.PRINT));

    private final String source;
    private final Diagnostics diagnostics;
    private final List<Token> tokens = new ArrayList<>();

    private int start = 0;
    private int current = 0;
    private int line = 1;

    public Lexer(String source, Diagnostics diagnostics) {
        this.source = source;
        this.diagnostics = diagnostics;
    }

    public List<Token> scanTokens() {
        while (!isAtEnd()) {
            start = current;
            scanToken();
        }
        tokens.add(new Token(TokenType.EOF, "", 0, line));
        return tokens;
    }

    private void scanToken() {
        char c = advance();
        switch (c) {
            case '(' -> addToken(TokenType.LEFT_PAREN);
            case ')' -> addToken(TokenType.RIGHT_PAREN);
            case '{' -> addToken(TokenType.LEFT_BRACE);
            case '}' -> addToken(TokenType.RIGHT_BRACE);
            case ',' -> addToken(TokenType.COMMA);
            case ';' -> addToken(TokenType.SEMICOLON);
            case '+' -> addToken(TokenType.PLUS);
            case '-' -> addToken(TokenType.MINUS);
            case '*' -> addToken(TokenType.STAR);
            case '#' -> skipLineComment();
            case '/' -> {
                if (match('/')) {
                    skipLineComment();
                } else {
                    addToken(TokenType.SLASH);
                }
            }
            case '!' -> addToken(match('=') ? TokenType.BANG_EQUAL : TokenType.BANG);
            case '=' -> addToken(match('=') ? TokenType.EQUAL_EQUAL : TokenType.EQUAL);
            case '<' -> addToken(match('=') ? TokenType.LESS_EQUAL : TokenType.LESS);
            case '>' -> addToken(match('=') ? TokenType.GREATER_EQUAL : TokenType.GREATER);
            case ' ', '\r', '\t' -> {
                // ignore whitespace
            }
            case '\n' -> line++;
            default -> {
                if (Character.isDigit(c)) {
                    number();
                } else if (isAlpha(c)) {
                    identifier();
                } else {
                    diagnostics.error(line, "unexpected character '" + c + "'");
                }
            }
        }
    }

    private void skipLineComment() {
        while (peek() != '\n' && !isAtEnd()) {
            advance();
        }
    }

    private void number() {
        while (Character.isDigit(peek())) {
            advance();
        }
        String text = source.substring(start, current);
        try {
            int value = Integer.parseInt(text);
            addTokenWithValue(TokenType.INT, value);
        } catch (NumberFormatException e) {
            diagnostics.error(line, "integer literal out of range: " + text);
        }
    }

    private void identifier() {
        while (isAlphaNumeric(peek())) {
            advance();
        }
        String text = source.substring(start, current);
        TokenType type = KEYWORDS.getOrDefault(text, TokenType.IDENTIFIER);
        addToken(type);
    }

    private boolean isAlpha(char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
    }

    private boolean isAlphaNumeric(char c) {
        return isAlpha(c) || Character.isDigit(c);
    }

    private boolean isAtEnd() {
        return current >= source.length();
    }

    private char advance() {
        return source.charAt(current++);
    }

    private boolean match(char expected) {
        if (isAtEnd() || source.charAt(current) != expected) {
            return false;
        }
        current++;
        return true;
    }

    private char peek() {
        return isAtEnd() ? '\0' : source.charAt(current);
    }

    private void addToken(TokenType type) {
        addTokenWithValue(type, 0);
    }

    private void addTokenWithValue(TokenType type, int value) {
        String text = source.substring(start, current);
        tokens.add(new Token(type, text, value, line));
    }
}
