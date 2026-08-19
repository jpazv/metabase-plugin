---
description: Inspeciona um card do Metabase — SQL, parametros, tipo de display. Uso: /mb-card <id>
---

## Contexto do boot

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && cat "$HUB/memory/metabase-boot.md"`

## Inspecionar card

1. Ler o ID do card do argumento do usuario
2. Consultar a API:
   ```bash
   curl -s "https://metabase.grupovelas.com.br/api/card/ID" \
     -H "X-Metabase-Session: TOKEN" | jq '{id, name, display, dataset_query: .dataset_query.native.query, template_tags: [.dataset_query.native."template-tags" | to_entries[] | {key: .key, type: .value.type, id: .value.id}]}'
   ```
3. Identificar em qual(is) dashboard(s) o card esta:
   ```bash
   HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2)
   python3 -c "import json; slim=json.load(open('$HUB/memory/mapa_metabase_slim.json')); [print(f'  dash {did}: {d[\"name\"]}') for did,d in slim['dashboards'].items() if any(str(ID) in str(d) for _ in [1])]"
   ```
4. Apresentar: nome, SQL (formatado), template-tags, tipo de display, dashboards onde esta

## Regras

- Nunca imprimir JSON completo da API
- Filtrar com jq apenas campos necessarios
- Formatar SQL pra ficar legivel
