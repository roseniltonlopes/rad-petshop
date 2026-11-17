"""
Patinhas & Cia - Pet Shop
Mini Sistema de Agendamento Veterinário / Pet Shop (RAD)

Aplicação desktop de tela única (tkinter) com:
  - CRUD de agendamentos em SQLite (chaves primárias, dados únicos por cliente,
    horário único na agenda);
  - Busca de endereço na API ViaCEP com opção de preenchimento manual
    caso a API esteja fora do ar;
  - Regras de data/hora (sem datas/horários passados, horário de funcionamento,
    intervalos de 30 minutos, domingo fechado);
  - Log de auditoria em log_agendamentos.txt (modo 'a').
"""

import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta

# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------
NOME_SISTEMA = "Patinhas & Cia - Pet Shop"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "petshop.db")
LOG_PATH = os.path.join(BASE_DIR, "log_agendamentos.txt")
VIACEP_URL = "https://viacep.com.br/ws/{cep}/json/"

SERVICOS = ["Consulta", "Vacinação", "Banho e Tosa", "Banho", "Tosa", "Exame"]

# Regras da agenda
HORA_ABERTURA = 8          # 08:00
HORA_FECHAMENTO = 18       # último horário começa às 17:30
INTERVALO_MIN = 30         # duração de cada atendimento
DIAS_FUNCIONAMENTO = {0, 1, 2, 3, 4, 5}   # seg (0) a sáb (5); domingo fechado
DIAS_MAX_ANTECEDENCIA = 180

DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


class RegraNegocioError(Exception):
    """Erro de validação exibido ao usuário."""


class ApiIndisponivelError(Exception):
    """A API ViaCEP não respondeu (sem internet, timeout, fora do ar)."""


# --------------------------------------------------------------------------
# Log de auditoria
# --------------------------------------------------------------------------
def registrar_log(tipo, mensagem, log_path=None):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    linha = f"[{agora}] {tipo} - {mensagem}\n"
    try:
        with open(log_path or LOG_PATH, "a", encoding="utf-8") as arq:
            arq.write(linha)
    except OSError:
        pass  # o log nunca pode derrubar a aplicação
    return linha


# --------------------------------------------------------------------------
# Limpeza e validação de entradas
# --------------------------------------------------------------------------
def somente_digitos(texto):
    texto = (texto or "").strip()
    for ch in ".-()/ ":
        texto = texto.replace(ch, "")
    return re.sub(r"\D", "", texto)


def limpar_texto(texto):
    texto = (texto or "").strip().replace("\t", " ").replace("\n", " ")
    while "  " in texto:
        texto = texto.replace("  ", " ")
    return texto


def formatar_cep(cep):
    cep = somente_digitos(cep)
    return f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else cep


def formatar_telefone(tel):
    tel = somente_digitos(tel)
    if len(tel) == 11:
        return f"({tel[:2]}) {tel[2:7]}-{tel[7:]}"
    if len(tel) == 10:
        return f"({tel[:2]}) {tel[2:6]}-{tel[6:]}"
    return tel


def validar_cep(cep):
    cep = somente_digitos(cep)
    if len(cep) != 8:
        raise RegraNegocioError("O CEP deve conter exatamente 8 dígitos.")
    return cep


def validar_telefone(tel):
    tel = somente_digitos(tel)
    if len(tel) not in (10, 11):
        raise RegraNegocioError("Telefone inválido. Informe DDD + número (10 ou 11 dígitos).")
    return tel


def gerar_horarios():
    """Lista de horários possíveis no dia: 08:00, 08:30 ... 17:30."""
    horarios = []
    atual = datetime(2000, 1, 1, HORA_ABERTURA, 0)
    fim = datetime(2000, 1, 1, HORA_FECHAMENTO, 0)
    while atual < fim:
        horarios.append(atual.strftime("%H:%M"))
        atual += timedelta(minutes=INTERVALO_MIN)
    return horarios


def interpretar_data(data_txt):
    data_txt = limpar_texto(data_txt).replace("-", "/").replace(".", "/")
    try:
        return datetime.strptime(data_txt, "%d/%m/%Y").date()
    except ValueError:
        raise RegraNegocioError("Data inválida. Use o formato DD/MM/AAAA (ex.: 25/12/2026).")


def validar_data_hora(data_txt, hora_txt, agora=None):
    """Aplica todas as regras de data/hora e devolve um datetime válido."""
    agora = agora or datetime.now()
    data = interpretar_data(data_txt)
    if data < agora.date():
        raise RegraNegocioError("Não é permitido agendar em datas passadas.")
    if data > agora.date() + timedelta(days=DIAS_MAX_ANTECEDENCIA):
        raise RegraNegocioError(f"Agendamentos só podem ser feitos com até {DIAS_MAX_ANTECEDENCIA} dias de antecedência.")
    if data.weekday() not in DIAS_FUNCIONAMENTO:
        raise RegraNegocioError(f"O pet shop não funciona aos {DIAS_SEMANA[data.weekday()]}s.")

    hora_txt = limpar_texto(hora_txt)
    if not hora_txt:
        raise RegraNegocioError("Selecione um horário.")
    try:
        hora = datetime.strptime(hora_txt, "%H:%M").time()
    except ValueError:
        raise RegraNegocioError("Horário inválido. Use HH:MM.")
    if hora_txt not in gerar_horarios():
        raise RegraNegocioError(
            f"Horário fora da agenda. Atendimentos das {HORA_ABERTURA:02d}:00 às "
            f"{HORA_FECHAMENTO:02d}:00, a cada {INTERVALO_MIN} minutos.")

    data_hora = datetime.combine(data, hora)
    if data_hora <= agora:
        raise RegraNegocioError("Não é permitido agendar em um horário que já passou.")
    return data_hora


# --------------------------------------------------------------------------
# API ViaCEP
# --------------------------------------------------------------------------
def consultar_cep(cep, timeout=5):
    """Retorna dict com logradouro, bairro, cidade_uf.
    Lança RegraNegocioError (CEP inválido/não encontrado) ou ApiIndisponivelError."""
    cep = validar_cep(cep)
    try:
        req = urllib.request.Request(VIACEP_URL.format(cep=cep),
                                     headers={"User-Agent": "PatinhasECia/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            raise RegraNegocioError("CEP em formato inválido.")
        raise ApiIndisponivelError(f"ViaCEP respondeu com erro HTTP {exc.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiIndisponivelError(f"Sem conexão com o ViaCEP ({exc}).")
    except (ValueError, json.JSONDecodeError):
        raise ApiIndisponivelError("Resposta inesperada do ViaCEP.")

    if dados.get("erro"):
        raise RegraNegocioError(f"CEP {formatar_cep(cep)} não encontrado.")
    return {
        "cep": formatar_cep(cep),
        "logradouro": dados.get("logradouro", ""),
        "bairro": dados.get("bairro", ""),
        "cidade_uf": f"{dados.get('localidade', '')}/{dados.get('uf', '')}",
    }


# --------------------------------------------------------------------------
# Banco de dados
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS tutores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL,
    telefone    TEXT NOT NULL UNIQUE,              -- um cadastro por cliente
    cep         TEXT NOT NULL,
    logradouro  TEXT NOT NULL,
    numero      TEXT NOT NULL,
    bairro      TEXT NOT NULL,
    cidade_uf   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pets (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tutor_id  INTEGER NOT NULL REFERENCES tutores(id) ON DELETE CASCADE,
    nome      TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (tutor_id, nome)                        -- pet único por tutor
);

CREATE TABLE IF NOT EXISTS agendamentos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id     INTEGER NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    servico    TEXT NOT NULL,
    data_hora  TEXT NOT NULL UNIQUE,               -- horário não pode ser reservado 2x
    criado_em  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""

SQL_LISTAR = """
SELECT a.id, p.nome, t.nome, t.telefone, t.cep, t.logradouro, t.numero,
       t.bairro, t.cidade_uf, a.servico, a.data_hora
  FROM agendamentos a
  JOIN pets p    ON p.id = a.pet_id
  JOIN tutores t ON t.id = p.tutor_id
 ORDER BY a.data_hora
"""


class Banco:
    def __init__(self, caminho=DB_PATH):
        self.con = sqlite3.connect(caminho)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(SCHEMA)
        self.con.commit()

    def fechar(self):
        self.con.close()

    # ---------- leitura ----------
    def listar(self):
        return self.con.execute(SQL_LISTAR).fetchall()

    def obter(self, ag_id):
        return self.con.execute(SQL_LISTAR.replace("ORDER BY a.data_hora", "WHERE a.id = ?"),
                                (ag_id,)).fetchone()

    def horarios_ocupados(self, data_iso, ignorar_id=None):
        rows = self.con.execute(
            "SELECT substr(data_hora, 12, 5) FROM agendamentos "
            "WHERE substr(data_hora, 1, 10) = ? AND id IS NOT ?",
            (data_iso, ignorar_id)).fetchall()
        return {r[0] for r in rows}

    def horarios_livres(self, data, ignorar_id=None, agora=None):
        agora = agora or datetime.now()
        if data.weekday() not in DIAS_FUNCIONAMENTO or data < agora.date():
            return []
        ocupados = self.horarios_ocupados(data.isoformat(), ignorar_id)
        livres = []
        for h in gerar_horarios():
            dt = datetime.combine(data, datetime.strptime(h, "%H:%M").time())
            if dt > agora and h not in ocupados:
                livres.append(h)
        return livres

    # ---------- escrita ----------
    def _tutor_do_agendamento(self, ag_id):
        row = self.con.execute(
            "SELECT p.tutor_id FROM agendamentos a JOIN pets p ON p.id = a.pet_id WHERE a.id = ?",
            (ag_id,)).fetchone()
        return row[0] if row else None

    def salvar(self, dados, ag_id=None):
        """Cria (ag_id=None) ou atualiza um agendamento. Retorna o id."""
        cur = self.con.cursor()
        try:
            cur.execute("BEGIN")
            # 1) Tutor (telefone é único por cliente)
            existente = cur.execute("SELECT id, nome FROM tutores WHERE telefone = ?",
                                    (dados["telefone"],)).fetchone()
            tutor_atual = self._tutor_do_agendamento(ag_id) if ag_id else None
            endereco = (dados["tutor"], dados["telefone"], dados["cep"], dados["logradouro"],
                        dados["numero"], dados["bairro"], dados["cidade_uf"])

            if existente and existente[0] != tutor_atual and \
                    existente[1].casefold() != dados["tutor"].casefold():
                raise RegraNegocioError(
                    f"O telefone {formatar_telefone(dados['telefone'])} já está cadastrado "
                    f"para o tutor \"{existente[1]}\". Cada cliente possui um cadastro único.")

            if existente:
                tutor_id = existente[0]
            elif tutor_atual:
                tutor_id = tutor_atual          # edição trocando o telefone do mesmo tutor
            else:
                tutor_id = None

            if tutor_id:
                cur.execute("""UPDATE tutores SET nome=?, telefone=?, cep=?, logradouro=?,
                               numero=?, bairro=?, cidade_uf=? WHERE id=?""", endereco + (tutor_id,))
            else:
                cur.execute("""INSERT INTO tutores (nome, telefone, cep, logradouro, numero,
                               bairro, cidade_uf) VALUES (?,?,?,?,?,?,?)""", endereco)
                tutor_id = cur.lastrowid

            # 2) Pet (nome único por tutor)
            pet = cur.execute("SELECT id FROM pets WHERE tutor_id = ? AND nome = ?",
                              (tutor_id, dados["pet"])).fetchone()
            if pet:
                pet_id = pet[0]
            else:
                cur.execute("INSERT INTO pets (tutor_id, nome) VALUES (?, ?)", (tutor_id, dados["pet"]))
                pet_id = cur.lastrowid

            # 3) Agendamento (data_hora única)
            ocupado = cur.execute("SELECT id FROM agendamentos WHERE data_hora = ? AND id IS NOT ?",
                                  (dados["data_hora"], ag_id)).fetchone()
            if ocupado:
                raise RegraNegocioError("Este horário já está reservado na agenda. Escolha outro horário.")

            if ag_id:
                cur.execute("UPDATE agendamentos SET pet_id=?, servico=?, data_hora=? WHERE id=?",
                            (pet_id, dados["servico"], dados["data_hora"], ag_id))
            else:
                cur.execute("INSERT INTO agendamentos (pet_id, servico, data_hora) VALUES (?,?,?)",
                            (pet_id, dados["servico"], dados["data_hora"]))
                ag_id = cur.lastrowid
            self.con.commit()
            return ag_id
        except sqlite3.IntegrityError as exc:
            self.con.rollback()
            if "data_hora" in str(exc):
                raise RegraNegocioError("Este horário já está reservado na agenda. Escolha outro horário.")
            raise RegraNegocioError(f"Dados duplicados ou inválidos: {exc}")
        except Exception:
            self.con.rollback()
            raise

    def excluir(self, ag_id):
        cur = self.con.execute("DELETE FROM agendamentos WHERE id = ?", (ag_id,))
        self.con.commit()
        return cur.rowcount


def preparar_dados(campos, agora=None):
    """Limpa e valida os campos do formulário. Retorna dict pronto para o banco."""
    pet = limpar_texto(campos.get("pet"))
    tutor = limpar_texto(campos.get("tutor"))
    if not pet:
        raise RegraNegocioError("Informe o nome do pet.")
    if not tutor:
        raise RegraNegocioError("Informe o nome do tutor.")
    telefone = validar_telefone(campos.get("telefone"))
    cep = formatar_cep(validar_cep(campos.get("cep")))
    logradouro = limpar_texto(campos.get("logradouro"))
    bairro = limpar_texto(campos.get("bairro"))
    cidade_uf = limpar_texto(campos.get("cidade_uf"))
    numero = limpar_texto(campos.get("numero"))
    if not (logradouro and bairro and cidade_uf):
        raise RegraNegocioError("Endereço incompleto. Busque o CEP ou preencha manualmente.")
    if not numero:
        raise RegraNegocioError("Informe o número/complemento do endereço.")
    servico = limpar_texto(campos.get("servico"))
    if servico not in SERVICOS:
        raise RegraNegocioError("Selecione um serviço válido.")
    data_hora = validar_data_hora(campos.get("data"), campos.get("hora"), agora)
    return {
        "pet": pet, "tutor": tutor, "telefone": telefone, "cep": cep,
        "logradouro": logradouro, "numero": numero, "bairro": bairro,
        "cidade_uf": cidade_uf, "servico": servico,
        "data_hora": data_hora.strftime("%Y-%m-%d %H:%M"),
    }


# --------------------------------------------------------------------------
# Interface gráfica (tela única)
# --------------------------------------------------------------------------
def iniciar_interface():
    import tkinter as tk
    from tkinter import messagebox, ttk

    COR_FUNDO = "#F6F3EE"
    COR_PRIMARIA = "#2F6F5E"
    COR_TEXTO = "#1F2A27"

    class App:
        def __init__(self, root):
            self.root = root
            self.banco = Banco()
            self.ag_id = None              # id em edição
            self.ultimo_cep = None
            self.manual = tk.BooleanVar(value=False)
            self.vars = {k: tk.StringVar() for k in
                         ("pet", "tutor", "telefone", "cep", "logradouro", "numero",
                          "bairro", "cidade_uf", "servico", "data", "hora")}

            root.title(NOME_SISTEMA)
            root.geometry("1180x700")
            root.minsize(980, 620)
            root.configure(bg=COR_FUNDO)
            self._estilos()
            self._montar()
            self.limpar()
            self.carregar_tabela()

        # ---------- layout ----------
        def _estilos(self):
            st = ttk.Style()
            st.theme_use("clam")
            st.configure(".", background=COR_FUNDO, foreground=COR_TEXTO, font=("Segoe UI", 10))
            st.configure("TLabelframe", background=COR_FUNDO)
            st.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground=COR_PRIMARIA)
            st.configure("Titulo.TLabel", font=("Segoe UI", 18, "bold"), foreground=COR_PRIMARIA)
            st.configure("Sub.TLabel", foreground="#5b6663")
            st.configure("Status.TLabel", foreground="#5b6663", font=("Segoe UI", 9))
            st.configure("Primario.TButton", background=COR_PRIMARIA, foreground="white", padding=(14, 6))
            st.map("Primario.TButton", background=[("active", "#245848"), ("disabled", "#9bb5ad")])
            st.configure("TButton", padding=(12, 6))
            st.configure("Treeview", rowheight=26, fieldbackground="white", background="white")
            st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#E4EDE9")

        def _campo(self, pai, rotulo, chave, linha, coluna, largura=24, **kw):
            ttk.Label(pai, text=rotulo).grid(row=linha, column=coluna, sticky="w", padx=6, pady=(4, 0))
            ent = ttk.Entry(pai, textvariable=self.vars[chave], width=largura, **kw)
            ent.grid(row=linha + 1, column=coluna, sticky="ew", padx=6, pady=(0, 4))
            return ent

        def _montar(self):
            topo = ttk.Frame(self.root, padding=(16, 12, 16, 4))
            topo.pack(fill="x")
            ttk.Label(topo, text="🐾 Patinhas & Cia", style="Titulo.TLabel").pack(side="left")
            ttk.Label(topo, text="  Agenda de atendimentos · Seg a Sáb, 08h às 18h",
                      style="Sub.TLabel").pack(side="left", pady=(8, 0))

            form = ttk.Frame(self.root, padding=(16, 4))
            form.pack(fill="x")
            for i in range(3):
                form.columnconfigure(i, weight=1)

            # --- Pet / Tutor
            f1 = ttk.LabelFrame(form, text="Pet e tutor", padding=8)
            f1.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
            f1.columnconfigure(0, weight=1)
            self._campo(f1, "Nome do pet *", "pet", 0, 0)
            self._campo(f1, "Tutor *", "tutor", 2, 0)
            self._campo(f1, "Telefone (DDD + número) *", "telefone", 4, 0)

            # --- Endereço
            f2 = ttk.LabelFrame(form, text="Endereço do tutor", padding=8)
            f2.grid(row=0, column=1, sticky="nsew", padx=8)
            f2.columnconfigure(0, weight=1)
            f2.columnconfigure(1, weight=1)
            ttk.Label(f2, text="CEP *").grid(row=0, column=0, sticky="w", padx=6, pady=(4, 0))
            linha_cep = ttk.Frame(f2)
            linha_cep.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6)
            linha_cep.columnconfigure(0, weight=1)
            self.ent_cep = ttk.Entry(linha_cep, textvariable=self.vars["cep"], width=12)
            self.ent_cep.grid(row=0, column=0, sticky="ew")
            self.ent_cep.bind("<KeyRelease>", self._cep_digitado)
            self.ent_cep.bind("<Return>", lambda e: self.buscar_cep())
            ttk.Button(linha_cep, text="Buscar CEP", command=self.buscar_cep).grid(row=0, column=1, padx=(6, 0))
            ttk.Checkbutton(f2, text="Inserir endereço manualmente (ViaCEP fora do ar)",
                            variable=self.manual, command=self._alternar_manual).grid(
                row=2, column=0, columnspan=2, sticky="w", padx=6, pady=2)
            self.ent_log = self._campo(f2, "Logradouro *", "logradouro", 3, 0)
            self.ent_num = self._campo(f2, "Número / compl. *", "numero", 3, 1, largura=10)
            self.ent_bairro = self._campo(f2, "Bairro *", "bairro", 5, 0)
            self.ent_cid = self._campo(f2, "Cidade/UF *", "cidade_uf", 5, 1, largura=14)

            # --- Atendimento
            f3 = ttk.LabelFrame(form, text="Atendimento", padding=8)
            f3.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
            f3.columnconfigure(0, weight=1)
            ttk.Label(f3, text="Serviço *").grid(row=0, column=0, sticky="w", padx=6, pady=(4, 0))
            ttk.Combobox(f3, textvariable=self.vars["servico"], values=SERVICOS,
                         state="readonly").grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 4))
            ent_data = self._campo(f3, "Data (DD/MM/AAAA) *", "data", 2, 0)
            ent_data.bind("<FocusOut>", lambda e: self.atualizar_horarios())
            ent_data.bind("<Return>", lambda e: self.atualizar_horarios())
            ttk.Label(f3, text="Horário disponível *").grid(row=4, column=0, sticky="w", padx=6, pady=(4, 0))
            self.cb_hora = ttk.Combobox(f3, textvariable=self.vars["hora"], state="readonly")
            self.cb_hora.grid(row=5, column=0, sticky="ew", padx=6, pady=(0, 4))
            self.cb_hora.bind("<Button-1>", lambda e: self.atualizar_horarios(manter=True))
            self.lbl_horarios = ttk.Label(f3, text="", style="Status.TLabel")
            self.lbl_horarios.grid(row=6, column=0, sticky="w", padx=6)

            # --- Botões
            barra = ttk.Frame(self.root, padding=(16, 8))
            barra.pack(fill="x")
            self.btn_cadastrar = ttk.Button(barra, text="Cadastrar", style="Primario.TButton",
                                            command=self.cadastrar)
            self.btn_cadastrar.pack(side="left")
            self.btn_atualizar = ttk.Button(barra, text="Salvar alterações", command=self.atualizar)
            self.btn_atualizar.pack(side="left", padx=6)
            self.btn_excluir = ttk.Button(barra, text="Excluir", command=self.excluir)
            self.btn_excluir.pack(side="left")
            ttk.Button(barra, text="Limpar / Novo", command=self.limpar).pack(side="left", padx=6)
            self.lbl_status = ttk.Label(barra, text="", style="Status.TLabel")
            self.lbl_status.pack(side="right")

            # --- Tabela
            tab = ttk.Frame(self.root, padding=(16, 0, 16, 12))
            tab.pack(fill="both", expand=True)
            colunas = [("id", "ID", 45), ("pet", "Pet", 100), ("tutor", "Tutor", 140),
                       ("telefone", "Telefone", 115), ("cep", "CEP", 80),
                       ("logradouro", "Logradouro", 180), ("numero", "Nº", 60),
                       ("bairro", "Bairro", 120), ("cidade", "Cidade/UF", 130),
                       ("servico", "Serviço", 105), ("data", "Data", 85), ("hora", "Horário", 65)]
            self.tree = ttk.Treeview(tab, columns=[c[0] for c in colunas], show="headings",
                                     selectmode="browse")
            for chave, titulo, larg in colunas:
                self.tree.heading(chave, text=titulo)
                self.tree.column(chave, width=larg, minwidth=40,
                                 anchor="center" if chave in ("id", "numero", "data", "hora", "cep") else "w")
            sy = ttk.Scrollbar(tab, orient="vertical", command=self.tree.yview)
            sx = ttk.Scrollbar(tab, orient="horizontal", command=self.tree.xview)
            self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
            self.tree.grid(row=0, column=0, sticky="nsew")
            sy.grid(row=0, column=1, sticky="ns")
            sx.grid(row=1, column=0, sticky="ew")
            tab.rowconfigure(0, weight=1)
            tab.columnconfigure(0, weight=1)
            self.tree.tag_configure("par", background="#F3F7F5")
            self.tree.tag_configure("passado", foreground="#9a9a9a")
            self.tree.bind("<<TreeviewSelect>>", self.selecionar)

        # ---------- endereço ----------
        def _alternar_manual(self):
            estado = "normal" if self.manual.get() else "readonly"
            for ent in (self.ent_log, self.ent_bairro, self.ent_cid):
                ent.configure(state=estado)
            if self.manual.get():
                self.ent_log.focus_set()

        def _cep_digitado(self, _evento=None):
            cep = somente_digitos(self.vars["cep"].get())
            if len(cep) == 8 and cep != self.ultimo_cep and not self.manual.get():
                self.buscar_cep()

        def buscar_cep(self):
            bruto = self.vars["cep"].get()
            try:
                cep = validar_cep(bruto)
            except RegraNegocioError as exc:
                registrar_log("CONSULTA CEP", f"CEP \"{limpar_texto(bruto)}\" rejeitado: {exc}")
                messagebox.showwarning("CEP inválido", str(exc))
                return
            self.ultimo_cep = cep
            self.root.config(cursor="watch")
            self.root.update_idletasks()
            try:
                end = consultar_cep(cep)
            except RegraNegocioError as exc:
                registrar_log("CONSULTA CEP", f"CEP \"{formatar_cep(cep)}\" não encontrado.")
                messagebox.showwarning("CEP não encontrado",
                                       f"{exc}\n\nConfira o número ou marque \"Inserir endereço "
                                       "manualmente\" para digitar o endereço.")
                return
            except ApiIndisponivelError as exc:
                registrar_log("CONSULTA CEP", f"Falha ao consultar CEP \"{formatar_cep(cep)}\": {exc}")
                if messagebox.askyesno("ViaCEP indisponível",
                                       "Não foi possível consultar o ViaCEP agora "
                                       "(sem internet ou serviço fora do ar).\n\n"
                                       "Deseja preencher o endereço manualmente?"):
                    self.manual.set(True)
                    self._alternar_manual()
                return
            except Exception as exc:  # nunca derrubar a aplicação
                registrar_log("CONSULTA CEP", f"Erro inesperado no CEP \"{formatar_cep(cep)}\": {exc}")
                messagebox.showerror("Erro", f"Erro inesperado ao consultar o CEP:\n{exc}")
                return
            finally:
                self.root.config(cursor="")

            self.manual.set(False)
            self._alternar_manual()
            for ent, chave in ((self.ent_log, "logradouro"), (self.ent_bairro, "bairro"),
                               (self.ent_cid, "cidade_uf")):
                ent.configure(state="normal")
                self.vars[chave].set(end[chave])
                ent.configure(state="readonly")
            self.vars["cep"].set(end["cep"])
            if not end["logradouro"]:   # CEP geral de cidade: libera digitação
                self.manual.set(True)
                self._alternar_manual()
                messagebox.showinfo("CEP geral", "Este CEP é da cidade inteira. Complete o "
                                                 "logradouro e o bairro manualmente.")
            registrar_log("CONSULTA CEP",
                          f"CEP \"{end['cep']}\" consultado com sucesso ({end['cidade_uf']}).")
            self.ent_num.focus_set()

        # ---------- agenda ----------
        def atualizar_horarios(self, manter=False):
            atual = self.vars["hora"].get()
            try:
                data = interpretar_data(self.vars["data"].get())
            except RegraNegocioError:
                self.cb_hora.configure(values=[])
                self.lbl_horarios.configure(text="Informe uma data válida.")
                return
            livres = self.banco.horarios_livres(data, ignorar_id=self.ag_id)
            self.cb_hora.configure(values=livres)
            if data < datetime.now().date():
                msg = "Data no passado: escolha outra data."
            elif data.weekday() not in DIAS_FUNCIONAMENTO:
                msg = "Fechado aos domingos."
            elif not livres:
                msg = "Sem horários livres nesta data."
            else:
                msg = f"{len(livres)} horário(s) livre(s) em {data.strftime('%d/%m/%Y')}."
            self.lbl_horarios.configure(text=msg)
            if not (manter and atual in livres):
                if atual not in livres:
                    self.vars["hora"].set("")

        # ---------- CRUD ----------
        def _coletar(self):
            return {k: v.get() for k, v in self.vars.items()}

        def cadastrar(self):
            try:
                dados = preparar_dados(self._coletar())
                novo_id = self.banco.salvar(dados)
            except RegraNegocioError as exc:
                messagebox.showwarning("Atenção", str(exc))
                return
            except sqlite3.Error as exc:
                messagebox.showerror("Erro no banco de dados", str(exc))
                return
            registrar_log("INSERÇÃO", f"Agendamento Pet \"{dados['pet']}\" (Tutor: {dados['tutor']}, "
                                      f"Serviço: {dados['servico']}) cadastrado com sucesso.")
            self.carregar_tabela()
            self.limpar()
            self._status(f"Agendamento #{novo_id} cadastrado.")
            messagebox.showinfo("Sucesso", "Agendamento cadastrado com sucesso!")

        def atualizar(self):
            if not self.ag_id:
                messagebox.showinfo("Atualizar", "Selecione um agendamento na tabela para editar.")
                return
            try:
                dados = preparar_dados(self._coletar())
                self.banco.salvar(dados, self.ag_id)
            except RegraNegocioError as exc:
                messagebox.showwarning("Atenção", str(exc))
                return
            except sqlite3.Error as exc:
                messagebox.showerror("Erro no banco de dados", str(exc))
                return
            nova_data = datetime.strptime(dados["data_hora"], "%Y-%m-%d %H:%M")
            registrar_log("ATUALIZAÇÃO", f"Agendamento ID {self.ag_id} alterado (Novo Serviço: "
                                         f"{dados['servico']}, Nova Data: {nova_data:%d/%m/%Y %H:%M}).")
            ag = self.ag_id
            self.carregar_tabela()
            self.limpar()
            self._status(f"Agendamento #{ag} atualizado.")
            messagebox.showinfo("Sucesso", "Agendamento atualizado com sucesso!")

        def excluir(self):
            if not self.ag_id:
                messagebox.showinfo("Excluir", "Selecione um agendamento na tabela para excluir.")
                return
            pet = self.vars["pet"].get()
            if not messagebox.askyesno("Confirmar exclusão",
                                       f"Deseja realmente excluir o agendamento ID {self.ag_id} "
                                       f"(\"{pet}\")?\nEsta ação não pode ser desfeita."):
                return
            try:
                self.banco.excluir(self.ag_id)
            except sqlite3.Error as exc:
                messagebox.showerror("Erro no banco de dados", str(exc))
                return
            registrar_log("EXCLUSÃO", f"Agendamento ID {self.ag_id} (\"{pet}\") removido do sistema.")
            ag = self.ag_id
            self.carregar_tabela()
            self.limpar()
            self._status(f"Agendamento #{ag} excluído.")

        def carregar_tabela(self):
            self.tree.delete(*self.tree.get_children())
            try:
                linhas = self.banco.listar()
            except sqlite3.Error as exc:
                messagebox.showerror("Erro no banco de dados", str(exc))
                return
            agora = datetime.now()
            for i, r in enumerate(linhas):
                dt = datetime.strptime(r[10], "%Y-%m-%d %H:%M")
                tags = ["par"] if i % 2 else []
                if dt < agora:
                    tags.append("passado")
                self.tree.insert("", "end", iid=str(r[0]), tags=tags, values=(
                    r[0], r[1], r[2], formatar_telefone(r[3]), r[4], r[5], r[6], r[7], r[8],
                    r[9], dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")))
            self._status(f"{len(linhas)} agendamento(s) na agenda.")

        def selecionar(self, _evento=None):
            sel = self.tree.selection()
            if not sel:
                return
            r = self.banco.obter(int(sel[0]))
            if not r:
                return
            self.ag_id = r[0]
            dt = datetime.strptime(r[10], "%Y-%m-%d %H:%M")
            valores = {"pet": r[1], "tutor": r[2], "telefone": formatar_telefone(r[3]), "cep": r[4],
                       "logradouro": r[5], "numero": r[6], "bairro": r[7], "cidade_uf": r[8],
                       "servico": r[9], "data": dt.strftime("%d/%m/%Y"), "hora": dt.strftime("%H:%M")}
            for ent in (self.ent_log, self.ent_bairro, self.ent_cid):
                ent.configure(state="normal")
            for k, v in valores.items():
                self.vars[k].set(v)
            self.ultimo_cep = somente_digitos(r[4])
            self.manual.set(False)
            self._alternar_manual()
            self.atualizar_horarios(manter=True)
            self.vars["hora"].set(valores["hora"])
            self.btn_cadastrar.state(["disabled"])
            self.btn_atualizar.state(["!disabled"])
            self.btn_excluir.state(["!disabled"])
            self._status(f"Editando agendamento #{self.ag_id}.")

        def limpar(self):
            self.ag_id = None
            self.ultimo_cep = None
            for ent in (self.ent_log, self.ent_bairro, self.ent_cid):
                ent.configure(state="normal")
            for v in self.vars.values():
                v.set("")
            self.manual.set(False)
            self._alternar_manual()
            self.vars["servico"].set(SERVICOS[0])
            self.vars["data"].set(datetime.now().strftime("%d/%m/%Y"))
            self.atualizar_horarios()
            self.tree.selection_remove(self.tree.selection()) if hasattr(self, "tree") else None
            self.btn_cadastrar.state(["!disabled"])
            self.btn_atualizar.state(["disabled"])
            self.btn_excluir.state(["disabled"])

        def _status(self, texto):
            self.lbl_status.configure(text=texto)

    root = tk.Tk()
    app = App(root)

    def ao_fechar():
        app.banco.fechar()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", ao_fechar)
    root.mainloop()


if __name__ == "__main__":
    iniciar_interface()
