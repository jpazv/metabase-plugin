---
description: Inspeciona um dashboard do Metabase — tabs, cards, filtros, tabelas. Uso: /mb-dash <id>
---

## Contexto do boot

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && cat "$HUB/memory/metabase-boot.md"`

## Inspecionar dashboard

1. Ler o ID do dashboard do argumento do usuario
2. Primeiro consultar o slim JSON:
   ```bash
   HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2)
   python3 -c "import json; d=json.load(open('$HUB/memory/mapa_metabase_slim.json'))['dashboards'].get('ID'); print(json.dumps(d, indent=2, ensure_ascii=False))" 
   ```
3. Se precisar de mais detalhes (filtros, params), consultar a API:
   ```bash
   curl -s "https://metabase.grupovelas.com.br/api/dashboard/ID" \
     -H "X-Metabase-Session: TOKEN" | jq '{name, tabs: [.tabs[]|{id,name}], param_count: (.parameters|length), card_count: (.dashcards|length)}'
   ```
4. Apresentar de forma estruturada: nome, tabs, cards por tab, filtros, tabelas usadas, ultima atualizacao

## Regras

- Nunca imprimir JSON completo da API
- Filtrar com jq apenas campos necessarios
- Usar slim JSON sempre que possivel antes de chamar API
