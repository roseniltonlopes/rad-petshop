"""Testes das regras de negócio. Rodar com: python -m unittest test_regras -v"""
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

import petshop_app as app

AGORA = datetime(2026, 11, 16, 10, 15)  # uma segunda-feira às 10:15 (data fixa = teste repetível)


def formulario(**alteracoes):
    campos = {"pet": "  Thor ", "tutor": "Carlos  Silva", "telefone": "(21) 99876-5432",
              "cep": "20020-010", "logradouro": "Av. Rio Branco", "numero": "100",
              "bairro": "Centro", "cidade_uf": "Rio de Janeiro/RJ", "servico": "Banho e Tosa",
              "data": "17/11/2026", "hora": "09:00"}
    campos.update(alteracoes)
    return app.preparar_dados(campos, AGORA)


class TestRegrasDeDataHora(unittest.TestCase):
    def test_bloqueia_data_passada(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "datas passadas"):
            app.validar_data_hora("15/11/2026", "09:00", AGORA)

    def test_bloqueia_horario_que_ja_passou_hoje(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "já passou"):
            app.validar_data_hora("16/11/2026", "10:00", AGORA)

    def test_aceita_horario_futuro_hoje(self):
        self.assertEqual(app.validar_data_hora("16/11/2026", "10:30", AGORA), datetime(2026, 11, 16, 10, 30))

    def test_bloqueia_domingo(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "domingo"):
            app.validar_data_hora("22/11/2026", "09:00", AGORA)

    def test_bloqueia_fora_do_expediente_e_do_intervalo(self):
        for hora in ("07:30", "18:00", "09:15"):
            with self.assertRaisesRegex(app.RegraNegocioError, "horário da agenda"):
                app.validar_data_hora("17/11/2026", hora, AGORA)

    def test_bloqueia_data_inexistente(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "DD/MM/AAAA"):
            app.validar_data_hora("31/02/2026", "09:00", AGORA)

    def test_bloqueia_antecedencia_maior_que_o_limite(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "antecedência"):
            app.validar_data_hora("17/11/2027", "09:00", AGORA)


class TestLimpezaDasEntradas(unittest.TestCase):
    def test_limpa_espacos_e_caracteres(self):
        dados = formulario()
        self.assertEqual(dados["pet"], "Thor")
        self.assertEqual(dados["tutor"], "Carlos Silva")
        self.assertEqual(dados["telefone"], "21998765432")
        self.assertEqual(dados["cep"], "20020-010")
        self.assertEqual(dados["data_hora"], "2026-11-17 09:00")

    def test_rejeita_cep_e_telefone_invalidos(self):
        with self.assertRaises(app.RegraNegocioError):
            app.validar_cep("123")
        with self.assertRaises(app.RegraNegocioError):
            formulario(telefone="1234")


class TestBanco(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.banco = app.Banco(os.path.join(self.pasta.name, "teste.db"))

    def tearDown(self):
        self.banco.fechar()
        self.pasta.cleanup()

    def test_crud_completo(self):
        id_ = self.banco.salvar(formulario())
        self.assertEqual(len(self.banco.listar()), 1)
        self.banco.salvar(formulario(servico="Vacinação", hora="11:00"), id_)
        self.assertEqual(self.banco.buscar(id_)[9], "Vacinação")
        self.banco.excluir(id_)
        self.assertEqual(self.banco.listar(), [])

    def test_nao_reserva_horario_ocupado(self):
        self.banco.salvar(formulario())
        with self.assertRaisesRegex(app.RegraNegocioError, "já está reservado"):
            self.banco.salvar(formulario(pet="Mel", tutor="Ana", telefone="11912345678"))

    def test_editar_mantendo_o_mesmo_horario(self):
        id_ = self.banco.salvar(formulario())
        self.banco.salvar(formulario(servico="Consulta"), id_)
        self.assertEqual(self.banco.buscar(id_)[9], "Consulta")

    def test_telefone_unico_por_cliente(self):
        self.banco.salvar(formulario())
        with self.assertRaisesRegex(app.RegraNegocioError, "já pertence"):
            self.banco.salvar(formulario(tutor="Outra Pessoa", hora="10:00"))

    def test_mesmo_cliente_reaproveita_o_cadastro(self):
        self.banco.salvar(formulario())
        self.banco.salvar(formulario(pet="thor", hora="10:00"))  # mesmo pet (maiúsc./minúsc.)
        self.banco.salvar(formulario(pet="Mel", hora="10:30"))   # pet novo do mesmo tutor
        contar = lambda tabela: self.banco.con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
        self.assertEqual(contar("tutores"), 1)
        self.assertEqual(contar("pets"), 2)

    def test_restricao_unique_no_proprio_banco(self):
        self.banco.salvar(formulario())
        with self.assertRaises(sqlite3.IntegrityError):
            self.banco.con.execute("INSERT INTO agendamentos (pet_id, servico, data_hora) "
                                   "VALUES (1, 'Consulta', '2026-11-17 09:00')")

    def test_horarios_livres(self):
        self.banco.salvar(formulario(data="16/11/2026", hora="11:00"))
        livres = self.banco.horarios_livres(date(2026, 11, 16), agora=AGORA)
        self.assertNotIn("10:00", livres)  # já passou
        self.assertNotIn("11:00", livres)  # ocupado
        self.assertIn("10:30", livres)
        self.assertEqual(self.banco.horarios_livres(date(2026, 11, 22), agora=AGORA), [])  # domingo

    def test_filtros(self):
        self.banco.salvar(formulario())                                                  # 17/11
        self.banco.salvar(formulario(pet="Mel", tutor="Ana Souza", telefone="11912345678",
                                     data="03/12/2026", servico="Consulta"))            # 03/12
        self.assertEqual(len(self.banco.listar(mes="11")), 1)
        self.assertEqual(len(self.banco.listar(dia="03", mes="12", ano="2026")), 1)
        self.assertEqual(self.banco.listar(busca="ana")[0][1], "Mel")       # nome do cliente
        self.assertEqual(self.banco.listar(busca="thor")[0][2], "Carlos Silva")  # nome do pet
        self.assertEqual(len(self.banco.listar(busca="(11) 91234")), 1)     # telefone formatado
        self.assertEqual(self.banco.listar(ano="2025"), [])


class TestViaCepELog(unittest.TestCase):
    def test_api_fora_do_ar(self):
        with mock.patch("urllib.request.urlopen", side_effect=app.urllib.error.URLError("offline")):
            with self.assertRaises(app.ApiIndisponivelError):
                app.consultar_cep("01001000")

    def test_log_em_modo_adicao(self):
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = os.path.join(pasta, "log.txt")
            app.registrar_log("TESTE", "primeira", arquivo)
            app.registrar_log("TESTE", "segunda", arquivo)
            with open(arquivo, encoding="utf-8") as log:
                self.assertEqual(len(log.readlines()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
