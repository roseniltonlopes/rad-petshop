/*
 * Patinhas & Cia — versão web (demonstração no GitHub Pages).
 * Mesmas regras do petshop_app.py: SQLite (via sql.js) com PK e UNIQUE, ViaCEP com
 * preenchimento manual, regras de data/hora, horário único e log de auditoria.
 *
 * Organização: 1. Configuração · 2. Utilidades · 3. Banco · 4. Validação · 5. ViaCEP
 *              6. Tela · 7. Ações (CRUD) · 8. Inicialização
 */
(() => {
  "use strict";

  // ======================= 1. Configuração =======================
  const SERVICOS = ["Consulta", "Vacinação", "Banho e Tosa", "Banho", "Tosa", "Exame"];
  const HORA_ABERTURA = 8, HORA_FECHAMENTO = 18, INTERVALO_MINUTOS = 30;
  const DIAS_ABERTOS = [1, 2, 3, 4, 5, 6];        // getDay(): 0 = domingo (fechado)
  const ANTECEDENCIA_MAXIMA_DIAS = 180;
  const MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
                 "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];
  const DIAS_SEMANA = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"];
  const CHAVE_BANCO = "patinhas_banco_v3", CHAVE_LOG = "patinhas_log_v3";
  const CAMPOS = ["pet", "responsavel", "telefone", "cep", "logradouro", "numero", "bairro", "cidade_uf", "servico", "data", "hora"];

  const ESQUEMA = `
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS responsaveis (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      nome TEXT NOT NULL,
      telefone TEXT NOT NULL UNIQUE,
      cep TEXT NOT NULL, logradouro TEXT NOT NULL, numero TEXT NOT NULL,
      bairro TEXT NOT NULL, cidade_uf TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      responsavel_id INTEGER NOT NULL REFERENCES responsaveis(id) ON DELETE CASCADE,
      nome TEXT NOT NULL COLLATE NOCASE,
      UNIQUE (responsavel_id, nome));
    CREATE TABLE IF NOT EXISTS agendamentos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pet_id INTEGER NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
      servico TEXT NOT NULL,
      data_hora TEXT NOT NULL UNIQUE,
      criado_em TEXT NOT NULL DEFAULT (datetime('now', 'localtime')));`;

  const CONSULTA_AGENDAMENTOS = `
    SELECT a.id, p.nome, r.nome, r.telefone, r.cep, r.logradouro, r.numero,
           r.bairro, r.cidade_uf, a.servico, a.data_hora
      FROM agendamentos a
      JOIN pets p    ON p.id = a.pet_id
      JOIN responsaveis r ON r.id = p.responsavel_id`;

  class RegraNegocioErro extends Error {}
  class ApiIndisponivelErro extends Error {}

  let banco;                     // conexão sql.js
  let agendamentoId = null;      // id em edição (null = novo)
  let ultimoCep = null;          // evita buscar o mesmo CEP duas vezes

  // ======================= 2. Utilidades =======================
  const $ = (id) => document.getElementById(id);
  const doisDigitos = (n) => String(n).padStart(2, "0");
  const limparTexto = (texto) => String(texto ?? "").trim().replace(/\s+/g, " ");
  const somenteDigitos = (texto) => String(texto ?? "").replace(/\D/g, "");
  const escaparHtml = (texto) => String(texto).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function formatarCep(cep) {
    cep = somenteDigitos(cep);
    return cep.length === 8 ? `${cep.slice(0, 5)}-${cep.slice(5)}` : cep;
  }

  function formatarTelefone(telefone) {
    const t = somenteDigitos(telefone);
    if (t.length === 11) return `(${t.slice(0, 2)}) ${t.slice(2, 7)}-${t.slice(7)}`;
    if (t.length === 10) return `(${t.slice(0, 2)}) ${t.slice(2, 6)}-${t.slice(6)}`;
    return t;
  }

  const dataIso = (d) => `${d.getFullYear()}-${doisDigitos(d.getMonth() + 1)}-${doisDigitos(d.getDate())}`;
  const isoParaBr = (iso) => iso.split("-").reverse().join("/");
  function criarDataHora(iso, hora = "00:00") {
    const [ano, mes, dia] = iso.split("-").map(Number);
    const [h, m] = hora.split(":").map(Number);
    return new Date(ano, mes - 1, dia, h, m);
  }

  function horariosDoDia() {
    const horarios = [];
    for (let minuto = HORA_ABERTURA * 60; minuto < HORA_FECHAMENTO * 60; minuto += INTERVALO_MINUTOS) {
      horarios.push(`${doisDigitos(Math.floor(minuto / 60))}:${doisDigitos(minuto % 60)}`);
    }
    return horarios;
  }

  // ======================= 3. Banco (SQLite no navegador) =======================
  const consultar = (sql, parametros = []) => {
    const comando = banco.prepare(sql);
    comando.bind(parametros);
    const linhas = [];
    while (comando.step()) linhas.push(comando.get());
    comando.free();
    return linhas;
  };
  const executar = (sql, parametros = []) => banco.run(sql, parametros);
  const ultimoIdInserido = () => consultar("SELECT last_insert_rowid()")[0][0];

  function gravarNoNavegador() {
    try {
      const bytes = banco.export();
      let binario = "";
      for (let i = 0; i < bytes.length; i += 0x8000) binario += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      localStorage.setItem(CHAVE_BANCO, btoa(binario));
    } catch (erro) { console.warn("Não foi possível guardar o banco no navegador", erro); }
  }

  function lerDoNavegador() {
    try {
      const base64 = localStorage.getItem(CHAVE_BANCO);
      return base64 ? Uint8Array.from(atob(base64), (c) => c.charCodeAt(0)) : null;
    } catch { return null; }
  }

  /** Lê um arquivo publicado ao lado da página (petshop.db ou o log). Devolve null se não existir. */
  async function baixarArquivoPublicado(nome, formato) {
    try {
      const resposta = await fetch(nome, { cache: "no-store" });
      if (!resposta.ok) return null;
      return formato === "text" ? await resposta.text() : new Uint8Array(await resposta.arrayBuffer());
    } catch { return null; }
  }

  function listarAgendamentos({ dia, mes, ano, busca }) {
    const condicoes = [], parametros = [];
    if (ano) { condicoes.push("substr(a.data_hora, 1, 4) = ?"); parametros.push(ano); }
    if (mes) { condicoes.push("substr(a.data_hora, 6, 2) = ?"); parametros.push(mes); }
    if (dia) { condicoes.push("substr(a.data_hora, 9, 2) = ?"); parametros.push(dia); }
    if (busca) {
      const termo = `%${busca}%`, telefone = `%${somenteDigitos(busca) || busca}%`;
      condicoes.push("(p.nome LIKE ? OR r.nome LIKE ? OR r.telefone LIKE ? OR a.servico LIKE ? OR r.bairro LIKE ? OR r.cidade_uf LIKE ?)");
      parametros.push(termo, termo, telefone, termo, termo, termo);
    }
    const onde = condicoes.length ? ` WHERE ${condicoes.join(" AND ")}` : "";
    return consultar(`${CONSULTA_AGENDAMENTOS}${onde} ORDER BY a.data_hora`, parametros);
  }

  function horariosLivres(iso, ignorarId = null) {
    const agora = new Date();
    if (!iso || criarDataHora(iso) < criarDataHora(dataIso(agora))) return [];
    if (!DIAS_ABERTOS.includes(criarDataHora(iso).getDay())) return [];
    const ocupados = consultar(
      "SELECT substr(data_hora, 12, 5) FROM agendamentos WHERE substr(data_hora, 1, 10) = ? AND id IS NOT ?",
      [iso, ignorarId]).map((linha) => linha[0]);
    return horariosDoDia().filter((hora) => !ocupados.includes(hora) && criarDataHora(iso, hora) > agora);
  }

  /** Data sugerida no formulário: hoje, ou o próximo dia que ainda tenha horário livre. */
  function proximoDiaComHorario() {
    const dia = new Date();
    for (let i = 0; i <= ANTECEDENCIA_MAXIMA_DIAS; i++, dia.setDate(dia.getDate() + 1)) {
      if (horariosLivres(dataIso(dia)).length) return dataIso(dia);
    }
    return dataIso(new Date());
  }

  function salvarAgendamento(dados, id = null) {
    executar("BEGIN");  // transação: confirma tudo ou desfaz tudo
    try {
      const responsavelId = salvarResponsavel(dados, id);
      const petId = salvarPet(responsavelId, dados.pet);
      if (id) {
        executar("UPDATE agendamentos SET pet_id = ?, servico = ?, data_hora = ? WHERE id = ?", [petId, dados.servico, dados.data_hora, id]);
      } else {
        executar("INSERT INTO agendamentos (pet_id, servico, data_hora) VALUES (?, ?, ?)", [petId, dados.servico, dados.data_hora]);
        id = ultimoIdInserido();
      }
      executar("COMMIT");
      gravarNoNavegador();
      return id;
    } catch (erro) {
      executar("ROLLBACK");
      if (/UNIQUE.*data_hora/.test(erro.message)) throw new RegraNegocioErro("Este horário já está reservado. Escolha outro horário.");
      throw erro;
    }
  }

  function salvarResponsavel(dados, agendamento) {
    const dono = consultar("SELECT id, nome FROM responsaveis WHERE telefone = ?", [dados.telefone])[0];
    const responsavelEmEdicao = agendamento
      ? consultar("SELECT p.responsavel_id FROM agendamentos a JOIN pets p ON p.id = a.pet_id WHERE a.id = ?", [agendamento])[0]?.[0]
      : null;
    if (dono && dono[0] !== responsavelEmEdicao && dono[1].toLowerCase() !== dados.responsavel.toLowerCase()) {
      throw new RegraNegocioErro(`O telefone ${formatarTelefone(dados.telefone)} já pertence ao cliente "${dono[1]}". Cada cliente tem um cadastro único.`);
    }
    const responsavelId = dono ? dono[0] : responsavelEmEdicao;
    const valores = [dados.responsavel, dados.telefone, dados.cep, dados.logradouro, dados.numero, dados.bairro, dados.cidade_uf];
    if (responsavelId) {
      executar("UPDATE responsaveis SET nome = ?, telefone = ?, cep = ?, logradouro = ?, numero = ?, bairro = ?, cidade_uf = ? WHERE id = ?", [...valores, responsavelId]);
      return responsavelId;
    }
    executar("INSERT INTO responsaveis (nome, telefone, cep, logradouro, numero, bairro, cidade_uf) VALUES (?, ?, ?, ?, ?, ?, ?)", valores);
    return ultimoIdInserido();
  }

  function salvarPet(responsavelId, nome) {
    const pet = consultar("SELECT id FROM pets WHERE responsavel_id = ? AND nome = ?", [responsavelId, nome])[0];
    if (pet) return pet[0];
    executar("INSERT INTO pets (responsavel_id, nome) VALUES (?, ?)", [responsavelId, nome]);
    return ultimoIdInserido();
  }

  // ======================= Log de auditoria =======================
  const lerLog = () => { try { return localStorage.getItem(CHAVE_LOG) || ""; } catch { return ""; } };

  function registrarLog(tipo, mensagem) {
    const d = new Date();
    const momento = `${doisDigitos(d.getDate())}/${doisDigitos(d.getMonth() + 1)}/${d.getFullYear()} ${d.toTimeString().slice(0, 8)}`;
    try { localStorage.setItem(CHAVE_LOG, `${lerLog()}[${momento}] ${tipo} - ${mensagem}\n`); } catch { /* sem armazenamento: segue */ }
    mostrarLog();
  }

  // ======================= 4. Validação =======================
  function validarDataHora(iso, hora) {
    if (!iso) throw new RegraNegocioErro("Informe uma data.");
    const agora = new Date(), hoje = criarDataHora(dataIso(agora)), data = criarDataHora(iso);
    const limite = new Date(hoje); limite.setDate(limite.getDate() + ANTECEDENCIA_MAXIMA_DIAS);
    if (data < hoje) throw new RegraNegocioErro("Não é permitido agendar em datas passadas.");
    if (data > limite) throw new RegraNegocioErro(`Só é possível agendar com até ${ANTECEDENCIA_MAXIMA_DIAS} dias de antecedência.`);
    if (!DIAS_ABERTOS.includes(data.getDay())) throw new RegraNegocioErro(`O pet shop não abre aos ${DIAS_SEMANA[data.getDay()]}s.`);
    if (!horariosDoDia().includes(hora)) throw new RegraNegocioErro("Escolha um horário disponível da agenda.");
    if (criarDataHora(iso, hora) <= agora) throw new RegraNegocioErro("Não é permitido agendar em um horário que já passou.");
    return `${iso} ${hora}`;
  }

  function lerFormulario() {
    const valor = (id) => limparTexto($(id).value);
    const falhar = (id, mensagem) => { $(id).classList.add("erro"); $(id).focus(); throw new RegraNegocioErro(mensagem); };
    const dados = {
      pet: valor("pet"), responsavel: valor("responsavel"), telefone: somenteDigitos($("telefone").value),
      cep: somenteDigitos($("cep").value), logradouro: valor("logradouro"), numero: valor("numero"),
      bairro: valor("bairro"), cidade_uf: valor("cidade_uf"), servico: valor("servico"),
    };
    if (!dados.pet) falhar("pet", "Informe o nome do pet.");
    if (!dados.responsavel) falhar("responsavel", "Informe o nome do responsável.");
    if (![10, 11].includes(dados.telefone.length)) falhar("telefone", "Telefone inválido. Informe DDD + número (10 ou 11 dígitos).");
    if (dados.cep.length !== 8) falhar("cep", "O CEP deve ter exatamente 8 dígitos.");
    if (!dados.logradouro || !dados.bairro || !dados.cidade_uf) falhar("cep", "Endereço incompleto. Busque o CEP ou preencha manualmente.");
    if (!dados.numero) falhar("numero", "Informe o número/complemento do endereço.");
    if (!SERVICOS.includes(dados.servico)) falhar("servico", "Selecione um serviço válido.");
    dados.cep = formatarCep(dados.cep);
    dados.data_hora = validarDataHora($("data").value, $("hora").value);
    return dados;
  }

  // ======================= 5. ViaCEP =======================
  async function consultarCep(cep) {
    let resposta;
    try {
      resposta = await fetch(`https://viacep.com.br/ws/${cep}/json/`, { signal: AbortSignal.timeout(6000) });
    } catch {
      throw new ApiIndisponivelErro("Sem conexão com o ViaCEP.");
    }
    if (resposta.status === 400) throw new RegraNegocioErro("CEP em formato inválido.");
    if (!resposta.ok) throw new ApiIndisponivelErro(`ViaCEP respondeu com erro HTTP ${resposta.status}.`);
    const resultado = await resposta.json().catch(() => { throw new ApiIndisponivelErro("Resposta inesperada do ViaCEP."); });
    if (resultado.erro) throw new RegraNegocioErro(`CEP ${formatarCep(cep)} não encontrado.`);
    return { cep: formatarCep(cep), logradouro: resultado.logradouro || "", bairro: resultado.bairro || "", cidade_uf: `${resultado.localidade}/${resultado.uf}` };
  }

  // ======================= 6. Tela =======================
  function aviso(mensagem) {
    const toast = $("toast");
    toast.textContent = mensagem;
    toast.classList.add("ativo");
    clearTimeout(aviso.temporizador);
    aviso.temporizador = setTimeout(() => toast.classList.remove("ativo"), 3200);
  }

  /** Caixa de mensagem (equivalente ao messagebox do tkinter). Resolve true se clicar em OK. */
  function caixaDeMensagem(titulo, texto, { pergunta = false, ok = "OK", cancelar = "Cancelar" } = {}) {
    const dialogo = $("dialogo");
    $("dialogoTitulo").textContent = titulo;
    $("dialogoTexto").textContent = texto;
    $("dialogoSim").textContent = ok;
    $("dialogoNao").textContent = cancelar;
    $("dialogoNao").hidden = !pergunta;
    return new Promise((resolver) => {
      dialogo.returnValue = "";
      dialogo.addEventListener("close", () => resolver(dialogo.returnValue === "sim"), { once: true });
      dialogo.showModal();
    });
  }

  function definirEnderecoManual(manual) {
    $("enderecoManual").checked = manual;
    for (const id of ["logradouro", "bairro", "cidade_uf"]) $(id).readOnly = !manual;
  }

  function atualizarHorarios(horaAtual = $("hora").value) {
    const iso = $("data").value;
    const livres = horariosLivres(iso, agendamentoId);
    $("hora").innerHTML = `<option value="">${livres.length ? "Selecione…" : "—"}</option>` +
      livres.map((hora) => `<option>${hora}</option>`).join("");
    if (livres.includes(horaAtual)) $("hora").value = horaAtual;

    let texto;
    if (!iso) texto = "Escolha uma data.";
    else if (iso < dataIso(new Date())) texto = "Data no passado: escolha outra data.";
    else if (!DIAS_ABERTOS.includes(criarDataHora(iso).getDay())) texto = "Fechado aos domingos.";
    else if (!livres.length) texto = "Sem horários livres nesta data.";
    else texto = `${livres.length} horário(s) livre(s) em ${isoParaBr(iso)}.`;
    $("avisoHorarios").textContent = texto;
  }

  function lerFiltros() {
    return { dia: $("filtroDia").value, mes: $("filtroMes").value, ano: $("filtroAno").value, busca: limparTexto($("filtroBusca").value) };
  }

  function atualizarAnosDoFiltro() {
    const escolhido = $("filtroAno").value;
    const anos = new Set(consultar("SELECT DISTINCT substr(data_hora, 1, 4) FROM agendamentos").map((l) => l[0]));
    anos.add(String(new Date().getFullYear()));
    $("filtroAno").innerHTML = `<option value="">Todos</option>` + [...anos].sort().map((a) => `<option>${a}</option>`).join("");
    $("filtroAno").value = anos.has(escolhido) ? escolhido : "";
  }

  function mostrarTabela() {
    const linhas = listarAgendamentos(lerFiltros());
    const agora = new Date();
    $("tabela").innerHTML = linhas.map((l) => {
      const [iso, hora] = l[10].split(" ");
      const classes = [criarDataHora(iso, hora) < agora ? "passado" : "", l[0] === agendamentoId ? "selecionada" : ""].join(" ");
      return `<tr data-id="${l[0]}" class="${classes.trim()}" tabindex="0">
        <td class="centro">${l[0]}</td><td>${escaparHtml(l[1])}</td><td>${escaparHtml(l[2])}</td>
        <td>${formatarTelefone(l[3])}</td><td class="centro">${escaparHtml(l[4])}</td><td>${escaparHtml(l[5])}</td>
        <td class="centro">${escaparHtml(l[6])}</td><td>${escaparHtml(l[7])}</td><td>${escaparHtml(l[8])}</td>
        <td>${escaparHtml(l[9])}</td><td class="centro">${isoParaBr(iso)}</td><td class="centro">${hora}</td></tr>`;
    }).join("");

    const total = consultar("SELECT COUNT(*) FROM agendamentos")[0][0];
    $("vazio").hidden = linhas.length > 0;
    $("vazio").textContent = total ? "Nenhum agendamento encontrado com esses filtros." : "Nenhum agendamento ainda. Cadastre o primeiro acima.";
    $("contador").textContent = `${linhas.length} de ${total} agendamento(s)`;
  }

  function mostrarLog() {
    $("log").textContent = lerLog() || "O log ainda está vazio.";
  }

  function modoEdicao(editando) {
    $("btnCadastrar").disabled = editando;
    $("btnAtualizar").disabled = !editando;
    $("btnExcluir").disabled = !editando;
    $("tituloFormulario").textContent = editando ? `Editando agendamento ID ${agendamentoId}` : "Novo agendamento";
  }

  function limparFormulario() {
    agendamentoId = null;
    ultimoCep = null;
    for (const id of CAMPOS) { $(id).value = ""; $(id).classList.remove("erro"); }
    $("servico").value = SERVICOS[0];
    $("data").value = proximoDiaComHorario();
    definirEnderecoManual(false);
    atualizarHorarios();
    modoEdicao(false);
    atualizarAnosDoFiltro();
    mostrarTabela();
  }

  function selecionarAgendamento(id) {
    const l = consultar(`${CONSULTA_AGENDAMENTOS} WHERE a.id = ?`, [id])[0];
    if (!l) return;
    agendamentoId = l[0];
    const [iso, hora] = l[10].split(" ");
    const valores = { pet: l[1], responsavel: l[2], telefone: formatarTelefone(l[3]), cep: l[4], logradouro: l[5],
                      numero: l[6], bairro: l[7], cidade_uf: l[8], servico: l[9], data: iso };
    for (const [campo, valor] of Object.entries(valores)) { $(campo).value = valor; $(campo).classList.remove("erro"); }
    ultimoCep = somenteDigitos(l[4]);
    definirEnderecoManual(false);
    atualizarHorarios(hora);
    modoEdicao(true);
    mostrarTabela();
  }

  // ======================= 7. Ações (CRUD + CEP) =======================
  async function buscarCep() {
    const texto = $("cep").value, cep = somenteDigitos(texto);
    try {
      if (cep.length !== 8) throw new RegraNegocioErro("O CEP deve ter exatamente 8 dígitos.");
      ultimoCep = cep;
      $("btnBuscarCep").disabled = true;
      const endereco = await consultarCep(cep);
      for (const [campo, valor] of Object.entries(endereco)) { $(campo).value = valor; $(campo).classList.remove("erro"); }
      definirEnderecoManual(!endereco.logradouro);   // CEP geral de cidade: completa à mão
      registrarLog("CONSULTA CEP", `CEP "${endereco.cep}" consultado com sucesso (${endereco.cidade_uf}).`);
      $("numero").focus();
    } catch (erro) {
      if (erro instanceof ApiIndisponivelErro) {
        registrarLog("CONSULTA CEP", `Falha ao consultar o CEP "${formatarCep(cep)}": ${erro.message}`);
        const manual = await caixaDeMensagem("ViaCEP indisponível", "Não foi possível consultar o ViaCEP agora.\n\nDeseja preencher o endereço manualmente?",
                                             { pergunta: true, ok: "Preencher manualmente", cancelar: "Agora não" });
        if (manual) { definirEnderecoManual(true); $("logradouro").focus(); }
      } else {
        registrarLog("CONSULTA CEP", `CEP "${limparTexto(texto)}" inválido ou não encontrado.`);
        await caixaDeMensagem("CEP", `${erro.message}\n\nConfira o número ou marque "Inserir endereço manualmente".`);
      }
    } finally {
      $("btnBuscarCep").disabled = false;
    }
  }

  /** Valida e grava. Devolve os dados salvos ou null se houve erro. */
  async function salvar(id = null) {
    for (const campo of CAMPOS) $(campo).classList.remove("erro");
    try {
      const dados = lerFormulario();
      dados.id = salvarAgendamento(dados, id);
      return dados;
    } catch (erro) {
      const texto = erro instanceof RegraNegocioErro ? erro.message : `Erro no banco de dados: ${erro.message}`;
      await caixaDeMensagem("Atenção", texto);
      return null;
    }
  }

  async function cadastrar(evento) {
    evento.preventDefault();
    const dados = await salvar();
    if (!dados) return;
    registrarLog("INSERÇÃO", `Agendamento Pet "${dados.pet}" (Responsável: ${dados.responsavel}, Serviço: ${dados.servico}) cadastrado com sucesso.`);
    limparFormulario();
    aviso(`Agendamento ID ${dados.id} cadastrado!`);
  }

  async function atualizar() {
    const dados = await salvar(agendamentoId);
    if (!dados) return;
    const [iso, hora] = dados.data_hora.split(" ");
    registrarLog("ATUALIZAÇÃO", `Agendamento ID ${dados.id} alterado (Novo Serviço: ${dados.servico}, Nova Data: ${isoParaBr(iso)} ${hora}).`);
    limparFormulario();
    aviso(`Agendamento ID ${dados.id} atualizado!`);
  }

  async function excluir() {
    const id = agendamentoId, pet = $("pet").value;
    const confirmou = await caixaDeMensagem("Confirmar exclusão", `Excluir o agendamento ID ${id} ("${pet}")?\nEsta ação não pode ser desfeita.`,
                                            { pergunta: true, ok: "Excluir" });
    if (!confirmou) return;
    executar("DELETE FROM agendamentos WHERE id = ?", [id]);
    gravarNoNavegador();
    registrarLog("EXCLUSÃO", `Agendamento ID ${id} ("${pet}") removido do sistema.`);
    limparFormulario();
    aviso(`Agendamento ID ${id} excluído.`);
  }

  function baixarArquivo(nome, conteudo, tipo) {
    const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([conteudo], { type: tipo })), download: nome });
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function trocarAba(botao) {
    for (const aba of document.querySelectorAll(".aba")) {
      const ativa = aba === botao;
      aba.classList.toggle("ativa", ativa);
      aba.setAttribute("aria-selected", ativa);
      $(aba.dataset.painel).hidden = !ativa;
    }
  }

  // ======================= 8. Inicialização =======================
  function preencherOpcoes() {
    $("servico").innerHTML = SERVICOS.map((s) => `<option>${s}</option>`).join("");
    $("filtroDia").innerHTML = `<option value="">Todos</option>` +
      Array.from({ length: 31 }, (_, i) => `<option>${doisDigitos(i + 1)}</option>`).join("");
    $("filtroMes").innerHTML = `<option value="">Todos</option>` +
      MESES.map((nome, i) => `<option value="${doisDigitos(i + 1)}">${nome}</option>`).join("");
    const hoje = new Date(), limite = new Date();
    limite.setDate(limite.getDate() + ANTECEDENCIA_MAXIMA_DIAS);
    $("data").min = dataIso(hoje);
    $("data").max = dataIso(limite);
  }

  function ligarEventos() {
    $("formulario").addEventListener("submit", cadastrar);
    $("btnAtualizar").addEventListener("click", atualizar);
    $("btnExcluir").addEventListener("click", excluir);
    $("btnLimpar").addEventListener("click", limparFormulario);
    $("btnBuscarCep").addEventListener("click", buscarCep);
    $("enderecoManual").addEventListener("change", (e) => definirEnderecoManual(e.target.checked));
    $("data").addEventListener("change", () => atualizarHorarios());
    $("hora").addEventListener("focus", () => atualizarHorarios());
    $("cep").addEventListener("input", () => {   // busca sozinho ao completar 8 dígitos
      const cep = somenteDigitos($("cep").value);
      if (cep.length === 8 && cep !== ultimoCep && !$("enderecoManual").checked) buscarCep();
    });
    $("cep").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); buscarCep(); } });
    $("telefone").addEventListener("blur", () => { $("telefone").value = formatarTelefone($("telefone").value); });

    for (const id of ["filtroDia", "filtroMes", "filtroAno"]) $(id).addEventListener("change", mostrarTabela);
    $("filtroBusca").addEventListener("input", mostrarTabela);
    $("btnLimparFiltros").addEventListener("click", () => {
      for (const id of ["filtroDia", "filtroMes", "filtroAno", "filtroBusca"]) $(id).value = "";
      mostrarTabela();
    });

    const abrirLinha = (e) => { const linha = e.target.closest("tr[data-id]"); if (linha) selecionarAgendamento(Number(linha.dataset.id)); };
    $("tabela").addEventListener("click", abrirLinha);
    $("tabela").addEventListener("keydown", (e) => { if (e.key === "Enter") abrirLinha(e); });
    for (const aba of document.querySelectorAll(".aba")) aba.addEventListener("click", () => trocarAba(aba));

    $("btnBaixarDb").addEventListener("click", () => baixarArquivo("petshop.db", banco.export(), "application/x-sqlite3"));
    $("btnBaixarLog").addEventListener("click", () => baixarArquivo("log_agendamentos.txt", lerLog(), "text/plain;charset=utf-8"));
    setInterval(() => { if (!agendamentoId) atualizarHorarios(); }, 60000);  // horários que passam saem da lista
  }

  async function iniciar() {
    preencherOpcoes();
    try {
      const SQL = await initSqlJs({ locateFile: (arquivo) => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/${arquivo}` });
      const salvo = lerDoNavegador();
      banco = new SQL.Database(salvo ?? await baixarArquivoPublicado("petshop.db", "arrayBuffer") ?? undefined);
      banco.exec(ESQUEMA);
      if (!salvo) {   // primeiro acesso: começa com a agenda e o log publicados junto da página
        gravarNoNavegador();
        try { localStorage.setItem(CHAVE_LOG, (await baixarArquivoPublicado("log_agendamentos.txt", "text")) ?? ""); } catch { /* segue sem log */ }
      }
    } catch (erro) {
      await caixaDeMensagem("Banco de dados", `Não foi possível iniciar o SQLite no navegador.\n\n${erro.message}`);
      return;
    }
    ligarEventos();
    mostrarLog();
    limparFormulario();
  }

  iniciar();
})();
