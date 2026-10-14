#!/usr/bin/env python3
"""tools/test_shapes.py: prove the package facade and the bundled file are the same module.

WHY: each script ships in two shapes, a root facade over the ``royal_caribbean/``
package and a flattened single file built by ``tools/bundle.py``. The flattened
namespace hides import-time errors (missing ``__all__``, circular imports,
names that only resolve inside one big module), so "bundle green / package red"
has happened three times in PR #147. This harness stages each shape in an
isolated temp dir, runs the SAME test files against it, and compares the public
surface (``__all__`` plus any exported name that is not actually bound).

Shapes: package = facade copied as <module>.py + royal_caribbean/ tree;
bundle = dist file copied as <module>.py and NO package dir (it must stand alone).
Exit 0 only if every shape passes and every script's surfaces match.

Usage: python tools/test_shapes.py [--script NAME] [--keep] [--no-build]
Stdlib only, cross-platform (no bash/cp). Add scripts by extending SCRIPTS.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCRIPTS = [
    {
        "module": "CheckRoyalCaribbeanCasinoOffers_entry",  # name the tests import
        "facade": "CheckRoyalCaribbeanCasinoOffers_entry.py",  # package entrypoint at repo root
        "bundle": "dist/CheckRoyalCaribbeanCasinoOffers_bundled.py",
        "tests": ["unittests/test_casino_offers.py"],
        "siblings": ["CheckRoyalCaribbeanPrice.py"],  # other root modules the tests import
    },
    {
        "module": "BrowseRoyalCaribbeanPrice_entry",
        "facade": "BrowseRoyalCaribbeanPrice_entry.py",
        "bundle": "dist/BrowseRoyalCaribbeanPrice_bundled.py",
        "tests": ["unittests/test_price_browser.py"],
        "siblings": ["CheckRoyalCaribbeanPrice.py"],
    },
]

SHAPES = ("package", "bundle")
PROBE = (
    "import importlib, json; m = importlib.import_module({mod!r}); "
    "a = getattr(m, '__all__', None); "
    "print('SURFACE=' + json.dumps({{'all': None if a is None else sorted(a), "
    "'unbound': [] if a is None else [n for n in a if not hasattr(m, n)]}}))"
)


def env_for(stage: Path) -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(stage))
    env.pop("PYTEST_ADDOPTS", None)
    return env


def build() -> None:
    proc = subprocess.run(
        [sys.executable, "tools/bundle.py"], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    if proc.returncode != 0:
        sys.exit(f"bundle.py failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}")


class StageError(Exception):
    """A shape could not be staged (e.g. missing source file); reported as that shape failing."""


def stage(script: dict, shape: str, dest: Path) -> None:
    """Copy exactly what this shape ships with, nothing else, into dest."""
    mod = script["module"]
    src = ROOT / (script["facade"] if shape == "package" else script["bundle"])
    if not src.is_file():
        raise StageError(f"missing source file: {src} (did the bundle build?)")
    shutil.copy2(src, dest / f"{mod}.py")
    if shape == "package":
        shutil.copytree(ROOT / "royal_caribbean", dest / "royal_caribbean",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for sib in script["siblings"]:
        shutil.copy2(ROOT / sib, dest / Path(sib).name)
    (dest / "unittests").mkdir()
    for test in script["tests"]:
        shutil.copy2(ROOT / test, dest / "unittests" / Path(test).name)


def run_pytest(script: dict, dest: Path) -> dict:
    tests = [f"unittests/{Path(t).name}" for t in script["tests"]]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=dest, env=env_for(dest), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    counts = {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|errors?)", out.splitlines()[-1] if out.strip() else "")}
    passed, bad = counts.get("passed", 0), sum(v for k, v in counts.items() if k != "passed")
    summary = f"PASS {passed}/{passed}" if proc.returncode == 0 else (
        f"FAIL {passed} passed, {bad} failed/errored" if counts else f"FAIL (exit {proc.returncode})")
    return {"ok": proc.returncode == 0, "summary": summary, "output": out}


def probe_surface(script: dict, dest: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", PROBE.format(mod=script["module"])], cwd=dest, env=env_for(dest),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in proc.stdout.splitlines():
        if line.startswith("SURFACE="):
            return {"ok": True, **json.loads(line[len("SURFACE="):])}
    last = (proc.stderr.strip().splitlines() or ["no output"])[-1]
    return {"ok": False, "error": last}


def diff_surfaces(a: dict, b: dict) -> list[str]:
    lines = []
    if a["all"] != b["all"]:
        if a["all"] is None or b["all"] is None:
            lines.append(f"__all__ present in only one shape: package={a['all'] is not None}, bundle={b['all'] is not None}")
        else:
            lines += [f"  only in package: {n}" for n in sorted(set(a["all"]) - set(b["all"]))]
            lines += [f"  only in bundle:  {n}" for n in sorted(set(b["all"]) - set(a["all"]))]
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare package-facade and bundled shapes of each script.")
    ap.add_argument("--script", help="limit to one module name from SCRIPTS")
    ap.add_argument("--keep", action="store_true", help="keep temp dirs and print their paths")
    ap.add_argument("--no-build", action="store_true", help="skip running tools/bundle.py")
    args = ap.parse_args(argv)

    scripts = [s for s in SCRIPTS if args.script in (None, s["module"])]
    if not scripts:
        sys.exit(f"no script named {args.script!r}; known: {[s['module'] for s in SCRIPTS]}")
    if not args.no_build:
        build()

    rows, failures, tmp_dirs = [], [], []
    try:  # cleanup below always runs, even on staging errors or unexpected exceptions
        for script in scripts:
            mod, results, surfaces = script["module"], {}, {}
            for shape in SHAPES:
                dest = Path(tempfile.mkdtemp(prefix=f"shape-{shape}-"))
                tmp_dirs.append(dest)
                try:
                    stage(script, shape, dest)
                except (StageError, OSError) as exc:
                    results[shape] = {"ok": False, "summary": f"FAIL {exc}", "output": ""}
                    surfaces[shape] = {"ok": False, "error": str(exc)}
                    failures.append(f"{mod}/{shape}: staging failed: {exc}")
                    continue
                results[shape] = run_pytest(script, dest)
                surfaces[shape] = probe_surface(script, dest)
                if not results[shape]["ok"]:
                    failures.append(f"{mod}/{shape}: pytest {results[shape]['summary']}")
                    tail = "\n".join(results[shape]["output"].strip().splitlines()[-12:])
                    print(f"--- {mod}/{shape} pytest tail ---\n{tail}\n")
                    print(f"\n{'='*40}\nFULL PYTEST OUTPUT FOR {mod}/{shape}:\n{'='*40}\n{results[shape]['output']}\n{'='*40}\n") #DEBUG
                if not surfaces[shape]["ok"]:
                    failures.append(f"{mod}/{shape}: import failed: {surfaces[shape]['error']}")
                elif surfaces[shape]["unbound"]:
                    failures.append(f"{mod}/{shape}: __all__ names not bound: " + ", ".join(surfaces[shape]["unbound"]))
            pkg, bun = surfaces["package"], surfaces["bundle"]
            if pkg["ok"] and bun["ok"]:
                diff = diff_surfaces(pkg, bun)
                parity = "MATCH" if not diff else "DIFF"
                if diff:
                    failures.append(f"{mod}: public surface differs\n" + "\n".join(diff))
            else:
                parity = "CANNOT COMPARE (import failed)"
                failures.append(f"{mod}: parity not checked, import failed in: "
                                + ", ".join(s for s in SHAPES if not surfaces[s]["ok"]))
            for shape in SHAPES:
                s = surfaces[shape]
                size = "n/a" if not s["ok"] else ("no __all__" if s["all"] is None else str(len(s["all"])))
                unb = "n/a" if not s["ok"] else (", ".join(s["unbound"]) or "none")
                rows.append((mod, shape, results[shape]["summary"], size, unb, parity))

        headers = ("script", "shape", "pytest", "__all__", "unbound", "parity")
        widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
        fmt = "  ".join("{:<%d}" % w for w in widths)
        print(fmt.format(*headers))
        print(fmt.format(*("-" * w for w in widths)))
        for r in rows:
            print(fmt.format(*r))

        if failures:
            print("\nFAILURES:\n" + "\n".join(f"- {f}" for f in failures))
        print("\nRESULT:", "FAIL" if failures else "OK")
    finally:
        for d in tmp_dirs:
            if args.keep:
                print(f"kept: {d}")
            else:
                shutil.rmtree(d, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
