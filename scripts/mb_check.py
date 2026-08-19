#!/usr/bin/env python3
"""
mb_check.py — Verifica alinhamento entre GitHub issues e estado do Metabase

Uso:
  python3 mb_check.py                    # todas issues abertas do JP
  python3 mb_check.py --issue 42         # issue especifica
  python3 mb_check.py --issue 42 38 35   # multiplas issues
  python3 mb_check.py --text "dash 10"   # busca por texto livre

Saida: JSON com status de cada issue verificada.
"""

import json
import os
import re
import subprocess
import sys
from difflib import SequenceMatcher

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.join(SCRIPT_DIR, "..", "..", "..")
MEMORY_DIR = os.path.join(HUB_DIR, "memory")
SLIM_PATH = os.path.join(MEMORY_DIR, "mapa_metabase_slim.json")
BOOT_PATH = os.path.join(MEMORY_DIR, "metabase-boot.md")

BASE_URL = "https://metabase.grupovelas.com.br/api"


def get_token():
    with open(BOOT_PATH) as f:
        for line in f:
            if line.strip().startswith("MB_TOKEN="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("Token nao encontrado em metabase-boot.md")


def load_slim():
    with open(SLIM_PATH) as f:
        return json.load(f)


def api_get(path, token):
    cmd = [
        "curl", "-s", f"{BASE_URL}/{path}",
        "-H", f"X-Metabase-Session: {token}"
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(proc.stdout)


def fuzzy_match(needle, haystack, threshold=0.55):
    needle_lower = needle.lower()
    haystack_lower = haystack.lower()
    if needle_lower in haystack_lower:
        return 1.0
    return SequenceMatcher(None, needle_lower, haystack_lower).ratio()


def extract_references(text):
    """Extrai referencias a dashboards, cards e tabelas de texto livre."""
    refs = {"dash_ids": [], "card_ids": [], "dash_names": [], "table_names": []}

    # IDs explicitos: "dash 10", "dashboard 316", "card 13596"
    for m in re.finditer(r'\b(?:dash(?:board)?)\s*[:#]?\s*(\d+)', text, re.I):
        refs["dash_ids"].append(int(m.group(1)))
    for m in re.finditer(r'\b(?:card)\s*[:#]?\s*(\d+)', text, re.I):
        refs["card_ids"].append(int(m.group(1)))

    # Nomes entre aspas ou apos marcadores
    for m in re.finditer(r'["“]([^"”]+)["”]', text):
        name = m.group(1).strip()
        if len(name) > 3:
            refs["dash_names"].append(name)

    # Nomes de tabelas (mv_, dim_, fat_, tb_, vw_)
    for m in re.finditer(r'\b((?:mv|dim|fat|tb|vw)_\w+)', text, re.I):
        refs["table_names"].append(m.group(1).lower())

    refs["dash_ids"] = list(dict.fromkeys(refs["dash_ids"]))
    refs["card_ids"] = list(dict.fromkeys(refs["card_ids"]))
    refs["dash_names"] = list(dict.fromkeys(refs["dash_names"]))
    refs["table_names"] = list(dict.fromkeys(refs["table_names"]))
    return refs


def auto_match_dashboards(text, dashboards, threshold=0.6):
    """Busca fuzzy automatica de nomes de dashboard no texto, sem precisar de marcadores."""
    matches = []
    text_lower = text.lower()
    text_words = set(re.findall(r'\b\w+\b', text_lower))
    for did, d in dashboards.items():
        dname = d.get("name", "")
        dname_clean = re.sub(r'[^\w\s]', '', dname).strip()
        if not dname_clean:
            continue
        # Sigla exata (2-4 chars uppercase no nome original)
        sigla_stop = {"TESTE", "RASCUNHO", "DADOS", "GERAL", "GERAIS", "ITC", "KPI", "SQL", "API", "CSV", "MTD", "TRI"}
        siglas = [s for s in re.findall(r'\b[A-Z]{2,5}\b', dname) if s not in sigla_stop]
        for s in siglas:
            if s.lower() in text_words:
                matches.append((did, d, 0.9))
                break
        else:
            if len(dname_clean) < 4:
                continue
            # Substring direta
            if dname_clean.lower() in text_lower:
                matches.append((did, d, 1.0))
                continue
            # Palavras-chave significativas do nome do dash (>3 chars, nao genericas)
            stopwords = {"teste", "rascunho", "dashboard", "relatorio", "relatório", "dados", "gerais", "aba", "analise", "análise", "geral"}
            keywords = [w for w in dname_clean.lower().split() if len(w) > 3 and w not in stopwords]
            if keywords:
                matched_kw = sum(1 for kw in keywords if kw in text_lower)
                ratio = matched_kw / len(keywords)
                if ratio >= 0.5 and matched_kw >= 2:
                    matches.append((did, d, round(ratio, 2)))
    # Dedup por ID, manter maior score
    seen = {}
    for did, d, score in matches:
        if did not in seen or score > seen[did][2]:
            seen[did] = (did, d, score)
    # Top 5 por score
    ranked = sorted(seen.values(), key=lambda x: x[2], reverse=True)[:5]
    return ranked


def check_refs_against_slim(refs, slim):
    """Cruza referencias extraidas com o indice slim."""
    results = []
    dashboards = slim.get("dashboards", {})
    table_index = slim.get("table_index", {})

    # Check dash IDs
    for did in refs["dash_ids"]:
        did_str = str(did)
        if did_str in dashboards:
            d = dashboards[did_str]
            results.append({
                "type": "dashboard",
                "id": did,
                "name": d["name"],
                "status": "found",
                "cards": d.get("card_count", 0),
                "collection": d.get("collection", ""),
                "updated_at": d.get("updated_at", ""),
            })
        else:
            results.append({
                "type": "dashboard",
                "id": did,
                "status": "not_found",
            })

    # Check card IDs (requer API)
    for cid in refs["card_ids"]:
        results.append({
            "type": "card",
            "id": cid,
            "status": "needs_api_check",
        })

    # Fuzzy match dash names
    for name in refs["dash_names"]:
        best_match = None
        best_score = 0
        for did, d in dashboards.items():
            score = fuzzy_match(name, d["name"])
            if score > best_score:
                best_score = score
                best_match = (did, d)
        if best_match and best_score >= 0.55:
            did, d = best_match
            results.append({
                "type": "dashboard",
                "id": int(did),
                "name": d["name"],
                "status": "fuzzy_match",
                "score": round(best_score, 2),
                "cards": d.get("card_count", 0),
                "collection": d.get("collection", ""),
            })

    # Check table names
    for tname in refs["table_names"]:
        if tname in table_index:
            dash_ids = table_index[tname]
            results.append({
                "type": "table",
                "name": tname,
                "status": "found",
                "used_in_dashboards": dash_ids,
            })
        else:
            results.append({
                "type": "table",
                "name": tname,
                "status": "not_found",
            })

    return results


def verify_card_via_api(card_id, token):
    """Verifica se um card existe e tem dados via API."""
    try:
        card = api_get(f"card/{card_id}", token)
        if "id" not in card:
            return {"id": card_id, "status": "not_found"}
        return {
            "id": card_id,
            "status": "found",
            "name": card.get("name", ""),
            "display": card.get("display", ""),
            "database_id": card.get("database_id"),
        }
    except Exception:
        return {"id": card_id, "status": "api_error"}


def get_jp_issues():
    """Puxa issues abertas do JP via kanban (fonte primaria) + gh issues."""
    issues = []
    seen_numbers = set()

    # Fonte primaria: kanban do projeto (tem status)
    ACTIVE_STATUSES = {"em andamento", "em validação", "em validacao", "solicitada"}
    try:
        cmd_kanban = [
            "gh", "project", "item-list", "1",
            "--owner", "Grupo-Velas",
            "--format", "json",
            "--limit", "400"
        ]
        proc_k = subprocess.run(cmd_kanban, capture_output=True, text=True)
        if proc_k.stdout.strip():
            kanban = json.loads(proc_k.stdout)
            items = kanban.get("items", [])
            for item in items:
                title = item.get("title", "")
                body = item.get("body", "") or ""
                status = item.get("status", "")
                if status.lower() not in ACTIVE_STATUSES:
                    continue
                is_jp = (
                    "[JP]" in title or "JP:" in title or
                    "jpazv" in (body or "").lower()
                )
                if is_jp:
                    # Evita duplicatas
                    number = item.get("content", {}).get("number")
                    if number and not any(i["number"] == number for i in issues):
                        issues.append({
                            "number": number or 0,
                            "title": title,
                            "body": body or "",
                            "labels": [],
                            "updatedAt": item.get("updatedAt", ""),
                            "kanban_status": status,
                        })
    except Exception:
        pass

    # Fallback: gh issue list (sem status do kanban)
    if not issues:
        cmd = [
            "gh", "issue", "list",
            "-R", "Grupo-Velas/produtividade-bi-dev",
            "--assignee", "jpazv",
            "--state", "open",
            "--json", "number,title,body,labels,updatedAt",
            "--limit", "20"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout.strip():
            for i in json.loads(proc.stdout):
                if "[JP]" in i.get("title", "") or "JP:" in i.get("title", ""):
                    if i["number"] not in seen_numbers:
                        issues.append(i)

    return issues


def check_issue(issue, slim, token):
    """Verifica uma issue contra o Metabase."""
    text = f"{issue.get('title', '')} {issue.get('body', '')}"
    refs = extract_references(text)
    dashboards = slim.get("dashboards", {})

    results = check_refs_against_slim(refs, slim)

    # Busca fuzzy automatica por nomes de dashboard no texto
    seen_ids = {str(r.get("id")) for r in results if r["type"] == "dashboard"}
    auto_matches = auto_match_dashboards(text, dashboards)
    for did, d, score in auto_matches:
        if did not in seen_ids:
            results.append({
                "type": "dashboard",
                "id": int(did),
                "name": d["name"],
                "status": "auto_match",
                "score": score,
                "cards": d.get("card_count", 0),
                "collection": d.get("collection", ""),
            })
            seen_ids.add(did)

    # Verificar cards via API
    for r in results:
        if r.get("status") == "needs_api_check" and r["type"] == "card":
            api_result = verify_card_via_api(r["id"], token)
            r.update(api_result)

    has_refs = len(results) > 0

    return {
        "issue_number": issue.get("number", 0),
        "issue_title": issue.get("title", ""),
        "kanban_status": issue.get("kanban_status", ""),
        "refs_found": has_refs,
        "checks": results,
    }


def format_briefing(checked_issues):
    """Formata o briefing compacto em texto."""
    from datetime import date
    lines = [f"## Briefing Metabase — {date.today().isoformat()}", ""]

    open_count = len(checked_issues)
    lines.append(f"### Tasks abertas ({open_count})")
    lines.append("")

    for ci in checked_issues:
        num = ci["issue_number"]
        title = ci["issue_title"]
        status_prefix = ci.get("kanban_status", "")
        if status_prefix:
            status_prefix = f" [{status_prefix}]"

        if not ci["refs_found"]:
            emoji = "\U0001f50d"
            detail = "verificacao manual necessaria (sem mencao a dash/card especifico)"
        else:
            all_found = all(c.get("status") in ("found", "fuzzy_match", "auto_match") for c in ci["checks"])
            any_missing = any(c.get("status") == "not_found" for c in ci["checks"])
            if all_found and ci["checks"]:
                emoji = "✅"
                details = []
                for c in ci["checks"]:
                    if c["type"] == "dashboard":
                        details.append(f"dash {c['id']} ({c.get('name', '')}, {c.get('cards', '?')} cards)")
                    elif c["type"] == "card":
                        details.append(f"card {c['id']} ({c.get('name', '')})")
                    elif c["type"] == "table":
                        details.append(f"tabela {c['name']} (em {len(c.get('used_in_dashboards', []))} dashes)")
                detail = "; ".join(details)
            elif any_missing:
                emoji = "⚠️"
                missing = [c for c in ci["checks"] if c.get("status") == "not_found"]
                found = [c for c in ci["checks"] if c.get("status") in ("found", "fuzzy_match")]
                parts = []
                if found:
                    parts.append(f"{len(found)} encontrados")
                if missing:
                    ids = [str(c.get("id", c.get("name", "?"))) for c in missing]
                    parts.append(f"{len(missing)} nao encontrados ({', '.join(ids)})")
                detail = "; ".join(parts)
            else:
                emoji = "⚠️"
                detail = "verificacao parcial"

        lines.append(f"**#{num}** — {title}{status_prefix}")
        lines.append(f"  {emoji} {detail}")
        lines.append("")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Verifica issues vs Metabase")
    parser.add_argument("--issue", nargs="+", type=int, help="Numeros de issues")
    parser.add_argument("--text", type=str, help="Texto livre para busca")
    parser.add_argument("--json-out", action="store_true", help="Saida em JSON")
    parser.add_argument("--briefing", action="store_true", help="Saida como briefing formatado")
    args = parser.parse_args()

    slim = load_slim()
    token = get_token()

    if args.text:
        fake_issue = {"number": 0, "title": args.text, "body": ""}
        result = check_issue(fake_issue, slim, token)
        if args.json_out:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.issue:
        issues = []
        for num in args.issue:
            cmd = ["gh", "issue", "view", str(num), "-R", "Grupo-Velas/produtividade-bi-dev", "--json", "number,title,body,labels"]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.stdout.strip():
                issues.append(json.loads(proc.stdout))
    else:
        issues = get_jp_issues()

    if not issues:
        print("Nenhuma issue aberta encontrada.")
        return

    checked = [check_issue(i, slim, token) for i in issues]

    if args.json_out:
        print(json.dumps(checked, ensure_ascii=False, indent=2))
    elif args.briefing:
        print(format_briefing(checked))
    else:
        print(format_briefing(checked))


if __name__ == "__main__":
    main()
