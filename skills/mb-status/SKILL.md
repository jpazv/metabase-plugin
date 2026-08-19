---
description: Visao geral rapida do Metabase — dashboards recentes e tasks abertas do JP. Uso: /mb-status
---

## Sync

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && python3 "$HUB/plugins/metabase/scripts/mb_sync.py" --quiet 2>&1 || echo "sync falhou"`

## Dashboards recentes

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && python3 -c "
import json
slim = json.load(open('$HUB/memory/mapa_metabase_slim.json'))
dashes = sorted(slim['dashboards'].items(), key=lambda x: x[1].get('updated_at',''), reverse=True)[:10]
for did, d in dashes:
    print(f\"  {did:>4} | {d['name'][:50]:<50} | {d.get('card_count',0):>3} cards | {d.get('updated_at','')[:10]}\")
"`

## Tasks abertas do JP

!`gh issue list --assignee jpazv --state open --json number,title,updatedAt --jq '.[] | "#\(.number) — \(.title) (\(.updatedAt[:10]))"' 2>&1 || echo "gh nao disponivel"`

## Apos o status

- Apresentar o resumo ao usuario de forma compacta
- Se o usuario quiser mais detalhes de um dash, sugerir `/mb-dash <id>`
- Se quiser verificar uma issue, sugerir `/mb-check <issue>`
