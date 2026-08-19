#!/usr/bin/env python3
"""
mb_sync.py — Sync incremental do índice Metabase + geração de artefatos

Uso:
  python3 mb_sync.py              # sync incremental
  python3 mb_sync.py --rebuild    # rebuild completo do índice
  python3 mb_sync.py --quiet      # sem output se nada mudou
  python3 mb_sync.py --generate   # sync + gera HTML e .md a partir do slim
  python3 mb_sync.py --full       # rebuild + generate (pipeline completo)
"""

import json
import os
import sys
import subprocess
import re
import unicodedata
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(SCRIPT_DIR, "..")
HUB_DIR = os.path.join(PLUGIN_DIR, "..", "..")
MEMORY_DIR = os.path.join(HUB_DIR, "memory")
SLIM_PATH = os.path.join(MEMORY_DIR, "mapa_metabase_slim.json")
BOOT_PATH = os.path.join(MEMORY_DIR, "metabase-boot.md")
TABLES_CACHE = os.path.join(MEMORY_DIR, ".mb_tables_cache.json")
CHANGELOG_PATH = os.path.join(MEMORY_DIR, "mapa_changelog.json")
OUTPUT_MD = os.path.join(MEMORY_DIR, "mapa_metabase.md")
OUTPUT_HTML = os.path.join(MEMORY_DIR, "mapa_metabase.html")

BASE_URL = "https://metabase.grupovelas.com.br/api"
MB_BASE = "https://metabase.grupovelas.com.br"


def get_token():
    with open(BOOT_PATH) as f:
        for line in f:
            if line.strip().startswith("MB_TOKEN="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("Token não encontrado em metabase-boot.md")


def api_get(path, token, jq_filter=None):
    cmd = [
        "curl", "-s", f"{BASE_URL}/{path}",
        "-H", f"X-Metabase-Session: {token}"
    ]
    if jq_filter:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        jq = subprocess.run(
            ["jq", "-c", jq_filter],
            input=proc.stdout, capture_output=True, text=True
        )
        return json.loads(jq.stdout)
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return json.loads(proc.stdout)


def load_slim():
    if os.path.exists(SLIM_PATH):
        with open(SLIM_PATH) as f:
            return json.load(f)
    return {"synced_at": None, "dashboards": {}, "table_index": {}}


def save_slim(data):
    data["synced_at"] = datetime.now(timezone.utc).isoformat()
    with open(SLIM_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def rebuild_table_index(dashboards):
    idx = defaultdict(list)
    for did, d in dashboards.items():
        for t in d.get("tables", []):
            if int(did) not in idx[t]:
                idx[t].append(int(did))
    for k in idx:
        idx[k].sort()
    return dict(idx)


def get_real_tables(token):
    if os.path.exists(TABLES_CACHE):
        with open(TABLES_CACHE) as f:
            return set(json.load(f))
    tables = api_get("table", token, '[.[] | select(.db_id == 2) | .name]')
    with open(TABLES_CACHE, "w") as f:
        json.dump(tables, f)
    return set(tables)


def fetch_dash_slim(dash_id, token, real_tables=None):
    jq = (
        '{'
        'name: .name, '
        'updated_at: .updated_at, '
        'collection: (.collection.name // null), '
        'tabs: [.tabs[]? | .name], '
        'tables: ([.dashcards[]? | select(.card != null) | .card | '
        'select(.table != null) | .table.name] | unique), '
        'sql_tables: ([.dashcards[]? | select(.card != null) | .card | '
        'select(.dataset_query.type == "native") | '
        '.dataset_query.native.query // "" | '
        '[scan("(?:FROM|JOIN)\\\\s+(\\\\w+)"; "i")] | .[][0]] | unique), '
        'card_count: ([.dashcards[]? | .card_id // empty] | length)'
        '}'
    )
    try:
        data = api_get(f"dashboard/{dash_id}", token, jq)
        all_tables = set(
            (data.get("tables") or []) + (data.get("sql_tables") or [])
        )
        if real_tables:
            all_tables = all_tables & real_tables
        data["tables"] = sorted(all_tables)
        data.pop("sql_tables", None)
        return data
    except Exception:
        return None


def compute_changelog(old_slim, new_slim):
    """Compara dois slims e retorna mudancas."""
    old_d = old_slim.get("dashboards", {})
    new_d = new_slim.get("dashboards", {})
    old_t = set(old_slim.get("table_index", {}).keys())
    new_t = set(new_slim.get("table_index", {}).keys())

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dashboards_added": sorted(set(new_d) - set(old_d)),
        "dashboards_removed": sorted(set(old_d) - set(new_d)),
        "dashboards_updated": sorted(
            did for did in set(old_d) & set(new_d)
            if old_d[did].get("updated_at") != new_d[did].get("updated_at")
        ),
        "tables_added": sorted(new_t - old_t),
        "tables_removed": sorted(old_t - new_t),
    }


def save_changelog(entry):
    """Append ao historico de changelog."""
    history = []
    if os.path.exists(CHANGELOG_PATH):
        with open(CHANGELOG_PATH) as f:
            history = json.load(f)
    history.append(entry)
    # Manter ultimos 100 registros
    history = history[-100:]
    with open(CHANGELOG_PATH, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def fetch_audit_data(dashboards, token):
    """Fetch revision history + GitHub issues for audit page."""
    revisions = []
    users_cache = {}
    # Prioritize recent dashboards so author shows in landing
    by_updated = sorted(dashboards.keys(),
                        key=lambda k: dashboards[k].get("updated_at", ""), reverse=True)
    for dash_id in by_updated[:60]:
        try:
            revs = api_get(f"revision?entity=dashboard&id={dash_id}", token)
            if isinstance(revs, list):
                for r in revs[:10]:
                    user = r.get("user", {})
                    uid = user.get("id")
                    name = user.get("common_name") or user.get("first_name", "?")
                    if uid:
                        users_cache[uid] = name
                    revisions.append({
                        "dashboard_id": dash_id,
                        "dashboard_name": dashboards[dash_id].get("name", "?"),
                        "timestamp": r.get("timestamp", ""),
                        "user": name,
                        "description": r.get("description", "?"),
                    })
        except Exception:
            pass

    revisions.sort(key=lambda x: x["timestamp"], reverse=True)
    revisions = revisions[:100]

    issues = []
    all_issues = []
    try:
        r = subprocess.run(
            ["gh", "issue", "list", "--repo", "Grupo-Velas/produtividade-bi-dev",
             "--state", "all",
             "--json", "number,title,state,updatedAt,author,labels,assignees",
             "--limit", "50"],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            all_issues = json.loads(r.stdout)
            issues = [i for i in all_issues if "[metabase]" in i["title"].lower()]
    except Exception:
        pass

    # Tasks: issues related to dashboards/metabase/BI work
    bi_keywords = ["metabase", "dashboard", "dash ", "aba ", "card ", "filtro",
                   "kpi", "grafico", "tabela", "nps", "lead score", "trafego",
                   "tráfego", "dre", "franquia", "relatório", "relatorio",
                   "performance", "criativos"]
    tasks = []
    for i in all_issues:
        t = unicodedata.normalize("NFD", i["title"].lower())
        t = re.sub(r"[̀-ͯ]", "", t)
        if any(k in t for k in bi_keywords):
            author = i.get("author", {}).get("login", "?")
            assignees = [a.get("login", "") for a in i.get("assignees", [])]
            tasks.append({
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "updated": i.get("updatedAt", "")[:10],
                "author": author,
                "assignees": assignees,
            })

    return {"revisions": revisions, "issues": issues, "tasks": tasks, "users": users_cache}


def classify_table(name):
    if name.startswith("mv_"):
        return "mv", "Materialized View"
    if name.startswith("tb_") or name.startswith("fat_"):
        return "tb", "Tabela Base"
    if name.startswith("dim_"):
        return "dim", "Dimensão"
    if name.startswith("vw_"):
        return "vw", "View"
    return "other", "Outro"


def group_dashboards(dashboards):
    """Agrupa dashboards por collection."""
    groups = defaultdict(list)
    for did, d in sorted(dashboards.items(), key=lambda x: x[1].get("name", "")):
        col = d.get("collection") or "Sem Collection"
        groups[col].append((did, d))
    return dict(sorted(groups.items()))


def generate_md(slim):
    """Gera mapa_metabase.md a partir do slim JSON."""
    dashboards = slim["dashboards"]
    table_index = slim["table_index"]
    now = slim.get("synced_at", "?")[:10]

    lines = [
        f"# Mapa do Metabase — Grupo Velas",
        f"",
        f"Gerado automaticamente em {now} por `mb_sync.py --generate`",
        f"",
        f"{len(dashboards)} dashboards · {len(table_index)} tabelas",
        f"",
        f"---",
        f"",
        f"## Tabelas",
        f"",
        f"| Tabela | Tipo | Dashboards |",
        f"|---|---|---|",
    ]

    for tbl in sorted(table_index.keys()):
        cls_key, cls_label = classify_table(tbl)
        dash_ids = table_index[tbl]
        count = len(dash_ids)
        lines.append(f"| `{tbl}` | {cls_label} | {count} |")

    lines += ["", "---", "", "## Dashboards por Collection", ""]

    groups = group_dashboards(dashboards)
    for col, items in groups.items():
        lines.append(f"### {col} ({len(items)} dashboards)")
        lines.append("")
        lines.append("| ID | Nome | Abas | Cards | Tabelas |")
        lines.append("|---|---|---|---|---|")
        for did, d in items:
            name = d.get("name", "?")
            tabs = len(d.get("tabs", []))
            cards = d.get("card_count", 0)
            tables = ", ".join(f"`{t}`" for t in d.get("tables", []))
            lines.append(f"| {did} | {name} | {tabs} | {cards} | {tables} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## Índice Reverso (Tabela → Dashboards)",
        "",
    ]

    for tbl in sorted(table_index.keys()):
        dash_ids = table_index[tbl]
        lines.append(f"### `{tbl}`")
        lines.append("")
        for did in dash_ids:
            d = dashboards.get(str(did), {})
            name = d.get("name", "?")
            lines.append(f"- [{name}]({MB_BASE}/dashboard/{did}) (ID {did})")
        lines.append("")

    with open(OUTPUT_MD, "w") as f:
        f.write("\n".join(lines))
    return OUTPUT_MD


def generate_html(slim):
    """Gera mapa_metabase.html interativo — landing-first com visao imediata."""
    dashboards = slim["dashboards"]
    table_index = slim["table_index"]
    now = slim.get("synced_at", "?")[:10]
    groups = group_dashboards(dashboards)

    # Pre-compute landing data in Python
    total_cards = sum(d.get("card_count", 0) for d in dashboards.values())

    # Full table catalog from cache
    cache_path = os.path.join(MEMORY_DIR, ".mb_tables_cache.json")
    all_tables = []
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            all_tables = sorted(set(json.load(f)))

    # Count by raw prefix for stats display
    from collections import Counter
    tbl_prefix_counts = Counter()
    for t in all_tables:
        for pfx in ("mv_", "dim_", "fat_", "vw_", "tb_"):
            if t.startswith(pfx):
                tbl_prefix_counts[pfx.rstrip("_")] += 1
                break
        else:
            tbl_prefix_counts["outras"] += 1

    # Audit data (revisions + GitHub issues) — must run before recent to populate author
    try:
        token = get_token()
        audit = fetch_audit_data(dashboards, token)
    except Exception:
        audit = {"revisions": [], "issues": [], "users": {}}
    audit_json = json.dumps(audit, ensure_ascii=False)

    # Recent dashboards (top 8 by updated_at)
    recent = sorted(dashboards.items(), key=lambda x: x[1].get("updated_at", ""), reverse=True)[:8]
    # Build last-author lookup from audit revisions
    last_author = {}
    for rev in audit.get("revisions", []):
        did = str(rev["dashboard_id"])
        if did not in last_author:
            last_author[did] = rev["user"]
    recent_json = json.dumps([{"id": did, "name": d["name"], "updated": d.get("updated_at", "")[:10],
                                "cards": d.get("card_count", 0), "col": d.get("collection", ""),
                                "author": last_author.get(str(did), "")}
                               for did, d in recent], ensure_ascii=False)

    # Top tables by dashboard count (top 10) — from table_index (referenced in SQL)
    top_tables = sorted(table_index.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    max_tbl = len(top_tables[0][1]) if top_tables else 1
    top_tables_json = json.dumps([{"name": t, "count": len(ids), "pct": round(len(ids) / max_tbl * 100)}
                                   for t, ids in top_tables], ensure_ascii=False)

    # Include which tables are referenced in dashboards
    referenced = set(table_index.keys())
    catalog_json = json.dumps([
        {"name": t, "type": classify_table(t)[0], "used": t in referenced,
         "dashboards": len(table_index.get(t, []))}
        for t in all_tables
    ], ensure_ascii=False)

    # Collections distribution
    col_counts = Counter(d.get("collection", "Sem colecao") for d in dashboards.values())
    cols_sorted = col_counts.most_common(12)
    max_col = cols_sorted[0][1] if cols_sorted else 1
    cols_json = json.dumps([{"name": c, "count": n, "pct": round(n / max_col * 100)}
                             for c, n in cols_sorted], ensure_ascii=False)

    data_json = json.dumps({
        "dashboards": dashboards,
        "table_index": table_index,
        "groups": {col: [(did, d) for did, d in items] for col, items in groups.items()},
    }, ensure_ascii=False)

    html = f"""<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mapa do Metabase — Grupo Velas</title>
<style>
:root {{
  --bg: #0F172A; --primary: #1E293B; --secondary: #334155;
  --accent: #22C55E; --on-accent: #0F172A;
  --fg: #F8FAFC; --card: #1B2336; --card-fg: #F8FAFC;
  --muted: #272F42; --muted-fg: #94A3B8;
  --border: rgba(71,85,105,.4); --ring: rgba(34,197,94,.35);
  --mv: #22C55E; --mv-bg: rgba(34,197,94,.12);
  --tb: #F59E0B; --tb-bg: rgba(245,158,11,.12);
  --dim: #A78BFA; --dim-bg: rgba(167,139,250,.12);
  --vw: #38BDF8; --vw-bg: rgba(56,189,248,.12);
  --ot: #64748B; --ot-bg: rgba(100,116,139,.1);
  --radius: 8px; --radius-lg: 12px;
  --space-1: 4px; --space-2: 8px; --space-3: 12px;
  --space-4: 16px; --space-5: 24px; --space-6: 32px;
  --font-body: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-mono: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace;
  --text-xs: 11px; --text-sm: 13px; --text-base: 14px;
  --text-lg: 16px; --text-xl: 20px; --text-2xl: 28px; --text-3xl: 36px;
}}
@media (prefers-color-scheme: light) {{
  :root {{
    --bg: #F8FAFC; --primary: #E2E8F0; --fg: #0F172A;
    --card: #FFFFFF; --card-fg: #1E293B; --muted: #F1F5F9;
    --muted-fg: #64748B; --border: rgba(30,41,59,.12);
    --mv: #16A34A; --mv-bg: rgba(22,163,74,.08);
    --tb: #D97706; --tb-bg: rgba(217,119,6,.08);
    --dim: #7C3AED; --dim-bg: rgba(124,58,237,.08);
    --vw: #0284C7; --vw-bg: rgba(2,132,199,.08);
    --ot: #475569; --ot-bg: rgba(71,85,105,.06);
    --secondary: #CBD5E1; --accent: #16A34A; --on-accent: #FFF;
  }}
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  min-height: 100vh; background: var(--bg); color: var(--fg);
  font-family: var(--font-body); font-size: var(--text-base);
  line-height: 1.5; -webkit-font-smoothing: antialiased;
}}
a {{ color: inherit; text-decoration: none; }}
.wrap {{ max-width: 1280px; margin: 0 auto; padding: var(--space-6) var(--space-5); }}

/* Header */
.header {{ display: flex; align-items: baseline; justify-content: space-between;
  flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-5); }}
.header h1 {{ font-size: var(--text-2xl); font-weight: 700; letter-spacing: -0.02em; }}
.header h1 svg {{ vertical-align: -3px; margin-right: var(--space-2); }}
.subtitle {{ color: var(--muted-fg); font-size: var(--text-sm); }}

/* Stats row */
.stats {{ display: grid; grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3); margin-bottom: var(--space-5); }}
.stat {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: var(--space-4);
}}
.stat-value {{
  font-family: var(--font-mono); font-size: var(--text-3xl);
  font-weight: 700; color: var(--accent); display: block;
  font-variant-numeric: tabular-nums; line-height: 1.1;
}}
.stat-label {{ font-size: var(--text-xs); color: var(--muted-fg);
  text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2px; }}
.stat-sub {{ font-size: var(--text-xs); color: var(--muted-fg); margin-top: var(--space-1); }}

/* Landing panels */
.landing {{ display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4);
  margin-bottom: var(--space-6); }}
.panel {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: var(--space-4);
  min-width: 0;
}}
.panel-title {{
  font-size: var(--text-sm); font-weight: 600; color: var(--muted-fg);
  text-transform: uppercase; letter-spacing: 0.04em;
  margin-bottom: var(--space-3); display: flex; align-items: center; gap: var(--space-2);
}}
.panel-title svg {{ opacity: .6; }}
.panel.full {{ grid-column: 1 / -1; }}

/* Recent list */
.recent-item {{
  display: flex; align-items: center; gap: var(--space-3);
  padding: 6px 0; border-bottom: 1px solid var(--border);
  transition: background 150ms; cursor: pointer;
}}
.recent-item:last-child {{ border-bottom: none; }}
.recent-item:hover {{ background: var(--muted); margin: 0 -8px; padding: 6px 8px;
  border-radius: var(--radius); }}
.recent-name {{ flex: 1; font-size: var(--text-sm); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }}
.recent-meta {{ font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--muted-fg); white-space: nowrap; }}
.recent-author {{ font-size: var(--text-xs); color: var(--accent); white-space: nowrap; font-weight: 500; }}
.task-list {{ max-height: 420px; overflow-y: auto; }}
.task-item {{ display: flex; align-items: center; gap: 8px; padding: 6px 0;
  border-bottom: 1px solid var(--border); font-size: var(--text-sm); text-decoration: none; color: var(--fg); }}
.task-item:last-child {{ border-bottom: none; }}
.task-item:hover {{ background: var(--muted); margin: 0 -8px; padding: 6px 8px; border-radius: 6px; }}
.task-num {{ font-family: var(--font-mono); font-size: var(--text-xs); color: var(--muted-fg); min-width: 32px; }}
.task-title {{ flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.task-state {{ font-size: 10px; padding: 1px 6px; border-radius: 10px; font-weight: 600; white-space: nowrap; }}
.task-state.open {{ background: rgba(34,197,94,.15); color: #22c55e; }}
.task-state.closed {{ background: rgba(139,92,246,.12); color: #a78bfa; }}
.task-author {{ font-size: var(--text-xs); color: var(--muted-fg); white-space: nowrap; }}
.task-date {{ font-size: var(--text-xs); color: var(--muted-fg); white-space: nowrap; font-family: var(--font-mono); }}
.recent-date {{ font-size: var(--text-xs); color: var(--muted-fg); white-space: nowrap; }}
.recent-col {{ font-size: var(--text-xs); color: var(--muted-fg);
  background: var(--muted); padding: 1px 6px; border-radius: 4px; white-space: nowrap;
  max-width: 120px; overflow: hidden; text-overflow: ellipsis; }}

/* Bar rows (tables + collections) */
.bar-row {{
  display: flex; align-items: center; gap: var(--space-3);
  padding: 5px 0; cursor: pointer; transition: background 150ms;
}}
.bar-row:hover {{ background: var(--muted); margin: 0 -8px; padding: 5px 8px;
  border-radius: var(--radius); }}
.bar-label {{ font-size: var(--text-sm); min-width: 0; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }}
.bar-label.mono {{ font-family: var(--font-mono); font-size: var(--text-xs); }}
.bar-track {{ flex: 1; height: 6px; background: var(--muted); border-radius: 3px;
  overflow: hidden; min-width: 40px; }}
.bar-fill {{ height: 100%; border-radius: 3px; background: var(--accent);
  transition: width 300ms ease; }}
.bar-count {{ font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--muted-fg); min-width: 20px; text-align: right; }}

/* Section divider */
.section {{ margin-bottom: var(--space-4); }}
.section-header {{
  display: flex; align-items: center; gap: var(--space-3);
  margin-bottom: var(--space-3); padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--border);
}}
.section-title {{ font-size: var(--text-lg); font-weight: 600; }}
.section-line {{ flex: 1; }}

/* Search bar */
.search-bar {{ position: relative; margin-bottom: var(--space-4); }}
.search {{
  width: 100%; padding: 10px var(--space-4) 10px 40px;
  border-radius: var(--radius-lg); border: 1px solid var(--border);
  background: var(--card); color: var(--fg); font-family: var(--font-body);
  font-size: var(--text-base); outline: none; transition: border-color 200ms, box-shadow 200ms;
}}
.search:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--ring); }}
.search-icon {{
  position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
  color: var(--muted-fg); pointer-events: none;
}}
.result-count {{ position: absolute; right: 12px; top: 50%; transform: translateY(-50%);
  font-size: var(--text-xs); color: var(--muted-fg); font-family: var(--font-mono); }}

/* Tabs */
.tabs-bar {{ display: flex; gap: 2px; background: var(--muted); border-radius: var(--radius);
  padding: 2px; margin-bottom: var(--space-4); display: inline-flex; }}
.tab {{
  padding: var(--space-2) var(--space-5); border-radius: 6px; border: none;
  background: transparent; color: var(--muted-fg); cursor: pointer;
  font-family: var(--font-body); font-size: var(--text-sm); font-weight: 500;
  transition: all 200ms;
}}
.tab:hover {{ color: var(--fg); }}
.tab:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
.tab.active {{ background: var(--card); color: var(--fg);
  box-shadow: 0 1px 3px rgba(0,0,0,.2); }}

/* Views */
.view {{ display: none; }}
.view.active {{ display: block; }}

/* Groups */
.group {{ margin-bottom: var(--space-3); }}
.group-toggle {{
  display: flex; align-items: center; gap: var(--space-2); width: 100%;
  padding: var(--space-2) var(--space-3); background: var(--muted);
  border: 1px solid var(--border); border-radius: var(--radius);
  color: var(--fg); font-size: var(--text-sm); font-weight: 600;
  font-family: var(--font-body); cursor: pointer; transition: border-color 200ms;
}}
.group-toggle:hover {{ border-color: var(--accent); }}
.group-toggle:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
.group-toggle svg {{ transition: transform 200ms; }}
.group.open .group-toggle svg {{ transform: rotate(90deg); }}
.group-count {{
  font-family: var(--font-mono); font-size: var(--text-xs);
  background: var(--card); padding: 1px 6px; border-radius: 4px; color: var(--muted-fg);
}}
.group-body {{ display: none; padding: var(--space-1) 0 0 0; }}
.group.open .group-body {{ display: block; }}

/* Dashboard rows */
.dash-row {{
  display: grid; grid-template-columns: 48px 1fr auto auto;
  gap: var(--space-2); align-items: center;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--border); transition: background 150ms;
}}
.dash-row:hover {{ background: var(--muted); border-radius: var(--radius); }}
.dash-id {{ font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--muted-fg); text-align: right; }}
.dash-name a {{ color: var(--card-fg); text-decoration: none; transition: color 200ms; }}
.dash-name a:hover {{ color: var(--accent); }}
.dash-name a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }}
.dash-cards {{ font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--muted-fg); white-space: nowrap; }}
.tags {{ display: flex; flex-wrap: wrap; gap: 2px; }}
.tag {{
  display: inline-block; padding: 1px 6px; border-radius: 4px;
  font-family: var(--font-mono); font-size: var(--text-xs);
  font-weight: 500; white-space: nowrap;
}}
.tag-mv {{ color: var(--mv); background: var(--mv-bg); }}
.tag-tb {{ color: var(--tb); background: var(--tb-bg); }}
.tag-dim {{ color: var(--dim); background: var(--dim-bg); }}
.tag-vw {{ color: var(--vw); background: var(--vw-bg); }}
.tag-ot {{ color: var(--ot); background: var(--ot-bg); }}

/* Table cards */
.tbl-card {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-2); cursor: pointer; transition: border-color 200ms;
}}
.tbl-card:hover {{ border-color: var(--accent); }}
.tbl-card:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
.tbl-header {{ display: flex; align-items: center; gap: var(--space-2); }}
.tbl-header svg {{ color: var(--muted-fg); transition: transform 200ms; }}
.tbl-card.open .tbl-header svg {{ transform: rotate(90deg); }}
.tbl-count {{ margin-left: auto; font-family: var(--font-mono);
  font-size: var(--text-xs); color: var(--muted-fg); }}
.tbl-drilldown {{ display: none; padding: var(--space-2) 0 0 var(--space-4); }}
.tbl-card.open .tbl-drilldown {{ display: block; }}
.tbl-dash-link {{ display: block; padding: 3px 0; color: var(--muted-fg);
  text-decoration: none; font-size: var(--text-sm); transition: color 200ms; }}
.tbl-dash-link:hover {{ color: var(--accent); }}
.tbl-dash-link:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

.empty {{ color: var(--muted-fg); padding: var(--space-6); text-align: center;
  font-size: var(--text-sm); }}

/* Responsive */
@media (max-width: 900px) {{
  .landing {{ grid-template-columns: 1fr; }}
}}
@media (max-width: 768px) {{
  .dash-row {{ grid-template-columns: 40px 1fr; gap: var(--space-1); }}
  .dash-cards, .tags {{ grid-column: 1 / -1; }}
  .stats {{ grid-template-columns: repeat(3, 1fr); }}
}}
@media (max-width: 480px) {{
  .wrap {{ padding: var(--space-4) var(--space-3); }}
  .stats {{ grid-template-columns: 1fr; }}
}}
/* Scrollbar */
#catalog::-webkit-scrollbar {{ width: 6px; }}
#catalog::-webkit-scrollbar-track {{ background: transparent; }}
#catalog::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
#catalog::-webkit-scrollbar-thumb:hover {{ background: var(--muted-fg); }}
/* Audit feed */
.audit-feed {{ max-height: 520px; overflow-y: auto; }}
.audit-item {{
  display: grid; grid-template-columns: 44px 1fr auto;
  gap: 0 var(--space-3); padding: var(--space-3) 0;
  border-bottom: 1px solid var(--border); align-items: start;
}}
.audit-item:last-child {{ border-bottom: none; }}
.audit-avatar {{
  width: 36px; height: 36px; border-radius: 50%;
  background: var(--muted); display: flex; align-items: center;
  justify-content: center; font-weight: 600; font-size: var(--text-sm);
  color: var(--accent); text-transform: uppercase; flex-shrink: 0;
}}
.audit-body {{ min-width: 0; }}
.audit-user {{ font-weight: 600; font-size: var(--text-sm); }}
.audit-desc {{ font-size: var(--text-sm); color: var(--muted-fg); margin-top: 1px; }}
.audit-desc .audit-target {{ color: var(--fg); font-weight: 500; }}
.audit-time {{ font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--muted-fg); white-space: nowrap; padding-top: 2px; }}

/* Issue cards */
.issue-card {{
  background: var(--muted); border-radius: var(--radius);
  padding: var(--space-3) var(--space-4); margin-bottom: var(--space-2);
  display: flex; align-items: center; gap: var(--space-3);
  transition: background 150ms; cursor: pointer;
}}
.issue-card:hover {{ background: var(--secondary); }}
.issue-icon {{
  width: 20px; height: 20px; flex-shrink: 0; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
}}
.issue-icon.open {{ background: rgba(34,197,94,.2); color: var(--accent); }}
.issue-icon.closed {{ background: rgba(139,92,246,.2); color: #A78BFA; }}
.issue-num {{ font-family: var(--font-mono); font-size: var(--text-xs);
  color: var(--muted-fg); min-width: 32px; }}
.issue-title {{ font-size: var(--text-sm); flex: 1; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }}
.issue-state {{
  font-size: var(--text-xs); padding: 2px 8px; border-radius: 99px;
  font-weight: 500; text-transform: uppercase; letter-spacing: 0.04em;
}}
.issue-state.open {{ background: rgba(34,197,94,.15); color: var(--accent); }}
.issue-state.closed {{ background: rgba(139,92,246,.15); color: #A78BFA; }}

/* Audit filter tabs */
.audit-tabs {{
  display: flex; gap: var(--space-2); margin-bottom: var(--space-3);
  border-bottom: 1px solid var(--border); padding-bottom: var(--space-2);
}}
.audit-tab {{
  background: none; border: none; color: var(--muted-fg);
  font-size: var(--text-sm); font-family: var(--font-body);
  padding: var(--space-1) var(--space-3); border-radius: var(--radius);
  cursor: pointer; transition: all 150ms;
}}
.audit-tab:hover {{ background: var(--muted); color: var(--fg); }}
.audit-tab.active {{ background: rgba(34,197,94,.12); color: var(--accent); font-weight: 600; }}
.audit-tab .count {{
  font-family: var(--font-mono); font-size: var(--text-xs);
  background: var(--muted); padding: 0 5px; border-radius: 4px;
  margin-left: 4px;
}}
.audit-tab.active .count {{ background: rgba(34,197,94,.2); }}

/* Search in audit */
.audit-search {{
  width: 100%; padding: 8px var(--space-3); margin-bottom: var(--space-3);
  border-radius: var(--radius); border: 1px solid var(--border);
  background: var(--card); color: var(--fg); font-family: var(--font-body);
  font-size: var(--text-sm); outline: none; transition: border-color 200ms;
}}
.audit-search:focus {{ border-color: var(--accent); }}

@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ transition-duration: 0ms !important; }}
}}
</style>

<div class="wrap">
  <div class="header">
    <div>
      <h1>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
        Mapa do Metabase
      </h1>
      <p class="subtitle">Grupo Velas &mdash; {now}</p>
    </div>
  </div>

  <div class="stats">
    <div class="stat">
      <span class="stat-value">{len(dashboards)}</span>
      <span class="stat-label">Dashboards</span>
      <span class="stat-sub">em {len(col_counts)} colecoes</span>
    </div>
    <div class="stat">
      <span class="stat-value">{len(all_tables)}</span>
      <span class="stat-label">Tabelas no banco</span>
      <span class="stat-sub">{tbl_prefix_counts.get("mv",0)} mv &middot; {tbl_prefix_counts.get("dim",0)} dim &middot; {tbl_prefix_counts.get("fat",0)} fat &middot; {tbl_prefix_counts.get("vw",0)} vw &middot; {tbl_prefix_counts.get("tb",0)} tb</span>
    </div>
    <div class="stat">
      <span class="stat-value">{total_cards:,}</span>
      <span class="stat-label">Cards</span>
      <span class="stat-sub">{len(table_index)} tabelas usadas em SQL</span>
    </div>
  </div>

  <!-- Landing panels -->
  <div class="landing">
    <div class="panel">
      <div class="panel-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        Atualizados recentemente
      </div>
      <div id="recent-list"></div>
    </div>
    <div class="panel">
      <div class="panel-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
        Tabelas mais conectadas
      </div>
      <div id="top-tables"></div>
    </div>
    <div class="panel full">
      <div class="panel-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
        Tasks GitHub
        <span style="font-size:var(--text-xs);font-weight:400;color:var(--muted-fg);text-transform:none;letter-spacing:0" id="tasks-subtitle"></span>
      </div>
      <div style="display:flex;gap:var(--space-2);margin-bottom:var(--space-3);flex-wrap:wrap" id="tasks-filters"></div>
      <div class="task-list" id="tasks-list"></div>
    </div>
    <div class="panel full">
      <div class="panel-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        Dashboards por area
      </div>
      <div id="col-dist" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:4px 24px"></div>
    </div>
    <div class="panel full">
      <div class="panel-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/></svg>
        Catalogo de tabelas
        <span style="font-size:var(--text-xs);font-weight:400;color:var(--muted-fg);text-transform:none;letter-spacing:0">
          &mdash; {len(all_tables)} tabelas &middot; verdes = usadas em dashboards
        </span>
      </div>
      <div style="display:flex;gap:var(--space-2);margin-bottom:var(--space-3);flex-wrap:wrap" id="cat-filters"></div>
      <div id="catalog" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:2px 16px;max-height:400px;overflow-y:auto"></div>
    </div>
  </div>

  <!-- Audit / Activity -->
  <div class="section">
    <div class="section-header">
      <span class="section-title">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-3px;margin-right:6px"><path d="M12 20v-6M6 20V10M18 20V4"/></svg>
        Auditoria
      </span>
      <span class="section-line"></span>
    </div>
  </div>

  <div class="landing" style="margin-bottom:var(--space-5)">
    <div class="panel">
      <div class="panel-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l2 2"/></svg>
        Atividade recente
      </div>
      <input class="audit-search" id="audit-search" placeholder="Filtrar por usuario ou dashboard..." aria-label="Filtrar auditoria">
      <div class="audit-feed" id="audit-feed"></div>
    </div>
    <div class="panel">
      <div class="panel-title">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
        Issues Metabase
      </div>
      <div class="audit-tabs" id="issue-tabs">
        <button class="audit-tab active" data-issue-filter="all">Todas <span class="count" id="issue-count-all">0</span></button>
        <button class="audit-tab" data-issue-filter="OPEN">Abertas <span class="count" id="issue-count-open">0</span></button>
        <button class="audit-tab" data-issue-filter="CLOSED">Fechadas <span class="count" id="issue-count-closed">0</span></button>
      </div>
      <div id="issue-list"></div>
    </div>
  </div>

  <!-- Detailed explorer -->
  <div class="section">
    <div class="section-header">
      <span class="section-title">Explorar</span>
      <span class="section-line"></span>
    </div>
  </div>

  <div class="search-bar">
    <svg class="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
    <input class="search" id="search" placeholder="Buscar por nome, tabela ou ID..." autocomplete="off" aria-label="Buscar">
    <span class="result-count" id="result-count"></span>
  </div>

  <div class="tabs-bar" role="tablist">
    <button class="tab active" data-view="dashboards" role="tab" aria-selected="true">Por colecao</button>
    <button class="tab" data-view="tables" role="tab" aria-selected="false">Por tabela</button>
  </div>

  <div class="view active" id="v-dashboards" role="tabpanel"></div>
  <div class="view" id="v-tables" role="tabpanel"></div>
</div>

<script>
var DATA = {data_json};
var MB = "{MB_BASE}";
var RECENT = {recent_json};
var TOP_TABLES = {top_tables_json};
var COLS = {cols_json};
var AUDIT = {audit_json};
var TASKS = AUDIT.tasks || [];
var CATALOG = {catalog_json};
var CHV = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>';

function norm(s) {{
  return s.normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase();
}}
function tagClass(t) {{
  if (t.startsWith("mv_")) return "tag-mv";
  if (t.startsWith("tb_") || t.startsWith("fat_")) return "tag-tb";
  if (t.startsWith("dim_")) return "tag-dim";
  if (t.startsWith("vw_")) return "tag-vw";
  return "tag-ot";
}}
function relDate(iso) {{
  if (!iso) return "";
  var d = iso.substring(0, 10);
  var today = new Date().toISOString().substring(0, 10);
  if (d === today) return "hoje";
  var y = new Date(today); y.setDate(y.getDate() - 1);
  if (d === y.toISOString().substring(0, 10)) return "ontem";
  return d.substring(5).replace("-", "/");
}}

// Landing: recent
(function() {{
  var el = document.getElementById("recent-list");
  var h = "";
  RECENT.forEach(function(r) {{
    var authorHtml = r.author ? '<span class="recent-author">' + r.author.split(" ")[0] + '</span>' : '';
    h += '<a class="recent-item" href="' + MB + '/dashboard/' + r.id + '" target="_blank" rel="noopener">' +
      '<span class="recent-name">' + r.name + '</span>' +
      '<span class="recent-col">' + r.col + '</span>' +
      authorHtml +
      '<span class="recent-meta">' + r.cards + ' cards</span>' +
      '<span class="recent-date">' + relDate(r.updated) + '</span></a>';
  }});
  el.innerHTML = h;
}})();

// Landing: top tables
(function() {{
  var el = document.getElementById("top-tables");
  var h = "";
  TOP_TABLES.forEach(function(t) {{
    h += '<div class="bar-row" data-filter="' + t.name + '">' +
      '<span class="bar-label mono tag ' + tagClass(t.name) + '">' + t.name + '</span>' +
      '<span class="bar-track"><span class="bar-fill" style="width:' + t.pct + '%"></span></span>' +
      '<span class="bar-count">' + t.count + '</span></div>';
  }});
  el.innerHTML = h;
}})();

// Landing: collections
(function() {{
  var el = document.getElementById("col-dist");
  var h = "";
  COLS.forEach(function(c) {{
    h += '<div class="bar-row" data-filter="' + c.name + '">' +
      '<span class="bar-label">' + c.name + '</span>' +
      '<span class="bar-track"><span class="bar-fill" style="width:' + c.pct + '%"></span></span>' +
      '<span class="bar-count">' + c.count + '</span></div>';
  }});
  el.innerHTML = h;
}})();

// Landing: catalog
(function() {{
  var el = document.getElementById("catalog");
  var filtersEl = document.getElementById("cat-filters");
  var types = {{}};
  CATALOG.forEach(function(t) {{ types[t.type] = (types[t.type] || 0) + 1; }});
  var typeOrder = ["mv", "dim", "fat", "vw", "tb", "outras"];
  var activeType = null;

  function renderFilters() {{
    var h = "";
    typeOrder.forEach(function(tp) {{
      if (!types[tp]) return;
      var cls = activeType === tp ? " active" : "";
      h += '<button class="tab' + cls + '" data-cat-type="' + tp + '" style="padding:4px 12px;font-size:11px">' +
        tp + ' <span style="opacity:.6">' + types[tp] + '</span></button>';
    }});
    h += activeType ? '<button class="tab" data-cat-type="" style="padding:4px 12px;font-size:11px">todas</button>' : "";
    filtersEl.innerHTML = h;
  }}

  function renderCatalog() {{
    var h = "";
    CATALOG.forEach(function(t) {{
      if (activeType && t.type !== activeType) return;
      var used = t.used ? "color:var(--accent)" : "color:var(--muted-fg)";
      var badge = t.dashboards > 0 ? ' <span style="font-size:10px;opacity:.6">' + t.dashboards + ' dash</span>' : "";
      h += '<div style="font-family:var(--font-mono);font-size:var(--text-xs);padding:3px 0;' + used + '">' +
        t.name + badge + '</div>';
    }});
    el.innerHTML = h;
  }}

  filtersEl.addEventListener("click", function(e) {{
    var btn = e.target.closest("[data-cat-type]");
    if (!btn) return;
    activeType = btn.dataset.catType || null;
    renderFilters();
    renderCatalog();
  }});

  renderFilters();
  renderCatalog();
}})();

// Landing: tasks
(function() {{
  var listEl = document.getElementById("tasks-list");
  var filtersEl = document.getElementById("tasks-filters");
  var subtitleEl = document.getElementById("tasks-subtitle");
  var activeFilter = "OPEN";

  var openCount = TASKS.filter(function(t) {{ return t.state === "OPEN"; }}).length;
  var closedCount = TASKS.filter(function(t) {{ return t.state === "CLOSED"; }}).length;
  subtitleEl.textContent = "— " + openCount + " abertas · " + closedCount + " fechadas";

  function renderFilters() {{
    var filters = [
      {{ key: "OPEN", label: "Abertas", count: openCount }},
      {{ key: "CLOSED", label: "Fechadas", count: closedCount }},
      {{ key: "all", label: "Todas", count: TASKS.length }}
    ];
    var h = "";
    filters.forEach(function(f) {{
      var cls = activeFilter === f.key ? " active" : "";
      h += '<button class="audit-tab' + cls + '" data-task-filter="' + f.key + '">' +
        f.label + ' <span class="count">' + f.count + '</span></button>';
    }});
    filtersEl.innerHTML = h;
  }}

  function renderTasks() {{
    var h = "";
    TASKS.forEach(function(t) {{
      if (activeFilter !== "all" && t.state !== activeFilter) return;
      var st = t.state === "OPEN" ? "open" : "closed";
      var stLabel = t.state === "OPEN" ? "aberta" : "fechada";
      h += '<a class="task-item" href="https://github.com/Grupo-Velas/produtividade-bi-dev/issues/' + t.number +
        '" target="_blank" rel="noopener">' +
        '<span class="task-num">#' + t.number + '</span>' +
        '<span class="task-title">' + t.title + '</span>' +
        '<span class="task-author">' + t.author + '</span>' +
        '<span class="task-state ' + st + '">' + stLabel + '</span>' +
        '<span class="task-date">' + (t.updated || "") + '</span></a>';
    }});
    if (!h) h = '<div style="color:var(--muted-fg);font-size:var(--text-sm);padding:var(--space-4)">Nenhuma task encontrada</div>';
    listEl.innerHTML = h;
  }}

  filtersEl.addEventListener("click", function(e) {{
    var btn = e.target.closest("[data-task-filter]");
    if (!btn) return;
    activeFilter = btn.dataset.taskFilter;
    renderFilters();
    renderTasks();
  }});

  renderFilters();
  renderTasks();
}})();

// Audit: activity feed
(function() {{
  var feed = document.getElementById("audit-feed");
  var searchEl = document.getElementById("audit-search");
  var revisions = AUDIT.revisions || [];

  function initials(name) {{
    var parts = (name || "?").split(" ");
    return parts.length > 1 ? (parts[0][0] + parts[parts.length-1][0]).toUpperCase()
      : (name || "?").substring(0, 2).toUpperCase();
  }}

  function renderFeed(filter) {{
    var fn = filter ? filter.toLowerCase() : "";
    var h = "";
    var shown = 0;
    revisions.forEach(function(r) {{
      if (fn && r.user.toLowerCase().indexOf(fn) === -1 &&
          r.dashboard_name.toLowerCase().indexOf(fn) === -1) return;
      if (shown >= 50) return;
      shown++;
      var ts = r.timestamp ? r.timestamp.substring(0,16).replace("T"," ") : "?";
      var dateStr = r.timestamp ? relDate(r.timestamp.substring(0,10)) + " " + r.timestamp.substring(11,16) : "?";
      h += '<div class="audit-item">' +
        '<div class="audit-avatar">' + initials(r.user) + '</div>' +
        '<div class="audit-body">' +
          '<span class="audit-user">' + r.user + '</span>' +
          '<div class="audit-desc">' + r.description +
            ' em <a class="audit-target" href="' + MB + '/dashboard/' + r.dashboard_id +
            '" target="_blank" rel="noopener">' + r.dashboard_name + '</a></div>' +
        '</div>' +
        '<span class="audit-time">' + dateStr + '</span></div>';
    }});
    if (!h) h = '<div style="color:var(--muted-fg);font-size:var(--text-sm);padding:var(--space-4)">Nenhuma atividade encontrada</div>';
    feed.innerHTML = h;
  }}

  searchEl.addEventListener("input", function() {{
    renderFeed(searchEl.value.trim());
  }});
  renderFeed();
}})();

// Audit: GitHub issues
(function() {{
  var listEl = document.getElementById("issue-list");
  var tabsEl = document.getElementById("issue-tabs");
  var issues = AUDIT.issues || [];
  var activeFilter = "all";

  var openCount = issues.filter(function(i) {{ return i.state === "OPEN"; }}).length;
  var closedCount = issues.filter(function(i) {{ return i.state === "CLOSED"; }}).length;
  document.getElementById("issue-count-all").textContent = issues.length;
  document.getElementById("issue-count-open").textContent = openCount;
  document.getElementById("issue-count-closed").textContent = closedCount;

  function renderIssues() {{
    var h = "";
    issues.forEach(function(i) {{
      if (activeFilter !== "all" && i.state !== activeFilter) return;
      var st = i.state === "OPEN" ? "open" : "closed";
      var stLabel = i.state === "OPEN" ? "aberta" : "fechada";
      var icon = i.state === "OPEN"
        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>'
        : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
      h += '<a class="issue-card" href="https://github.com/Grupo-Velas/produtividade-bi-dev/issues/' + i.number +
        '" target="_blank" rel="noopener">' +
        '<span class="issue-icon ' + st + '">' + icon + '</span>' +
        '<span class="issue-num">#' + i.number + '</span>' +
        '<span class="issue-title">' + i.title + '</span>' +
        '<span class="issue-state ' + st + '">' + stLabel + '</span></a>';
    }});
    if (!h) h = '<div style="color:var(--muted-fg);font-size:var(--text-sm);padding:var(--space-4)">Nenhuma issue [Metabase] encontrada</div>';
    listEl.innerHTML = h;
  }}

  tabsEl.addEventListener("click", function(e) {{
    var tab = e.target.closest("[data-issue-filter]");
    if (!tab) return;
    activeFilter = tab.dataset.issueFilter;
    tabsEl.querySelectorAll(".audit-tab").forEach(function(t) {{ t.classList.remove("active"); }});
    tab.classList.add("active");
    renderIssues();
  }});
  renderIssues();
}})();

function renderDashboards(filter) {{
  var el = document.getElementById("v-dashboards");
  var fn = filter ? norm(filter) : "";
  var html = "", total = 0;
  for (var col in DATA.groups) {{
    var items = DATA.groups[col];
    var filtered = items.filter(function(item) {{
      var did = item[0], d = item[1];
      if (!fn) return true;
      return norm(d.name).includes(fn) || did.includes(fn) ||
        (d.tables || []).some(function(t) {{ return norm(t).includes(fn); }});
    }});
    if (!filtered.length) continue;
    total += filtered.length;
    var isOpen = fn ? " open" : "";
    html += '<div class="group' + isOpen + '">' +
      '<button class="group-toggle" data-toggle="group" aria-expanded="false">' +
      CHV + ' ' + col + ' <span class="group-count">' + filtered.length + '</span></button>' +
      '<div class="group-body">';
    filtered.forEach(function(item) {{
      var did = item[0], d = item[1];
      var tables = (d.tables || []).map(function(t) {{
        return '<span class="tag ' + tagClass(t) + '">' + t + '</span>';
      }}).join("");
      html += '<div class="dash-row">' +
        '<span class="dash-id">' + did + '</span>' +
        '<span class="dash-name"><a href="' + MB + '/dashboard/' + did + '" target="_blank" rel="noopener">' + d.name + '</a></span>' +
        '<span class="dash-cards">' + (d.card_count || 0) + ' cards</span>' +
        '<span class="tags">' + tables + '</span></div>';
    }});
    html += '</div></div>';
  }}
  if (!html) html = '<div class="empty">Nenhum dashboard encontrado</div>';
  el.innerHTML = html;
  return total;
}}

function renderTables(filter) {{
  var el = document.getElementById("v-tables");
  var fn = filter ? norm(filter) : "";
  var html = "", total = 0;
  var entries = Object.entries(DATA.table_index).sort(function(a,b) {{ return a[0].localeCompare(b[0]); }});
  entries.forEach(function(entry) {{
    var tbl = entry[0], dashIds = entry[1];
    if (fn && !norm(tbl).includes(fn) && !dashIds.some(function(id) {{
      var dd = DATA.dashboards[String(id)] || {{}};
      return norm(dd.name || "").includes(fn) || String(id).includes(fn);
    }})) return;
    total++;
    html += '<div class="tbl-card" tabindex="0" data-toggle="tbl">' +
      '<div class="tbl-header">' + CHV +
      ' <span class="tag ' + tagClass(tbl) + '">' + tbl + '</span>' +
      '<span class="tbl-count">' + dashIds.length + ' dashboards</span></div>' +
      '<div class="tbl-drilldown">';
    dashIds.forEach(function(did) {{
      var d = DATA.dashboards[String(did)] || {{}};
      html += '<a class="tbl-dash-link" href="' + MB + '/dashboard/' + did + '" target="_blank" rel="noopener">' +
        (d.name || "?") + ' <span style="opacity:.5">#' + did + '</span></a>';
    }});
    html += '</div></div>';
  }});
  if (!html) html = '<div class="empty">Nenhuma tabela encontrada</div>';
  el.innerHTML = html;
  return total;
}}

function applyFilter(term) {{
  var input = document.getElementById("search");
  input.value = term;
  input.focus();
  var d = renderDashboards(term);
  var t = renderTables(term);
  document.getElementById("result-count").textContent = d + " dashboards, " + t + " tabelas";
  input.scrollIntoView({{ behavior: "smooth", block: "start" }});
}}

// Tabs
document.querySelectorAll(".tab").forEach(function(t) {{
  t.addEventListener("click", function() {{
    document.querySelectorAll(".tab").forEach(function(x) {{ x.classList.remove("active"); x.setAttribute("aria-selected","false"); }});
    document.querySelectorAll(".view").forEach(function(x) {{ x.classList.remove("active"); }});
    t.classList.add("active"); t.setAttribute("aria-selected","true");
    document.getElementById("v-" + t.dataset.view).classList.add("active");
  }});
}});

// Search
var timer;
document.getElementById("search").addEventListener("input", function(e) {{
  clearTimeout(timer);
  timer = setTimeout(function() {{
    var v = e.target.value.trim();
    var d = renderDashboards(v);
    var t = renderTables(v);
    var rc = document.getElementById("result-count");
    rc.textContent = v ? d + " dashboards, " + t + " tabelas" : "";
  }}, 200);
}});

// Event delegation
document.addEventListener("click", function(e) {{
  var btn = e.target.closest("[data-toggle]");
  if (btn) {{
    var t = btn.dataset.toggle;
    if (t === "group") btn.parentElement.classList.toggle("open");
    else if (t === "tbl") btn.classList.toggle("open");
    return;
  }}
  var bar = e.target.closest("[data-filter]");
  if (bar) {{
    applyFilter(bar.dataset.filter);
  }}
}});
document.addEventListener("keydown", function(e) {{
  if (e.key !== "Enter") return;
  var el = e.target.closest("[data-toggle=tbl]");
  if (el) el.classList.toggle("open");
}});

renderDashboards();
renderTables();
</script>"""

    with open(OUTPUT_HTML, "w") as f:
        f.write(html)
    return OUTPUT_HTML


def sync(rebuild=False, quiet=False, generate=False):
    token = get_token()

    code = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
         f"{BASE_URL}/user/current", "-H", f"X-Metabase-Session: {token}"],
        capture_output=True, text=True
    ).stdout.strip()
    if code == "401":
        print("⚠ Token expirado. Renove em metabase-boot.md")
        sys.exit(1)

    old_slim = load_slim()

    remote = api_get(
        "dashboard",
        token,
        '[.[] | {id: .id, updated_at: .updated_at}]'
    )

    remote_map = {str(d["id"]): d["updated_at"] for d in remote}

    if rebuild:
        changed = list(remote_map.keys())
    else:
        changed = []
        for did, updated in remote_map.items():
            local = old_slim["dashboards"].get(did, {})
            if local.get("updated_at") != updated:
                changed.append(did)

    removed = [d for d in old_slim["dashboards"] if d not in remote_map]

    slim = json.loads(json.dumps(old_slim))
    for d in removed:
        del slim["dashboards"][d]

    if not changed and not removed:
        if not quiet:
            print(f"✓ Metabase index up-to-date ({len(slim['dashboards'])} dashboards)")
        if generate:
            generate_md(slim)
            generate_html(slim)
            if not quiet:
                print(f"✓ Gerados: {OUTPUT_MD} e {OUTPUT_HTML}")
        return

    real_tables = get_real_tables(token)

    if not quiet:
        print(f"Syncing {len(changed)} dashboards...")

    for i, did in enumerate(changed):
        data = fetch_dash_slim(did, token, real_tables)
        if data:
            slim["dashboards"][did] = data
        if not quiet and (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(changed)}...")

    slim["table_index"] = rebuild_table_index(slim["dashboards"])
    save_slim(slim)

    # Changelog
    cl = compute_changelog(old_slim, slim)
    if cl["dashboards_added"] or cl["dashboards_removed"] or cl["tables_added"] or cl["tables_removed"]:
        save_changelog(cl)
        if not quiet:
            if cl["tables_added"]:
                print(f"  ⚡ Novas tabelas: {', '.join(cl['tables_added'])}")
            if cl["tables_removed"]:
                print(f"  ⚠ Tabelas removidas: {', '.join(cl['tables_removed'])}")
            if cl["dashboards_added"]:
                names = [slim["dashboards"].get(d, {}).get("name", d) for d in cl["dashboards_added"][:5]]
                print(f"  + {len(cl['dashboards_added'])} dashboards novos: {', '.join(names)}")
            if cl["dashboards_removed"]:
                print(f"  - {len(cl['dashboards_removed'])} dashboards removidos")

    if not quiet:
        print(
            f"✓ Synced: {len(changed)} updated, {len(removed)} removed. "
            f"Total: {len(slim['dashboards'])} dashboards, "
            f"{len(slim['table_index'])} tables"
        )

    if generate:
        generate_md(slim)
        generate_html(slim)
        if not quiet:
            print(f"✓ Gerados: {OUTPUT_MD} e {OUTPUT_HTML}")


if __name__ == "__main__":
    args = set(sys.argv[1:])
    full = "--full" in args
    rebuild = "--rebuild" in args or full
    quiet = "--quiet" in args
    generate = "--generate" in args or full
    sync(rebuild=rebuild, quiet=quiet, generate=generate)
