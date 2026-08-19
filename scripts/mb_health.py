#!/usr/bin/env python3
"""
mb_health.py — Monitor de integridade dos dashboards Metabase

Detecta cards quebrados (erro ou zero linhas).
Gera health.json consumido pelo mb_sync.py na geração do HTML.

Uso:
  python3 mb_health.py                # checa dashboards alterados nos últimos 7 dias
  python3 mb_health.py --all          # visão geral completa (primeira rodada)
  python3 mb_health.py --dash 10 316  # checa dashboards específicos
  python3 mb_health.py --days 14      # alterados nos últimos 14 dias
  python3 mb_health.py --dry-run      # mostra o que faria sem rodar queries
"""

import json
import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.join(SCRIPT_DIR, "..", "..", "..")
MEMORY_DIR = os.path.join(HUB_DIR, "memory")
BOOT_PATH = os.path.join(MEMORY_DIR, "metabase-boot.md")
SLIM_PATH = os.path.join(MEMORY_DIR, "mapa_metabase_slim.json")
HEALTH_PATH = os.path.join(MEMORY_DIR, "health.json")

BASE_URL = "https://metabase.grupovelas.com.br/api"
MAX_WORKERS = 8


def get_token():
    with open(BOOT_PATH) as f:
        for line in f:
            if line.strip().startswith("MB_TOKEN="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError("Token não encontrado em metabase-boot.md")


def http_request(method, path, token, body=None):
    cmd = ["curl", "-s", "-w", "\n__HTTP__%{http_code}",
           f"{BASE_URL}/{path}", "-H", f"X-Metabase-Session: {token}"]
    if method == "POST":
        cmd += ["-X", "POST", "-H", "Content-Type: application/json",
                "-d", json.dumps(body or {})]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return 0, {"error": "timeout"}
    output = proc.stdout
    status = 200
    if "__HTTP__" in output:
        parts = output.rsplit("__HTTP__", 1)
        output = parts[0]
        try:
            status = int(parts[1].strip())
        except ValueError:
            pass
    try:
        return status, json.loads(output) if output.strip() else {}
    except json.JSONDecodeError:
        return status, {"_raw": output[:200]}


def get_dashboard_cards(dash_id, token):
    status, data = http_request("GET", f"dashboard/{dash_id}", token)
    if status != 200 or not isinstance(data, dict):
        return None, []
    cards = []
    seen = set()
    for dc in data.get("dashcards", []):
        card = dc.get("card", {})
        cid = card.get("id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        cards.append({
            "card_id": cid,
            "name": card.get("name", "?"),
            "display": card.get("display", "?"),
            "query_type": card.get("query_type", "?"),
        })
    return data.get("name", "?"), cards


def check_card(card_id, token):
    status, body = http_request("POST", f"card/{card_id}/query", token)

    if status == 404:
        return {"status": "not_found", "rows": 0, "error": "Card não encontrado"}
    if status == 403:
        return {"status": "forbidden", "rows": 0, "error": "Sem permissão"}
    if status == 0:
        return {"status": "timeout", "rows": 0, "error": "Timeout"}
    if status >= 500:
        return {"status": "server_error", "rows": 0, "error": f"HTTP {status}"}

    query_status = body.get("status", "unknown")
    if query_status == "failed":
        error = body.get("error", "Erro desconhecido")
        if isinstance(error, str) and len(error) > 150:
            error = error[:150] + "..."
        return {"status": "error", "rows": 0, "error": error}

    if query_status == "completed":
        row_count = body.get("row_count", 0)
        return {"status": "ok" if row_count > 0 else "empty", "rows": row_count, "error": None}

    return {"status": "unknown", "rows": 0, "error": f"Status: {query_status}"}


def check_dashboard(dash_id, token, dry_run=False):
    dash_name, cards = get_dashboard_cards(dash_id, token)
    if dash_name is None:
        return {"dash_id": dash_id, "name": "?", "total_cards": 0,
                "ok": 0, "empty": 0, "broken": 0, "cards": [],
                "error": "Dashboard não encontrado",
                "checked_at": datetime.now(timezone.utc).isoformat()}

    if dry_run:
        return {"dash_id": dash_id, "name": dash_name, "total_cards": len(cards),
                "ok": 0, "empty": 0, "broken": 0, "cards": [],
                "checked_at": datetime.now(timezone.utc).isoformat()}

    def _check(c):
        result = check_card(c["card_id"], token)
        return {**c, **result}

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_check, c): c for c in cards}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                c = futures[future]
                results.append({**c, "status": "error", "rows": 0, "error": str(e)[:100]})

    broken = sum(1 for r in results if r["status"] in ("error", "not_found", "server_error", "forbidden", "timeout"))
    empty = sum(1 for r in results if r["status"] == "empty")
    ok = sum(1 for r in results if r["status"] == "ok")

    return {
        "dash_id": dash_id,
        "name": dash_name,
        "total_cards": len(cards),
        "ok": ok, "empty": empty, "broken": broken,
        "cards": sorted(results, key=lambda r: (0 if r["status"] in ("error","not_found","server_error") else 1 if r["status"]=="empty" else 2)),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


TRUTH_DASH = 10
SOCIO_CLONES = [343,341,103,351,344,271,348,273,345,352,347,274,346,353,84,277,279,349,350]
COMPARE_CARDS = ["Leads", "Agendamento", "Avs", "Tratamentos", "Faturamento", "Renovações",
                 "%D2", "%Mix", "%CVS", "Ticket Médio", "Fat/Av", "%No-show", "%CVF"]


def get_dash_scalar_map(dash_id, token):
    """Get {card_name: (card_id, dashcard_id)} for scalar cards in a dashboard."""
    status, data = http_request("GET", f"dashboard/{dash_id}", token)
    if status != 200:
        return {}, {}, []
    result = {}
    filters = {}
    params = data.get("parameters", [])
    for dc in data.get("dashcards", []):
        c = dc.get("card", {})
        if c.get("display") == "scalar" and c.get("id"):
            name = c.get("name", "?")
            if name not in result:
                result[name] = (c["id"], dc["id"])
                q = c.get("dataset_query", {}).get("query", {})
                f = q.get("filter")
                if f and isinstance(f, list) and f[0] == "contains":
                    filters[name] = f[2] if len(f) > 2 else None
    return result, filters, params


def get_socio_values(token):
    """Get real sócio values from dash 10 card 91 (Métricas consolidadas)."""
    status, data = http_request("GET", f"dashboard/{TRUTH_DASH}", token)
    if status != 200:
        return []
    dc_id = None
    for dc in data.get("dashcards", []):
        if dc.get("card", {}).get("id") == 91:
            dc_id = dc["id"]
            break
    if not dc_id:
        return []
    _, body = http_request("POST",
        f"dashboard/{TRUTH_DASH}/dashcard/{dc_id}/card/91/query", token,
        {"parameters": [{"id": "ff97c004", "type": "date/all-options", "value": "thismonth"}]})
    cols = [c.get("name") for c in body.get("data", {}).get("cols", [])]
    rows = body.get("data", {}).get("rows", [])
    if "socio" not in cols:
        return []
    si = cols.index("socio")
    return sorted(set(str(r[si]) for r in rows if r[si]))


def match_socio(contains_val, socio_list):
    """Match a 'contains' filter value to the exact sócio name."""
    if not contains_val:
        return None
    cv = contains_val.lower()
    matches = [s for s in socio_list if cv in s.lower()]
    return matches[0] if len(matches) == 1 else (matches[0] if matches else None)


def query_card_in_dash(dash_id, dc_id, card_id, token, params):
    """Run a card within a dashboard context with given parameters."""
    _, body = http_request("POST",
        f"dashboard/{dash_id}/dashcard/{dc_id}/card/{card_id}/query",
        token, {"parameters": params})
    if body.get("status") == "completed":
        rows = body.get("data", {}).get("rows", [])
        if rows and rows[0]:
            return rows[0][0]
    return None


def compare_dashboards(token):
    """Compare sócio clone dashboards against dash 10 (source of truth)."""
    print("Buscando valores de sócio...")
    socio_values = get_socio_values(token)
    if not socio_values:
        print("  Erro: não conseguiu buscar valores de sócio")
        return []
    print(f"  {len(socio_values)} sócios encontrados\n")

    print("Mapeando cards do dash 10 (fonte de verdade)...")
    truth_cards, _, truth_params = get_dash_scalar_map(TRUTH_DASH, token)
    compare_names = [n for n in COMPARE_CARDS if n in truth_cards]
    print(f"  {len(compare_names)} cards para comparar: {', '.join(compare_names)}\n")

    results = []

    for clone_id in SOCIO_CLONES:
        clone_cards, clone_filters, _ = get_dash_scalar_map(clone_id, token)
        if not clone_cards:
            continue

        any_filter = next(iter(clone_filters.values()), None) if clone_filters else None
        socio_exact = match_socio(any_filter, socio_values)
        clone_name = f"Dash {clone_id}"

        status_data, dash_data = http_request("GET", f"dashboard/{clone_id}", token)
        if status_data == 200:
            clone_name = dash_data.get("name", clone_name)

        if not socio_exact:
            print(f"  ⚠ {clone_name}: não conseguiu mapear sócio (filter='{any_filter}')")
            continue

        sys.stdout.write(f"  {clone_name[:45]}...")
        sys.stdout.flush()

        truth_params_list = [
            {"id": "ff97c004", "type": "date/all-options", "value": "thismonth"},
            {"id": "8dc354c1", "type": "string/=", "value": [socio_exact]},
        ]
        clone_params_list = [
            {"id": "ff97c004", "type": "date/all-options", "value": "thismonth"},
        ]

        diffs = []
        matched = 0

        def _compare_card(card_name):
            if card_name not in clone_cards or card_name not in truth_cards:
                return None
            t_cid, t_dcid = truth_cards[card_name]
            c_cid, c_dcid = clone_cards[card_name]
            truth_val = query_card_in_dash(TRUTH_DASH, t_dcid, t_cid, token, truth_params_list)
            clone_val = query_card_in_dash(clone_id, c_dcid, c_cid, token, clone_params_list)
            return card_name, truth_val, clone_val

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(_compare_card, name) for name in compare_names]
            for f in as_completed(futures):
                r = f.result()
                if r is None:
                    continue
                card_name, truth_val, clone_val = r
                if truth_val is not None and clone_val is not None:
                    try:
                        tv = float(truth_val)
                        cv = float(clone_val)
                        if tv == 0 and cv == 0:
                            matched += 1
                            continue
                        if tv != 0 and abs(tv - cv) / abs(tv) > 0.01:
                            diffs.append({
                                "card": card_name,
                                "truth": truth_val,
                                "clone": clone_val,
                                "diff_pct": round((cv - tv) / tv * 100, 1) if tv else None,
                            })
                        else:
                            matched += 1
                    except (ValueError, TypeError):
                        if str(truth_val) != str(clone_val):
                            diffs.append({"card": card_name, "truth": truth_val, "clone": clone_val, "diff_pct": None})
                        else:
                            matched += 1
                elif truth_val is None and clone_val is None:
                    matched += 1

        if diffs:
            print(f" 🔴 {len(diffs)} divergências, {matched} OK")
            for d in diffs:
                pct = f" ({d['diff_pct']:+.1f}%)" if d.get("diff_pct") is not None else ""
                print(f"       {d['card']:20s} verdade={d['truth']}  clone={d['clone']}{pct}")
        else:
            print(f" 🟢 {matched} cards conferem")

        results.append({
            "clone_id": clone_id,
            "clone_name": clone_name,
            "socio": socio_exact,
            "matched": matched,
            "diffs": diffs,
        })

    return results


def load_previous_health():
    if os.path.exists(HEALTH_PATH):
        with open(HEALTH_PATH) as f:
            return json.load(f)
    return {"dashboards": {}, "checked_at": None}


def save_health(health):
    with open(HEALTH_PATH, "w") as f:
        json.dump(health, f, indent=2, ensure_ascii=False)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Monitor de integridade Metabase")
    parser.add_argument("--dash", type=int, nargs="+", help="Dashboard(s) específico(s)")
    parser.add_argument("--days", type=int, default=7, help="Checar dashboards alterados nos últimos N dias (default 7)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true", help="Checar todos os dashboards")
    parser.add_argument("--compare", action="store_true", help="Comparar clones de sócio vs dash 10")
    args = parser.parse_args()

    token = get_token()

    if args.compare:
        print("Comparando dashboards de sócio vs fonte de verdade (dash 10)...\n")
        results = compare_dashboards(token)
        total_diffs = sum(len(r["diffs"]) for r in results)
        total_ok = sum(r["matched"] for r in results)
        print(f"\n{'='*50}")
        print(f"{len(results)} sócios comparados")
        print(f"  🟢 {total_ok} valores conferem")
        print(f"  🔴 {total_diffs} divergências")

        health = load_previous_health()
        health["compare"] = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "results": results,
            "total_diffs": total_diffs,
            "total_ok": total_ok,
        }
        save_health(health)
        print(f"\nSalvo em: {HEALTH_PATH}")
        return

    with open(SLIM_PATH) as f:
        slim = json.load(f)
    dashboards = slim["dashboards"]

    if args.dash:
        dash_ids = [str(d) for d in args.dash]
    elif args.all:
        dash_ids = sorted(dashboards.keys(),
                          key=lambda k: dashboards[k].get("updated_at", ""), reverse=True)
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
        dash_ids = [k for k, v in dashboards.items()
                    if v.get("updated_at", "") >= cutoff]
        dash_ids.sort(key=lambda k: dashboards[k].get("updated_at", ""), reverse=True)

    if not dash_ids:
        print(f"Nenhum dashboard alterado nos últimos {args.days} dias.")
        return

    health = load_previous_health()
    total_broken = 0
    total_empty = 0
    total_ok = 0

    mode = "todos" if args.all else f"últimos {args.days} dias" if not args.dash else "específicos"
    print(f"Checando {len(dash_ids)} dashboards ({mode})...\n")

    for i, did in enumerate(dash_ids):
        d = dashboards.get(did, {})
        name = d.get("name", did)[:50]
        sys.stdout.write(f"  [{i+1}/{len(dash_ids)}] {name}...")
        sys.stdout.flush()

        result = check_dashboard(int(did), token, dry_run=args.dry_run)
        health["dashboards"][did] = {
            k: v for k, v in result.items() if k != "cards"
        }
        health["dashboards"][did]["problems"] = [
            {"card_id": c["card_id"], "name": c["name"], "status": c["status"], "error": c.get("error")}
            for c in result.get("cards", [])
            if c["status"] not in ("ok", "dry_run")
        ]

        b = result.get("broken", 0)
        e = result.get("empty", 0)
        o = result.get("ok", 0)
        total_broken += b
        total_empty += e
        total_ok += o

        if b > 0:
            print(f" 🔴 {b} quebrados" + (f", 🟡 {e} vazios" if e else ""))
        elif e > 0:
            print(f" 🟡 {e} vazios")
        else:
            print(f" 🟢 {o} cards OK")

    health["checked_at"] = datetime.now(timezone.utc).isoformat()
    health["summary"] = {
        "total_dashboards": len(dash_ids),
        "total_ok": total_ok,
        "total_empty": total_empty,
        "total_broken": total_broken,
    }
    save_health(health)

    print(f"\n{'='*50}")
    print(f"{len(dash_ids)} dashboards · {total_ok + total_empty + total_broken} cards")
    print(f"  🟢 {total_ok} OK")
    print(f"  🟡 {total_empty} vazios")
    print(f"  🔴 {total_broken} quebrados")
    print(f"\nSalvo em: {HEALTH_PATH}")

    problems = []
    for did in dash_ids:
        dh = health["dashboards"].get(did, {})
        for p in dh.get("problems", []):
            if p["status"] not in ("empty",):
                problems.append((did, dh.get("name", "?"), p))

    if problems:
        print(f"\n--- Cards com problema ---")
        for did, dname, p in problems:
            print(f"  🔴 Dash {did} ({dname[:30]}) → Card {p['card_id']} ({p['name'][:30]}): {p.get('error','?')}")


if __name__ == "__main__":
    main()
