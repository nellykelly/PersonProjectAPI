package com.nelsonkoskela.tinyjvm;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Walks the resolved AST and emits the .tvm file (build spec sections
 * 5.4 and 6.3): a leading JMP over every function body to the top-level
 * code, each function laid out in turn, then the top-level statements,
 * then HALT.
 *
 * Calling convention (not specified by the opcode table, so fixed here
 * and documented -- the hand-assembled samples in tools/tinyjvm/vm/
 * samples/build_samples.py already committed to this same convention
 * before this compiler existed, precisely so the two would agree):
 * the caller pushes each argument in order, then CALLs; the callee's
 * first instructions STORE each argument off the shared value stack
 * into its own local slots, in *reverse* parameter order (last pushed =
 * top of stack = last parameter = stored first); the callee leaves
 * exactly one value on the shared stack before RET. CALL/RET never
 * touch the value stack themselves in vm.c, so this convention is
 * entirely a compiler-side agreement.
 */
public final class CodeGen {
    private record PendingCall(int operandPos, String functionName, int line) {
    }

    private final Resolver.Resolved resolved;
    private final Diagnostics diagnostics;
    private final Chunk chunk = new Chunk();
    private final Map<String, Integer> functionAddr = new HashMap<>();
    private final List<PendingCall> pendingCalls = new ArrayList<>();

    public CodeGen(Resolver.Resolved resolved, Diagnostics diagnostics) {
        this.resolved = resolved;
        this.diagnostics = diagnostics;
    }

    /** Returns the full .tvm file bytes, or null if code generation
     * itself found a problem (in practice this should never happen once
     * Resolver has already validated the program -- defensive only). */
    public byte[] generate() {
        chunk.writeByte(Opcode.JMP.code);
        int initialJumpOperand = chunk.reserveU16();

        for (Stmt.FuncDecl fn : resolved.functions()) {
            functionAddr.put(fn.name(), chunk.size());
            genFunctionBody(fn);
        }

        for (PendingCall pc : pendingCalls) {
            Integer addr = functionAddr.get(pc.functionName());
            if (addr == null) {
                // Resolver already validated every call target exists;
                // reaching this means CodeGen and Resolver have drifted
                // apart, not a user error.
                diagnostics.error(pc.line(), "internal error: unresolved function '" + pc.functionName() + "'");
                continue;
            }
            chunk.patchU16(pc.operandPos(), addr);
        }

        int topLevelAddr = chunk.size();
        patchJumpHere(initialJumpOperand, /* from */ initialJumpOperand + 2, topLevelAddr);

        for (Stmt s : resolved.topLevelStatements()) {
            genStmt(s);
        }
        chunk.writeByte(Opcode.HALT.code);

        if (diagnostics.hadError()) {
            return null;
        }
        return toTvmFile(topLevelAddr, chunk.toByteArray());
    }

    private static byte[] toTvmFile(int entry, byte[] code) {
        byte[] out = new byte[9 + code.length];
        out[0] = 'T';
        out[1] = 'V';
        out[2] = 'M';
        out[3] = 0;
        out[4] = 1; // version
        writeU16LE(out, 5, entry);
        writeU16LE(out, 7, code.length);
        System.arraycopy(code, 0, out, 9, code.length);
        return out;
    }

    private static void writeU16LE(byte[] arr, int pos, int value) {
        arr[pos] = (byte) (value & 0xFF);
        arr[pos + 1] = (byte) ((value >> 8) & 0xFF);
    }

    private void genFunctionBody(Stmt.FuncDecl fn) {
        // Prologue: pop each argument off the shared stack into its slot,
        // in reverse order -- see the calling-convention note above.
        // Parameters occupy slots 0..N-1 in declaration order, which is
        // Resolver's own invariant (resolveFunction declares them first,
        // in order, before anything else in the function).
        List<String> params = fn.params();
        for (int i = params.size() - 1; i >= 0; i--) {
            chunk.writeByte(Opcode.STORE.code);
            chunk.writeU8(i);
        }
        for (Stmt s : fn.body().statements()) {
            genStmt(s);
        }
        // Fall-through safety net: TL doesn't check "all paths return",
        // so a function whose control flow runs off the end of its body
        // still needs to leave exactly one value and hit RET, matching
        // the calling convention, rather than falling into the next
        // function's bytecode. A well-defined 0 beats corrupting the
        // caller's stack.
        chunk.writeByte(Opcode.PUSH.code);
        chunk.writeI32(0);
        chunk.writeByte(Opcode.RET.code);
    }

    private void genStmt(Stmt stmt) {
        switch (stmt) {
            case Stmt.Let let -> {
                genExpr(let.initializer());
                chunk.writeByte(Opcode.STORE.code);
                chunk.writeU8(resolved.slots().get(let));
            }
            case Stmt.ExprStmt e -> {
                genExpr(e.expression());
                if (leavesAValueOnStack(e.expression())) {
                    chunk.writeByte(Opcode.POP.code);
                }
            }
            case Stmt.Print p -> {
                genExpr(p.value());
                chunk.writeByte(Opcode.PRINT.code);
            }
            case Stmt.Block block -> {
                for (Stmt s : block.statements()) {
                    genStmt(s);
                }
            }
            case Stmt.If ifStmt -> genIf(ifStmt);
            case Stmt.While w -> genWhile(w);
            case Stmt.Return r -> {
                if (r.value() != null) {
                    genExpr(r.value());
                } else {
                    chunk.writeByte(Opcode.PUSH.code);
                    chunk.writeI32(0);
                }
                chunk.writeByte(Opcode.RET.code);
            }
            case Stmt.FuncDecl ignored -> {
                // Grammar never nests one here (block = "{" {statement} "}",
                // not {declaration}); Parser enforces this. Nothing to do.
            }
        }
    }

    /** PINMODE and DWRITE consume their arguments and push nothing back
     * (see vm.c) -- every other expression, including DREAD and every
     * user function call (which always leaves a return value per the
     * calling convention, even a defaulted 0), leaves exactly one value.
     * An expression statement must only emit a discarding POP when
     * there's actually something to discard, or a bare
     * `pinMode(2, OUTPUT);` -- exactly how the thermostat sample already
     * on the site calls it -- would underflow the stack at runtime. */
    private boolean leavesAValueOnStack(Expr expr) {
        if (expr instanceof Expr.Call call && call.callee() instanceof Expr.Variable v) {
            return !(v.name().equals("pinMode") || v.name().equals("digitalWrite"));
        }
        return true;
    }

    private void genIf(Stmt.If ifStmt) {
        genExpr(ifStmt.condition());
        chunk.writeByte(Opcode.JZ.code);
        int jzOperand = chunk.reserveU16();
        genStmt(ifStmt.thenBranch());
        if (ifStmt.elseBranch() != null) {
            chunk.writeByte(Opcode.JMP.code);
            int jmpOperand = chunk.reserveU16();
            patchJumpHere(jzOperand);
            genStmt(ifStmt.elseBranch());
            patchJumpHere(jmpOperand);
        } else {
            patchJumpHere(jzOperand);
        }
    }

    private void genWhile(Stmt.While w) {
        int loopStart = chunk.size();
        genExpr(w.condition());
        chunk.writeByte(Opcode.JZ.code);
        int exitOperand = chunk.reserveU16();
        genStmt(w.body());
        emitJumpTo(Opcode.JMP.code, loopStart);
        patchJumpHere(exitOperand);
    }

    /** Patches a JMP/JZ/JNZ operand to branch to the *current* chunk
     * position -- the common case (branch to "right after this
     * construct"). Offset is relative to the position right after the
     * 2-byte operand, matching vm.c's branch_to exactly. */
    private void patchJumpHere(int operandPos) {
        int offset = chunk.size() - (operandPos + 2);
        chunk.patchU16(operandPos, offset & 0xFFFF);
    }

    /** Same as {@link #patchJumpHere(int)} but for the one-off case
     * (the leading JMP-over-functions) where "here" needs to be computed
     * explicitly rather than read from the chunk's current size. */
    private void patchJumpHere(int operandPos, int operandEndPos, int targetAddr) {
        int offset = targetAddr - operandEndPos;
        chunk.patchU16(operandPos, offset & 0xFFFF);
    }

    /** Emits a full JMP/JZ/JNZ instruction with a known target address up
     * front (the while-loop back-edge, whose target -- loopStart -- is
     * already behind us when we emit it, unlike a forward branch). */
    private void emitJumpTo(int opcode, int targetAddr) {
        chunk.writeByte(opcode);
        int operandPos = chunk.reserveU16();
        int offset = targetAddr - (operandPos + 2);
        chunk.patchU16(operandPos, offset & 0xFFFF);
    }

    private void genExpr(Expr expr) {
        switch (expr) {
            case Expr.IntLiteral lit -> {
                chunk.writeByte(Opcode.PUSH.code);
                chunk.writeI32(lit.value());
            }
            case Expr.BoolLiteral lit -> {
                chunk.writeByte(Opcode.PUSH.code);
                chunk.writeI32(lit.value() ? 1 : 0);
            }
            case Expr.Variable v -> {
                Integer constant = Builtins.CONSTANTS.get(v.name());
                if (constant != null) {
                    chunk.writeByte(Opcode.PUSH.code);
                    chunk.writeI32(constant);
                } else {
                    chunk.writeByte(Opcode.LOAD.code);
                    chunk.writeU8(resolved.slots().get(v));
                }
            }
            case Expr.Assign a -> {
                genExpr(a.value());
                chunk.writeByte(Opcode.DUP.code); // leave a copy: assignment is an expression
                chunk.writeByte(Opcode.STORE.code);
                chunk.writeU8(resolved.slots().get(a));
            }
            case Expr.Unary u -> genUnary(u);
            case Expr.Binary b -> genBinary(b);
            case Expr.Call call -> genCall(call);
        }
    }

    private void genUnary(Expr.Unary u) {
        switch (u.operator()) {
            case MINUS -> {
                chunk.writeByte(Opcode.PUSH.code);
                chunk.writeI32(0);
                genExpr(u.operand());
                chunk.writeByte(Opcode.SUB.code); // 0 - operand
            }
            case BANG -> {
                genExpr(u.operand());
                genNotOfTopOfStack();
            }
            default -> throw new IllegalStateException("not a unary operator: " + u.operator());
        }
    }

    /** Consumes the top of stack and pushes 1 if it was exactly 0, else
     * 0 -- logical NOT, matching design rule 5 (non-zero is true). Used
     * directly for `!`, and to synthesize <=/>= from LT (see genBinary). */
    private void genNotOfTopOfStack() {
        chunk.writeByte(Opcode.JZ.code);
        int toOneOperand = chunk.reserveU16();
        chunk.writeByte(Opcode.PUSH.code);
        chunk.writeI32(0);
        chunk.writeByte(Opcode.JMP.code);
        int toEndOperand = chunk.reserveU16();
        patchJumpHere(toOneOperand);
        chunk.writeByte(Opcode.PUSH.code);
        chunk.writeI32(1);
        patchJumpHere(toEndOperand);
    }

    /** The mirror image of {@link #genNotOfTopOfStack()}: pushes 1 if the
     * consumed value was non-zero, else 0. Used for != (SUB then "was
     * the difference non-zero"). */
    private void genTruthOfTopOfStack() {
        chunk.writeByte(Opcode.JNZ.code);
        int toOneOperand = chunk.reserveU16();
        chunk.writeByte(Opcode.PUSH.code);
        chunk.writeI32(0);
        chunk.writeByte(Opcode.JMP.code);
        int toEndOperand = chunk.reserveU16();
        patchJumpHere(toOneOperand);
        chunk.writeByte(Opcode.PUSH.code);
        chunk.writeI32(1);
        patchJumpHere(toEndOperand);
    }

    /** Comparisons deliberately have no dedicated opcodes beyond LT
     * (build spec section 6.2): == and != synthesize from SUB plus a
     * zero test; <=, >, >= all synthesize from LT (a<b) plus swapping
     * operands and/or negating, since a>b is b<a, a<=b is !(b<a), and
     * a>=b is !(a<b). */
    private void genBinary(Expr.Binary b) {
        switch (b.operator()) {
            case PLUS -> {
                genExpr(b.left());
                genExpr(b.right());
                chunk.writeByte(Opcode.ADD.code);
            }
            case MINUS -> {
                genExpr(b.left());
                genExpr(b.right());
                chunk.writeByte(Opcode.SUB.code);
            }
            case STAR -> {
                genExpr(b.left());
                genExpr(b.right());
                chunk.writeByte(Opcode.MUL.code);
            }
            case SLASH -> {
                genExpr(b.left());
                genExpr(b.right());
                chunk.writeByte(Opcode.DIV.code);
            }
            case EQUAL_EQUAL -> {
                genExpr(b.left());
                genExpr(b.right());
                chunk.writeByte(Opcode.SUB.code);
                genNotOfTopOfStack();
            }
            case BANG_EQUAL -> {
                genExpr(b.left());
                genExpr(b.right());
                chunk.writeByte(Opcode.SUB.code);
                genTruthOfTopOfStack();
            }
            case LESS -> {
                genExpr(b.left());
                genExpr(b.right());
                chunk.writeByte(Opcode.LT.code);
            }
            case GREATER -> {
                genExpr(b.right());
                genExpr(b.left());
                chunk.writeByte(Opcode.LT.code); // a>b <=> b<a
            }
            case LESS_EQUAL -> {
                genExpr(b.right());
                genExpr(b.left());
                chunk.writeByte(Opcode.LT.code); // b<a ...
                genNotOfTopOfStack();            // ... negated is a<=b
            }
            case GREATER_EQUAL -> {
                genExpr(b.left());
                genExpr(b.right());
                chunk.writeByte(Opcode.LT.code); // a<b ...
                genNotOfTopOfStack();            // ... negated is a>=b
            }
            default -> throw new IllegalStateException("not a binary operator: " + b.operator());
        }
    }

    private void genCall(Expr.Call call) {
        // Resolver already required this cast to succeed (rejects any
        // call whose callee isn't a bare name) before CodeGen ever runs.
        String name = ((Expr.Variable) call.callee()).name();
        List<Expr> args = call.arguments();

        if (Builtins.CALL_ARITY.containsKey(name)) {
            for (Expr arg : args) {
                genExpr(arg);
            }
            Opcode op = switch (name) {
                case "pinMode" -> Opcode.PINMODE;
                case "digitalWrite" -> Opcode.DWRITE;
                case "digitalRead" -> Opcode.DREAD;
                default -> throw new IllegalStateException("unknown builtin: " + name);
            };
            chunk.writeByte(op.code);
            return;
        }

        for (Expr arg : args) {
            genExpr(arg);
        }
        chunk.writeByte(Opcode.CALL.code);
        int operandPos = chunk.reserveU16();
        Integer addr = functionAddr.get(name);
        if (addr != null) {
            chunk.patchU16(operandPos, addr);
        } else {
            pendingCalls.add(new PendingCall(operandPos, name, call.line()));
        }
    }
}
