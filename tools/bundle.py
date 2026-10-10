"""tools/bundle.py: Single-file distribution bundler for Royal Caribbean utilities.

Flattens submodules into a single-file distribution script with clean, deduplicated,
and properly sorted top-level imports.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys

from collections import deque
from pathlib import Path
from typing import Dict, List, NamedTuple, Set

# Ensure UTF-8 output encoding across Windows console environments
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

TOOLS_DIR = Path(__file__).parent.resolve()
ROOT_DIR = TOOLS_DIR.parent
PACKAGE_DIR = ROOT_DIR / "royal_caribbean"
DIST_DIR = ROOT_DIR / "dist"

# Core submodules required across CLI entrypoints
BASE_SUBMODULES = [
    PACKAGE_DIR / "models.py",
    PACKAGE_DIR / "utils" / "constants.py",
    PACKAGE_DIR / "utils" / "logging.py",
    PACKAGE_DIR / "utils" / "notify.py",
    PACKAGE_DIR / "config" / "loaders.py",
    PACKAGE_DIR / "api" / "client.py",
    PACKAGE_DIR / "api" / "auth.py",
]


class BundleSpec(NamedTuple):
    name: str
    entry_point: Path
    dist_output: Path
    modules: List[Path]


# Bundle Configuration Registry
BUNDLE_CONFIGS: Dict[str, BundleSpec] = {
    "casino": BundleSpec(
        name="Casino Offers Tracker",
        entry_point=ROOT_DIR / "CheckRoyalCaribbeanCasinoOffers_entry.py",
        dist_output=DIST_DIR / "CheckRoyalCaribbeanCasinoOffers_bundled.py",
        modules=BASE_SUBMODULES + [PACKAGE_DIR / "core" / "casino.py"],
    ),
#    "browse": BundleSpec(
#        name="Ship & Itinerary Browser",
#        entry_point=ROOT_DIR / "BrowseRoyalCaribbeanPrice_entry.py",
#        dist_output=DIST_DIR / "BrowseRoyalCaribbeanPrice_bundled.py",
#        modules=BASE_SUBMODULES + [PACKAGE_DIR / "core" / "price.py"],
#    ),
#    "price": BundleSpec(
#        name="Price Tracker",
#        entry_point=ROOT_DIR / "CheckRoyalCaribbeanPrice_entry.py",
#        dist_output=DIST_DIR / "CheckRoyalCaribbeanPrice_bundled.py",
#        modules=BASE_SUBMODULES + [PACKAGE_DIR / "core" / "price.py"],
#    ),
}


class PackageImportExtractor(ast.NodeTransformer):
    """AST Transformer to extract external imports for header deduplication while preserving
    imports enclosed within try/except blocks.
    """

    def __init__(self, package_name: str = "royal_caribbean") -> None:
        super().__init__()
        self.package_name = package_name
        self.plain_imports: Set[str] = set()
        self.from_imports: Dict[str, Set[str]] = {}
        self.future_imports: Set[str] = set()
        self._in_try_block: bool = False

    def visit_Try(self, node: ast.Try) -> ast.AST:
        previous_state = self._in_try_block
        self._in_try_block = True
        self.generic_visit(node)
        self._in_try_block = previous_state
        return node

    def visit_Import(self, node: ast.Import) -> ast.AST | None:
        if self._in_try_block:
            return node

        for alias in node.names:
            if not alias.name.startswith(self.package_name):
                name_str = alias.name + (
                    f" as {alias.asname}" if alias.asname else ""
                )
                self.plain_imports.add(name_str)

        return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST | None:
        if self._in_try_block:
            return node

        if node.level and node.level > 0:
            return None

        module_name = node.module or ""

        if module_name == self.package_name or module_name.startswith(
            f"{self.package_name}."
        ):
            return None

        if module_name == "__future__":
            for alias in node.names:
                self.future_imports.add(alias.name)
            return None

        if module_name not in self.from_imports:
            self.from_imports[module_name] = set()

        for alias in node.names:
            name_str = alias.name + (
                f" as {alias.asname}" if alias.asname else ""
            )
            self.from_imports[module_name].add(name_str)

        return None


class AllExpander(ast.NodeTransformer):
    """AST Transformer that replaces dynamic submodule __all__ assignments
    with evaluated, static string literals in bundled output.
    """

    def __init__(self, collected_exports: set[str]):
        super().__init__()
        # Ensure 'main' is always present for CLI artifacts
        self.exports = sorted(list(collected_exports | {"main"}))

    def visit_Assign(self, node: ast.Assign) -> ast.AST | None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                new_list = ast.List(
                    elts=[ast.Constant(value=name) for name in self.exports],
                    ctx=ast.Load(),
                )
                return ast.Assign(targets=node.targets, value=new_list)
        return self.generic_visit(node)


def extract_submodule_exports(module_path: Path) -> set[str]:
    """Collects top-level public exports from a submodule ONLY if __all__ is explicitly defined."""
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    exports: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(
                                elt.value, str
                            ):
                                exports.add(elt.value)

    return exports


def process_module(source_code: str) -> tuple[str, PackageImportExtractor]:
    """Parses AST, extracts imports, and returns cleaned submodule body code."""
    tree = ast.parse(source_code)
    extractor = PackageImportExtractor()
    modified_tree = extractor.visit(tree)
    ast.fix_missing_locations(modified_tree)

    cleaned_code = ast.unparse(modified_tree)
    return cleaned_code, extractor


def process_entrypoint(
    source_code: str, collected_exports: set[str]
) -> tuple[str, PackageImportExtractor]:
    """Parses AST, strips package imports, and expands dynamic __all__ into static literals."""
    tree = ast.parse(source_code)

    extractor = PackageImportExtractor()
    tree = extractor.visit(tree)

    expander = AllExpander(collected_exports)
    tree = expander.visit(tree)

    ast.fix_missing_locations(tree)
    cleaned_code = ast.unparse(tree)

    return cleaned_code, extractor


def build_import_header(extractors: List[PackageImportExtractor]) -> str:
    """Consolidates and formats deduplicated imports according to PEP 8 standards."""
    all_future: Set[str] = set()
    all_plain: Set[str] = set()
    all_from: Dict[str, Set[str]] = {}

    for ext in extractors:
        all_future.update(ext.future_imports)
        all_plain.update(ext.plain_imports)

        for mod, names in ext.from_imports.items():
            if mod not in all_from:
                all_from[mod] = set()
            all_from[mod].update(names)

    lines: List[str] = []

    if all_future:
        sorted_future = ", ".join(sorted(all_future))
        lines.append(f"from __future__ import {sorted_future}")

    if all_plain:
        for name in sorted(all_plain):
            lines.append(f"import {name}")

    if all_from:
        for mod in sorted(all_from.keys()):
            sorted_names = ", ".join(sorted(all_from[mod]))
            lines.append(f"from {mod} import {sorted_names}")

    return "\n".join(lines)


def get_module_dependencies(
    module_path: Path, package_name: str = "royal_caribbean"
) -> Set[Path]:
    """Inspects a Python file's AST to find all internal package modules it imports."""
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    dependencies: Set[Path] = set()

    for node in ast.walk(tree):
        module_name = None
        if isinstance(node, ast.ImportFrom) and node.module:
            module_name = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{package_name}."):
                    module_name = alias.name

        if module_name and (
            module_name == package_name
            or module_name.startswith(f"{package_name}.")
        ):
            parts = module_name.split(".")[1:]  # strip 'royal_caribbean'
            if parts:
                rel_path = Path(*parts).with_suffix(".py")
                target_path = PACKAGE_DIR / rel_path
                if target_path.exists() and target_path != module_path:
                    dependencies.add(target_path)

    return dependencies


def resolve_module_build_order(modules: List[Path]) -> List[Path]:
    """Uses Kahn's Algorithm to dynamically sort modules into a dependency-safe bundle build order."""
    module_set = set(modules)
    graph: Dict[Path, Set[Path]] = {m: set() for m in modules}
    in_degree: Dict[Path, int] = {m: 0 for m in modules}

    for mod in modules:
        deps = get_module_dependencies(mod) & module_set
        for dep in deps:
            graph[dep].add(mod)
            in_degree[mod] += 1

    queue = deque([m for m in modules if in_degree[m] == 0])
    ordered: List[Path] = []

    while queue:
        curr = queue.popleft()
        ordered.append(curr)

        for neighbor in graph[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(ordered) != len(modules):
        unresolved = [
            m.relative_to(ROOT_DIR).as_posix()
            for m in modules
            if in_degree[m] > 0
        ]
        raise RuntimeError(
            f"❌ Circular dependency detected in package modules! Could not order: {', '.join(unresolved)}"
        )

    return ordered


def build_single_bundle(spec: BundleSpec) -> None:
    """Builds a single distribution bundle according to its Spec."""
    print(f"\n📦 Building standalone bundle: {spec.name} ({spec.entry_point.name})...")

    if not spec.entry_point.exists():
        print(f"❌ Entrypoint file not found: {spec.entry_point.relative_to(ROOT_DIR)}")
        return

    DIST_DIR.mkdir(parents=True, exist_ok=True)

    sorted_modules = resolve_module_build_order(spec.modules)

    extractors: List[PackageImportExtractor] = []
    bundled_blocks: List[str] = []
    collected_exports: set[str] = set()

    for module_path in sorted_modules:
        if not module_path.exists():
            print(
                f"⚠️  Skipping missing module: {module_path.relative_to(ROOT_DIR)}"
            )
            continue

        print(
            f"  └─ Bundling submodule: {module_path.relative_to(ROOT_DIR)}"
        )
        source = module_path.read_text(encoding="utf-8")

        collected_exports.update(extract_submodule_exports(module_path))

        cleaned_code, extractor = process_module(source)
        extractors.append(extractor)

        rel_path = module_path.relative_to(ROOT_DIR)
        block_header = (
            f"\n# {'=' * 70}\n# MODULE: {rel_path}\n# {'=' * 70}\n"
        )
        bundled_blocks.append(block_header + cleaned_code)

    print(f"  └─ Processing entrypoint: {spec.entry_point.name}")
    entry_source = spec.entry_point.read_text(encoding="utf-8")

    cleaned_code, extractor = process_entrypoint(
        entry_source, collected_exports
    )
    extractors.append(extractor)

    block_header = f"\n# {'=' * 70}\n# MAIN ENTRYPOINT: {spec.entry_point.name}\n# {'=' * 70}\n"
    bundled_blocks.append(block_header + cleaned_code)

    import_header = build_import_header(extractors)
    header_section = f'"""Single-file distribution bundle generated by bundle.py for {spec.entry_point.name}."""\n\n'
    header_section += import_header + "\n\n"

    full_output = header_section + "\n".join(bundled_blocks) + "\n"
    full_output = re.sub(r"\n{3,}", "\n\n", full_output)

    spec.dist_output.write_text(full_output, encoding="utf-8")
    print(
        f"✅ Bundle successfully generated at: {spec.dist_output.relative_to(ROOT_DIR)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Single-file distribution bundler for Royal Caribbean scripts."
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Target bundle(s) to build (default: all)",
    )
    args = parser.parse_args()

    # Hand-code default value and validate choices manually to work around
    # argparse edge-case error to avoid argparse nargs='*' choice-validation bugs
    targets = args.targets or ["all"]
    valid_choices = list(BUNDLE_CONFIGS.keys()) + ["all"]
    for target in targets:
        if target not in valid_choices:
            parser.error(f"argument targets: invalid choice: {target!r} (choose from {', '.join(map(repr, valid_choices))})")

    targets_to_build = (
        list(BUNDLE_CONFIGS.keys())
        if "all" in targets
        else targets
    )

    for target in targets_to_build:
        build_single_bundle(BUNDLE_CONFIGS[target])


if __name__ == "__main__":
    main()
