"""
Patinhas & Cia - Mini Sistema de Agendamento Veterinário / Pet Shop (RAD)

Aplicação de tela única feita com tkinter + SQLite + API ViaCEP.
Organização do arquivo:
    1. Configuração
    2. Log de auditoria
    3. Limpeza e validação das entradas
    4. Consulta ao ViaCEP
    5. Banco de dados
    6. Interface gráfica
"""

import json
import os
import re
import sqlite3
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from tkinter import messagebox, ttk

# ==========================================================================
# 1. Configuração
# ==========================================================================
PASTA = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_BANCO = os.path.join(PASTA, "petshop.db")
ARQUIVO_LOG = os.path.join(PASTA, "log_agendamentos.txt")
URL_VIACEP = "https://viacep.com.br/ws/{cep}/json/"

SERVICOS = ["Consulta", "Vacinação", "Banho e Tosa", "Banho", "Tosa", "Exame"]

# Regras da agenda
HORA_ABERTURA = 8                 # primeiro horário: 08:00
HORA_FECHAMENTO = 18              # último horário: 17:30
INTERVALO_MINUTOS = 30
DIAS_ABERTOS = {0, 1, 2, 3, 4, 5}  # segunda (0) a sábado (5); domingo (6) fechado
ANTECEDENCIA_MAXIMA_DIAS = 180

DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
         "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
TODOS = "Todos"


class RegraNegocioError(Exception):
    """Erro de validação: a mensagem é mostrada ao usuário."""


class ApiIndisponivelError(Exception):
    """O ViaCEP não respondeu (sem internet, timeout ou serviço fora do ar)."""


# ==========================================================================
# 2. Log de auditoria
# ==========================================================================
def registrar_log(tipo, mensagem, arquivo=None):
    """Acrescenta uma linha ao log. O modo 'a' preserva o histórico anterior."""
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    try:
        with open(arquivo or ARQUIVO_LOG, "a", encoding="utf-8") as log:
            log.write(f"[{agora}] {tipo} - {mensagem}\n")
    except OSError:
        pass  # uma falha no log nunca pode derrubar a aplicação


# ==========================================================================
# 3. Limpeza e validação das entradas
# ==========================================================================
def limpar_texto(texto):
    """Remove espaços nas pontas e espaços repetidos no meio."""
    return " ".join((texto or "").strip().split())


def somente_digitos(texto):
    """Mantém só os números: '(85) 99999-1111' -> '85999991111'."""
    texto = (texto or "").strip()
    for caractere in ".-()/ ":
        texto = texto.replace(caractere, "")
    return re.sub(r"\D", "", texto)


def formatar_cep(cep):
    cep = somente_digitos(cep)
    return f"{cep[:5]}-{cep[5:]}" if len(cep) == 8 else cep


def formatar_telefone(telefone):
    tel = somente_digitos(telefone)
    if len(tel) == 11:
        return f"({tel[:2]}) {tel[2:7]}-{tel[7:]}"
    if len(tel) == 10:
        return f"({tel[:2]}) {tel[2:6]}-{tel[6:]}"
    return tel


def validar_cep(cep):
    cep = somente_digitos(cep)
    if len(cep) != 8:
        raise RegraNegocioError("O CEP deve ter exatamente 8 dígitos.")
    return cep


def validar_telefone(telefone):
    telefone = somente_digitos(telefone)
    if len(telefone) not in (10, 11):
        raise RegraNegocioError("Telefone inválido. Informe DDD + número (10 ou 11 dígitos).")
    return telefone


def horarios_do_dia():
    """Todos os horários da agenda: ['08:00', '08:30', ..., '17:30']."""
    horarios = []
    minuto = HORA_ABERTURA * 60
    while minuto < HORA_FECHAMENTO * 60:
        horarios.append(f"{minuto // 60:02d}:{minuto % 60:02d}")
        minuto += INTERVALO_MINUTOS
    return horarios


def ler_data(texto):
    """Converte 'DD/MM/AAAA' em date."""
    try:
        return datetime.strptime(limpar_texto(texto), "%d/%m/%Y").date()
    except ValueError:
        raise RegraNegocioError("Data inválida. Use o formato DD/MM/AAAA (ex.: 25/12/2026).")


def validar_data_hora(data_texto, hora_texto, agora=None):
    """Aplica as regras de data e hora e devolve o datetime do agendamento."""
    agora = agora or datetime.now()
    data = ler_data(data_texto)

    if data < agora.date():
        raise RegraNegocioError("Não é permitido agendar em datas passadas.")
    if data > agora.date() + timedelta(days=ANTECEDENCIA_MAXIMA_DIAS):
        raise RegraNegocioError(f"Só é possível agendar com até {ANTECEDENCIA_MAXIMA_DIAS} dias de antecedência.")
    if data.weekday() not in DIAS_ABERTOS:
        raise RegraNegocioError(f"O pet shop não abre aos {DIAS_SEMANA[data.weekday()]}s.")

    hora_texto = limpar_texto(hora_texto)
    if hora_texto not in horarios_do_dia():
        raise RegraNegocioError(f"Escolha um horário da agenda ({HORA_ABERTURA:02d}:00 às "
                                f"{HORA_FECHAMENTO:02d}:00, a cada {INTERVALO_MINUTOS} minutos).")

    data_hora = datetime.strptime(f"{data} {hora_texto}", "%Y-%m-%d %H:%M")
    if data_hora <= agora:
        raise RegraNegocioError("Não é permitido agendar em um horário que já passou.")
    return data_hora


def preparar_dados(campos, agora=None):
    """Limpa e valida os campos do formulário. Devolve um dicionário pronto para o banco."""
    dados = {chave: limpar_texto(campos.get(chave))
             for chave in ("pet", "tutor", "logradouro", "numero", "bairro", "cidade_uf", "servico")}

    if not dados["pet"]:
        raise RegraNegocioError("Informe o nome do pet.")
    if not dados["tutor"]:
        raise RegraNegocioError("Informe o nome do tutor.")
    dados["telefone"] = validar_telefone(campos.get("telefone"))
    dados["cep"] = formatar_cep(validar_cep(campos.get("cep")))
    if not (dados["logradouro"] and dados["bairro"] and dados["cidade_uf"]):
        raise RegraNegocioError("Endereço incompleto. Busque o CEP ou preencha manualmente.")
    if not dados["numero"]:
        raise RegraNegocioError("Informe o número/complemento do endereço.")
    if dados["servico"] not in SERVICOS:
        raise RegraNegocioError("Selecione um serviço válido.")

    data_hora = validar_data_hora(campos.get("data"), campos.get("hora"), agora)
    dados["data_hora"] = data_hora.strftime("%Y-%m-%d %H:%M")  # formato ISO: ordena corretamente
    return dados


# ==========================================================================
# 4. Consulta ao ViaCEP
# ==========================================================================
def consultar_cep(cep, timeout=5):
    """Busca o endereço no ViaCEP.

    Lança RegraNegocioError se o CEP for inválido/inexistente e
    ApiIndisponivelError se não houver conexão com o serviço.
    """
    cep = validar_cep(cep)
    try:
        with urllib.request.urlopen(URL_VIACEP.format(cep=cep), timeout=timeout) as resposta:
            resultado = json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        if erro.code == 400:
            raise RegraNegocioError("CEP em formato inválido.")
        raise ApiIndisponivelError(f"ViaCEP respondeu com erro HTTP {erro.code}.")
    except (urllib.error.URLError, OSError, ValueError) as erro:
        raise ApiIndisponivelError(f"Sem conexão com o ViaCEP ({erro}).")

    if resultado.get("erro"):
        raise RegraNegocioError(f"CEP {formatar_cep(cep)} não encontrado.")
    return {
        "cep": formatar_cep(cep),
        "logradouro": resultado.get("logradouro", ""),
        "bairro": resultado.get("bairro", ""),
        "cidade_uf": f"{resultado.get('localidade', '')}/{resultado.get('uf', '')}",
    }


# ==========================================================================
# 5. Banco de dados
# ==========================================================================
ESQUEMA = """
CREATE TABLE IF NOT EXISTS tutores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL,
    telefone    TEXT NOT NULL UNIQUE,      -- identifica o cliente: um cadastro por pessoa
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
    UNIQUE (tutor_id, nome)                -- o mesmo tutor não repete o nome do pet
);
CREATE TABLE IF NOT EXISTS agendamentos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id     INTEGER NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    servico    TEXT NOT NULL,
    data_hora  TEXT NOT NULL UNIQUE,       -- um horário só pode ser reservado uma vez
    criado_em  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""

CONSULTA_AGENDAMENTOS = """
SELECT a.id, p.nome, t.nome, t.telefone, t.cep, t.logradouro, t.numero,
       t.bairro, t.cidade_uf, a.servico, a.data_hora
  FROM agendamentos a
  JOIN pets p    ON p.id = a.pet_id
  JOIN tutores t ON t.id = p.tutor_id
"""


class Banco:
    """Toda a comunicação com o SQLite fica nesta classe."""

    def __init__(self, arquivo=ARQUIVO_BANCO):
        self.con = sqlite3.connect(arquivo)
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(ESQUEMA)

    def fechar(self):
        self.con.close()

    # ---------------- leitura ----------------
    def listar(self, dia="", mes="", ano="", busca=""):
        """Lista os agendamentos aplicando os filtros (vazio = sem filtro)."""
        condicoes, parametros = [], []
        if ano:
            condicoes.append("substr(a.data_hora, 1, 4) = ?")
            parametros.append(ano)
        if mes:
            condicoes.append("substr(a.data_hora, 6, 2) = ?")
            parametros.append(mes)
        if dia:
            condicoes.append("substr(a.data_hora, 9, 2) = ?")
            parametros.append(dia)
        if busca:
            termo = f"%{busca}%"
            telefone = f"%{somente_digitos(busca) or busca}%"
            condicoes.append("(p.nome LIKE ? OR t.nome LIKE ? OR t.telefone LIKE ? OR "
                             "a.servico LIKE ? OR t.bairro LIKE ? OR t.cidade_uf LIKE ?)")
            parametros += [termo, termo, telefone, termo, termo, termo]

        sql = CONSULTA_AGENDAMENTOS
        if condicoes:
            sql += " WHERE " + " AND ".join(condicoes)
        return self.con.execute(sql + " ORDER BY a.data_hora", parametros).fetchall()

    def buscar(self, agendamento_id):
        return self.con.execute(CONSULTA_AGENDAMENTOS + " WHERE a.id = ?", (agendamento_id,)).fetchone()

    def anos_cadastrados(self):
        linhas = self.con.execute("SELECT DISTINCT substr(data_hora, 1, 4) FROM agendamentos").fetchall()
        return sorted({linha[0] for linha in linhas} | {str(datetime.now().year)})

    def horarios_livres(self, data, ignorar_id=None, agora=None):
        """Horários futuros e ainda não reservados na data informada."""
        agora = agora or datetime.now()
        if data < agora.date() or data.weekday() not in DIAS_ABERTOS:
            return []
        ocupados = {linha[0] for linha in self.con.execute(
            "SELECT substr(data_hora, 12, 5) FROM agendamentos "
            "WHERE substr(data_hora, 1, 10) = ? AND id IS NOT ?", (data.isoformat(), ignorar_id))}
        return [hora for hora in horarios_do_dia()
                if hora not in ocupados
                and datetime.strptime(f"{data} {hora}", "%Y-%m-%d %H:%M") > agora]

    # ---------------- escrita ----------------
    def salvar(self, dados, agendamento_id=None):
        """Cadastra (sem id) ou atualiza (com id) um agendamento. Devolve o id."""
        try:
            with self.con:  # transação: confirma tudo ou desfaz tudo em caso de erro
                tutor_id = self._salvar_tutor(dados, agendamento_id)
                pet_id = self._salvar_pet(tutor_id, dados["pet"])
                valores = (pet_id, dados["servico"], dados["data_hora"])
                if agendamento_id:
                    self.con.execute("UPDATE agendamentos SET pet_id = ?, servico = ?, data_hora = ? "
                                     "WHERE id = ?", valores + (agendamento_id,))
                else:
                    agendamento_id = self.con.execute(
                        "INSERT INTO agendamentos (pet_id, servico, data_hora) VALUES (?, ?, ?)",
                        valores).lastrowid
            return agendamento_id
        except sqlite3.IntegrityError as erro:
            if "data_hora" in str(erro):
                raise RegraNegocioError("Este horário já está reservado. Escolha outro horário.")
            raise RegraNegocioError(f"Dados duplicados ou inválidos: {erro}")

    def excluir(self, agendamento_id):
        with self.con:
            self.con.execute("DELETE FROM agendamentos WHERE id = ?", (agendamento_id,))

    def _salvar_tutor(self, dados, agendamento_id):
        """O telefone identifica o cliente. Reaproveita o cadastro ou cria um novo."""
        dono_do_telefone = self.con.execute(
            "SELECT id, nome FROM tutores WHERE telefone = ?", (dados["telefone"],)).fetchone()
        tutor_em_edicao = self._tutor_do_agendamento(agendamento_id)

        if (dono_do_telefone and dono_do_telefone[0] != tutor_em_edicao
                and dono_do_telefone[1].casefold() != dados["tutor"].casefold()):
            raise RegraNegocioError(
                f"O telefone {formatar_telefone(dados['telefone'])} já pertence ao cliente "
                f"\"{dono_do_telefone[1]}\". Cada cliente tem um cadastro único.")

        tutor_id = dono_do_telefone[0] if dono_do_telefone else tutor_em_edicao
        valores = (dados["tutor"], dados["telefone"], dados["cep"], dados["logradouro"],
                   dados["numero"], dados["bairro"], dados["cidade_uf"])
        if tutor_id:
            self.con.execute("UPDATE tutores SET nome = ?, telefone = ?, cep = ?, logradouro = ?, "
                             "numero = ?, bairro = ?, cidade_uf = ? WHERE id = ?", valores + (tutor_id,))
            return tutor_id
        return self.con.execute("INSERT INTO tutores (nome, telefone, cep, logradouro, numero, bairro, "
                                "cidade_uf) VALUES (?, ?, ?, ?, ?, ?, ?)", valores).lastrowid

    def _salvar_pet(self, tutor_id, nome):
        pet = self.con.execute("SELECT id FROM pets WHERE tutor_id = ? AND nome = ?",
                               (tutor_id, nome)).fetchone()
        if pet:
            return pet[0]
        return self.con.execute("INSERT INTO pets (tutor_id, nome) VALUES (?, ?)",
                                (tutor_id, nome)).lastrowid

    def _tutor_do_agendamento(self, agendamento_id):
        if not agendamento_id:
            return None
        linha = self.con.execute("SELECT p.tutor_id FROM agendamentos a JOIN pets p ON p.id = a.pet_id "
                                 "WHERE a.id = ?", (agendamento_id,)).fetchone()
        return linha[0] if linha else None


# ==========================================================================
# 6. Interface gráfica (tela única)
# ==========================================================================
COR_FUNDO = "#F6F3EE"
COR_PRIMARIA = "#2F6F5E"
COR_TEXTO = "#1F2A27"
COR_SUAVE = "#5F6B67"

COLUNAS_TABELA = [  # (chave, título, largura)
    ("id", "ID", 45), ("pet", "Pet", 100), ("tutor", "Tutor", 150), ("telefone", "Telefone", 115),
    ("cep", "CEP", 80), ("logradouro", "Logradouro", 190), ("numero", "Nº", 60),
    ("bairro", "Bairro", 130), ("cidade_uf", "Cidade/UF", 140), ("servico", "Serviço", 110),
    ("data", "Data", 90), ("hora", "Horário", 70),
]
CAMPOS_FORMULARIO = ["pet", "tutor", "telefone", "cep", "logradouro", "numero",
                     "bairro", "cidade_uf", "servico", "data", "hora"]


class App:
    def __init__(self, janela):
        self.janela = janela
        self.banco = Banco()
        self.agendamento_id = None  # id do agendamento em edição (None = novo)
        self.ultimo_cep = None      # evita buscar o mesmo CEP duas vezes
        self.campos = {nome: tk.StringVar() for nome in CAMPOS_FORMULARIO}
        self.endereco_manual = tk.BooleanVar()
        self.filtros = {nome: tk.StringVar(value=TODOS) for nome in ("dia", "mes", "ano")}
        self.busca = tk.StringVar()

        janela.title("Patinhas & Cia - Agenda do Pet Shop")
        janela.geometry("1280x760")
        janela.minsize(1100, 640)
        janela.configure(bg=COR_FUNDO)
        try:
            janela.state("zoomed")  # ocupa a tela inteira (Windows)
        except tk.TclError:
            pass

        self._configurar_estilos()
        self._criar_cabecalho()
        self._criar_formulario()
        self._criar_botoes()
        self._criar_agenda()
        self.limpar_formulario()

    # ---------------- montagem da tela ----------------
    def _configurar_estilos(self):
        estilo = ttk.Style()
        estilo.theme_use("clam")
        estilo.configure(".", background=COR_FUNDO, foreground=COR_TEXTO, font=("Segoe UI", 10))
        estilo.configure("TLabelframe", background=COR_FUNDO)
        estilo.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground=COR_PRIMARIA)
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 18, "bold"), foreground=COR_PRIMARIA)
        estilo.configure("Suave.TLabel", foreground=COR_SUAVE, font=("Segoe UI", 9))
        estilo.configure("TButton", padding=(12, 5))
        estilo.configure("Primario.TButton", background=COR_PRIMARIA, foreground="white")
        estilo.map("Primario.TButton", background=[("active", "#245848"), ("disabled", "#9BB5AD")])
        estilo.configure("Treeview", rowheight=26)
        estilo.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#E4EDE9")

    def _criar_cabecalho(self):
        topo = ttk.Frame(self.janela, padding=(16, 10, 16, 0))
        topo.pack(fill="x")
        ttk.Label(topo, text="🐾 Patinhas & Cia", style="Titulo.TLabel").pack(side="left")
        ttk.Label(topo, text="   Agenda de atendimentos · Seg a Sáb, 08h às 18h · intervalos de 30 min",
                  style="Suave.TLabel").pack(side="left", pady=(8, 0))

    def _campo(self, quadro, rotulo, nome, linha, coluna=0, largura=24):
        """Cria um rótulo com uma caixa de texto embaixo e devolve a caixa."""
        ttk.Label(quadro, text=rotulo).grid(row=linha, column=coluna, sticky="w", padx=6)
        caixa = ttk.Entry(quadro, textvariable=self.campos[nome], width=largura)
        caixa.grid(row=linha + 1, column=coluna, sticky="ew", padx=6, pady=(0, 6))
        return caixa

    def _criar_formulario(self):
        formulario = ttk.Frame(self.janela, padding=(16, 8, 16, 0))
        formulario.pack(fill="x")
        for coluna in range(3):
            formulario.columnconfigure(coluna, weight=1, uniform="blocos")

        # Bloco 1: pet e tutor
        bloco = ttk.LabelFrame(formulario, text="Pet e tutor", padding=8)
        bloco.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        bloco.columnconfigure(0, weight=1)
        self._campo(bloco, "Nome do pet *", "pet", 0)
        self._campo(bloco, "Tutor *", "tutor", 2)
        self._campo(bloco, "Telefone (DDD + número) *", "telefone", 4)

        # Bloco 2: endereço (ViaCEP)
        bloco = ttk.LabelFrame(formulario, text="Endereço do tutor", padding=8)
        bloco.grid(row=0, column=1, sticky="nsew", padx=8)
        bloco.columnconfigure(0, weight=2)
        bloco.columnconfigure(1, weight=1)
        ttk.Label(bloco, text="CEP *").grid(row=0, column=0, sticky="w", padx=6)
        linha_cep = ttk.Frame(bloco)
        linha_cep.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6)
        linha_cep.columnconfigure(0, weight=1)
        caixa_cep = ttk.Entry(linha_cep, textvariable=self.campos["cep"])
        caixa_cep.grid(row=0, column=0, sticky="ew")
        caixa_cep.bind("<KeyRelease>", self._ao_digitar_cep)
        caixa_cep.bind("<Return>", lambda evento: self.buscar_cep())
        ttk.Button(linha_cep, text="Buscar CEP", command=self.buscar_cep).grid(row=0, column=1, padx=(6, 0))
        ttk.Checkbutton(bloco, text="Inserir endereço manualmente (ViaCEP fora do ar)",
                        variable=self.endereco_manual, command=self._aplicar_modo_endereco
                        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        self.caixas_endereco = [
            self._campo(bloco, "Logradouro *", "logradouro", 3, 0),
            self._campo(bloco, "Bairro *", "bairro", 5, 0),
            self._campo(bloco, "Cidade/UF *", "cidade_uf", 5, 1, largura=14),
        ]
        self.caixa_numero = self._campo(bloco, "Número *", "numero", 3, 1, largura=10)

        # Bloco 3: atendimento
        bloco = ttk.LabelFrame(formulario, text="Atendimento", padding=8)
        bloco.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        bloco.columnconfigure(0, weight=1)
        ttk.Label(bloco, text="Serviço *").grid(row=0, column=0, sticky="w", padx=6)
        ttk.Combobox(bloco, textvariable=self.campos["servico"], values=SERVICOS,
                     state="readonly").grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        caixa_data = self._campo(bloco, "Data (DD/MM/AAAA) *", "data", 2)
        caixa_data.bind("<FocusOut>", lambda evento: self.atualizar_horarios())
        caixa_data.bind("<Return>", lambda evento: self.atualizar_horarios())
        ttk.Label(bloco, text="Horário disponível *").grid(row=4, column=0, sticky="w", padx=6)
        self.combo_hora = ttk.Combobox(bloco, textvariable=self.campos["hora"], state="readonly")
        self.combo_hora.grid(row=5, column=0, sticky="ew", padx=6)
        self.combo_hora.bind("<Button-1>", lambda evento: self.atualizar_horarios())
        self.aviso_horarios = ttk.Label(bloco, style="Suave.TLabel")
        self.aviso_horarios.grid(row=6, column=0, sticky="w", padx=6, pady=(4, 0))

    def _criar_botoes(self):
        barra = ttk.Frame(self.janela, padding=(16, 8))
        barra.pack(fill="x")
        self.botao_cadastrar = ttk.Button(barra, text="Cadastrar", style="Primario.TButton",
                                          command=self.cadastrar)
        self.botao_atualizar = ttk.Button(barra, text="Salvar alterações", command=self.atualizar)
        self.botao_excluir = ttk.Button(barra, text="Excluir", command=self.excluir)
        self.botao_cadastrar.pack(side="left")
        self.botao_atualizar.pack(side="left", padx=6)
        self.botao_excluir.pack(side="left")
        ttk.Button(barra, text="Limpar / Novo", command=self.limpar_formulario).pack(side="left", padx=6)
        self.status = ttk.Label(barra, style="Suave.TLabel")
        self.status.pack(side="right")

    def _criar_agenda(self):
        quadro = ttk.LabelFrame(self.janela, text="Agenda", padding=8)
        quadro.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        # Filtros
        filtros = ttk.Frame(quadro)
        filtros.pack(fill="x", pady=(0, 8))
        opcoes = {
            "dia": [TODOS] + [f"{dia:02d}" for dia in range(1, 32)],
            "mes": [TODOS] + MESES,
            "ano": [TODOS],
        }
        self.combos_filtro = {}
        for nome, rotulo, largura in (("dia", "Dia", 6), ("mes", "Mês", 11), ("ano", "Ano", 7)):
            ttk.Label(filtros, text=rotulo).pack(side="left", padx=(0, 4))
            combo = ttk.Combobox(filtros, textvariable=self.filtros[nome], values=opcoes[nome],
                                 width=largura, state="readonly")
            combo.pack(side="left", padx=(0, 12))
            combo.bind("<<ComboboxSelected>>", lambda evento: self.carregar_tabela())
            self.combos_filtro[nome] = combo
        ttk.Label(filtros, text="Buscar (cliente, pet, telefone, serviço, bairro, cidade)").pack(side="left", padx=(0, 4))
        caixa_busca = ttk.Entry(filtros, textvariable=self.busca, width=28)
        caixa_busca.pack(side="left", fill="x", expand=True)
        caixa_busca.bind("<KeyRelease>", lambda evento: self.carregar_tabela())
        ttk.Button(filtros, text="Limpar filtros", command=self.limpar_filtros).pack(side="left", padx=(8, 0))

        # Tabela (a única parte da tela com rolagem)
        area = ttk.Frame(quadro)
        area.pack(fill="both", expand=True)
        self.tabela = ttk.Treeview(area, columns=[c[0] for c in COLUNAS_TABELA],
                                   show="headings", selectmode="browse")
        for chave, titulo, largura in COLUNAS_TABELA:
            centralizar = chave in ("id", "cep", "numero", "data", "hora")
            self.tabela.heading(chave, text=titulo)
            self.tabela.column(chave, width=largura, minwidth=40, anchor="center" if centralizar else "w")
        rolagem_y = ttk.Scrollbar(area, orient="vertical", command=self.tabela.yview)
        rolagem_x = ttk.Scrollbar(area, orient="horizontal", command=self.tabela.xview)
        self.tabela.configure(yscrollcommand=rolagem_y.set, xscrollcommand=rolagem_x.set)
        self.tabela.grid(row=0, column=0, sticky="nsew")
        rolagem_y.grid(row=0, column=1, sticky="ns")
        rolagem_x.grid(row=1, column=0, sticky="ew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        self.tabela.tag_configure("passado", foreground="#9A9A9A")
        self.tabela.bind("<<TreeviewSelect>>", self.selecionar_agendamento)

    # ---------------- endereço / ViaCEP ----------------
    def _aplicar_modo_endereco(self):
        """Libera a digitação do endereço só no modo manual."""
        estado = "normal" if self.endereco_manual.get() else "readonly"
        for caixa in self.caixas_endereco:
            caixa.configure(state=estado)

    def _ao_digitar_cep(self, evento=None):
        """Busca automaticamente quando o CEP completa 8 dígitos."""
        cep = somente_digitos(self.campos["cep"].get())
        if len(cep) == 8 and cep != self.ultimo_cep and not self.endereco_manual.get():
            self.buscar_cep()

    def buscar_cep(self):
        texto = self.campos["cep"].get()
        try:
            cep = validar_cep(texto)
            self.ultimo_cep = cep
            self.janela.config(cursor="watch")
            self.janela.update_idletasks()
            endereco = consultar_cep(cep)
        except RegraNegocioError as erro:
            registrar_log("CONSULTA CEP", f"CEP \"{limpar_texto(texto)}\" inválido ou não encontrado.")
            messagebox.showwarning("CEP", f"{erro}\n\nConfira o número ou marque "
                                          "\"Inserir endereço manualmente\".")
            return
        except ApiIndisponivelError as erro:
            registrar_log("CONSULTA CEP", f"Falha ao consultar o CEP \"{formatar_cep(texto)}\": {erro}")
            if messagebox.askyesno("ViaCEP indisponível",
                                   "Não foi possível consultar o ViaCEP agora.\n\n"
                                   "Deseja preencher o endereço manualmente?"):
                self.endereco_manual.set(True)
                self._aplicar_modo_endereco()
            return
        finally:
            self.janela.config(cursor="")

        self.endereco_manual.set(not endereco["logradouro"])  # CEP geral de cidade: completa à mão
        self._preencher_endereco(endereco)
        registrar_log("CONSULTA CEP", f"CEP \"{endereco['cep']}\" consultado com sucesso "
                                      f"({endereco['cidade_uf']}).")
        self.caixa_numero.focus_set()

    def _preencher_endereco(self, endereco):
        for caixa in self.caixas_endereco:
            caixa.configure(state="normal")  # campo readonly não aceita valor novo
        for chave, valor in endereco.items():
            self.campos[chave].set(valor)
        self._aplicar_modo_endereco()

    # ---------------- agenda ----------------
    def atualizar_horarios(self):
        """Mostra no combo apenas os horários livres e futuros da data digitada."""
        try:
            data = ler_data(self.campos["data"].get())
        except RegraNegocioError:
            self.combo_hora.configure(values=[])
            self.aviso_horarios.configure(text="Informe uma data válida.")
            return
        livres = self.banco.horarios_livres(data, ignorar_id=self.agendamento_id)
        self.combo_hora.configure(values=livres)
        if self.campos["hora"].get() not in livres:
            self.campos["hora"].set("")

        if data < datetime.now().date():
            aviso = "Data no passado: escolha outra data."
        elif data.weekday() not in DIAS_ABERTOS:
            aviso = "Fechado aos domingos."
        elif not livres:
            aviso = "Sem horários livres nesta data."
        else:
            aviso = f"{len(livres)} horário(s) livre(s) em {data:%d/%m/%Y}."
        self.aviso_horarios.configure(text=aviso)

    def carregar_tabela(self):
        """Recarrega a tabela respeitando os filtros."""
        dia, mes, ano = (self.filtros[nome].get() for nome in ("dia", "mes", "ano"))
        filtro_mes = f"{MESES.index(mes) + 1:02d}" if mes in MESES else ""
        try:
            linhas = self.banco.listar(dia=dia if dia != TODOS else "", mes=filtro_mes,
                                       ano=ano if ano != TODOS else "",
                                       busca=limpar_texto(self.busca.get()))
            self.combos_filtro["ano"].configure(values=[TODOS] + self.banco.anos_cadastrados())
        except sqlite3.Error as erro:
            messagebox.showerror("Banco de dados", str(erro))
            return

        self.tabela.delete(*self.tabela.get_children())
        agora = datetime.now()
        for linha in linhas:
            data_hora = datetime.strptime(linha[10], "%Y-%m-%d %H:%M")
            valores = (linha[0], linha[1], linha[2], formatar_telefone(linha[3]), *linha[4:10],
                       f"{data_hora:%d/%m/%Y}", f"{data_hora:%H:%M}")
            tags = ("passado",) if data_hora < agora else ()
            self.tabela.insert("", "end", iid=str(linha[0]), values=valores, tags=tags)
        self.status.configure(text=f"{len(linhas)} agendamento(s) exibido(s).")

    def limpar_filtros(self):
        for filtro in self.filtros.values():
            filtro.set(TODOS)
        self.busca.set("")
        self.carregar_tabela()

    # ---------------- CRUD ----------------
    def _ler_formulario(self):
        return {nome: variavel.get() for nome, variavel in self.campos.items()}

    def _salvar(self, agendamento_id=None):
        """Valida e grava. Devolve os dados salvos ou None se houve erro."""
        try:
            dados = preparar_dados(self._ler_formulario())
            dados["id"] = self.banco.salvar(dados, agendamento_id)
            return dados
        except RegraNegocioError as erro:
            messagebox.showwarning("Atenção", str(erro))
        except sqlite3.Error as erro:
            messagebox.showerror("Banco de dados", f"Não foi possível salvar: {erro}")
        return None

    def cadastrar(self):
        dados = self._salvar()
        if dados:
            registrar_log("INSERÇÃO", f"Agendamento Pet \"{dados['pet']}\" (Tutor: {dados['tutor']}, "
                                      f"Serviço: {dados['servico']}) cadastrado com sucesso.")
            self.limpar_formulario()
            messagebox.showinfo("Sucesso", "Agendamento cadastrado com sucesso!")

    def atualizar(self):
        dados = self._salvar(self.agendamento_id)
        if dados:
            nova_data = datetime.strptime(dados["data_hora"], "%Y-%m-%d %H:%M")
            registrar_log("ATUALIZAÇÃO", f"Agendamento ID {dados['id']} alterado (Novo Serviço: "
                                         f"{dados['servico']}, Nova Data: {nova_data:%d/%m/%Y %H:%M}).")
            self.limpar_formulario()
            messagebox.showinfo("Sucesso", "Agendamento atualizado com sucesso!")

    def excluir(self):
        pet = self.campos["pet"].get()
        if not messagebox.askyesno("Confirmar exclusão", f"Excluir o agendamento ID {self.agendamento_id} "
                                                         f"(\"{pet}\")?\nEsta ação não pode ser desfeita."):
            return
        try:
            self.banco.excluir(self.agendamento_id)
        except sqlite3.Error as erro:
            messagebox.showerror("Banco de dados", f"Não foi possível excluir: {erro}")
            return
        registrar_log("EXCLUSÃO", f"Agendamento ID {self.agendamento_id} (\"{pet}\") removido do sistema.")
        self.limpar_formulario()

    def selecionar_agendamento(self, evento=None):
        """Ao clicar numa linha, os dados voltam para o formulário (modo edição)."""
        selecao = self.tabela.selection()
        if not selecao:
            return
        linha = self.banco.buscar(int(selecao[0]))
        if not linha:
            return
        self.agendamento_id = linha[0]
        data_hora = datetime.strptime(linha[10], "%Y-%m-%d %H:%M")
        self.endereco_manual.set(False)
        self._preencher_endereco({"cep": linha[4], "logradouro": linha[5], "bairro": linha[7],
                                  "cidade_uf": linha[8]})
        for nome, valor in (("pet", linha[1]), ("tutor", linha[2]), ("telefone", formatar_telefone(linha[3])),
                            ("numero", linha[6]), ("servico", linha[9]),
                            ("data", f"{data_hora:%d/%m/%Y}"), ("hora", f"{data_hora:%H:%M}")):
            self.campos[nome].set(valor)
        self.ultimo_cep = somente_digitos(linha[4])
        self.atualizar_horarios()
        self._modo_edicao(True)

    def limpar_formulario(self):
        self.agendamento_id = None
        self.ultimo_cep = None
        self.endereco_manual.set(False)
        self._preencher_endereco({"cep": "", "logradouro": "", "bairro": "", "cidade_uf": ""})
        for nome in ("pet", "tutor", "telefone", "numero", "hora"):
            self.campos[nome].set("")
        self.campos["servico"].set(SERVICOS[0])
        self.campos["data"].set(f"{self._proximo_dia_com_horario():%d/%m/%Y}")
        self.atualizar_horarios()
        self.carregar_tabela()
        self._modo_edicao(False)

    def _proximo_dia_com_horario(self):
        """Data sugerida: hoje, ou o próximo dia que ainda tenha horário livre."""
        dia = datetime.now().date()
        for _ in range(ANTECEDENCIA_MAXIMA_DIAS + 1):
            if self.banco.horarios_livres(dia):
                return dia
            dia += timedelta(days=1)
        return datetime.now().date()

    def _modo_edicao(self, editando):
        """Cadastrar fica ativo no modo novo; Salvar/Excluir só no modo edição."""
        self.botao_cadastrar.state(["disabled" if editando else "!disabled"])
        for botao in (self.botao_atualizar, self.botao_excluir):
            botao.state(["!disabled" if editando else "disabled"])
        if editando:
            self.status.configure(text=f"Editando o agendamento ID {self.agendamento_id}.")

    def fechar(self):
        self.banco.fechar()
        self.janela.destroy()


def main():
    janela = tk.Tk()
    app = App(janela)
    janela.protocol("WM_DELETE_WINDOW", app.fechar)
    janela.mainloop()


if __name__ == "__main__":
    main()
