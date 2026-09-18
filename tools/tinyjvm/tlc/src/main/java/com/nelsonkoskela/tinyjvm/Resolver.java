package com.nelsonkoskela.tinyjvm;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;

/** The pass between parse and codegen (build spec section 5.3): assigns
 * every local variable and parameter a slot index (0-255) within its
 * function, rejects use-before-declaration and duplicate declarations in
 * the same scope, and validates every call resolves to a known function
 * or built-in with the right argument count.
 *
 * Slots are never reused across sibling blocks in the same function --
 * once assigned, a slot number is never handed out again, even after
 * that block's scope closes. Simpler and safer than reclaiming slots,
 * and 256 per function is nowhere close to a real constraint for
 * programs this size.
 *
 * Produces a side table ({@link Resolved#slots}) rather than mutating
 * the AST, since Expr/Stmt are immutable records -- CodeGen looks up
 * each Stmt.Let / Expr.Variable / Expr.Assign node's slot by identity.
 */
public final class Resolver {
    public record Resolved(
            List<Stmt.FuncDecl> functions,
            List<Stmt> topLevelStatements,
            Map<Object, Integer> slots) {
    }

    private final Diagnostics diagnostics;
    private final Map<String, Integer> functionArity = new HashMap<>();
    private final IdentityHashMap<Object, Integer> slots = new IdentityHashMap<>();

    private final List<Map<String, Integer>> scopes = new ArrayList<>();
    private int nextSlot;
    private boolean inFunction;

    public Resolver(Diagnostics diagnostics) {
        this.diagnostics = diagnostics;
    }

    public Resolved resolve(List<Stmt> program) {
        List<Stmt.FuncDecl> functions = new ArrayList<>();
        List<Stmt> topLevel = new ArrayList<>();
        for (Stmt s : program) {
            if (s instanceof Stmt.FuncDecl fn) {
                functions.add(fn);
            } else {
                topLevel.add(s);
            }
        }

        // Pass 1: register every function's name + arity before resolving
        // any body, so forward references and recursion both work.
        for (Stmt.FuncDecl fn : functions) {
            if (functionArity.containsKey(fn.name())) {
                diagnostics.error(fn.line(), "duplicate function '" + fn.name() + "'");
                continue;
            }
            functionArity.put(fn.name(), fn.params().size());
        }

        // Pass 2: resolve each function body in its own fresh slot space,
        // then the top-level statements in the implicit top-level scope
        // (frame 0 in the VM -- see vm.h).
        for (Stmt.FuncDecl fn : functions) {
            resolveFunction(fn);
        }
        beginFunctionScope();
        for (Stmt s : topLevel) {
            resolveStmt(s);
        }
        endFunctionScope();

        return new Resolved(functions, topLevel, slots);
    }

    private void resolveFunction(Stmt.FuncDecl fn) {
        beginFunctionScope();
        for (String param : fn.params()) {
            declare(fn.line(), param);
        }
        inFunction = true;
        for (Stmt s : fn.body().statements()) {
            resolveStmt(s);
        }
        inFunction = false;
        endFunctionScope();
    }

    private void beginFunctionScope() {
        scopes.clear();
        scopes.add(new HashMap<>());
        nextSlot = 0;
    }

    private void endFunctionScope() {
        scopes.clear();
    }

    private void beginBlockScope() {
        scopes.add(new HashMap<>());
    }

    private void endBlockScope() {
        scopes.remove(scopes.size() - 1);
    }

    /** Declares `name` in the innermost scope, assigning it the next free
     * slot in the current function. Reports (but doesn't throw on) a
     * duplicate declaration in that same scope -- resolution continues
     * with the new slot anyway, so later uses still get a slot number
     * instead of cascading into "undefined variable" errors too. */
    private int declare(int line, String name) {
        Map<String, Integer> innermost = scopes.get(scopes.size() - 1);
        if (innermost.containsKey(name)) {
            diagnostics.error(line, "'" + name + "' is already declared in this scope");
        }
        int slot = nextSlot++;
        innermost.put(name, slot);
        return slot;
    }

    /** Looks up `name` from the innermost scope outward, within the
     * current function only -- there are no closures, so an outer
     * function's locals are never visible. */
    private Integer lookup(String name) {
        for (int i = scopes.size() - 1; i >= 0; i--) {
            Integer slot = scopes.get(i).get(name);
            if (slot != null) {
                return slot;
            }
        }
        return null;
    }

    private void resolveStmt(Stmt stmt) {
        switch (stmt) {
            case Stmt.Let let -> {
                resolveExpr(let.initializer());
                int slot = declare(let.line(), let.name());
                slots.put(let, slot);
            }
            case Stmt.If ifStmt -> {
                resolveExpr(ifStmt.condition());
                resolveStmt(ifStmt.thenBranch());
                if (ifStmt.elseBranch() != null) {
                    resolveStmt(ifStmt.elseBranch());
                }
            }
            case Stmt.While w -> {
                resolveExpr(w.condition());
                resolveStmt(w.body());
            }
            case Stmt.Return r -> {
                if (!inFunction) {
                    diagnostics.error(r.line(), "'return' outside a function");
                }
                if (r.value() != null) {
                    resolveExpr(r.value());
                }
            }
            case Stmt.Print p -> resolveExpr(p.value());
            case Stmt.Block block -> {
                beginBlockScope();
                for (Stmt s : block.statements()) {
                    resolveStmt(s);
                }
                endBlockScope();
            }
            case Stmt.ExprStmt e -> resolveExpr(e.expression());
            case Stmt.FuncDecl ignored -> {
                // Grammar only allows funcDecl at the top level (block =
                // "{" { statement } "}", not { declaration }); Parser
                // never nests one here. Nothing to do.
            }
        }
    }

    private void resolveExpr(Expr expr) {
        switch (expr) {
            case Expr.IntLiteral ignored -> {
            }
            case Expr.BoolLiteral ignored -> {
            }
            case Expr.Variable v -> {
                Integer slot = lookup(v.name());
                if (slot != null) {
                    slots.put(v, slot);
                } else if (!Builtins.CONSTANTS.containsKey(v.name())
                        && !functionArity.containsKey(v.name())) {
                    diagnostics.error(v.line(), "undefined variable '" + v.name() + "'");
                } else if (functionArity.containsKey(v.name())) {
                    diagnostics.error(v.line(),
                            "'" + v.name() + "' is a function -- call it with (...)");
                }
                // else: a recognized builtin constant, nothing to resolve.
            }
            case Expr.Assign a -> {
                resolveExpr(a.value());
                Integer slot = lookup(a.name());
                if (slot != null) {
                    slots.put(a, slot);
                } else {
                    diagnostics.error(a.line(), "undefined variable '" + a.name() + "'");
                }
            }
            case Expr.Unary u -> resolveExpr(u.operand());
            case Expr.Binary b -> {
                resolveExpr(b.left());
                resolveExpr(b.right());
            }
            case Expr.Call call -> resolveCall(call);
        }
    }

    private void resolveCall(Expr.Call call) {
        for (Expr arg : call.arguments()) {
            resolveExpr(arg);
        }
        if (!(call.callee() instanceof Expr.Variable calleeVar)) {
            diagnostics.error(call.line(), "can only call a function by name");
            return;
        }
        String name = calleeVar.name();
        Integer builtinArity = Builtins.CALL_ARITY.get(name);
        if (builtinArity != null) {
            if (call.arguments().size() != builtinArity) {
                diagnostics.error(call.line(),
                        name + "() takes " + builtinArity + " argument(s), got " + call.arguments().size());
            }
            return;
        }
        Integer arity = functionArity.get(name);
        if (arity == null) {
            diagnostics.error(call.line(), "undefined function '" + name + "'");
            return;
        }
        if (call.arguments().size() != arity) {
            diagnostics.error(call.line(),
                    name + "() takes " + arity + " argument(s), got " + call.arguments().size());
        }
    }
}
