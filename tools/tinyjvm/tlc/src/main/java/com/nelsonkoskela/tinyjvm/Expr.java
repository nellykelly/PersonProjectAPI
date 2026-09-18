package com.nelsonkoskela.tinyjvm;

import java.util.List;

/** The expression AST (build spec section 5.2). A sealed hierarchy plus
 * `switch` pattern matching in Resolver/CodeGen is enough -- no visitor
 * framework needed, and it keeps every node to one line. */
public sealed interface Expr {
    int line();

    record IntLiteral(int value, int line) implements Expr {
    }

    record BoolLiteral(boolean value, int line) implements Expr {
    }

    record Variable(String name, int line) implements Expr {
    }

    record Assign(String name, Expr value, int line) implements Expr {
    }

    /** operator is BANG (!) or MINUS (unary negation). */
    record Unary(TokenType operator, Expr operand, int line) implements Expr {
    }

    record Binary(TokenType operator, Expr left, Expr right, int line) implements Expr {
    }

    /** The grammar allows `call` to chain onto any primary
     * (`call = primary { "(" [args] ")" }`), but this language has no
     * first-class functions -- Resolver rejects any Call whose callee
     * isn't a bare {@link Variable}. Kept general here anyway so the AST
     * matches the grammar, not just the subset that's currently legal. */
    record Call(Expr callee, List<Expr> arguments, int line) implements Expr {
    }
}
