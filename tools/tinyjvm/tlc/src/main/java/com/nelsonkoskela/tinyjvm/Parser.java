package com.nelsonkoskela.tinyjvm;

import java.util.ArrayList;
import java.util.List;

/** Recursive descent, one method per grammar rule (build spec section
 * 4's EBNF), precedence encoded by the call chain (equality calls
 * comparison calls term ...) -- the standard Crafting Interpreters
 * structure, and the clearest to read.
 *
 * On a syntax error, {@link #error} reports it to Diagnostics and throws
 * a ParseError; the nearest enclosing loop (block()'s statement loop, or
 * the top-level program loop) catches it and calls synchronize() to skip
 * to a likely statement boundary, so one bad statement doesn't abort the
 * whole parse -- a single run can report several errors. Compilation
 * still fails overall if any were reported (Main.java checks
 * Diagnostics.hadError()).
 */
public final class Parser {
    private static final class ParseError extends RuntimeException {
    }

    private final List<Token> tokens;
    private final Diagnostics diagnostics;
    private int current = 0;

    public Parser(List<Token> tokens, Diagnostics diagnostics) {
        this.tokens = tokens;
        this.diagnostics = diagnostics;
    }

    public List<Stmt> parseProgram() {
        List<Stmt> statements = new ArrayList<>();
        while (!isAtEnd()) {
            Stmt s = declaration();
            if (s != null) {
                statements.add(s);
            }
        }
        return statements;
    }

    // ---- declarations / statements ----

    private Stmt declaration() {
        try {
            if (match(TokenType.FN)) {
                return funcDecl();
            }
            return statement();
        } catch (ParseError e) {
            synchronize();
            return null;
        }
    }

    private Stmt.FuncDecl funcDecl() {
        int line = previous().line();
        Token name = consume(TokenType.IDENTIFIER, "expected function name");
        consume(TokenType.LEFT_PAREN, "expected '(' after function name");
        List<String> params = new ArrayList<>();
        if (!check(TokenType.RIGHT_PAREN)) {
            do {
                params.add(consume(TokenType.IDENTIFIER, "expected parameter name").lexeme());
            } while (match(TokenType.COMMA));
        }
        consume(TokenType.RIGHT_PAREN, "expected ')' after parameters");
        Stmt.Block body = block();
        return new Stmt.FuncDecl(name.lexeme(), params, body, line);
    }

    private Stmt statement() {
        if (match(TokenType.LET)) return letStmt();
        if (match(TokenType.IF)) return ifStmt();
        if (match(TokenType.WHILE)) return whileStmt();
        if (match(TokenType.RETURN)) return returnStmt();
        if (match(TokenType.PRINT)) return printStmt();
        if (check(TokenType.LEFT_BRACE)) return block();
        return exprStmt();
    }

    private Stmt letStmt() {
        int line = previous().line();
        Token name = consume(TokenType.IDENTIFIER, "expected variable name");
        consume(TokenType.EQUAL, "expected '=' after variable name");
        Expr init = expression();
        consume(TokenType.SEMICOLON, "expected ';' after let statement");
        return new Stmt.Let(name.lexeme(), init, line);
    }

    private Stmt ifStmt() {
        int line = previous().line();
        consume(TokenType.LEFT_PAREN, "expected '(' after 'if'");
        Expr condition = expression();
        consume(TokenType.RIGHT_PAREN, "expected ')' after if condition");
        Stmt thenBranch = statement();
        Stmt elseBranch = null;
        if (match(TokenType.ELSE)) {
            elseBranch = statement();
        }
        return new Stmt.If(condition, thenBranch, elseBranch, line);
    }

    private Stmt whileStmt() {
        int line = previous().line();
        consume(TokenType.LEFT_PAREN, "expected '(' after 'while'");
        Expr condition = expression();
        consume(TokenType.RIGHT_PAREN, "expected ')' after while condition");
        Stmt body = statement();
        return new Stmt.While(condition, body, line);
    }

    private Stmt returnStmt() {
        int line = previous().line();
        Expr value = check(TokenType.SEMICOLON) ? null : expression();
        consume(TokenType.SEMICOLON, "expected ';' after return statement");
        return new Stmt.Return(value, line);
    }

    private Stmt printStmt() {
        int line = previous().line();
        Expr value = expression();
        consume(TokenType.SEMICOLON, "expected ';' after print statement");
        return new Stmt.Print(value, line);
    }

    private Stmt.Block block() {
        Token brace = consume(TokenType.LEFT_BRACE, "expected '{'");
        List<Stmt> statements = new ArrayList<>();
        while (!check(TokenType.RIGHT_BRACE) && !isAtEnd()) {
            try {
                statements.add(statement());
            } catch (ParseError e) {
                synchronize();
            }
        }
        consume(TokenType.RIGHT_BRACE, "expected '}' after block");
        return new Stmt.Block(statements, brace.line());
    }

    private Stmt exprStmt() {
        int line = peek().line();
        Expr expr = expression();
        consume(TokenType.SEMICOLON, "expected ';' after expression");
        return new Stmt.ExprStmt(expr, line);
    }

    // ---- expressions, lowest to highest precedence ----

    private Expr expression() {
        return assignment();
    }

    private Expr assignment() {
        Expr expr = equality();
        if (match(TokenType.EQUAL)) {
            Token equals = previous();
            Expr value = assignment(); // right-associative
            if (expr instanceof Expr.Variable v) {
                return new Expr.Assign(v.name(), value, v.line());
            }
            error(equals.line(), "invalid assignment target");
            return expr; // degrade gracefully so parsing can continue
        }
        return expr;
    }

    private Expr equality() {
        Expr expr = comparison();
        while (match(TokenType.EQUAL_EQUAL, TokenType.BANG_EQUAL)) {
            Token op = previous();
            expr = new Expr.Binary(op.type(), expr, comparison(), op.line());
        }
        return expr;
    }

    private Expr comparison() {
        Expr expr = term();
        while (match(TokenType.LESS, TokenType.LESS_EQUAL, TokenType.GREATER, TokenType.GREATER_EQUAL)) {
            Token op = previous();
            expr = new Expr.Binary(op.type(), expr, term(), op.line());
        }
        return expr;
    }

    private Expr term() {
        Expr expr = factor();
        while (match(TokenType.PLUS, TokenType.MINUS)) {
            Token op = previous();
            expr = new Expr.Binary(op.type(), expr, factor(), op.line());
        }
        return expr;
    }

    private Expr factor() {
        Expr expr = unary();
        while (match(TokenType.STAR, TokenType.SLASH)) {
            Token op = previous();
            expr = new Expr.Binary(op.type(), expr, unary(), op.line());
        }
        return expr;
    }

    private Expr unary() {
        if (match(TokenType.MINUS, TokenType.BANG)) {
            Token op = previous();
            return new Expr.Unary(op.type(), unary(), op.line());
        }
        return call();
    }

    private Expr call() {
        Expr expr = primary();
        while (match(TokenType.LEFT_PAREN)) {
            List<Expr> args = new ArrayList<>();
            if (!check(TokenType.RIGHT_PAREN)) {
                do {
                    args.add(expression());
                } while (match(TokenType.COMMA));
            }
            Token paren = consume(TokenType.RIGHT_PAREN, "expected ')' after arguments");
            expr = new Expr.Call(expr, args, paren.line());
        }
        return expr;
    }

    private Expr primary() {
        if (match(TokenType.INT)) {
            Token t = previous();
            return new Expr.IntLiteral(t.value(), t.line());
        }
        if (match(TokenType.TRUE)) {
            return new Expr.BoolLiteral(true, previous().line());
        }
        if (match(TokenType.FALSE)) {
            return new Expr.BoolLiteral(false, previous().line());
        }
        if (match(TokenType.IDENTIFIER)) {
            Token t = previous();
            return new Expr.Variable(t.lexeme(), t.line());
        }
        if (match(TokenType.LEFT_PAREN)) {
            Expr expr = expression();
            consume(TokenType.RIGHT_PAREN, "expected ')' after expression");
            return expr;
        }
        throw error(peek().line(), "expected an expression, got " + peek());
    }

    // ---- token stream primitives ----

    private boolean match(TokenType... types) {
        for (TokenType type : types) {
            if (check(type)) {
                advance();
                return true;
            }
        }
        return false;
    }

    private boolean check(TokenType type) {
        return !isAtEnd() && peek().type() == type;
    }

    private Token advance() {
        if (!isAtEnd()) current++;
        return previous();
    }

    private boolean isAtEnd() {
        return peek().type() == TokenType.EOF;
    }

    private Token peek() {
        return tokens.get(current);
    }

    private Token previous() {
        return tokens.get(current - 1);
    }

    private Token consume(TokenType type, String message) {
        if (check(type)) {
            return advance();
        }
        throw error(peek().line(), message + " (got " + peek() + ")");
    }

    private ParseError error(int line, String message) {
        diagnostics.error(line, message);
        return new ParseError();
    }

    /** Skip to a likely statement boundary after a syntax error: past the
     * next ';', or right before a token that clearly starts a new
     * statement. Keeps one bad statement from cascading into a wall of
     * spurious follow-on errors.
     *
     * Deliberately does NOT unconditionally advance() before the loop --
     * a `consume()` failure often means the *current* token is already a
     * perfectly good recovery point (e.g. a missing ';' before a `let`
     * that's otherwise fine), and skipping it would throw away a whole
     * valid statement instead of just resyncing to it. */
    private void synchronize() {
        while (!isAtEnd()) {
            if (previous().type() == TokenType.SEMICOLON) {
                return;
            }
            switch (peek().type()) {
                case FN, LET, IF, WHILE, RETURN, PRINT -> {
                    return;
                }
                default -> {
                }
            }
            advance();
        }
    }
}
