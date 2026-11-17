"""Popula o petshop.db com agendamentos fictícios (Faker) usando as mesmas regras da aplicação.

Uso: python gerar_dados_teste.py [quantidade]
Cada operação também é registrada em log_agendamentos.txt, como na interface.
"""
import random
import sys
from datetime import datetime, timedelta

from faker import Faker

import petshop_app as app

CEPS_REAIS = ["01001000", "20020010", "30130010", "80010010", "60060120", "70040010"]
PETS = ["Thor", "Mel", "Luna", "Bob", "Nina", "Pipoca", "Max", "Belinha", "Simba", "Fred"]


def main(qtd=8):
    fake = Faker("pt_BR")
    banco = app.Banco()
    criados = 0
    tentativas = 0
    while criados < qtd and tentativas < qtd * 10:
        tentativas += 1
        cep = random.choice(CEPS_REAIS)
        try:
            end = app.consultar_cep(cep)
            app.registrar_log("CONSULTA CEP", f"CEP \"{end['cep']}\" consultado com sucesso ({end['cidade_uf']}).")
        except (app.ApiIndisponivelError, app.RegraNegocioError):
            end = {"cep": app.formatar_cep(cep), "logradouro": fake.street_name(),
                   "bairro": fake.bairro(), "cidade_uf": f"{fake.city()}/{fake.estado_sigla()}"}
        dia = datetime.now().date() + timedelta(days=random.randint(1, 20))
        campos = {"pet": random.choice(PETS), "tutor": fake.name(), "telefone": fake.msisdn()[-11:],
                  "cep": end["cep"], "logradouro": end["logradouro"] or fake.street_name(),
                  "numero": str(fake.building_number()), "bairro": end["bairro"] or "Centro",
                  "cidade_uf": end["cidade_uf"], "servico": random.choice(app.SERVICOS),
                  "data": dia.strftime("%d/%m/%Y"), "hora": random.choice(app.gerar_horarios())}
        try:
            dados = app.preparar_dados(campos)
            banco.salvar(dados)
        except app.RegraNegocioError as exc:
            print("ignorado:", exc)
            continue
        app.registrar_log("INSERÇÃO", f"Agendamento Pet \"{dados['pet']}\" (Tutor: {dados['tutor']}, "
                                      f"Serviço: {dados['servico']}) cadastrado com sucesso.")
        criados += 1
    banco.fechar()
    print(f"{criados} agendamentos fictícios criados em {app.DB_PATH}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
