"""The `storyteller` command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _report(path: str):
    from .report import Report

    return Report.open(path)


def cmd_outline(args: argparse.Namespace) -> int:
    print(_report(args.report).outline())
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    report = _report(args.report)
    path = report.save()
    print(path)
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    report = _report(args.report)
    report.finalize(provenance=not args.no_provenance)
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    report = _report(args.report)
    if not report.remove(args.key):
        print(f"no section or block {args.key!r}; see `storyteller outline {args.report}`", file=sys.stderr)
        return 1
    report.save()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .check import check_file

    status = 0
    for name in args.files:
        problems = check_file(name)
        size = Path(name).stat().st_size
        if problems:
            status = 1
            print(f"{name} ({size / 1e6:.1f} MB):")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"{name} ({size / 1e6:.1f} MB): self-contained")
    return status


def cmd_pdf(args: argparse.Namespace) -> int:
    from .pdf import to_pdf

    try:
        print(to_pdf(args.file, args.output, page=args.page))
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .live import serve

    path = Path(args.report)
    if path.suffix.lower() not in (".html", ".htm"):
        path = path.with_name(path.name + ".html")
    serve(path, port=args.port)
    return 0


def cmd_comments(args: argparse.Namespace) -> int:
    import json

    from . import comments

    try:
        if args.action == "list":
            found = comments.load(args.report)
            shown = [c for c in found if args.all or c["status"] == "open"]
            if args.json:
                print(json.dumps(shown, indent=1, ensure_ascii=False))
                return 0
            hidden = len(found) - len(shown)
            if not shown:
                print("no open comments" + (f" ({hidden} resolved; --all shows them)" if hidden else ""))
            for comment in shown:
                print(comments.describe(comment) + "\n")
            if shown and hidden:
                print(f"({hidden} resolved; --all shows them)")
        elif args.action in ("reply", "resolve"):
            comment = comments.reply(args.report, args.id, args.message or "", resolve=args.action == "resolve")
            print(comments.describe(comment))
        elif args.action == "import":
            other = comments.embedded(Path(args.file).read_text(encoding="utf-8"))
            if other is None:
                print(f"{args.file} has no comments slot", file=sys.stderr)
                return 1
            added, updated = comments.merge(args.report, other)
            print(f"{added} added, {updated} updated")
    except (FileNotFoundError, KeyError, ValueError) as error:
        print(error.args[0] if error.args else error, file=sys.stderr)
        return 1
    return 0


def cmd_palette(args: argparse.Namespace) -> int:
    from html import escape

    from .report import Report
    from .theme import palette

    def swatches(colors: list[str]) -> str:
        cells = "".join(
            f'<span style="display:inline-grid;gap:4px;justify-items:center;margin:0 6px 10px 0">'
            f'<span style="width:3.2em;height:2em;border-radius:8px;background:{escape(c)}"></span>'
            f"<code>{escape(c)}</code></span>"
            for c in colors
        )
        return f"<div>{cells}</div>"

    report = Report("code flower palette", args.output, subtitle="Colours for charts and reports")
    with report.section("categorical", "Categorical", description="Assign in this order; never cycle."):
        for mode in ("light", "dark"):
            report.text(f"**{mode}**")
            report.html(swatches(palette.categorical(mode=mode)))
    with report.section("sequential", "Sequential and diverging"):
        for name in palette.SEQUENTIAL_NAMES:
            report.text(f"**cf_{name}** (continuous), and 7 ordered classes")
            report.html(swatches(palette.colormap_stops(name)) + swatches(palette.sequential(7, name)))
        report.text("**cf_div** (diverging)")
        report.html(swatches(palette.diverging_stops()))
    with report.section("status", "Status"):
        report.html(swatches(list(palette.STATUS["light"].values())))
    print(report.save())
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import build

    print(build(Path(args.output)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="storyteller", description="Standalone HTML analysis reports.")
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("outline", help="print a report's sections with their keys and status")
    sub.add_argument("report", help="the report, e.g. reports/pbmc (or reports/pbmc.html)")
    sub.set_defaults(run=cmd_outline)

    sub = commands.add_parser("build", help="re-render a report's HTML from its store")
    sub.add_argument("report")
    sub.set_defaults(run=cmd_build)

    sub = commands.add_parser("finalize", help="drop draft marks, fit the size budget, check and write")
    sub.add_argument("report")
    sub.add_argument("--no-provenance", action="store_true", help="leave out the software and scripts appendix")
    sub.set_defaults(run=cmd_finalize)

    sub = commands.add_parser("remove", help="remove a section or block by key")
    sub.add_argument("report")
    sub.add_argument("key")
    sub.set_defaults(run=cmd_remove)

    sub = commands.add_parser("comments", help="list, answer and resolve the comments readers left on a report")
    actions = sub.add_subparsers(dest="action", required=True)
    action = actions.add_parser("list", help="open comments: where, on which words, and what they say")
    action.add_argument("report")
    action.add_argument("--all", action="store_true", help="resolved ones too")
    action.add_argument("--json", action="store_true")
    action = actions.add_parser("reply", help="answer a comment, leaving it open")
    action.add_argument("report")
    action.add_argument("id")
    action.add_argument("message")
    action = actions.add_parser("resolve", help="mark a comment done, with an optional answer")
    action.add_argument("report")
    action.add_argument("id")
    action.add_argument("message", nargs="?", help="what you changed, shown to the reader")
    action = actions.add_parser("import", help="add the comments from a copy a reader saved")
    action.add_argument("report")
    action.add_argument("file")
    sub.set_defaults(run=cmd_comments)

    sub = commands.add_parser("check", help="check that HTML files are self-contained and small enough")
    sub.add_argument("files", nargs="+")
    sub.set_defaults(run=cmd_check)

    sub = commands.add_parser("pdf", help="print a report to PDF with headless Chromium (needs playwright)")
    sub.add_argument("file")
    sub.add_argument("-o", "--output")
    sub.add_argument("--page", choices=["A4", "Letter"], help="override the report's page size")
    sub.set_defaults(run=cmd_pdf)

    sub = commands.add_parser("serve", help="show a report in a browser, updating live (127.0.0.1)")
    sub.add_argument("report")
    sub.add_argument("--port", type=int, default=8765)
    sub.set_defaults(run=cmd_serve)

    sub = commands.add_parser("palette", help="write a page showing the colour palettes")
    sub.add_argument("-o", "--output", default="codeflower-palette.html")
    sub.set_defaults(run=cmd_palette)

    sub = commands.add_parser("demo", help="write a demo report (needs numpy, pandas and matplotlib)")
    sub.add_argument("-o", "--output", default="storyteller-demo.html")
    sub.set_defaults(run=cmd_demo)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
