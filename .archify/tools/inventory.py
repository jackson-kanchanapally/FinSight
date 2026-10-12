#!/usr/bin/env python3
"""Deterministic Almonds architecture inventory. No application imports or secrets."""
from __future__ import annotations

import ast
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".archify" / "latest"
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".mjs"}
ROOTS = [
    ROOT / "backend" / "app",
    ROOT / "backend" / "alembic",
    ROOT / "backend" / "tests",
    ROOT / "frontend" / "src",
    ROOT / "frontend" / "scripts",
]


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def files() -> list[Path]:
    return sorted(path for base in ROOTS for path in base.rglob("*") if path.suffix in CODE_SUFFIXES)


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)
    return result.stdout.strip()


def module_for(path: Path) -> str:
    if path.suffix != ".py":
        return rel(path)
    base = ROOT / "backend"
    name = path.relative_to(base).with_suffix("").as_posix().replace("/", ".")
    return name.removesuffix(".__init__")


def dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = dotted(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    return None


def literal(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal(node.left, constants), literal(node.right, constants)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append("{dynamic}")
        return "".join(parts)
    return None


def python_inventory(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    module = module_for(path)
    imports, aliases, imported_names = [], {}, {}
    constants: dict[str, str] = {}
    router_prefix = ""
    definitions = []
    endpoints = []
    models = []

    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = literal(node.value, constants)
            if value is not None:
                constants[node.targets[0].id] = value
            if node.targets[0].id == "router" and isinstance(node.value, ast.Call):
                for keyword in node.value.keywords:
                    if keyword.arg == "prefix":
                        router_prefix = literal(keyword.value, constants) or ""
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
            for item in node.names:
                imports.append(f"{node.module}.{item.name}")
                imported_names[item.asname or item.name] = f"{node.module}.{item.name}"
        elif isinstance(node, ast.Import):
            for item in node.names:
                imports.append(item.name)
                aliases[item.asname or item.name.split(".")[0]] = item.name

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef):
            self.scope.append(node.name)
            table = None
            fks = []
            indexed = []
            for child in node.body:
                if isinstance(child, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in child.targets):
                    table = literal(child.value, constants)
                for call in ast.walk(child):
                    if isinstance(call, ast.Call) and dotted(call.func) in {"ForeignKey", "sqlalchemy.ForeignKey"} and call.args:
                        target = literal(call.args[0], constants)
                        if target:
                            fks.append(target)
                    if isinstance(call, ast.Call) and dotted(call.func) and dotted(call.func).endswith("mapped_column"):
                        if any(k.arg == "index" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords):
                            indexed.append(getattr(child, "target", ast.Name(id="unknown")).id if isinstance(getattr(child, "target", None), ast.Name) else "declared-column")
            if table:
                models.append({"class": node.name, "table": table, "foreign_keys": sorted(set(fks)), "indexed_fields": indexed, "line": node.lineno})
            self.generic_visit(node)
            self.scope.pop()

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
            qualname = ".".join([*self.scope, node.name])
            calls, side_effects = [], set()
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                name = dotted(child.func)
                if not name:
                    continue
                target = imported_names.get(name, aliases.get(name.split(".")[0], name))
                if target != name and "." in name and name.split(".")[0] in aliases:
                    target = aliases[name.split(".")[0]] + "." + ".".join(name.split(".")[1:])
                calls.append({"target": target, "line": child.lineno})
                if name in {"open", "Path.read_text", "Path.write_text", "Path.read_bytes", "Path.write_bytes"}:
                    side_effects.add("filesystem")
                if name.startswith("db.") or name in {"select", "delete", "update", "insert"}:
                    side_effects.add("database")
                if any(part in name.lower() for part in ("request", "urlopen", "httpx", "generate", "fetch")):
                    side_effects.add("external-or-model-call")
            definitions.append({
                "name": node.name,
                "qualname": f"{module}.{qualname}",
                "kind": "async-function" if isinstance(node, ast.AsyncFunctionDef) else ("method" if self.scope else "function"),
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "parameters": [arg.arg for arg in node.args.args],
                "calls": calls,
                "side_effects": sorted(side_effects),
            })
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                dec = dotted(decorator.func) or ""
                match = re.fullmatch(r"(?:router|app)\.(get|post|put|delete|patch)", dec)
                if match and decorator.args:
                    path_value = literal(decorator.args[0], constants)
                    if path_value is not None:
                        prefix = router_prefix if dec.startswith("router.") else ""
                        endpoints.append({"method": match.group(1).upper(), "path": prefix + path_value,
                                          "handler": f"{module}.{node.name}", "line": node.lineno})
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_FunctionDef = _function
        visit_AsyncFunctionDef = _function

    Visitor().visit(tree)
    project_imports = sorted(set(name for name in imports if name == "app" or name.startswith("app.")))
    return {"path": rel(path), "module": module, "language": "python", "lines": text.count("\n") + 1,
            "imports": project_imports, "definitions": definitions, "endpoints": endpoints, "models": models}


def resolve_frontend_import(path: Path, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    target = (path.parent / spec).resolve()
    for candidate in [target, *[Path(str(target) + suffix) for suffix in (".ts", ".tsx", ".mjs")], target / "index.ts", target / "index.tsx"]:
        if candidate.exists() and ROOT in candidate.parents:
            return rel(candidate)
    return None


def typescript_inventory(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    imports = []
    imported_symbols = set()
    for match in re.finditer(r'import\s+(?:type\s+)?(.+?)\s+from\s+["\'](.+?)["\']', text, re.S):
        target = resolve_frontend_import(path, match.group(2))
        if target:
            imports.append(target)
        imported_symbols.update(re.findall(r"\b[A-Z_a-z][A-Za-z0-9_]*\b", match.group(1)))
    definitions = []
    pattern = re.compile(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\((.*?)\)|(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=", re.S)
    for match in pattern.finditer(text):
        name = match.group(1) or match.group(3)
        line = text.count("\n", 0, match.start()) + 1
        definitions.append({"name": name, "qualname": f"{rel(path)}::{name}", "kind": "function-or-const", "line": line})
    calls = []
    for name in sorted(imported_symbols):
        if re.search(rf"\b{re.escape(name)}\s*\(", text):
            calls.append({"target": name, "relationship": "imported-symbol-call"})
        elif re.search(rf"<{re.escape(name)}\b", text):
            calls.append({"target": name, "relationship": "rendered-component"})
    endpoints = sorted(set(re.findall(r"[\"'`](/api/[A-Za-z0-9_?=&/${}.:-]+)", text)))
    return {"path": rel(path), "module": rel(path), "language": "typescript" if path.suffix != ".mjs" else "javascript",
            "lines": text.count("\n") + 1, "imports": sorted(set(imports)), "definitions": definitions,
            "calls": calls, "frontend_api_paths": endpoints}


def main() -> None:
    records = [python_inventory(path) if path.suffix == ".py" else typescript_inventory(path) for path in files()]
    module_to_path = {row["module"]: row["path"] for row in records}
    dependency_edges = []
    for row in records:
        for imported in row["imports"]:
            target = module_to_path.get(imported, imported if imported.startswith("frontend/") else None)
            if target:
                dependency_edges.append({"from": row["path"], "to": target, "kind": "direct-import"})
    dependency_edges = [dict(values) for values in {tuple(sorted(edge.items())) for edge in dependency_edges}]
    dependency_edges.sort(key=lambda edge: (edge["from"], edge["to"]))
    endpoints = [endpoint | {"file": row["path"]} for row in records for endpoint in row.get("endpoints", [])]
    models = [model | {"file": row["path"]} for row in records for model in row.get("models", [])]
    functions = [definition | {"file": row["path"]} for row in records for definition in row.get("definitions", [])]
    call_edges = []
    for function in functions:
        for call in function.get("calls", []):
            call_edges.append({"from": function["qualname"], "to": call["target"], "line": call["line"], "kind": "static-call-expression"})
    incoming = Counter(edge["to"] for edge in dependency_edges)
    outgoing = Counter(edge["from"] for edge in dependency_edges)
    revision = git("rev-parse", "HEAD")
    graph = defaultdict(set)
    for edge in dependency_edges:
        graph[edge["from"]].add(edge["to"])
    index = 0
    stack: list[str] = []
    indexes: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def strong(node: str) -> None:
        nonlocal index
        indexes[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph[node]:
            if target not in indexes:
                strong(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], indexes[target])
        if low[node] == indexes[node]:
            component = []
            while True:
                current = stack.pop()
                on_stack.remove(current)
                component.append(current)
                if current == node:
                    break
            if len(component) > 1:
                components.append(sorted(component))

    for node in sorted(set(graph) | {target for targets in graph.values() for target in targets}):
        if node not in indexes:
            strong(node)

    inventory = {
        "repository_revision": revision,
        "generated_from": [rel(base) for base in ROOTS],
        "counts": {"source_files": len(records),
                   "application_source_files": sum(row["path"].startswith(("backend/app/", "frontend/src/")) for row in records),
                   "functions_methods": len(functions), "api_endpoints": len(endpoints),
                   "database_models": len(models), "file_import_edges": len(dependency_edges), "call_expressions": len(call_edges)},
        "files": records,
        "high_connectivity": [{"path": path, "incoming": incoming[path], "outgoing": outgoing[path], "total": incoming[path] + outgoing[path]}
                              for path in sorted(set(incoming) | set(outgoing), key=lambda p: (-(incoming[p] + outgoing[p]), p))[:30]],
        "largest_files": sorted(({"path": row["path"], "lines": row["lines"]} for row in records), key=lambda row: (-row["lines"], row["path"]))[:30],
        "static_import_cycles": sorted(components, key=lambda group: (-len(group), group)),
        "limitations": [
            "Python calls through dependency injection, protocols, SQLAlchemy events, and dynamic dispatch are not resolved.",
            "TypeScript call mapping records imported symbol calls and rendered components; closures and property calls remain dynamic.",
            "API path matching preserves dynamic placeholders and is supplemented by the hand-verified architecture report.",
        ],
    }
    outputs = {
        "inventory.json": inventory,
        "file-dependencies.json": {"revision": revision, "edges": dependency_edges},
        "function-calls.json": {"revision": revision, "functions": functions, "edges": call_edges},
        "api-map.json": {"revision": revision, "backend_endpoints": endpoints,
                         "frontend_api_references": [{"file": row["path"], "paths": row.get("frontend_api_paths", [])}
                                                     for row in records if row.get("frontend_api_paths")]},
        "database-map.json": {"revision": revision, "models": models},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (OUT / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(inventory["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
