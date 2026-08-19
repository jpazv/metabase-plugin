---
description: "Gerar textos resumidos com a estrutura definida de tarefas da nossa gestao de projeto. Uso: /tarefa-registro"
---

O objetivo desta Skill e gerar automaticamente o registro das tarefas executadas durante uma sessao de trabalho, produzindo um texto pronto para ser cadastrado na ferramenta de gestao de projetos da empresa.

## Objetivo da Skill

Ao ser executada, a Skill devera analisar toda a conversa da sessao e identificar:

- funcionalidades desenvolvidas
- correcoes (bugs/fixes)
- melhorias realizadas
- refatoracoes
- configuracoes efetuadas
- integracoes implementadas
- alteracoes em banco de dados
- alteracoes em APIs
- mudancas em fluxos do n8n
- alteracoes em documentacao
- qualquer outra atividade tecnica relevante realizada durante a sessao

A Skill deve consolidar essas informacoes em uma ou mais tarefas, agrupando assuntos relacionados quando fizer sentido.

## Formato de saida

Para cada tarefa encontrada, gerar exatamente a seguinte estrutura:

### Titulo

Um titulo curto, objetivo e descritivo da atividade realizada.

### Objetivo

Responder a pergunta: **O que precisava ser resolvido ou entregue?**

Descreva claramente o objetivo da demanda.

### Contexto

Responder a pergunta: **Quais informacoes ajudam o time a entender a demanda?**

Inclua informacoes como:

- problema identificado
- decisoes tomadas
- tecnologias envolvidas
- arquivos ou componentes alterados
- impacto da alteracao
- dependencias relevantes

O contexto deve ser suficiente para que outro desenvolvedor compreenda a atividade sem precisar reler toda a conversa.

### Criterio de sucesso

Responder a pergunta: **Como saberemos que a demanda foi concluida com sucesso?**

Liste os resultados esperados ou efetivamente alcancados, por exemplo:

- funcionalidade implementada
- erro corrigido
- documentacao atualizada
- fluxo funcionando corretamente
- performance melhorada
- integracao concluida
- testes realizados com sucesso

## Regras importantes

- Baseie-se exclusivamente no conteudo da sessao
- Nao invente atividades que nao ocorreram
- Agrupe alteracoes relacionadas em uma unica tarefa quando fizer sentido
- Separe tarefas apenas quando representarem entregas distintas
- Escreva em linguagem profissional, objetiva e adequada para ferramentas como Jira, ClickUp, Azure DevOps ou similares
- Evite mencionar que as informacoes foram extraidas da conversa
- Foque na entrega realizada, e nao no processo de discussao
- Sempre escreva como se a tarefa fosse ser registrada oficialmente no backlog ou historico do projeto

## Fluxo esperado

1. Analisar toda a conversa da sessao
2. Identificar as atividades executadas
3. Agrupar atividades relacionadas
4. Gerar uma ou mais tarefas no formato especificado
5. Retornar somente as tarefas prontas para cadastro, sem explicacoes adicionais
