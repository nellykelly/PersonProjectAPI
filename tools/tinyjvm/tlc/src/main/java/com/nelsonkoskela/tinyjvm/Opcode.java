package com.nelsonkoskela.tinyjvm;

import java.util.HashMap;
import java.util.Map;

/** Mirrors tools/tinyjvm/vm/vm.h's VMOpcode enum exactly -- see
 * docs/build-spec-tiny-jvm.md section 6.2 and app/blueprints/tiny_jvm/
 * routes.py's OPCODES table, the three places this instruction set is
 * kept in lockstep. One source of truth on the Java side, shared by
 * CodeGen (which emits these) and Disassembler (which reads them back
 * for --dump). LT (0x14) was added during this implementation -- see
 * CodeGen's comparison codegen for why SUB + a zero test alone can't
 * synthesize a sign test, only equality. */
public enum Opcode {
    PUSH(0x01), POP(0x02), DUP(0x03),
    ADD(0x10), SUB(0x11), MUL(0x12), DIV(0x13), LT(0x14),
    LOAD(0x20), STORE(0x21),
    JMP(0x30), JZ(0x31), JNZ(0x32),
    CALL(0x40), RET(0x41),
    PRINT(0x50), PINMODE(0x51), DWRITE(0x52), DREAD(0x53),
    HALT(0xFF);

    public final int code;

    Opcode(int code) {
        this.code = code;
    }

    private static final Map<Integer, Opcode> BY_CODE = new HashMap<>();

    static {
        for (Opcode op : values()) {
            BY_CODE.put(op.code, op);
        }
    }

    public static Opcode fromCode(int code) {
        return BY_CODE.get(code);
    }
}
