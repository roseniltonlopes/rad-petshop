/* Patinhas & Cia — versão web do protótipo RAD.
   Mesmas regras da versão desktop: SQLite (sql.js) com PK/UNIQUE, ViaCEP com
   fallback manual, bloqueio de datas/horários passados e de horário ocupado,
   log de auditoria em modo "append". */
(() => {
  "use strict";

  const SERVICOS = ["Consulta", "Vacinação", "Banho e Tosa", "Banho", "Tosa", "Exame"];
  const HORA_ABERTURA = 8, HORA_FECHAMENTO = 18, INTERVALO_MIN = 30;
  const DIAS_FUNCIONAMENTO = new Set([1, 2, 3, 4, 5, 6]); // seg..sáb (JS: 0 = domingo)
  const DIAS_MAX = 180;
  const CHAVE_DB = "patinhas_db_v1", CHAVE_LOG = "patinhas_log_v1";

  const SCHEMA = `
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS tutores (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      nome TEXT NOT NULL,
      telefone TEXT NOT NULL UNIQUE,
      cep TEXT NOT NULL, logradouro TEXT NOT NULL, numero TEXT NOT NULL,
      bairro TEXT NOT NULL, cidade_uf TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS pets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      tutor_id INTEGER NOT NULL REFERENCES tutores(id) ON DELETE CASCADE,
      nome TEXT NOT NULL COLLATE NOCASE,
      UNIQUE (tutor_id, nome)
    );
    CREATE TABLE IF NOT EXISTS agendamentos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pet_id INTEGER NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
      servico TEXT NOT NULL,
      data_hora TEXT NOT NULL UNIQUE,
      criado_em TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );`;

  const SQL_LISTAR = `
    SELECT a.id, p.nome, t.nome, t.telefone, t.cep, t.logradouro, t.numero,
           t.bairro, t.cidade_uf, a.servico, a.data_hora
      FROM agendamentos a JOIN pets p ON p.id = a.pet_id JOIN tutores t ON t.id = p.tutor_id`;

  class RegraErro extends Error {}
  class ApiForaErro extends Error {}

  const $ = (id) => document.getElementById(id);
  const campos = ["pet", "tutor", "telefone", "cep", "logradouro", "numero", "bairro", "cidade_uf", "servico", "data", "hora"];
  let db, agId = null, ultimoCep = null;

  // ---------------- utilidades ----------------
  const pad = (n) => String(n).padStart(2, "0");
  const limpar = (t) => String(t ?? "").trim().replace(/\s+/g, " ");
  const digitos = (t) => String(t ?? "").trim().replace(/[.\-()\/\s]/g, "").replace(/\D/g, "");
  const fmtCep = (c) => { c = digitos(c); return c.length === 8 ? `${c.slice(0, 5)}-${c.slice(5)}` : c; };
  const fmtTel = (t) => {
    t = digitos(t);
    if (t.length === 11) return `(${t.slice(0, 2)}) ${t.slice(2, 7)}-${t.slice(7)}`;
    if (t.length === 10) return `(${t.slice(0, 2)}) ${t.slice(2, 6)}-${t.slice(6)}`;
    return t;
  };
  const isoData = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const brData = (iso) => iso.split("-").reverse().join("/");
  const parseIso = (iso) => { const [a, m, d] = iso.split("-").map(Number); return new Date(a, m - 1, d); };
  const agoraTxt = () => { const d = new Date(); return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
  const DIAS_SEMANA = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"];

  function gerarHorarios() {
    const out = [];
    for (let m = HORA_ABERTURA * 60; m < HORA_FECHAMENTO * 60; m += INTERVALO_MIN) out.push(`${pad(Math.floor(m / 60))}:${pad(m % 60)}`);
    return out;
  }

  // ---------------- persistência ----------------
  function salvarDb() {
    try {
      const bytes = db.export();
      let bin = "";
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      localStorage.setItem(CHAVE_DB, btoa(bin));
    } catch (e) { console.warn("Não foi possível persistir o banco", e); }
  }
  function carregarDbSalvo() {
    try {
      const b64 = localStorage.getItem(CHAVE_DB);
      if (!b64) return null;
      const bin = atob(b64), arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      return arr;
    } catch { return null; }
  }
  function lerLog() { try { return localStorage.getItem(CHAVE_LOG) || ""; } catch { return ""; } }
  function registrarLog(tipo, msg) {
    const linha = `[${agoraTxt()}] ${tipo} - ${msg}\n`;
    try { localStorage.setItem(CHAVE_LOG, lerLog() + linha); } catch { /* sem storage: segue */ }
    $("log").textContent = lerLog() || "(vazio)";
    $("log").scrollTop = $("log").scrollHeight;
  }

  const query = (sql, params = []) => {
    const st = db.prepare(sql); st.bind(params);
    const rows = []; while (st.step()) rows.push(st.get()); st.free(); return rows;
  };
  const run = (sql, params = []) => db.run(sql, params);
  const ultimoId = () => query("SELECT last_insert_rowid()")[0][0];

  // ---------------- validação ----------------
  function validarDataHora(dataIso, hora, agora = new Date()) {
    if (!dataIso || !/^\d{4}-\d{2}-\d{2}$/.test(dataIso)) throw new RegraErro("Informe uma data válida.");
    const data = parseIso(dataIso);
    const hoje = new Date(agora.getFullYear(), agora.getMonth(), agora.getDate());
    if (data < hoje) throw new RegraErro("Não é permitido agendar em datas passadas.");
    const limite = new Date(hoje); limite.setDate(limite.getDate() + DIAS_MAX);
    if (data > limite) throw new RegraErro(`Agendamentos só podem ser feitos com até ${DIAS_MAX} dias de antecedência.`);
    if (!DIAS_FUNCIONAMENTO.has(data.getDay())) throw new RegraErro(`O pet shop não funciona aos ${DIAS_SEMANA[data.getDay()]}s.`);
    if (!hora) throw new RegraErro("Selecione um horário disponível.");
    if (!gerarHorarios().includes(hora)) throw new RegraErro(`Horário fora da agenda (${pad(HORA_ABERTURA)}:00 às ${pad(HORA_FECHAMENTO)}:00, a cada ${INTERVALO_MIN} min).`);
    const [h, m] = hora.split(":").map(Number);
    const dt = new Date(data); dt.setHours(h, m, 0, 0);
    if (dt <= agora) throw new RegraErro("Não é permitido agendar em um horário que já passou.");
    return `${dataIso} ${hora}`;
  }

  function prepararDados() {
    const v = Object.fromEntries(campos.map((c) => [c, $(c).value]));
    const d = {
      pet: limpar(v.pet), tutor: limpar(v.tutor), telefone: digitos(v.telefone), cep: digitos(v.cep),
      logradouro: limpar(v.logradouro), numero: limpar(v.numero), bairro: limpar(v.bairro),
      cidade_uf: limpar(v.cidade_uf), servico: limpar(v.servico),
    };
    const falha = (id, msg) => { $(id).classList.add("erro"); $(id).focus(); throw new RegraErro(msg); };
    if (!d.pet) falha("pet", "Informe o nome do pet.");
    if (!d.tutor) falha("tutor", "Informe o nome do tutor.");
    if (![10, 11].includes(d.telefone.length)) falha("telefone", "Telefone inválido. Informe DDD + número (10 ou 11 dígitos).");
    if (d.cep.length !== 8) falha("cep", "O CEP deve conter exatamente 8 dígitos.");
    if (!d.logradouro || !d.bairro || !d.cidade_uf) falha("cep", "Endereço incompleto. Busque o CEP ou marque o preenchimento manual.");
    if (!d.numero) falha("numero", "Informe o número/complemento do endereço.");
    if (!SERVICOS.includes(d.servico)) falha("servico", "Selecione um serviço válido.");
    d.cep = fmtCep(d.cep);
    d.data_hora = validarDataHora(v.data, v.hora);
    return d;
  }

  // ---------------- banco: regras ----------------
  function horariosLivres(dataIso, ignorarId = null, agora = new Date()) {
    if (!dataIso) return [];
    const data = parseIso(dataIso);
    const hoje = new Date(agora.getFullYear(), agora.getMonth(), agora.getDate());
    if (data < hoje || !DIAS_FUNCIONAMENTO.has(data.getDay())) return [];
    const ocupados = new Set(query("SELECT substr(data_hora,12,5) FROM agendamentos WHERE substr(data_hora,1,10)=? AND id IS NOT ?", [dataIso, ignorarId]).map((r) => r[0]));
    return gerarHorarios().filter((h) => {
      const [hh, mm] = h.split(":").map(Number);
      const dt = new Date(data); dt.setHours(hh, mm, 0, 0);
      return dt > agora && !ocupados.has(h);
    });
  }

  function salvar(d, id = null) {
    run("BEGIN");
    try {
      const existente = query("SELECT id, nome FROM tutores WHERE telefone = ?", [d.telefone])[0];
      const tutorAtual = id ? query("SELECT p.tutor_id FROM agendamentos a JOIN pets p ON p.id=a.pet_id WHERE a.id=?", [id])[0]?.[0] : null;
      if (existente && existente[0] !== tutorAtual && existente[1].toLocaleLowerCase() !== d.tutor.toLocaleLowerCase())
        throw new RegraErro(`O telefone ${fmtTel(d.telefone)} já está cadastrado para o tutor "${existente[1]}". Cada cliente possui um cadastro único.`);
      let tutorId = existente ? existente[0] : (tutorAtual || null);
      const end = [d.tutor, d.telefone, d.cep, d.logradouro, d.numero, d.bairro, d.cidade_uf];
      if (tutorId) run("UPDATE tutores SET nome=?, telefone=?, cep=?, logradouro=?, numero=?, bairro=?, cidade_uf=? WHERE id=?", [...end, tutorId]);
      else { run("INSERT INTO tutores (nome, telefone, cep, logradouro, numero, bairro, cidade_uf) VALUES (?,?,?,?,?,?,?)", end); tutorId = ultimoId(); }

      let petId = query("SELECT id FROM pets WHERE tutor_id=? AND nome=?", [tutorId, d.pet])[0]?.[0];
      if (!petId) { run("INSERT INTO pets (tutor_id, nome) VALUES (?,?)", [tutorId, d.pet]); petId = ultimoId(); }

      if (query("SELECT id FROM agendamentos WHERE data_hora=? AND id IS NOT ?", [d.data_hora, id]).length)
        throw new RegraErro("Este horário já está reservado na agenda. Escolha outro horário.");

      if (id) run("UPDATE agendamentos SET pet_id=?, servico=?, data_hora=? WHERE id=?", [petId, d.servico, d.data_hora, id]);
      else { run("INSERT INTO agendamentos (pet_id, servico, data_hora) VALUES (?,?,?)", [petId, d.servico, d.data_hora]); id = ultimoId(); }
      run("COMMIT");
      salvarDb();
      return id;
    } catch (e) {
      run("ROLLBACK");
      if (/UNIQUE.*data_hora/.test(e.message)) throw new RegraErro("Este horário já está reservado na agenda. Escolha outro horário.");
      if (/UNIQUE|constraint/i.test(e.message)) throw new RegraErro(`Dados duplicados ou inválidos: ${e.message}`);
      throw e;
    }
  }

  // ---------------- ViaCEP ----------------
  async function consultarCep(cep) {
    let resp;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 6000);
    try { resp = await fetch(`https://viacep.com.br/ws/${cep}/json/`, { signal: ctrl.signal }); }
    catch (e) { throw new ApiForaErro("Sem conexão com o ViaCEP."); }
    finally { clearTimeout(timer); }
    if (resp.status === 400) throw new RegraErro("CEP em formato inválido.");
    if (!resp.ok) throw new ApiForaErro(`ViaCEP respondeu com erro HTTP ${resp.status}.`);
    let j;
    try { j = await resp.json(); } catch { throw new ApiForaErro("Resposta inesperada do ViaCEP."); }
    if (j.erro) throw new RegraErro(`CEP ${fmtCep(cep)} não encontrado.`);
    return { cep: fmtCep(cep), logradouro: j.logradouro || "", bairro: j.bairro || "", cidade_uf: `${j.localidade}/${j.uf}` };
  }

  // ---------------- UI helpers ----------------
  function toast(msg) {
    const t = $("toast"); t.textContent = msg; t.classList.add("ativo");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("ativo"), 3200);
  }
  function dialogo(titulo, texto, { confirmar = false, ok = "OK", cancelar = "Cancelar" } = {}) {
    const dlg = $("dialogo");
    $("dlgTitulo").textContent = titulo; $("dlgTexto").textContent = texto;
    $("dlgSim").textContent = ok; $("dlgNao").textContent = cancelar; $("dlgNao").hidden = !confirmar;
    if (typeof dlg.showModal !== "function") return Promise.resolve(confirmar ? window.confirm(`${titulo}\n\n${texto}`) : (window.alert(`${titulo}\n\n${texto}`), true));
    return new Promise((res) => {
      dlg.returnValue = "";
      dlg.addEventListener("close", () => res(dlg.returnValue === "sim"), { once: true });
      dlg.showModal();
    });
  }
  const aviso = (msg, titulo = "Atenção") => dialogo(titulo, msg);

  function setManual(on) {
    $("manual").checked = on;
    ["logradouro", "bairro", "cidade_uf"].forEach((id) => { $(id).readOnly = !on; });
    if (on) $("logradouro").focus();
  }

  function atualizarHorarios(manter) {
    const sel = $("hora"), atual = manter ?? sel.value, dataIso = $("data").value;
    const livres = horariosLivres(dataIso, agId);
    if (manter && !livres.includes(manter)) livres.push(manter), livres.sort();
    sel.innerHTML = `<option value="">${livres.length ? "Selecione…" : "—"}</option>` + livres.map((h) => `<option>${h}</option>`).join("");
    if (livres.includes(atual)) sel.value = atual;
    let dica;
    const hojeIso = isoData(new Date());
    if (!dataIso) dica = "Escolha uma data.";
    else if (dataIso < hojeIso) dica = "Data no passado: escolha outra data.";
    else if (!DIAS_FUNCIONAMENTO.has(parseIso(dataIso).getDay())) dica = "Fechado aos domingos.";
    else if (!livres.length) dica = "Sem horários livres nesta data.";
    else dica = `${livres.length} horário(s) livre(s) em ${brData(dataIso)}.`;
    $("dicaHorarios").textContent = dica;
  }

  function renderTabela() {
    const linhas = query(SQL_LISTAR + " ORDER BY a.data_hora");
    const agora = new Date();
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    $("tabela").innerHTML = linhas.map((r) => {
      const [dIso, hora] = r[10].split(" ");
      const dt = parseIso(dIso); const [hh, mm] = hora.split(":").map(Number); dt.setHours(hh, mm);
      const cls = [dt < agora ? "passado" : "", r[0] === agId ? "selecionada" : ""].join(" ").trim();
      return `<tr data-id="${r[0]}" class="${cls}" tabindex="0">
        <td class="num">${r[0]}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td>${fmtTel(r[3])}</td>
        <td class="num">${esc(r[4])}</td><td>${esc(r[5])}</td><td class="num">${esc(r[6])}</td><td>${esc(r[7])}</td>
        <td>${esc(r[8])}</td><td>${esc(r[9])}</td><td class="num">${brData(dIso)}</td><td class="num">${hora}</td></tr>`;
    }).join("");
    $("vazio").hidden = linhas.length > 0;
    $("contador").textContent = `${linhas.length} agendamento(s)`;
  }

  function modoEdicao(on) {
    $("btnCadastrar").disabled = on;
    $("btnAtualizar").disabled = !on;
    $("btnExcluir").disabled = !on;
    $("tituloForm").textContent = on ? "Editar agendamento" : "Novo agendamento";
    $("seloEdicao").hidden = !on;
    $("seloEdicao").textContent = on ? `ID ${agId}` : "";
  }

  function limparForm() {
    agId = null; ultimoCep = null;
    campos.forEach((c) => { $(c).value = ""; $(c).classList.remove("erro"); });
    $("servico").value = SERVICOS[0];
    let d = new Date();
    if (!horariosLivres(isoData(d)).length) { do d.setDate(d.getDate() + 1); while (!DIAS_FUNCIONAMENTO.has(d.getDay())); }
    $("data").value = isoData(d);
    setManual(false);
    atualizarHorarios();
    modoEdicao(false);
    renderTabela();
  }

  function selecionar(id) {
    const r = query(SQL_LISTAR + " WHERE a.id = ?", [id])[0];
    if (!r) return;
    agId = r[0];
    const [dIso, hora] = r[10].split(" ");
    const vals = { pet: r[1], tutor: r[2], telefone: fmtTel(r[3]), cep: r[4], logradouro: r[5], numero: r[6], bairro: r[7], cidade_uf: r[8], servico: r[9], data: dIso };
    Object.entries(vals).forEach(([k, v]) => { $(k).value = v; $(k).classList.remove("erro"); });
    ultimoCep = digitos(r[4]);
    setManual(false);
    atualizarHorarios(hora);
    modoEdicao(true);
    renderTabela();
    $("form").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------------- ações ----------------
  async function buscarCep() {
    const bruto = $("cep").value, cep = digitos(bruto);
    if (cep.length !== 8) {
      registrarLog("CONSULTA CEP", `CEP "${limpar(bruto)}" rejeitado: formato inválido.`);
      return aviso("O CEP deve conter exatamente 8 dígitos.", "CEP inválido");
    }
    ultimoCep = cep;
    $("btnCep").disabled = true; $("btnCep").textContent = "Buscando…";
    try {
      const end = await consultarCep(cep);
      $("cep").value = end.cep;
      ["logradouro", "bairro", "cidade_uf"].forEach((k) => { $(k).value = end[k]; $(k).classList.remove("erro"); });
      $("cep").classList.remove("erro");
      registrarLog("CONSULTA CEP", `CEP "${end.cep}" consultado com sucesso (${end.cidade_uf}).`);
      if (!end.logradouro) { setManual(true); toast("CEP geral da cidade: complete logradouro e bairro."); }
      else { setManual(false); $("numero").focus(); }
    } catch (e) {
      if (e instanceof RegraErro) {
        registrarLog("CONSULTA CEP", `CEP "${fmtCep(cep)}" não encontrado.`);
        await aviso(`${e.message}\n\nConfira o número ou marque "Inserir endereço manualmente".`, "CEP não encontrado");
      } else {
        registrarLog("CONSULTA CEP", `Falha ao consultar CEP "${fmtCep(cep)}": ${e.message}`);
        const sim = await dialogo("ViaCEP indisponível", "Não foi possível consultar o ViaCEP agora (sem internet ou serviço fora do ar).\n\nDeseja preencher o endereço manualmente?", { confirmar: true, ok: "Preencher manualmente", cancelar: "Agora não" });
        if (sim) setManual(true);
      }
    } finally {
      $("btnCep").disabled = false; $("btnCep").textContent = "Buscar CEP";
    }
  }

  async function cadastrar(ev) {
    ev?.preventDefault();
    campos.forEach((c) => $(c).classList.remove("erro"));
    let d, id;
    try { d = prepararDados(); id = salvar(d); }
    catch (e) { return aviso(e instanceof RegraErro ? e.message : `Erro no banco de dados: ${e.message}`); }
    registrarLog("INSERÇÃO", `Agendamento Pet "${d.pet}" (Tutor: ${d.tutor}, Serviço: ${d.servico}) cadastrado com sucesso.`);
    limparForm();
    toast(`Agendamento #${id} cadastrado!`);
  }

  async function atualizar() {
    if (!agId) return;
    campos.forEach((c) => $(c).classList.remove("erro"));
    let d;
    try { d = prepararDados(); salvar(d, agId); }
    catch (e) { return aviso(e instanceof RegraErro ? e.message : `Erro no banco de dados: ${e.message}`); }
    const [dIso, hora] = d.data_hora.split(" ");
    registrarLog("ATUALIZAÇÃO", `Agendamento ID ${agId} alterado (Novo Serviço: ${d.servico}, Nova Data: ${brData(dIso)} ${hora}).`);
    const id = agId;
    limparForm();
    toast(`Agendamento #${id} atualizado!`);
  }

  async function excluir() {
    if (!agId) return;
    const pet = $("pet").value, id = agId;
    const ok = await dialogo("Confirmar exclusão", `Deseja realmente excluir o agendamento ID ${id} ("${pet}")?\nEsta ação não pode ser desfeita.`, { confirmar: true, ok: "Excluir" });
    if (!ok) return;
    try { run("DELETE FROM agendamentos WHERE id = ?", [id]); salvarDb(); }
    catch (e) { return aviso(`Erro no banco de dados: ${e.message}`); }
    registrarLog("EXCLUSÃO", `Agendamento ID ${id} ("${pet}") removido do sistema.`);
    limparForm();
    toast(`Agendamento #${id} excluído.`);
  }

  function baixar(nome, conteudo, tipo) {
    const url = URL.createObjectURL(new Blob([conteudo], { type: tipo }));
    const a = Object.assign(document.createElement("a"), { href: url, download: nome });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function semear() {
    // dados de demonstração no primeiro acesso
    const exemplos = [
      ["Thor", "Carlos Silva", "21998765432", "20020-010", "Avenida Rio Branco", "100", "Centro", "Rio de Janeiro/RJ", "Banho e Tosa", "09:00"],
      ["Mel", "Ana Souza", "11912345678", "01001-000", "Praça da Sé", "45", "Sé", "São Paulo/SP", "Vacinação", "10:30"],
      ["Luna", "Bruno Lima", "31987654321", "30130-010", "Praça Sete de Setembro", "12", "Centro", "Belo Horizonte/MG", "Consulta", "14:00"],
    ];
    const d = new Date();
    exemplos.forEach((e, i) => {
      do d.setDate(d.getDate() + 1); while (!DIAS_FUNCIONAMENTO.has(d.getDay()));
      try {
        salvar({ pet: e[0], tutor: e[1], telefone: e[2], cep: e[3], logradouro: e[4], numero: e[5], bairro: e[6], cidade_uf: e[7], servico: e[8], data_hora: `${isoData(d)} ${e[9]}` });
        registrarLog("INSERÇÃO", `Agendamento Pet "${e[0]}" (Tutor: ${e[1]}, Serviço: ${e[8]}) cadastrado com sucesso.`);
      } catch (err) { console.warn(err); }
    });
  }

  // ---------------- inicialização ----------------
  async function iniciar() {
    $("servico").innerHTML = SERVICOS.map((s) => `<option>${s}</option>`).join("");
    const hoje = new Date(), max = new Date(); max.setDate(max.getDate() + DIAS_MAX);
    $("data").min = isoData(hoje); $("data").max = isoData(max);

    try {
      const SQL = await initSqlJs({ locateFile: (f) => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/${f}` });
      const salvo = carregarDbSalvo();
      db = salvo ? new SQL.Database(salvo) : new SQL.Database();
      db.exec(SCHEMA);
      if (!salvo) semear();
    } catch (e) {
      $("form").querySelectorAll("input,select,button").forEach((el) => { el.disabled = true; });
      aviso(`Não foi possível iniciar o banco de dados SQLite no navegador.\n\n${e.message}`, "Erro no banco de dados");
      return;
    }

    $("log").textContent = lerLog() || "(vazio)";
    $("form").addEventListener("submit", cadastrar);
    $("btnAtualizar").addEventListener("click", atualizar);
    $("btnExcluir").addEventListener("click", excluir);
    $("btnLimpar").addEventListener("click", limparForm);
    $("btnCep").addEventListener("click", buscarCep);
    $("manual").addEventListener("change", (e) => setManual(e.target.checked));
    $("data").addEventListener("change", () => atualizarHorarios());
    $("hora").addEventListener("focus", () => atualizarHorarios($("hora").value || undefined));
    $("cep").addEventListener("input", (e) => {
      const c = digitos(e.target.value);
      if (c.length === 8 && c !== ultimoCep && !$("manual").checked) buscarCep();
    });
    $("cep").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); buscarCep(); } });
    $("telefone").addEventListener("blur", (e) => { const t = digitos(e.target.value); if ([10, 11].includes(t.length)) e.target.value = fmtTel(t); });
    $("tabela").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) selecionar(Number(tr.dataset.id)); });
    $("tabela").addEventListener("keydown", (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr[data-id]"); if (tr) selecionar(Number(tr.dataset.id)); } });
    $("btnBaixarDb").addEventListener("click", () => baixar("petshop.db", db.export(), "application/x-sqlite3"));
    $("btnBaixarLog").addEventListener("click", () => baixar("log_agendamentos.txt", lerLog(), "text/plain;charset=utf-8"));
    setInterval(() => { if (!agId) atualizarHorarios(); }, 60000); // horários que passam somem sozinhos

    limparForm();
  }

  iniciar();
})();
