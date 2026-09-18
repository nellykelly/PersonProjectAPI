package com.nelsonkoskela.tinyjvm;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** The `tlc` CLI (build spec section 5.5):
 * {@code tlc program.tl -o program.tvm}   compile
 * {@code tlc program.tl --dump}           human-readable disassembly to stdout
 * (both flags may be given together). Hand-parsed -- three flags is not
 * worth a dependency like picocli. Exit codes follow sysexits.h: 0
 * success, 64 bad usage, 65 compile error (EX_DATAERR), 66 can't read
 * the input file (EX_NOINPUT), 74 can't write the output file
 * (EX_IOERR). */
public final class Main {
    public static void main(String[] args) {
        String sourcePath = null;
        String outputPath = null;
        boolean dump = false;

        int i = 0;
        while (i < args.length) {
            String arg = args[i];
            switch (arg) {
                case "-o" -> {
                    if (i + 1 >= args.length) {
                        usageError("-o requires a filename");
                    }
                    outputPath = args[++i];
                }
                case "--dump" -> dump = true;
                default -> {
                    if (sourcePath != null) {
                        usageError("unexpected argument: " + arg);
                    }
                    sourcePath = arg;
                }
            }
            i++;
        }

        if (sourcePath == null) {
            usageError("usage: tlc <program.tl> [-o out.tvm] [--dump]");
        }
        if (outputPath == null && !dump) {
            usageError("specify -o <file>, --dump, or both");
        }

        String source;
        try {
            source = Files.readString(Path.of(sourcePath));
        } catch (IOException e) {
            System.err.println("error: could not read " + sourcePath + ": " + e.getMessage());
            System.exit(66);
            return;
        }

        Diagnostics diagnostics = new Diagnostics();

        List<Token> tokens = new Lexer(source, diagnostics).scanTokens();
        List<Stmt> program = new Parser(tokens, diagnostics).parseProgram();
        exitIfErrors(diagnostics);

        Resolver.Resolved resolved = new Resolver(diagnostics).resolve(program);
        exitIfErrors(diagnostics);

        byte[] tvm = new CodeGen(resolved, diagnostics).generate();
        exitIfErrors(diagnostics);
        if (tvm == null) {
            System.err.println("error: code generation failed with no diagnostics (internal error)");
            System.exit(70); // EX_SOFTWARE
            return;
        }

        if (dump) {
            System.out.print(Disassembler.disassemble(tvm));
        }
        if (outputPath != null) {
            try {
                Files.write(Path.of(outputPath), tvm);
            } catch (IOException e) {
                System.err.println("error: could not write " + outputPath + ": " + e.getMessage());
                System.exit(74);
            }
        }
    }

    private static void exitIfErrors(Diagnostics diagnostics) {
        if (diagnostics.hadError()) {
            diagnostics.printAll();
            System.exit(65);
        }
    }

    private static void usageError(String message) {
        System.err.println(message);
        System.exit(64);
    }
}
