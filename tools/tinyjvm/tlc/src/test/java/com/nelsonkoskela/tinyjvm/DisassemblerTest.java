package com.nelsonkoskela.tinyjvm;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DisassemblerTest {

    private byte[] compile(String source) {
        Diagnostics d = new Diagnostics();
        List<Token> tokens = new Lexer(source, d).scanTokens();
        List<Stmt> program = new Parser(tokens, d).parseProgram();
        Resolver.Resolved resolved = new Resolver(d).resolve(program);
        assertFalse(d.hadError());
        return new CodeGen(resolved, d).generate();
    }

    @Test
    void jumpTargetIsArithmeticNotStringConcatenation() {
        // Regression test: "-> " + ip + rel (no parens) silently degrades
        // into string concatenation in Java (leftmost operand is a
        // String, so + becomes concat left-to-right) instead of computing
        // ip + rel first -- e.g. ip=35, rel=34 must print "-> 69", not the
        // digit-concatenated "-> 3534".
        byte[] tvm = compile("let i = 0; while (i < 3) { i = i + 1; }");
        String dump = Disassembler.disassemble(tvm);
        assertFalse(dump.contains("3534"), "target arrow concatenated digits instead of adding: \n" + dump);
        // Every "-> N" target must be a real, small in-range address, not
        // some multi-digit concatenation artifact.
        for (String line : dump.split("\n")) {
            int arrow = line.indexOf("-> ");
            if (arrow == -1) continue;
            String targetStr = line.substring(arrow + 3).trim();
            int target = Integer.parseInt(targetStr);
            assertTrue(target >= 0 && target < 200, "implausible jump target: " + line);
        }
    }
}
