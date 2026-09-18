package com.nelsonkoskela.tinyjvm;

import java.util.Map;

/** Everything that's built into the language rather than declared by a
 * program (build spec section 4, design rule 4): the three GPIO calls
 * that compile to dedicated opcodes instead of CALL, and the small set
 * of named constants a GPIO program needs. Shared between Resolver (to
 * recognize these names instead of reporting "undefined") and CodeGen
 * (to emit the right opcode / inline the right literal).
 *
 * The constants aren't in the build spec's language design section, but
 * the "over-temp alarm" sample already live on the site
 * (app/blueprints/tiny_jvm/routes.py's SAMPLE_PROGRAMS) calls
 * `pinMode(2, OUTPUT)`, so OUTPUT has to resolve to something. Values
 * match the Arduino core's own convention (HIGH/OUTPUT = 1, LOW/INPUT =
 * 0), which is also what the real Wokwi/Arduino VMHost will be wrapping.
 */
public final class Builtins {
    private Builtins() {
    }

    /** name -> required argument count. */
    public static final Map<String, Integer> CALL_ARITY = Map.of(
            "pinMode", 2,
            "digitalWrite", 2,
            "digitalRead", 1);

    /** name -> compile-time constant value, inlined as a PUSH. */
    public static final Map<String, Integer> CONSTANTS = Map.of(
            "OUTPUT", 1,
            "INPUT", 0,
            "HIGH", 1,
            "LOW", 0);
}
