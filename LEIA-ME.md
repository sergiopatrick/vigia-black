# Vigia da Black

Robô que testa pos.sanar.com.br e cetrus.com.br várias vezes por dia durante a Black November de 2026 e avisa o canal #vigia-dos-sites no Slack da Sanar quando algo quebra. Para sozinho em 01/12/2026 (`FIM` em `config.py`).

## O que roda e quando

| Tarefa | Quando (Brasília) | O que confere |
|---|---|---|
| `no-ar` | a cada 5 min | home e vitrine dos dois sites respondem, e o checkout responde para um curso real |
| `jornada` | toda hora, aos 7 min | navegador real abre home, vitrine e cada curso, confere preço, botão de matrícula, checkout com o mesmo valor do site, turma disponível (Cetrus), formulário, tag de medição e Google (noindex). Também abre no celular |
| `velocidade` | 8h, 13h e 19h | nota de performance, LCP, CLS e TBT no celular (PageSpeed com chave, ou Lighthouse no próprio servidor) |
| `formulario` | 10h30 e 16h30 | envia um cadastro de teste de verdade em cada site e confere que o HubSpot aceitou |
| `resumo` | 9h | resumo do dia anterior no Slack |

A Sanar Pós tem todos os cursos testados toda hora. O Cetrus tem cerca de 430 cursos, então entram 20 por hora em rodízio e todos passam a cada 22 horas. Curso que precisa ser testado toda hora vai em `prioritarias` no `config.py`.

## Como ele decide alertar

Cada página tem uma linha de base aprendida na primeira rodada saudável (tinha preço, tinha formulário, tinha turma, o valor batia com o checkout). Alerta só quando algo que existia some, então decisão de cadastro (curso sem preço de propósito) não vira alarme.

Falha crítica é conferida de novo depois de 5 minutos dentro da mesma rodada. No `no-ar`, precisa falhar em duas rodadas seguidas. Só então vai para o Slack, com print. Enquanto continuar quebrado, manda lembrete a cada 2 horas. Quando volta, avisa que voltou e quanto tempo durou. Se uma mudança ficar mais de 12 horas alertada, o robô entende que é o novo normal e para de lembrar.

O desafio "Checking your browser" do WordPress.com não é queda. O robô registra como bloqueio e só avisa se ficar cego em metade das páginas da rodada.

## Segredos do repositório

Configurar em Settings, Secrets and variables, Actions.

| Segredo | Para quê | Obrigatório |
|---|---|---|
| `SLACK_WEBHOOK_URL` | webhook do canal #vigia-dos-sites no Slack da Sanar | sim |
| `VIGIA_EMAIL_TESTE` | e-mail base do cadastro de teste, vira `nome+vigia-pos-AAAAMMDDHHMM@dominio` | para o formulário |
| `VIGIA_TELEFONE_TESTE` | telefone do cadastro de teste | para o formulário |
| `HUBSPOT_TOKEN` | token de app privado com leitura de contatos, confirma que o lead chegou | não |
| `PSI_KEY` | chave da API do PageSpeed, traz também os dados reais do Chrome (CrUX) | não |

## Estado

Fica no branch `estado`. Um arquivo por tarefa e site (falhas abertas e linha de base), eventos por dia em `eventos/AAAA-MM-DD/` e prints das falhas em `prints/`.

## Rodar na mão

```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python vigia.py jornada --site pos
.venv/bin/python vigia.py formulario --site cetrus --simular   # preenche e não envia
```

Para pausar tudo, desligar os workflows em Actions.
