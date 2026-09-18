package com.nelsonkoskela.tinyjvm;

import java.util.List;

/** The statement AST (build spec section 5.2). `elseBranch` and `value`
 * are nullable where the grammar marks the production optional
 * (`ifStmt`'s else, `returnStmt`'s expression). */
public sealed interface Stmt {
    int line();

    record Let(String name, Expr initializer, int line) implements Stmt {
    }

    record If(Expr condition, Stmt thenBranch, Stmt elseBranch, int line) implements Stmt {
    }

    record While(Expr condition, Stmt body, int line) implements Stmt {
    }

    record Return(Expr value, int line) implements Stmt {
    }

    /** Not in the build spec's summary grammar, but required by the
     * SAMPLE_PROGRAMS already live on the site ("print a;", "print
     * fact(6);" -- no parens, a statement form, not a call expression).
     * A dedicated statement rather than folding into exprStmt because it
     * compiles straight to the PRINT opcode, never through CALL. */
    record Print(Expr value, int line) implements Stmt {
    }

    record Block(List<Stmt> statements, int line) implements Stmt {
    }

    record ExprStmt(Expr expression, int line) implements Stmt {
    }

    record FuncDecl(String name, List<String> params, Block body, int line) implements Stmt {
    }
}
