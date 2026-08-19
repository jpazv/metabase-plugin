---
description: Verifica alinhamento entre GitHub issues e estado do Metabase. Uso: /mb-check [issue_number] ou /mb-check (todas abertas do JP).
---

## Verificar issues

Se o usuario passou um numero de issue, verificar essa especifica.
Se nao passou nada, verificar todas as abertas do JP.

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && python3 "$HUB/plugins/metabase/scripts/mb_check.py" --briefing $ARGS 2>&1`

## Apos o check

- Apresentar o briefing ao usuario
- Se alguma issue tiver status "verificacao manual necessaria", oferecer para inspecionar o dash/card via API
- Se o usuario quiser gerar um report de validacao, sugerir `/mb-report <issue>`
