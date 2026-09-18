package com.nelsonkoskela.tinyjvm;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class LexerTest {

    private List<Token> lex(String source, Diagnostics diagnostics) {
        return new Lexer(source, diagnostics).scanTokens();
    }

    @Test
    void tokenizesLetStatementWithArithmetic() {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = lex("let x = 1 + 2;", d);
        assertFalse(d.hadError());
        assertEquals(
                List.of(TokenType.LET, TokenType.IDENTIFIER, TokenType.EQUAL, TokenType.INT,
                        TokenType.PLUS, TokenType.INT, TokenType.SEMICOLON, TokenType.EOF),
                tokens.stream().map(Token::type).toList());
    }

    @Test
    void distinguishesEqualsFromEqualEquals() {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = lex("a = b == c;", d);
        assertFalse(d.hadError());
        assertEquals(TokenType.EQUAL, tokens.get(1).type());
        assertEquals(TokenType.EQUAL_EQUAL, tokens.get(3).type());
    }

    @Test
    void distinguishesBangFromBangEqual() {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = lex("!a != b;", d);
        assertEquals(TokenType.BANG, tokens.get(0).type());
        assertEquals(TokenType.BANG_EQUAL, tokens.get(2).type());
    }

    @Test
    void recognizesAllKeywords() {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = lex("let if else while fn return true false print", d);
        assertEquals(
                List.of(TokenType.LET, TokenType.IF, TokenType.ELSE, TokenType.WHILE,
                        TokenType.FN, TokenType.RETURN, TokenType.TRUE, TokenType.FALSE,
                        TokenType.PRINT, TokenType.EOF),
                tokens.stream().map(Token::type).toList());
    }

    @Test
    void keywordPrefixIsStillAnIdentifier() {
        // "letter" must not be lexed as LET + "ter" -- maximal munch.
        Diagnostics d = new Diagnostics();
        List<Token> tokens = lex("letter", d);
        assertEquals(TokenType.IDENTIFIER, tokens.get(0).type());
        assertEquals("letter", tokens.get(0).lexeme());
    }

    @Test
    void parsesIntegerLiteralValue() {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = lex("12345", d);
        assertEquals(12345, tokens.get(0).value());
    }

    @Test
    void skipsSlashSlashAndHashComments() {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = lex("let x = 1; // trailing\n# whole line\nlet y = 2;", d);
        assertFalse(d.hadError());
        // Two full statements' worth of tokens, comments produce none.
        long letCount = tokens.stream().filter(t -> t.type() == TokenType.LET).count();
        assertEquals(2, letCount);
    }

    @Test
    void tracksLineNumbersAcrossNewlines() {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = lex("let x = 1;\nlet y = 2;", d);
        Token secondLet = tokens.stream().filter(t -> t.type() == TokenType.LET).toList().get(1);
        assertEquals(2, secondLet.line());
    }

    @Test
    void reportsUnexpectedCharacterButKeepsScanning() {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = lex("let x = 1 @ 2;", d);
        assertTrue(d.hadError());
        // Scanning continued past the bad character rather than stopping dead.
        assertTrue(tokens.stream().anyMatch(t -> t.type() == TokenType.SEMICOLON));
    }
}
