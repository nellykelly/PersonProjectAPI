---
title: "Project: Timed-Squares"
kind: project
---

# What it is

A turn-based survival game at /projects/timed-squares, playable in the browser on an
HTML5 Canvas with no install. It is a recreation of an earlier game Nelson built in
JavaScript/Processing and Python/Pygame, rebuilt here in vanilla JavaScript embedded in
a Flask template. There is a public leaderboard and no login.

# Core rule

Strictly turn-based, not real-time. The player moves exactly one cell per key press
(arrow keys or WASD). The instant that move resolves, every active obstacle takes its own
one-step move, then it is the player's turn again.

Collision is decided on where everything **ends** the turn, not on what the player moved
through. Stepping onto a square an obstacle is in the middle of leaving is safe, because
its telegraph arrow promised it was going elsewhere — reading the arrows correctly has
to be rewarded. Two exceptions in opposite directions: a head-on position swap is fatal
(the two would pass through each other), and a jumper hopping over the player's square is
not.

# Telegraphing

Every obstacle shows what it is about to do before it does it — an arrow rotated to its
exact next move. The game is meant to be beatable by reading the board, not memorising
it, so the arrow must point at the real upcoming move. Each obstacle's next move is
decided once at spawn and re-decided immediately after it executes a move, never both in
the same step, which keeps "shown, then resolved next turn" true turn over turn.

# Obstacle types

Standard (one cell, straight), Jumper (two cells, can hop over an intervening cell),
L-mover (a knight-style hop), Diagonal, Bouncer (reverses before a step would leave the
board), Zigzag, Chaser (re-aims one cell toward the player each turn), and Turret (never
moves; counts down a fuse then fires a full row or column beam, lit a turn ahead).
Difficulty scales with turns survived: spawn rate rises, more edges open up, and harder
obstacle types unlock progressively so the early game teaches one pattern at a time.

# Testing

The game engine has no Python test coverage — it is client-side JavaScript with no test
runner wired up — so it was verified by driving the real engine in a browser:
dispatching real keydown events and asserting that an obstacle's position after resolving
always matches exactly what its arrow showed beforehand.

# What it demonstrates

State-machine design with a strict turn order, a fair "telegraph then execute"
contract, and verifying a system by driving it rather than trusting it by inspection.
