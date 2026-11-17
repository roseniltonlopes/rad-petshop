"""Testes automatizados das regras de negócio (python -m unittest test_regras -v)."""
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import petshop_app as app

AGORA = datetime(2026, 11, 16, 10, 15)   # segunda-feira, 10:15


def campos(**kw):
    base = {"pet": "  Thor ", "tutor": "Carlos  Silva", "telefone": "(21) 99876-5432",
            "cep": "20020-010", "logradouro": "Av. Rio Branco", "numero": "100",
            "bairro": "Centro", "cidade_uf": "Rio de Janeiro/RJ", "servico": "Banho e Tosa",
            "data": "17/11/2026", "hora": "09:00"}
    base.update(kw)
    return base


class TestDataHora(unittest.TestCase):
    def test_data_passada(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "datas passadas"):
            app.validar_data_hora("15/11/2026", "09:00", AGORA)

    def test_horario_passado_hoje(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "já passou"):
            app.validar_data_hora("16/11/2026", "10:00", AGORA)

    def test_horario_futuro_hoje(self):
        self.assertEqual(app.validar_data_hora("16/11/2026", "10:30", AGORA),
                         datetime(2026, 11, 16, 10, 30))

    def test_domingo(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "domingo"):
            app.validar_data_hora("22/11/2026", "09:00", AGORA)

    def test_fora_do_expediente_e_intervalo(self):
        for h in ("07:30", "18:00", "09:15"):
            with self.assertRaisesRegex(app.RegraNegocioError, "fora da agenda"):
                app.validar_data_hora("17/11/2026", h, AGORA)

    def test_data_invalida(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "DD/MM/AAAA"):
            app.validar_data_hora("31/02/2026", "09:00", AGORA)

    def test_antecedencia_maxima(self):
        with self.assertRaisesRegex(app.RegraNegocioError, "antecedência"):
            app.validar_data_hora("17/11/2027", "09:00", AGORA)


class TestLimpeza(unittest.TestCase):
    def test_preparar_dados_limpa(self):
        d = app.preparar_dados(campos(), AGORA)
        self.assertEqual(d["pet"], "Thor")
        self.assertEqual(d["tutor"], "Carlos Silva")
        self.assertEqual(d["telefone"], "21998765432")
        self.assertEqual(d["cep"], "20020-010")
        self.assertEqual(d["data_hora"], "2026-11-17 09:00")

    def test_cep_invalido(self):
        with self.assertRaises(app.RegraNegocioError):
            app.validar_cep("123")

    def test_telefone_invalido(self):
        with self.assertRaises(app.RegraNegocioError):
            app.preparar_dados(campos(telefone="1234"), AGORA)


class TestBanco(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = app.Banco(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.fechar()
        self.tmp.cleanup()

    def test_crud(self):
        ag = self.db.salvar(app.preparar_dados(campos(), AGORA))
        self.assertEqual(len(self.db.listar()), 1)
        self.db.salvar(app.preparar_dados(campos(servico="Vacinação", hora="11:00"), AGORA), ag)
        self.assertEqual(self.db.obter(ag)[9], "Vacinação")
        self.assertEqual(self.db.excluir(ag), 1)
        self.assertEqual(self.db.listar(), [])

    def test_horario_ocupado(self):
        self.db.salvar(app.preparar_dados(campos(), AGORA))
        outro = campos(pet="Mel", tutor="Ana", telefone="11912345678")
        with self.assertRaisesRegex(app.RegraNegocioError, "já está reservado"):
            self.db.salvar(app.preparar_dados(outro, AGORA))

    def test_editar_mantendo_mesmo_horario(self):
        ag = self.db.salvar(app.preparar_dados(campos(), AGORA))
        self.db.salvar(app.preparar_dados(campos(servico="Consulta"), AGORA), ag)
        self.assertEqual(self.db.obter(ag)[9], "Consulta")

    def test_telefone_unico_por_cliente(self):
        self.db.salvar(app.preparar_dados(campos(), AGORA))
        intruso = campos(tutor="Outra Pessoa", hora="10:00")
        with self.assertRaisesRegex(app.RegraNegocioError, "já está cadastrado"):
            self.db.salvar(app.preparar_dados(intruso, AGORA))

    def test_mesmo_cliente_reaproveita_cadastro(self):
        self.db.salvar(app.preparar_dados(campos(), AGORA))
        self.db.salvar(app.preparar_dados(campos(pet="thor", hora="10:00"), AGORA))
        self.db.salvar(app.preparar_dados(campos(pet="Mel", hora="10:30"), AGORA))
        con = self.db.con
        self.assertEqual(con.execute("SELECT COUNT(*) FROM tutores").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM pets").fetchone()[0], 2)

    def test_constraint_unique_no_banco(self):
        import sqlite3
        self.db.salvar(app.preparar_dados(campos(), AGORA))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.con.execute("INSERT INTO agendamentos (pet_id, servico, data_hora) "
                                "VALUES (1, 'Consulta', '2026-11-17 09:00')")

    def test_horarios_livres(self):
        self.db.salvar(app.preparar_dados(campos(data="16/11/2026", hora="11:00"), AGORA))
        livres = self.db.horarios_livres(datetime(2026, 11, 16).date(), agora=AGORA)
        self.assertNotIn("10:00", livres)   # passado
        self.assertNotIn("11:00", livres)   # ocupado
        self.assertIn("10:30", livres)
        self.assertEqual(self.db.horarios_livres(datetime(2026, 11, 22).date(), agora=AGORA), [])


class TestViaCep(unittest.TestCase):
    def test_api_fora_do_ar(self):
        with mock.patch("urllib.request.urlopen", side_effect=app.urllib.error.URLError("offline")):
            with self.assertRaises(app.ApiIndisponivelError):
                app.consultar_cep("01001000")

    def test_log_modo_append(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "log.txt")
            app.registrar_log("TESTE", "um", p)
            app.registrar_log("TESTE", "dois", p)
            with open(p, encoding="utf-8") as f:
                self.assertEqual(len(f.readlines()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
