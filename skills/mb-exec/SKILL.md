---
description: Executa uma task do Metabase de ponta a ponta — le a issue, monta o payload, pede confirmacao, executa, checa e documenta. Uso: /mb-exec <issue_number>
---

## Fluxo automatizado

Esta skill executa o fluxo completo de uma task do Metabase:

1. **Ler** — busca a issue no GitHub e parseia os campos estruturados
2. **Montar** — prepara o payload (SQL, card config, filtros, position)
3. **Confirmar** — mostra o que vai fazer e espera aprovacao do usuario
4. **Executar** — cria/ajusta via API do Metabase
5. **Checar** — valida cada criterio de aceite
6. **Documentar** — gera report MD e sugere mover task pra "Em validacao"

## Passo 1 — Ler a issue

```bash
gh issue view ISSUE_NUMBER -R Grupo-Velas/produtividade-bi-dev --json number,title,body,labels,assignees
```

Parsear os campos do template:
- **Tipo**: criar-card, ajustar-card, criar-aba, ajustar-filtro, clonar-dash, criar-dash, remover-card
- **Dashboard**: `dash:ID` ou nome
- **Especificacao**: card name, tipo, tabelas, filtros, aba, sql
- **Criterio de aceite**: lista de checagens

## Passo 2 — Montar payload

Carregar contexto do Metabase:

!`HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2) && cat "$HUB/memory/metabase-boot.md"`

Dependendo do tipo:

### criar-card
1. Consultar o dashboard alvo para obter tabs, params existentes
2. Montar SQL baseado nas tabelas e filtros da especificacao
3. Definir template-tags compatíveis com os params do dash
4. Preparar payload do card (POST /api/card)
5. Preparar payload do dashcard (PUT /api/dashboard/:id)

### ajustar-card
1. Consultar o card atual (GET /api/card/:id)
2. Identificar diferencas entre estado atual e especificacao
3. Preparar payload de update (PUT /api/card/:id)

### criar-aba
1. Consultar dashboard para tabs atuais
2. Preparar novo tab no payload do PUT /api/dashboard/:id

### ajustar-filtro
1. Consultar dashboard para params e dashcards
2. Identificar template-tags sem mapping
3. Preparar parameter_mappings no PUT /api/dashboard/:id

### clonar-dash
1. Consultar dashboard origem completo
2. Preparar POST /api/dashboard com adaptacoes

## Passo 3 — Confirmar

Apresentar ao usuario:
- Resumo da acao em linguagem natural
- Endpoint(s) que serao chamados
- Campos principais do payload (nome, SQL resumido, filtros)
- Perguntar: "Confirma a execucao?"

**NAO executar sem confirmacao explicita do usuario.**

## Passo 4 — Executar

Agrupar todas as chamadas num unico script Python para eficiencia de tokens.
Suprimir output de resposta: `-o /dev/null -w '%{http_code}'`.
Guardar apenas IDs criados para o passo de checagem.

## Passo 5 — Checar

Rodar verificacao equivalente ao `/mb-check`:
- Card existe? (GET /api/card/:id)
- Card esta no dashboard? (GET /api/dashboard/:id → dashcards)
- Filtros mapeados? (parameter_mappings nao vazio)
- Dados retornando? (POST /api/card/:id/query com limit 1)
- Tipo de display correto?

Apresentar resultado ao usuario.

## Passo 6 — Documentar

Gerar report MD:
```bash
HUB=$(grep '^hub_path' ~/.config/agents/machine.toml | cut -d'"' -f2)
python3 "$HUB/plugins/metabase/scripts/mb_report.py" ISSUE_NUMBER
```

Sugerir:
- Commitar report no hub
- Mover task para "Em validacao" no kanban (requer confirmacao)

## Regras

- **Somente SELECT** no banco — proibido ALTER/CREATE/DELETE/UPDATE no schema
- Criar/modificar cards e dashboards via API e permitido
- Nunca imprimir JSON completo de resposta
- PUT dashboard exige `tabs` + `dashcards` juntos
- Sempre confirmar antes de executar
- Se qualquer checagem falhar, reportar e NAO documentar como concluido
