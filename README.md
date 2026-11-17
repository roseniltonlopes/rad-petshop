# 🐾 Patinhas & Cia: Agenda do Pet Shop (RAD)

Mini sistema de agendamento veterinário / pet shop feito com **Python + tkinter + SQLite + ViaCEP**, seguindo os princípios de Desenvolvimento Rápido de Aplicações.

**Demo web:** https://roseniltonlopes.github.io/rad-petshop/
(versão de demonstração em HTML/JS com as mesmas regras; o SQLite roda no navegador via sql.js)

## Como rodar a versão desktop (entregável principal)

```bash
python petshop_app.py
```

Usa apenas a biblioteca padrão do Python 3, então não há nada para instalar.

## Funcionalidades

| Requisito | Como foi atendido |
|---|---|
| Tela única | Formulário, botões, filtros e `Treeview` na mesma janela, que abre maximizada. Só a tabela rola |
| ViaCEP | Botão **Buscar CEP** ou busca automática ao completar 8 dígitos; preenche logradouro, bairro e cidade/UF |
| API fora do ar | Ao falhar, o sistema oferece **inserir endereço manualmente** (há também uma caixa de seleção para isso) |
| CRUD | Cadastrar, listar (tabela com cada dado na sua coluna), clicar na linha para editar e excluir com confirmação |
| Filtros | Dia, mês, ano e busca por responsável, pet, telefone, serviço, bairro ou cidade |
| Regras de data/hora | Não permite datas nem horários passados. Seg–Sáb, 08h–18h, intervalos de 30 min, até 180 dias de antecedência |
| Horário ocupado | O combo mostra só horários livres, e o banco tem `data_hora UNIQUE` |
| Chaves e unicidade | PK em todas as tabelas, `telefone UNIQUE` (um cadastro por cliente), `UNIQUE(responsavel_id, nome)` para pets |
| Log | `log_agendamentos.txt` em modo `'a'` (CONSULTA CEP, INSERÇÃO, ATUALIZAÇÃO, EXCLUSÃO) |
| Erros | `try/except` em API, banco e validação. Entradas limpas com `.strip()`/`.replace()` |

## Modelo de dados

```
responsaveis (id PK, nome, telefone UNIQUE, cep, logradouro, numero, bairro, cidade_uf)
pets         (id PK, responsavel_id FK → responsaveis, nome, UNIQUE(responsavel_id, nome))
agendamentos (id PK, pet_id FK → pets, servico, data_hora UNIQUE, criado_em)
```

## Arquivos

| Arquivo | Conteúdo |
|---|---|
| `petshop_app.py` | Aplicação desktop (tkinter) |
| `petshop.db` | Banco SQLite com 20 clientes de Fortaleza |
| `log_agendamentos.txt` | Log de auditoria |
| `Relatorio_RAD.pdf` | Relatório RAD (2 páginas) |
| `test_regras.py` | 19 testes automatizados (`python -m unittest test_regras -v`) |
| `docs/` | Versão web publicada no GitHub Pages |
