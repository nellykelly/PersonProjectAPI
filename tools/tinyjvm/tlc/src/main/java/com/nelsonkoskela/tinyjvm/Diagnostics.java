package com.nelsonkoskela.tinyjvm;

import java.util.ArrayList;
import java.util.List;

/** Collects compile errors across the lexer/parser/resolver so one run
 * can report several problems instead of stopping at the first. Never
 * throws -- callers check {@link #hadError()} once a phase is done and
 * decide whether to continue to the next one; Main.java exits 65
 * (EX_DATAERR) if anything was ever reported. */
public final class Diagnostics {
    public record Error(int line, String message) {
    }

    private final List<Error> errors = new ArrayList<>();

    public void error(int line, String message) {
        errors.add(new Error(line, message));
    }

    public boolean hadError() {
        return !errors.isEmpty();
    }

    public List<Error> errors() {
        return errors;
    }

    public void printAll() {
        for (Error e : errors) {
            System.err.println("[line " + e.line() + "] error: " + e.message());
        }
    }
}
