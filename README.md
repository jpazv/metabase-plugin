# Metabase Plugin — Grupo Velas

Plugin de automacao para o Metabase do Grupo Velas, integrado com Claude Code.

## O que faz

- **Boot automatico**: ao abrir uma sessao de BI, sincroniza o indice do Metabase, puxa tasks abertas do kanban, cruza com o estado real dos dashboards, e entrega um briefing
- **Execucao assistida**: le uma issue estruturada, monta o payload, pede confirmacao, executa via API, verifica, e documenta
- **Verificacao**: cruza qualquer issue do GitHub com o estado do Metabase (busca fuzzy por nomes, IDs, tabelas)
- **Documentacao**: gera relatorios MD de validacao para tasks em revisao

## Skills

| Skill | O que faz |
|-------|-----------|
| `/mb-exec <issue>` | Fluxo completo: ler → montar → confirmar → executar → checar → documentar |
| `/mb-check [issue]` | Verifica alinhamento entre issue e Metabase |
| `/mb-status` | Visao geral: dashboards recentes + tasks ativas |
| `/mb-dash <id>` | Inspeciona dashboard (tabs, cards, filtros) |
| `/mb-card <id>` | Inspeciona card (SQL, params, display) |
| `/mb-report <issue>` | Gera relatorio MD de validacao |
| `metabase-boot` | Bootstrap automatico de sessao com briefing |

## Template de issue

O repo inclui um template de issue obrigatorio (`.github/ISSUE_TEMPLATE/metabase-task.yml`) com campos estruturados:

- **Tipo**: criar-card, ajustar-card, criar-aba, ajustar-filtro, clonar-dash, etc.
- **Dashboard**: ID ou nome do dashboard alvo
- **Especificacao**: card name, tipo, tabelas, filtros, aba, SQL
- **Criterio de aceite**: checklist de validacao

Isso permite que o `/mb-exec` leia a issue e execute automaticamente.

## Fluxo de trabalho

```
1. Colega cria issue usando o template
2. JP abre sessao Claude Code → boot mostra briefing com a nova task
3. /mb-exec 123 → le, monta, confirma, executa, checa, documenta
4. Report MD gerado → task movida pra "Em validacao"
5. Colega valida visualmente → task concluida
```

## Setup

### Pre-requisitos
- Claude Code com acesso ao hub
- `gh` CLI autenticado com scope `project`
- Token do Metabase em `$HUB/memory/metabase-boot.md`

### Instalar skills

Copiar a pasta `skills/` para `~/.claude/skills/`:

```bash
cp -r skills/* ~/.claude/skills/
```

Ou usar link simbolico:

```bash
for skill in skills/*/; do
  name=$(basename "$skill")
  ln -sf "$(pwd)/$skill" "$HOME/.claude/skills/$name"
done
```

### Configurar scripts

Os scripts precisam saber onde esta o hub. Eles usam paths relativos a partir de `$HUB/plugins/metabase/scripts/`.

## Scripts

| Script | Funcao |
|--------|--------|
| `scripts/mb_sync.py` | Sync incremental do indice Metabase |
| `scripts/mb_health.py` | Health check dos dashboards |
| `scripts/mb_check.py` | Verificacao issue vs Metabase |
| `scripts/mb_report.py` | Geracao de relatorio MD |

## Dados gerados

- `mapa_metabase_slim.json` — indice com IDs, nomes, tabelas de todos os dashboards
- `health.json` — resultado do ultimo health check
- `reports/metabase/YYYY-MM-DD-slug.md` — relatorios de validacao
