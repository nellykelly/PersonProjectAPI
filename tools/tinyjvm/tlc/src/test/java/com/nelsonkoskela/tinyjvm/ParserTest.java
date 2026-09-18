package com.nelsonkoskela.tinyjvm;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ParserTest {

    private List<Stmt> parse(String source, Diagnostics d) {
        List<Token> tokens = new Lexer(source, d).scanTokens();
        return new Parser(tokens, d).parseProgram();
    }

    @Test
    void parsesLetStatement() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("let x = 1 + 2;", d);
        assertFalse(d.hadError());
        Stmt.Let let = assertInstanceOf(Stmt.Let.class, program.get(0));
        assertEquals("x", let.name());
        Expr.Binary add = assertInstanceOf(Expr.Binary.class, let.initializer());
        assertEquals(TokenType.PLUS, add.operator());
    }

    @Test
    void parsesIfElse() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("if (x) { print 1; } else { print 2; }", d);
        assertFalse(d.hadError());
        Stmt.If ifStmt = assertInstanceOf(Stmt.If.class, program.get(0));
        assertInstanceOf(Expr.Variable.class, ifStmt.condition());
        assertInstanceOf(Stmt.Block.class, ifStmt.thenBranch());
        assertInstanceOf(Stmt.Block.class, ifStmt.elseBranch());
    }

    @Test
    void ifWithoutElseLeavesElseBranchNull() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("if (x) { print 1; }", d);
        Stmt.If ifStmt = assertInstanceOf(Stmt.If.class, program.get(0));
        assertNull(ifStmt.elseBranch());
    }

    @Test
    void parsesWhileLoop() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("while (i < 10) { i = i + 1; }", d);
        assertFalse(d.hadError());
        Stmt.While w = assertInstanceOf(Stmt.While.class, program.get(0));
        Expr.Binary cond = assertInstanceOf(Expr.Binary.class, w.condition());
        assertEquals(TokenType.LESS, cond.operator());
    }

    @Test
    void parsesFunctionDeclarationWithParams() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("fn add(a, b) { return a + b; }", d);
        assertFalse(d.hadError());
        Stmt.FuncDecl fn = assertInstanceOf(Stmt.FuncDecl.class, program.get(0));
        assertEquals("add", fn.name());
        assertEquals(List.of("a", "b"), fn.params());
        Stmt.Return ret = assertInstanceOf(Stmt.Return.class, fn.body().statements().get(0));
        assertInstanceOf(Expr.Binary.class, ret.value());
    }

    @Test
    void parsesFunctionCallAsExpression() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("print fact(6);", d);
        assertFalse(d.hadError());
        Stmt.Print p = assertInstanceOf(Stmt.Print.class, program.get(0));
        Expr.Call call = assertInstanceOf(Expr.Call.class, p.value());
        Expr.Variable callee = assertInstanceOf(Expr.Variable.class, call.callee());
        assertEquals("fact", callee.name());
        assertEquals(1, call.arguments().size());
    }

    @Test
    void returnWithNoValueLeavesValueNull() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("fn f() { return; }", d);
        Stmt.FuncDecl fn = assertInstanceOf(Stmt.FuncDecl.class, program.get(0));
        Stmt.Return ret = assertInstanceOf(Stmt.Return.class, fn.body().statements().get(0));
        assertNull(ret.value());
    }

    @Test
    void respectsArithmeticPrecedence() {
        // 1 + 2 * 3 must parse as 1 + (2 * 3), not (1 + 2) * 3.
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("let x = 1 + 2 * 3;", d);
        Stmt.Let let = assertInstanceOf(Stmt.Let.class, program.get(0));
        Expr.Binary top = assertInstanceOf(Expr.Binary.class, let.initializer());
        assertEquals(TokenType.PLUS, top.operator());
        assertInstanceOf(Expr.IntLiteral.class, top.left());
        Expr.Binary right = assertInstanceOf(Expr.Binary.class, top.right());
        assertEquals(TokenType.STAR, right.operator());
    }

    @Test
    void unaryBindsTighterThanBinary() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("let x = -a + b;", d);
        Stmt.Let let = assertInstanceOf(Stmt.Let.class, program.get(0));
        Expr.Binary add = assertInstanceOf(Expr.Binary.class, let.initializer());
        assertInstanceOf(Expr.Unary.class, add.left());
    }

    @Test
    void assignmentIsRightAssociative() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("a = b = 1;", d);
        Stmt.ExprStmt outer = assertInstanceOf(Stmt.ExprStmt.class, program.get(0));
        Expr.Assign assignA = assertInstanceOf(Expr.Assign.class, outer.expression());
        assertEquals("a", assignA.name());
        Expr.Assign assignB = assertInstanceOf(Expr.Assign.class, assignA.value());
        assertEquals("b", assignB.name());
    }

    @Test
    void invalidAssignmentTargetIsReportedNotThrown() {
        Diagnostics d = new Diagnostics();
        // 1 = 2 is not a valid assignment target -- must be reported as a
        // compile error, not crash the parser.
        parse("1 = 2;", d);
        assertTrue(d.hadError());
    }

    @Test
    void reportsMissingSemicolonAndKeepsParsingNextStatement() {
        Diagnostics d = new Diagnostics();
        List<Stmt> program = parse("let x = 1\nlet y = 2;", d);
        assertTrue(d.hadError());
        // Recovery should still find the second let statement.
        assertTrue(program.stream().anyMatch(
                s -> s instanceof Stmt.Let let && let.name().equals("y")));
    }

    @Test
    void reportsMultipleErrorsInOneRun() {
        Diagnostics d = new Diagnostics();
        parse("let = ; let = ;", d);
        assertTrue(d.errors().size() >= 2);
    }
}
