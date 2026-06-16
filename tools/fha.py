#!/usr/bin/env python3
"""
fha — family history archive CLI.

Subcommands live in individual tool files under tools/; each is also
runnable standalone (e.g. python tools/lint.py --root …).

This file is intentionally thin — just a dispatcher.  All logic lives in the
individual tool modules.  Adding a new tool: implement it in tools/newtool.py
with a register(subs) function, then add one import + one register() call here.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sure sibling tool modules are importable when this file is run directly
sys.path.insert(0, str(Path(__file__).parent))

import argparse
from _lib import find_archive_root, EXIT_FAILURE


def _require_root(args: argparse.Namespace) -> Path:
    """Resolve the archive root from --root flag or auto-detection."""
    if getattr(args, 'root', None):
        return Path(args.root).resolve()
    detected = find_archive_root()
    if detected is None:
        print('ERROR: cannot find archive root (no fha.yaml found). '
              'Use --root to specify.', file=sys.stderr)
        sys.exit(EXIT_FAILURE)
    return detected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='fha',
        description='Family history archive (fha) tool suite.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Run any subcommand with -h/--help for full options.\n'
            'Documentation: TOOLING.md in the archive root.'
        ),
    )
    parser.add_argument(
        '--root', metavar='PATH',
        help='Archive root (default: auto-detect by walking up from CWD)',
    )
    parser.add_argument(
        '--spec-root', metavar='PATH',
        help='Spec docs root when SPEC.md/TOOLING.md are not in the archive '
             '(e.g. running from the public spec repo)',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """
    Entry point for `fha` (or `python tools/fha.py`).

    Tool modules are imported inside this function rather than at the top of
    the file so that a syntax error or missing dependency in one tool doesn't
    prevent the other tools from loading.  Each register() call adds that
    tool's subcommand to the shared parser.
    """
    # Lazy imports: keep them inside main() for the reason above.
    from id import register as id_register
    from index import register as index_register
    from lint import register as lint_register
    from stubs import register as stubs_register
    from views import register as views_register

    parser = build_parser()
    subs = parser.add_subparsers(dest='command', metavar='COMMAND')

    id_register(subs)
    index_register(subs)
    lint_register(subs)
    stubs_register(subs)
    views_register(subs)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args) or 0


if __name__ == '__main__':
    sys.exit(main())
