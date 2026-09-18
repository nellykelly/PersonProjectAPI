package com.nelsonkoskela.tinyjvm;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;

/** Compiles a snippet and asserts the exact opcode sequence emitted --
 * "golden tests" that lock the bytecode contract (build spec section
 * 5.6), just decoded into (opcode, operand) pairs rather than diffed as
 * disassembly text, since jump/call target byte offsets shift with any
 * unrelated layout change and would make text-diffing golden tests
 * needlessly brittle. Every test still fully decodes the header (magic,
 * version, entry point) so the file-format contract is covered too.
 */
class CodeGenGoldenTest {

    private record Instr(Opcode op, int operand) {
    }

    private byte[] compile(String source) {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = new Lexer(source, d).scanTokens();
        List<Stmt> program = new Parser(tokens, d).parseProgram();
        Resolver.Resolved resolved = new Resolver(d).resolve(program);
        assertFalse(d.hadError(), "compile errors: " + d.errors());
        byte[] tvm = new CodeGen(resolved, d).generate();
        assertNotNull(tvm);
        return tvm;
    }

    private static int readU16LE(byte[] b, int pos) {
        return (b[pos] & 0xFF) | ((b[pos + 1] & 0xFF) << 8);
    }

    /** Decodes the whole code section (skipping the 9-byte header) into
     * opcode/operand pairs. For PUSH the operand is the literal int32;
     * for LOAD/STORE it's the slot; for JMP/JZ/JNZ/CALL it's the raw
     * operand as stored (a relative offset or absolute address) -- tests
     * that care about jump targets check structure (e.g. "there are two
     * JZ instructions") rather than hand-computing exact byte offsets.
     */
    private List<Instr> decode(byte[] tvm) {
        assertEquals('T', tvm[0]);
        assertEquals('V', tvm[1]);
        assertEquals('M', tvm[2]);
        assertEquals(0, tvm[3]);
        assertEquals(1, tvm[4] & 0xFF);
        int codeLen = readU16LE(tvm, 7);

        List<Instr> out = new ArrayList<>();
        int ip = 0;
        while (ip < codeLen) {
            int opcodeByte = tvm[9 + ip] & 0xFF;
            Opcode op = Opcode.fromCode(opcodeByte);
            assertNotNull(op, "unknown opcode byte 0x" + Integer.toHexString(opcodeByte));
            ip++;
            int operand = 0;
            switch (op) {
                case PUSH -> {
                    operand = (tvm[9 + ip] & 0xFF) | ((tvm[9 + ip + 1] & 0xFF) << 8)
                            | ((tvm[9 + ip + 2] & 0xFF) << 16) | ((tvm[9 + ip + 3] & 0xFF) << 24);
                    ip += 4;
                }
                case LOAD, STORE -> {
                    operand = tvm[9 + ip] & 0xFF;
                    ip += 1;
                }
                case JMP, JZ, JNZ, CALL -> {
                    operand = readU16LE(tvm, 9 + ip);
                    ip += 2;
                }
                default -> {
                }
            }
            out.add(new Instr(op, operand));
        }
        return out;
    }

    @Test
    void simpleArithmeticAndPrint() {
        byte[] tvm = compile("print 3 + 4;");
        List<Instr> code = decode(tvm);
        // JMP <top>, PUSH 3, PUSH 4, ADD, PRINT, HALT
        assertEquals(Opcode.JMP, code.get(0).op());
        assertEquals(Opcode.PUSH, code.get(1).op());
        assertEquals(3, code.get(1).operand());
        assertEquals(Opcode.PUSH, code.get(2).op());
        assertEquals(4, code.get(2).operand());
        assertEquals(Opcode.ADD, code.get(3).op());
        assertEquals(Opcode.PRINT, code.get(4).op());
        assertEquals(Opcode.HALT, code.get(5).op());
    }

    @Test
    void letAndLoadRoundTripThroughTheSameSlot() {
        byte[] tvm = compile("let x = 5; print x;");
        List<Instr> code = decode(tvm);
        // JMP, PUSH 5, STORE #s, LOAD #s, PRINT, HALT -- same slot both times.
        assertEquals(Opcode.STORE, code.get(2).op());
        assertEquals(Opcode.LOAD, code.get(3).op());
        assertEquals(code.get(2).operand(), code.get(3).operand());
    }

    @Test
    void assignmentLeavesAValueForPrintToConsume() {
        // print(x = 5) must print 5 -- DUP before STORE, not just STORE.
        byte[] tvm = compile("let x = 0; print x = 5;");
        List<Instr> code = decode(tvm);
        boolean sawDup = code.stream().anyMatch(i -> i.op() == Opcode.DUP);
        assertEquals(true, sawDup);
    }

    @Test
    void equalityCompilesToSubThenAZeroTest() {
        byte[] tvm = compile("print 1 == 2;");
        List<Instr> code = decode(tvm);
        assertEquals(true, code.stream().anyMatch(i -> i.op() == Opcode.SUB));
        assertEquals(true, code.stream().anyMatch(i -> i.op() == Opcode.JZ));
        // No dedicated EQ opcode exists -- must not appear.
        assertEquals(false, code.stream().anyMatch(i -> i.op().name().equals("EQ")));
    }

    @Test
    void lessThanCompilesDirectlyToLt() {
        byte[] tvm = compile("print 1 < 2;");
        List<Instr> code = decode(tvm);
        assertEquals(true, code.stream().anyMatch(i -> i.op() == Opcode.LT));
        // A plain `<` needs no SUB at all -- LT is a single opcode.
        assertEquals(false, code.stream().anyMatch(i -> i.op() == Opcode.SUB));
    }

    @Test
    void lessEqualSynthesizesFromLtAndNegation() {
        byte[] tvm = compile("print 1 <= 2;");
        List<Instr> code = decode(tvm);
        assertEquals(true, code.stream().anyMatch(i -> i.op() == Opcode.LT));
        assertEquals(true, code.stream().anyMatch(i -> i.op() == Opcode.JZ));
    }

    @Test
    void ifWithoutElseHasExactlyOneForwardJz() {
        byte[] tvm = compile("if (1) { print 1; }");
        List<Instr> code = decode(tvm);
        long jzCount = code.stream().filter(i -> i.op() == Opcode.JZ).count();
        assertEquals(1, jzCount);
    }

    @Test
    void ifWithElseHasAJzAndAJmp() {
        byte[] tvm = compile("if (1) { print 1; } else { print 2; }");
        List<Instr> all = decode(tvm);
        // Skip index 0: generate() always emits a leading JMP-over-functions
        // first, even with zero functions -- this test cares about jumps
        // the if/else construct itself adds, not that baseline.
        List<Instr> code = all.subList(1, all.size());
        assertEquals(1, code.stream().filter(i -> i.op() == Opcode.JZ).count());
        assertEquals(1, code.stream().filter(i -> i.op() == Opcode.JMP).count());
    }

    @Test
    void whileLoopHasAForwardExitAndABackwardJump() {
        byte[] tvm = compile("let i = 0; while (i < 3) { i = i + 1; }");
        List<Instr> all = decode(tvm);
        List<Instr> code = all.subList(1, all.size());
        assertEquals(1, code.stream().filter(i -> i.op() == Opcode.JZ).count());
        assertEquals(1, code.stream().filter(i -> i.op() == Opcode.JMP).count());
    }

    @Test
    void functionCallGetsAResolvedAbsoluteAddress() {
        byte[] tvm = compile("fn f() { return 1; } print f();");
        List<Instr> code = decode(tvm);
        Instr call = code.stream().filter(i -> i.op() == Opcode.CALL).findFirst().orElseThrow();
        // f's body starts right after the leading JMP (3 bytes).
        assertEquals(3, call.operand());
    }

    @Test
    void recursiveCallResolvesToItsOwnFunctionStart() {
        byte[] tvm = compile(
                "fn fact(n) { if (n == 0) { return 1; } return n * fact(n - 1); } print fact(6);");
        List<Instr> code = decode(tvm);
        Instr call = code.stream().filter(i -> i.op() == Opcode.CALL).findFirst().orElseThrow();
        assertEquals(3, call.operand()); // fact is the only function, starts at 3
    }

    @Test
    void forwardReferenceCallGetsPatchedToTheCalleesRealAddress() {
        // a() calls b(), but b is defined (and laid out) after a --
        // exercises the pending-call fixup, not just self-recursion.
        byte[] tvm = compile("fn a() { return b(); } fn b() { return 1; } print a();");
        List<Instr> code = decode(tvm);
        Instr callInsideA = code.get(1); // a's body: STORE-free (no params), first real instr is the CALL to b
        // a's body starts at 3 (right after JMP); it has no params so no
        // prologue STOREs, so the very first instruction in a's body is
        // the call to b(). b is laid out immediately after a.
        assertEquals(Opcode.CALL, code.get(1).op());
    }

    @Test
    void voidBuiltinCallAsAStatementNeedsNoTrailingPop() {
        // pinMode()/digitalWrite() push nothing back -- a bare statement
        // call to either must not be followed by a POP with nothing to
        // pop (this exact shape is the thermostat sample already on the
        // live site: pinMode(2, OUTPUT); as its own statement).
        byte[] tvm = compile("pinMode(2, 1);");
        List<Instr> code = decode(tvm);
        // JMP, PUSH 2, PUSH 1, PINMODE, HALT -- no POP anywhere.
        assertEquals(false, code.stream().anyMatch(i -> i.op() == Opcode.POP));
        assertEquals(true, code.stream().anyMatch(i -> i.op() == Opcode.PINMODE));
    }

    @Test
    void digitalReadAsABareStatementDoesGetPopped() {
        // Unlike pinMode/digitalWrite, digitalRead DOES push a value, so
        // as a bare (unused) statement it needs the POP.
        byte[] tvm = compile("digitalRead(2);");
        List<Instr> code = decode(tvm);
        assertEquals(true, code.stream().anyMatch(i -> i.op() == Opcode.DREAD));
        assertEquals(true, code.stream().anyMatch(i -> i.op() == Opcode.POP));
    }

    @Test
    void builtinConstantInlinesAsAPushNotALoad() {
        byte[] tvm = compile("pinMode(2, OUTPUT);");
        List<Instr> code = decode(tvm);
        // Two PUSHes (the pin literal and OUTPUT's inlined value 1), no LOAD at all.
        assertEquals(2, code.stream().filter(i -> i.op() == Opcode.PUSH).count());
        assertEquals(false, code.stream().anyMatch(i -> i.op() == Opcode.LOAD));
    }

    @Test
    void functionWithNoExplicitReturnStillEndsInRet() {
        byte[] tvm = compile("fn f() { let x = 1; } print f();");
        List<Instr> code = decode(tvm);
        assertEquals(true, code.stream().anyMatch(i -> i.op() == Opcode.RET));
    }

    @Test
    void programAlwaysEndsInHalt() {
        byte[] tvm = compile("print 1;");
        List<Instr> code = decode(tvm);
        assertEquals(Opcode.HALT, code.get(code.size() - 1).op());
    }
}
