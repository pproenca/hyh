#!/usr/bin/env python3
"""Codemod to strip docstrings and comments from Python files using libcst."""

import argparse
import sys
from pathlib import Path

import libcst as cst
from libcst import matchers as m


class RemoveCommentsAndDocstrings(cst.CSTTransformer):
    """Transformer that removes all docstrings and comments from Python code."""

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        # Remove docstrings at the top of the module
        new_body = [
            node
            for node in updated_node.body
            if not m.matches(node, m.SimpleStatementLine(body=[m.Expr(value=m.SimpleString())]))
        ]
        return updated_node.with_changes(
            body=new_body,
            header=[item for item in updated_node.header if not isinstance(item, cst.Comment)],
            footer=[item for item in updated_node.footer if not isinstance(item, cst.Comment)],
        )

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        return self._remove_docstring(updated_node)

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        return self._remove_docstring(updated_node)

    def _remove_docstring[T: cst.FunctionDef | cst.ClassDef](self, node: T) -> T:
        body = node.body
        if isinstance(body, cst.IndentedBlock):
            new_body_content = [
                stmt
                for stmt in body.body
                if not m.matches(stmt, m.SimpleStatementLine(body=[m.Expr(value=m.SimpleString())]))
            ]
            return node.with_changes(body=body.with_changes(body=new_body_content))
        return node

    def leave_Comment(
        self, original_node: cst.Comment, updated_node: cst.Comment
    ) -> cst.RemovalSentinel:
        return cst.RemovalSentinel.REMOVE

    def leave_EmptyLine(
        self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
    ) -> cst.EmptyLine:
        if updated_node.comment:
            return updated_node.with_changes(comment=None)
        return updated_node


def process_file(path: Path, *, dry_run: bool, verbose: bool) -> bool:
    """Process a single Python file. Returns True if changes were made."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = cst.parse_module(source)
        transformer = RemoveCommentsAndDocstrings()
        modified_tree = tree.visit(transformer)
        modified_code = modified_tree.code

        if modified_code == source:
            return False

        if dry_run:
            if verbose:
                print(f"Would modify: {path}")
        else:
            path.write_text(modified_code, encoding="utf-8")
            if verbose:
                print(f"Modified: {path}")

        return True

    except Exception as e:
        print(f"Error processing {path}: {e}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Strip docstrings and comments from Python files")
    parser.add_argument("folder", type=Path, help="Folder to process recursively")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be changed without writing"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print processed files")
    args = parser.parse_args()

    folder: Path = args.folder
    if not folder.is_dir():
        print(f"Error: {folder} is not a directory", file=sys.stderr)
        return 1

    files = list(folder.rglob("*.py"))
    modified_count = 0

    for py_file in files:
        if process_file(py_file, dry_run=args.dry_run, verbose=args.verbose):
            modified_count += 1

    action = "Would modify" if args.dry_run else "Modified"
    print(f"{action} {modified_count}/{len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
