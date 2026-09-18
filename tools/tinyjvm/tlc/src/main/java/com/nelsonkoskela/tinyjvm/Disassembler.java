package com.nelsonkoskela.tinyjvm;

/** Human-readable listing of a .tvm file's code section -- offset,
 * opcode name, operand, and the decoded jump/call target (build spec
 * section 5.4) -- so the bytecode `tlc --dump` produces is inspectable
 * without needing the VM at all. */
public final class Disassembler {
    private Disassembler() {
    }

    public static String disassemble(byte[] tvmFile) {
        StringBuilder sb = new StringBuilder();
        int version = tvmFile[4] & 0xFF;
        int entry = readU16LE(tvmFile, 5);
        int codeLen = readU16LE(tvmFile, 7);
        sb.append(String.format("; version=%d entry=%d code_len=%d%n", version, entry, codeLen));

        int ip = 0;
        while (ip < codeLen) {
            int opcodePos = ip;
            int opcodeByte = tvmFile[9 + ip] & 0xFF;
            Opcode op = Opcode.fromCode(opcodeByte);
            ip++;

            String name = op != null ? op.name() : String.format("??(0x%02X)", opcodeByte);
            String operand = "";
            String target = "";

            if (op == Opcode.PUSH) {
                operand = String.valueOf(readI32LE(tvmFile, 9 + ip));
                ip += 4;
            } else if (op == Opcode.LOAD || op == Opcode.STORE) {
                operand = "#" + (tvmFile[9 + ip] & 0xFF);
                ip += 1;
            } else if (op == Opcode.JMP || op == Opcode.JZ || op == Opcode.JNZ) {
                int rel = readI16LE(tvmFile, 9 + ip);
                ip += 2;
                operand = String.valueOf(rel);
                target = " -> " + (ip + rel); // relative to the position right after this operand, matching vm.c's branch_to
            } else if (op == Opcode.CALL) {
                int addr = readU16LE(tvmFile, 9 + ip);
                ip += 2;
                operand = String.valueOf(addr);
                target = " -> " + addr;
            }

            sb.append(String.format("%4d: %-8s %s%s%n", opcodePos, name, operand, target));
        }
        return sb.toString();
    }

    private static int readU16LE(byte[] b, int pos) {
        return (b[pos] & 0xFF) | ((b[pos + 1] & 0xFF) << 8);
    }

    private static int readI16LE(byte[] b, int pos) {
        return (short) readU16LE(b, pos);
    }

    private static int readI32LE(byte[] b, int pos) {
        return (b[pos] & 0xFF) | ((b[pos + 1] & 0xFF) << 8)
                | ((b[pos + 2] & 0xFF) << 16) | ((b[pos + 3] & 0xFF) << 24);
    }
}
