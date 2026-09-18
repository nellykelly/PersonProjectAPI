package com.nelsonkoskela.tinyjvm;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ResolverTest {

    private Resolver.Resolved resolve(String source, Diagnostics d) {
        List<Token> tokens = new Lexer(source, d).scanTokens();
        List<Stmt> program = new Parser(tokens, d).parseProgram();
        return new Resolver(d).resolve(program);
    }

    @Test
    void assignsSequentialSlotsToTopLevelLets() {
        Diagnostics d = new Diagnostics();
        Resolver.Resolved r = resolve("let a = 1; let b = 2; let c = a + b;", d);
        assertFalse(d.hadError());
        Stmt.Let letA = (Stmt.Let) r.topLevelStatements().get(0);
        Stmt.Let letB = (Stmt.Let) r.topLevelStatements().get(1);
        Stmt.Let letC = (Stmt.Let) r.topLevelStatements().get(2);
        assertEquals(0, r.slots().get(letA));
        assertEquals(1, r.slots().get(letB));
        assertEquals(2, r.slots().get(letC));
    }

    @Test
    void functionParametersGetSlotsInDeclarationOrder() {
        Diagnostics d = new Diagnostics();
        Resolver.Resolved r = resolve("fn add(a, b) { return a + b; }", d);
        assertFalse(d.hadError());
        Stmt.FuncDecl fn = r.functions().get(0);
        Stmt.Return ret = (Stmt.Return) fn.body().statements().get(0);
        Expr.Binary add = (Expr.Binary) ret.value();
        assertEquals(0, r.slots().get(add.left()));  // param a
        assertEquals(1, r.slots().get(add.right())); // param b
    }

    @Test
    void slotsAreNeverReusedAcrossSiblingBlocksInOneFunction() {
        // Two separate `let x` in sibling if/else branches must NOT share
        // a slot number -- slots only ever increase within a function.
        Diagnostics d = new Diagnostics();
        Resolver.Resolved r = resolve(
                "if (1) { let x = 1; } else { let x = 2; }", d);
        assertFalse(d.hadError());
        Stmt.If ifStmt = (Stmt.If) r.topLevelStatements().get(0);
        Stmt.Let thenLet = (Stmt.Let) ((Stmt.Block) ifStmt.thenBranch()).statements().get(0);
        Stmt.Let elseLet = (Stmt.Let) ((Stmt.Block) ifStmt.elseBranch()).statements().get(0);
        assertNotEquals(r.slots().get(thenLet), r.slots().get(elseLet));
    }

    @Test
    void innerBlockCanShadowAnOuterVariable() {
        Diagnostics d = new Diagnostics();
        resolve("let x = 1; { let x = 2; print x; } print x;", d);
        assertFalse(d.hadError());
    }

    @Test
    void duplicateDeclarationInSameScopeIsAnError() {
        Diagnostics d = new Diagnostics();
        resolve("let x = 1; let x = 2;", d);
        assertTrue(d.hadError());
    }

    @Test
    void useBeforeDeclarationIsAnError() {
        Diagnostics d = new Diagnostics();
        resolve("print x; let x = 1;", d);
        assertTrue(d.hadError());
    }

    @Test
    void undefinedVariableIsAnError() {
        Diagnostics d = new Diagnostics();
        resolve("print nope;", d);
        assertTrue(d.hadError());
    }

    @Test
    void assigningToUndefinedVariableIsAnError() {
        Diagnostics d = new Diagnostics();
        resolve("nope = 1;", d);
        assertTrue(d.hadError());
    }

    @Test
    void recursiveFunctionCallResolvesFine() {
        Diagnostics d = new Diagnostics();
        resolve("fn fact(n) { if (n == 0) { return 1; } return n * fact(n - 1); } print fact(6);", d);
        assertFalse(d.hadError());
    }

    @Test
    void forwardReferenceToALaterFunctionResolvesFine() {
        Diagnostics d = new Diagnostics();
        resolve("fn a() { return b(); } fn b() { return 1; } print a();", d);
        assertFalse(d.hadError());
    }

    @Test
    void callingAnUndeclaredFunctionIsAnError() {
        Diagnostics d = new Diagnostics();
        resolve("print nope();", d);
        assertTrue(d.hadError());
    }

    @Test
    void wrongArgumentCountIsAnError() {
        Diagnostics d = new Diagnostics();
        resolve("fn add(a, b) { return a + b; } print add(1);", d);
        assertTrue(d.hadError());
    }

    @Test
    void builtinConstantsResolveWithoutError() {
        Diagnostics d = new Diagnostics();
        resolve("pinMode(2, OUTPUT); digitalWrite(2, HIGH);", d);
        assertFalse(d.hadError());
    }

    @Test
    void builtinCallWrongArityIsAnError() {
        Diagnostics d = new Diagnostics();
        resolve("pinMode(2);", d);
        assertTrue(d.hadError());
    }

    @Test
    void returnOutsideFunctionIsAnError() {
        Diagnostics d = new Diagnostics();
        resolve("return 1;", d);
        assertTrue(d.hadError());
    }

    @Test
    void returnInsideFunctionIsFine() {
        Diagnostics d = new Diagnostics();
        resolve("fn f() { return 1; }", d);
        assertFalse(d.hadError());
    }

    @Test
    void duplicateFunctionNameIsAnError() {
        Diagnostics d = new Diagnostics();
        resolve("fn f() { return 1; } fn f() { return 2; }", d);
        assertTrue(d.hadError());
    }
}
