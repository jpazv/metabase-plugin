---
description: Bootstrap de sessao Metabase. Ativa quando a conversa envolver dashboards, cards, BI, KPI, ou API do Metabase. Roda sync, verifica issues do JP, e entrega briefing.
---

## Boot — sync do indice

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && python3 "$HUB/plugins/metabase/scripts/mb_sync.py" --quiet 2>&1 || echo "sync falhou"`

## Boot — briefing automatico (issues do JP + check vs Metabase)

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && python3 "$HUB/plugins/metabase/scripts/mb_check.py" --briefing 2>&1 || echo "check falhou"`

## Contexto

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && cat "$HUB/memory/metabase-boot.md"`

## Regras obrigatorias

- **Somente SELECT** — proibido ALTER/CREATE/DELETE/UPDATE no banco
- **Nunca** imprimir JSON inteiro de resposta da API no terminal
- **Sempre** filtrar com jq apenas campos necessarios
- **Agrupar** multiplas operacoes num unico script Python
- **Suprimir** output de PUT/POST: `-o /dev/null -w '%{http_code}'`
- **Usar o indice slim** antes de chamar a API — a maioria das perguntas de localizacao pode ser respondida sem chamada
- **PUT dashboard** exige `tabs` + `dashcards` juntos — omitir um apaga o outro
- **Conferir KPI** no dashboard 10 e colecao "Dados Gerais" (id 13) antes de entregar

## Skills disponiveis

- `/mb-check [issue]` — verifica alinhamento entre issue e Metabase
- `/mb-status` — visao geral rapida (dashboards recentes + tasks)
- `/mb-dash <id>` — inspeciona dashboard
- `/mb-card <id>` — inspeciona card
- `/mb-report [issue]` — gera relatorio MD de validacao
