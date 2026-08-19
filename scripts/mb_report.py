#!/usr/bin/env python3
"""
mb_report.py — Gera relatorio MD de validacao de uma issue do Metabase

Uso:
  python3 mb_report.py 42              # gera report pra issue 42
  python3 mb_report.py --last          # gera report pra ultima verificacao

Saida: arquivo MD em $HUB/memory/reports/metabase/YYYY-MM-DD-<slug>.md
"""

import json
import os
import re
import subprocess
import sys
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.join(SCRIPT_DIR, "..", "..", "..")
REPORTS_DIR = os.path.join(HUB_DIR, "memory", "reports", "metabase")


def slugify(text, max_len=40):
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text).strip('-')
    return text[:max_len]


def get_issue(number):
    cmd = [
        "gh", "issue", "view", str(number),
        "-R", "Grupo-Velas/produtividade-bi-dev",
        "--json", "number,title,body,labels,state,assignees"
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout.strip():
        return json.loads(proc.stdout)
    return None


def run_check(number):
    cmd = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "mb_check.py"),
        "--issue", str(number),
        "--json-out"
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout.strip():
        results = json.loads(proc.stdout)
        return results[0] if results else None
    return None


def generate_report(issue, check_result):
    today = date.today().isoformat()
    slug = slugify(issue.get("title", f"issue-{issue['number']}"))
    filename = f"{today}-{slug}.md"
    filepath = os.path.join(REPORTS_DIR, filename)

    lines = [
        f"# Relatorio de validacao — #{issue['number']}",
        "",
        f"**Data:** {today}",
        f"**Issue:** #{issue['number']} — {issue.get('title', '')}",
        f"**Estado:** {issue.get('state', 'unknown')}",
        f"**Assignees:** {', '.join(a.get('login', '') for a in issue.get('assignees', []))}",
        "",
        "---",
        "",
        "## Descricao da issue",
        "",
    ]

    body = issue.get("body", "") or ""
    if len(body) > 500:
        body = body[:500] + "..."
    lines.append(body)
    lines.append("")

    lines.append("## Verificacao contra o Metabase")
    lines.append("")

    if not check_result or not check_result.get("refs_found"):
        lines.append("Nenhuma referencia a dashboard, card ou tabela encontrada no texto da issue.")
        lines.append("Verificacao manual necessaria.")
    else:
        for c in check_result.get("checks", []):
            ctype = c.get("type", "?")
            status = c.get("status", "?")

            if ctype == "dashboard":
                emoji = "✅" if status in ("found", "fuzzy_match") else "❌"
                name = c.get("name", "")
                cards = c.get("cards", "?")
                coll = c.get("collection", "")
                lines.append(f"- {emoji} **Dashboard {c.get('id', '?')}** — {name}")
                lines.append(f"  - Cards: {cards}")
                lines.append(f"  - Collection: {coll}")
                if status == "fuzzy_match":
                    lines.append(f"  - Match score: {c.get('score', '?')}")
            elif ctype == "card":
                emoji = "✅" if status == "found" else "❌"
                name = c.get("name", "")
                lines.append(f"- {emoji} **Card {c.get('id', '?')}** — {name}")
                if c.get("display"):
                    lines.append(f"  - Tipo: {c['display']}")
            elif ctype == "table":
                emoji = "✅" if status == "found" else "❌"
                dashes = c.get("used_in_dashboards", [])
                lines.append(f"- {emoji} **Tabela {c.get('name', '?')}**")
                lines.append(f"  - Usada em {len(dashes)} dashboards")
            lines.append("")

    lines.append("## Pendencias")
    lines.append("")
    lines.append("- [ ] Verificar visualmente no Metabase")
    lines.append("- [ ] Confirmar filtros funcionando")
    lines.append("- [ ] Marcar task como concluida")
    lines.append("")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(filepath, "w") as f:
        f.write("\n".join(lines))

    return filepath


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Gera relatorio de validacao")
    parser.add_argument("issue_number", nargs="?", type=int, help="Numero da issue")
    parser.add_argument("--last", action="store_true", help="Ultima verificacao")
    args = parser.parse_args()

    if not args.issue_number and not args.last:
        print("Uso: mb_report.py <issue_number> ou --last")
        sys.exit(1)

    if args.last:
        reports = sorted(
            [f for f in os.listdir(REPORTS_DIR) if f.endswith(".md")],
            reverse=True
        )
        if reports:
            print(os.path.join(REPORTS_DIR, reports[0]))
        else:
            print("Nenhum report encontrado.")
        return

    issue = get_issue(args.issue_number)
    if not issue:
        print(f"Issue #{args.issue_number} nao encontrada.")
        sys.exit(1)

    check_result = run_check(args.issue_number)
    filepath = generate_report(issue, check_result)
    print(f"Report gerado: {filepath}")


if __name__ == "__main__":
    main()
