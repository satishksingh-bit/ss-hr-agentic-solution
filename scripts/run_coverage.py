#!/usr/bin/env python3
"""Stdlib AST + trace.Trace statement and branch coverage runner enforcing >= 80% threshold across src/hr_agent/."""

from __future__ import annotations

import argparse
import ast
import dis
import sys
import threading
import trace
from pathlib import Path

import pytest


def _bytecode_lines(source: str, filename: str) -> set[int]:
    code = compile(source, filename, "exec")
    lines: set[int] = set()

    def _walk(c: object) -> None:
        for _, ln in dis.findlinestarts(c):  # type: ignore[arg-type]
            if ln is not None:
                lines.add(ln)
        for const in getattr(c, "co_consts", ()):
            if hasattr(const, "co_code"):
                _walk(const)

    _walk(code)
    return lines


def collect_executable_lines(py_file: Path) -> set[int]:
    source = py_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_file))
    exec_lines: set[int] = set()
    stmt_types = (
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.Return,
        ast.Raise,
        ast.Assert,
        ast.Delete,
        ast.Pass,
        ast.Break,
        ast.Continue,
        ast.Import,
        ast.ImportFrom,
        ast.Global,
        ast.Nonlocal,
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Match,
    )
    for node in ast.walk(tree):
        if isinstance(node, stmt_types):
            exec_lines.add(node.lineno)
        elif isinstance(node, ast.Expr):
            if not (
                isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                exec_lines.add(node.lineno)
    return exec_lines & _bytecode_lines(source, str(py_file))


def _stmt_header_lines(stmt: ast.stmt) -> set[int]:
    end_ln = (
        stmt.body[0].lineno - 1
        if getattr(stmt, "body", None)
        else (stmt.end_lineno or stmt.lineno)
    )
    return set(range(stmt.lineno, max(stmt.lineno, end_ln) + 1))


def collect_branch_points(py_file: Path) -> list[tuple[set[int], set[int]]]:
    """Return list of (decision_header_lines, target_stmt_lines) branch outcomes in py_file."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    branches: list[tuple[set[int], set[int]]] = []

    def _walk_block(stmts: list[ast.stmt]) -> None:
        for idx, stmt in enumerate(stmts):
            next_stmt = stmts[idx + 1] if idx + 1 < len(stmts) else None
            if isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While)):
                dec_lines = _stmt_header_lines(stmt)
                if stmt.body:
                    branches.append((dec_lines, _stmt_header_lines(stmt.body[0])))
                    _walk_block(stmt.body)
                if stmt.orelse:
                    branches.append((dec_lines, _stmt_header_lines(stmt.orelse[0])))
                    _walk_block(stmt.orelse)
                elif next_stmt is not None:
                    branches.append((dec_lines, _stmt_header_lines(next_stmt)))
            elif isinstance(stmt, ast.Try):
                dec_lines = {stmt.lineno}
                if stmt.body:
                    branches.append((dec_lines, _stmt_header_lines(stmt.body[0])))
                    _walk_block(stmt.body)
                for handler in stmt.handlers:
                    if handler.body:
                        branches.append(
                            (dec_lines, _stmt_header_lines(handler.body[0]))
                        )
                        _walk_block(handler.body)
                if stmt.orelse:
                    _walk_block(stmt.orelse)
                if stmt.finalbody:
                    _walk_block(stmt.finalbody)
            elif isinstance(
                stmt,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.With,
                    ast.AsyncWith,
                ),
            ):
                _walk_block(stmt.body)

    _walk_block(tree.body)
    return branches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run pytest with stdlib trace statement and branch coverage"
    )
    parser.add_argument("--fail-under", type=float, default=80.0)
    args, extra_pytest_args = parser.parse_known_args(argv)
    if extra_pytest_args and extra_pytest_args[0] == "--":
        extra_pytest_args = extra_pytest_args[1:]
    pytest_args = ["-q", *extra_pytest_args]

    tracer = trace.Trace(count=1, trace=0)
    threading.settrace(tracer.globaltrace)
    try:
        exit_code = int(tracer.runfunc(pytest.main, pytest_args))
    finally:
        threading.settrace(None)
    if exit_code != 0:
        return exit_code

    results = tracer.results()
    src_dir = (Path(__file__).resolve().parent.parent / "src" / "hr_agent").resolve()
    total_stmts = 0
    total_covered = 0
    total_branches = 0
    total_cov_branches = 0

    print(
        f"\n{'Name':<40} {'Stmts':>6} {'Miss':>6} {'Cover':>7} {'Branch':>7} {'BrMiss':>7} {'BrCov':>7}"
    )
    print("-" * 86)
    for py_file in sorted(src_dir.rglob("*.py")):
        all_hit_lines = {
            ln
            for (fname, ln), cnt in results.counts.items()
            if cnt > 0 and Path(fname).resolve() == py_file
        }
        exec_lines = collect_executable_lines(py_file)
        covered = exec_lines & all_hit_lines
        n_stmts = len(exec_lines)
        n_cov = len(covered)
        n_miss = n_stmts - n_cov
        pct = (n_cov / n_stmts * 100.0) if n_stmts else 100.0

        branch_points = collect_branch_points(py_file)
        n_br = len(branch_points)
        n_br_cov = sum(
            1
            for dec_lns, tgt_lns in branch_points
            if (dec_lns & all_hit_lines) and (tgt_lns & all_hit_lines)
        )
        n_br_miss = n_br - n_br_cov
        br_pct = (n_br_cov / n_br * 100.0) if n_br else 100.0

        total_stmts += n_stmts
        total_covered += n_cov
        total_branches += n_br
        total_cov_branches += n_br_cov
        rel = py_file.relative_to(src_dir.parent.parent)
        print(
            f"{rel!s:<40} {n_stmts:>6} {n_miss:>6} {pct:>6.1f}% {n_br:>7} {n_br_miss:>7} {br_pct:>6.1f}%"
        )

    print("-" * 86)
    tot_pct = (total_covered / total_stmts * 100.0) if total_stmts else 100.0
    tot_br_pct = (
        (total_cov_branches / total_branches * 100.0) if total_branches else 100.0
    )
    print(
        f"{'TOTAL':<40} {total_stmts:>6} {total_stmts - total_covered:>6} {tot_pct:>6.1f}% "
        f"{total_branches:>7} {total_branches - total_cov_branches:>7} {tot_br_pct:>6.1f}%"
    )
    if tot_pct < args.fail_under:
        print(
            f"FAIL: Required test coverage of {args.fail_under:.1f}% not reached ({tot_pct:.1f}%)",
            file=sys.stderr,
        )
        return 1
    print(
        f"PASS: Required test coverage of {args.fail_under:.1f}% reached (Line: {tot_pct:.1f}%, Branch: {tot_br_pct:.1f}%)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
