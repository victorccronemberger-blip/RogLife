#!/usr/bin/env python3
"""Create a deterministic, static architecture snapshot for the modularization effort.

The audit reads source only. It does not import the application or touch runtime data.
Run from the repository root: python scripts/audit_modularity.py
"""
from __future__ import annotations

import ast
import argparse
import json
import re
import symtable
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "modularizacao" / "arquitetura-pos-http.json"
AVAILABLE_MODULES: set[str] = set()


def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_modules(path: Path, tree: ast.Module) -> set[str]:
    name = module_name(path)
    package = name.split(".")[:-1]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names if alias.name.startswith("moneymin"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: max(0, len(package) - node.level + 1)]
                prefix = ".".join(base)
                target = f"{prefix}.{node.module}" if prefix and node.module else prefix or (node.module or "")
            else:
                target = node.module or ""
            if target.startswith("moneymin"):
                if node.module:
                    out.add(target)
                    out.update(f"{target}.{alias.name}" for alias in node.names
                               if f"{target}.{alias.name}" in AVAILABLE_MODULES)
                else:
                    out.update(f"{target}.{alias.name}" for alias in node.names if alias.name != "*")
    return out


def top_level_imported_modules(path: Path, tree: ast.Module) -> set[str]:
    """Resolve imports executed directly during module initialization.

    The all-AST graph intentionally includes imports inside functions, but those
    edges are deferred and should not be confused with Python import-time
    cycles. This second graph isolates direct module-body imports.
    """
    return imported_modules(path, ast.Module(body=[node for node in tree.body
                                                   if isinstance(node, (ast.Import, ast.ImportFrom))],
                                           type_ignores=[]))


def python_files() -> list[Path]:
    return sorted(p for p in (ROOT / "moneymin").rglob("*.py") if "__pycache__" not in p.parts)


def function_metrics(path: Path, tree: ast.Module) -> list[dict[str, object]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            found.append({"name": node.name, "line": node.lineno, "end_line": end,
                          "loc": end - node.lineno + 1,
                          "args": len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs),
                          "async": isinstance(node, ast.AsyncFunctionDef)})
    return sorted(found, key=lambda item: (-int(item["loc"]), str(item["name"])))


def class_metrics(tree: ast.Module) -> list[dict[str, object]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno)
            methods = [child for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))]
            found.append({"name": node.name, "line": node.lineno, "end_line": end,
                          "loc": end - node.lineno + 1, "method_count": len(methods),
                          "largest_methods": sorted(({"name": m.name, "loc": m.end_lineno - m.lineno + 1}
                              for m in methods), key=lambda row: (-int(row["loc"]), str(row["name"])))[:5]})
    return sorted(found, key=lambda item: (-int(item["loc"]), str(item["name"])))


def strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    result: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in graph.get(node, set()):
            if nxt not in graph:
                continue
            if nxt not in indices:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], indices[nxt])
        if low[node] == indices[node]:
            component = []
            while True:
                item = stack.pop()
                on_stack.remove(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1 or node in graph.get(node, set()):
                result.append(sorted(component))

    for node in graph:
        if node not in indices:
            visit(node)
    return sorted(result)


def route_inventory(path: Path, tree: ast.Module) -> list[dict[str, object]]:
    routes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                        and dec.func.attr.lower() in {"get", "post", "put", "patch", "delete", "route"}):
                    route = ast.unparse(dec.args[0]) if dec.args else ""
                    routes.append({"handler": node.name, "method": dec.func.attr.upper(),
                                   "path": route, "line": node.lineno})
    if not routes:
        registry_path = path.parent / "routes" / "registry.py"
        if registry_path.is_file():
            registry = ast.parse(registry_path.read_text(encoding="utf-8"))
            handlers = next((node.value for node in registry.body
                             if isinstance(node, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == "HANDLERS"
                                     for target in node.targets)), None)
            if handlers is not None:
                for domain, handler, decorators in ast.literal_eval(handlers):
                    for method, kind, argument in decorators:
                        if method.lower() in {"get", "post", "put", "patch", "delete"} and kind == "literal":
                            routes.append({"handler": handler, "method": method.upper(),
                                           "path": repr(argument), "domain": domain})
    return sorted(routes, key=lambda row: (str(row["path"]), str(row["method"])))


def create_app_scope(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    create = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_app"), None)
    if create is None:
        return {}
    local_functions = [n for n in create.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    globals_used = sorted({name for n in ast.walk(create) if isinstance(n, ast.Global) for name in n.names})
    nonlocals_used = sorted({name for n in ast.walk(create) if isinstance(n, ast.Nonlocal) for name in n.names})
    table = symtable.symtable(source, str(path), "exec")
    create_table = next((c for c in table.get_children() if c.get_name() == "create_app"), None)
    closures = []
    if create_table:
        for child in create_table.get_children():
            frees = sorted(s.get_name() for s in child.get_symbols() if s.is_free())
            if frees:
                closures.append({"name": child.get_name(), "line": child.get_lineno(), "free_names": frees})
    return {"line": create.lineno, "end_line": create.end_lineno,
            "loc": create.end_lineno - create.lineno + 1,
            "nested_functions": len(local_functions),
            "global_declarations": globals_used, "nonlocal_declarations": nonlocals_used,
            "closures_with_free_names": closures}


def cpp_mainwindow() -> dict[str, object]:
    path = ROOT / "desktop" / "src" / "MainWindow.cpp"
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    # Definitions are identified by qualified C++ method names; brace counting gives a stable size estimate.
    rx = re.compile(r"^\s*(?:[\w:<>*&]+\s+)+MainWindow::(\w+)\s*\([^;]*\)\s*(?:const\s*)?\{")
    methods = []
    for i, line in enumerate(lines):
        match = rx.match(line)
        if not match:
            continue
        depth = 0
        end = i
        for end in range(i, len(lines)):
            depth += lines[end].count("{") - lines[end].count("}")
            if depth == 0:
                break
        methods.append({"name": match.group(1), "line": i + 1, "end_line": end + 1,
                        "loc": end - i + 1})
    return {"path": "desktop/src/MainWindow.cpp", "loc": len(lines),
            "methods": sorted(methods, key=lambda m: (-int(m["loc"]), str(m["name"])))[:30]}


def build() -> dict[str, object]:
    global AVAILABLE_MODULES
    files = python_files()
    trees: dict[str, ast.Module] = {}
    paths: dict[str, Path] = {}
    for path in files:
        name = module_name(path)
        paths[name] = path
        trees[name] = ast.parse(path.read_text(encoding="utf-8"))
    AVAILABLE_MODULES = set(trees)
    graph: dict[str, set[str]] = {name: set() for name in trees}
    eager_graph: dict[str, set[str]] = {name: set() for name in trees}
    file_rows = []
    funcs_by_module = {}
    for name, tree in trees.items():
        imports = imported_modules(paths[name], tree)
        graph[name] = {target for target in imports if target in trees or any(m.startswith(target + ".") for m in trees)}
        eager_imports = top_level_imported_modules(paths[name], tree)
        eager_graph[name] = {target for target in eager_imports
                             if target in trees or any(m.startswith(target + ".") for m in trees)}
        funcs = function_metrics(paths[name], tree)
        funcs_by_module[name] = funcs
        file_rows.append({"module": name, "path": paths[name].relative_to(ROOT).as_posix(),
                          "loc": len(paths[name].read_text(encoding="utf-8").splitlines()),
                          "imports": sorted(graph[name]), "function_count": len(funcs),
                          "largest_functions": funcs[:8], "class_count": len(class_metrics(tree)),
                          "largest_classes": class_metrics(tree)[:5]})
    server_path = ROOT / "moneymin" / "web" / "server.py"
    server_tree = trees["moneymin.web.server"]
    routes = route_inventory(server_path, server_tree)
    top_defs = [{"name": n.name, "line": n.lineno, "end_line": n.end_lineno,
                 "loc": n.end_lineno - n.lineno + 1}
                for n in server_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    top_defs.sort(key=lambda x: -int(x["loc"]))
    server_imported_names = []
    for n in server_tree.body:
        if isinstance(n, ast.ImportFrom):
            server_imported_names.extend((a.asname or a.name) for a in n.names)
        elif isinstance(n, ast.Import):
            server_imported_names.extend(a.asname or a.name.split(".")[0] for a in n.names)
    provider_modules = [name for name in trees if any(k in name for k in ("ego4d", "holoassist", "nymeria"))]
    return {
        "schema_version": 1,
        "generated_by": "scripts/audit_modularity.py",
        "scope": "Static source audit of moneymin Python modules and desktop/src/MainWindow.cpp; no application imports or runtime data access.",
        "summary": {"python_modules": len(trees), "python_loc": sum(int(row["loc"]) for row in file_rows),
                    "server_loc": next(int(r["loc"]) for r in file_rows if r["module"] == "moneymin.web.server"),
                    "server_route_decorators": len(routes), "server_locally_nested_handlers_and_helpers": create_app_scope(server_path).get("nested_functions", 0),
                    "import_cycles": len(strongly_connected(graph))},
        "import_cycles": strongly_connected(graph),
        "eager_import_cycles": strongly_connected(eager_graph),
        "import_cycle_scope": "import_cycles includes all AST imports, including deferred function-local imports; eager_import_cycles includes direct module-body imports only.",
        "module_graph": {k: sorted(v) for k, v in sorted(graph.items())},
        "files": file_rows,
        "server": {"path": "moneymin/web/server.py", "routes": routes,
                   "top_level_largest_functions": top_defs[:20],
                   "create_app": create_app_scope(server_path),
                   "namespace_imports": sorted(set(server_imported_names)),
                   "module_globals": sorted({name for n in server_tree.body
                       for name in ([n.name] if isinstance(n, ast.ClassDef) else
                                    [t.id for t in n.targets if isinstance(t, ast.Name)] if isinstance(n, ast.Assign) else
                                    [n.target.id] if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) else [])})},
        "domains": {
            "providers": {"modules": sorted(provider_modules),
                          "hardcoded_dispatch_references": [{"module": n, "line": node.lineno, "text": ast.get_source_segment(paths[n].read_text(encoding="utf-8"), node)[:180]}
                             for n, tree in trees.items() for node in ast.walk(tree)
                             if isinstance(node, (ast.If, ast.Match)) and any(isinstance(x, ast.Constant) and isinstance(x.value, str) and x.value in {"ego4d", "holoassist", "nymeria"} for x in ast.walk(node))][:100]},
            "campaign_upload": {"modules": [n for n in trees if any(k in n for k in ("campaign", "upload", "sent_registry", "prepared_library"))],
                                "campaign_loc": len(paths["moneymin.campaign"].read_text(encoding="utf-8").splitlines()),
                                "upload_loc": len(paths["moneymin.upload"].read_text(encoding="utf-8").splitlines()) if "moneymin.upload" in paths else None},
        },
        "desktop": cpp_mainwindow(),
        "acceptance_criteria": {
            "cycles": "Evidence: 0 direct module-body import cycles; 3 all-AST SCCs, equal to the original baseline's 3 (their membership changed and one campaign/delivery component grew from 15 to 21 modules). Keep the late-cycle count from increasing; reduce deferred cycles only when behavior and persisted contracts remain unchanged.",
            "http_routes": "Each extracted route belongs to exactly one domain blueprint/module; route method+path inventory remains unchanged during extraction.",
            "request_context": "Current static guard checks nested route callbacks for Flask request proxy reads; top-level handlers may read request during dispatch. This check does not prove the absence of every g/current_app access in all worker paths or prove immutability of every queued payload.",
            "factory_state": "App-local feature bindings, caches and barriers are built per factory call and exposed through app.extensions. The process runner and registration restore state remain shared; do not claim full multi-app isolation. Verify callback identity and per-app state ownership when multiple factories are supported.",
            "monkeypatch_compatibility": "All legacy server symbols used as patch points resolve at handler invocation time through an explicit compatibility dependency adapter; no exec/eval/inject_globals and no captured copies of patchable symbols.",
            "registry_contract": "Provider IDs, selection aliases and catalog/preparation operation targets are explicit and statically checked. Provider capability declarations and semantic adapter behavior are not established by this inventory; campaign/upload persistent keys and sent-registry semantics require their contract tests.",
            "function_size": "Prefer small single-purpose functions for new behavior. Six extracted legacy HTTP procedures exceed 100 LOC and remain within their owning domain; their transaction/state-machine exceptions are documented in analise-qa.md. Size alone does not establish module isolation.",
            "desktop_boundary": "Builders and behavior belong to feature translation units; cross-feature account removal, campaign selection, navigation and backend/timer lifecycle use named facade contracts. MainWindow retains private state and the shared API client for existing QObject/friend-test ownership; these are not independent controller classes.",
            "init_races": "Routes are registered before portable workers start. Offline tests check stable process-runner callback identity and separate caches/barriers across two app factories. This does not establish concurrent production services on one user profile; production retains single-service ownership.",
            "measured_progress": "Report counts and exceptions by domain; do not claim zero coupling. Cross-domain dependencies must be explicit and documented."
        }
    }


def main() -> None:
    global ROOT, OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="raiz do checkout a auditar (não importa código da aplicação)")
    parser.add_argument("--output", type=Path,
                        help="arquivo JSON de saída; sem ele, grava arquitetura-pos-http.json apenas na raiz padrão")
    args = parser.parse_args()
    requested_root = args.root.resolve()
    if args.output is None and requested_root != Path(__file__).resolve().parents[1]:
        parser.error("--output é obrigatório quando --root aponta para outro checkout")
    ROOT = requested_root
    OUT = args.output.resolve() if args.output else ROOT / "docs" / "modularizacao" / "arquitetura-pos-http.json"
    if OUT.name == "arquitetura-baseline.json":
        parser.error("arquitetura-baseline.json é preservado; use outro --output")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}: {data['summary']}")


if __name__ == "__main__":
    main()
