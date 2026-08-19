---
description: Gera relatorio MD de validacao de uma issue do Metabase. Uso: /mb-report <issue_number>
---

## Gerar relatorio

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && python3 "$HUB/plugins/metabase/scripts/mb_report.py" $ARGS 2>&1`

## Apos gerar

1. Ler o report gerado e apresentar ao usuario
2. Perguntar se quer ajustar algo (adicionar observacoes, pendencias especificas)
3. Se o usuario quiser commitar, fazer commit no hub:
   ```bash
   git -C "$HUB" add memory/reports/metabase/ && git -C "$HUB" commit -m "report: validacao issue #N"
   ```
