import re
import unicodedata

from flask import (
    Blueprint, flash, g, redirect,
    render_template, request, session, url_for,
)
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash

from banco import conectar

# Deixa as funções de usuário separadas das funções de doação.
usuarios = Blueprint("usuarios", __name__)

CIDADES = ("Curitiba", "São José dos Pinhais")
PERFIS = ("doador", "coletor", "ambos")


def parte_login(texto):
    # Retira acentos e espaços para montar um login fácil de digitar.
    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(letra for letra in texto if not unicodedata.combining(letra))
    return re.sub(r"[^a-z0-9]", "", texto)


@usuarios.before_app_request
def carregar_usuario():
    # A sessão guarda o ID; os dados atuais são consultados no banco.
    g.usuario = None
    id_usuario = session.get("id_usuario")

    if id_usuario is None:
        return

    with conectar() as conexao:
        with conexao.cursor(row_factory=dict_row) as cursor:
                        # Disponibiliza os dados da conta para preencher as doações.
            cursor.execute(
                """
                SELECT
                    id, primeiro_nome, sobrenome, login, perfil,
                    telefone, cidade, uf
                FROM usuario
                WHERE id = %s
                """,
                (id_usuario,),
            )
            g.usuario = cursor.fetchone()

    # Se a conta não existir mais, encerra o acesso salvo.
    if g.usuario is None:
        session.clear()


@usuarios.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    erro = None
    dados = {}

    if request.method == "POST":
        # Limpa espaços dos dados pessoais, mas preserva a senha inteira.
                # Reúne os dados pessoais enviados pelo formulário.
        campos = (
            "primeiro_nome", "sobrenome", "idade", "email",
            "telefone", "perfil", "cidade", "uf", "local_preferido",
        )
        dados = {
            campo: request.form.get(campo, "").strip()
            for campo in campos
        }

        senha = request.form.get("senha", "")
        confirmacao = request.form.get("confirmacao", "")
        dados["email"] = dados["email"].lower()
                # Guarda apenas os números, mesmo se a pessoa usar parênteses e traços.
        dados["telefone"] = re.sub(r"[^0-9]", "", dados["telefone"])

        try:
            idade = int(dados["idade"])
        except ValueError:
            idade = 0

        primeiro = parte_login(dados["primeiro_nome"])
        ultimo = parte_login(dados["sobrenome"])

        # Confere os campos também no servidor.
        if not all(dados.values()):
            erro = "Preencha todos os campos."
        elif not primeiro or not ultimo:
            erro = "Informe um primeiro nome e um sobrenome válidos."
        elif (
            len(dados["primeiro_nome"]) > 50
            or len(dados["sobrenome"]) > 50
        ):
            erro = "O nome e o sobrenome devem ter até 50 caracteres cada."
        elif not 1 <= idade <= 120:
            erro = "Informe uma idade entre 1 e 120 anos."
        elif (
            len(dados["email"]) > 254
            or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", dados["email"])
        ):
            erro = "Informe um e-mail válido."
                # Aceita telefone fixo ou celular brasileiro, incluindo o DDD.
        elif len(dados["telefone"]) not in (10, 11):
            erro = "Informe um telefone com DDD, com 10 ou 11 números."
        elif dados["perfil"] not in PERFIS:
            erro = "Escolha como deseja participar."
        elif dados["uf"] != "PR" or dados["cidade"] not in CIDADES:
            erro = "Escolha Curitiba ou São José dos Pinhais, no Paraná."
        elif len(dados["local_preferido"]) > 200:
            erro = "Descreva o local preferido em até 200 caracteres."
        elif not 8 <= len(senha) <= 128:
            erro = "A senha deve ter entre 8 e 128 caracteres."
        elif senha != confirmacao:
            erro = "As senhas não são iguais."

        if erro is None:
            # Guarda um hash, sem salvar a senha original.
            senha_hash = generate_password_hash(senha)

            with conectar() as conexao:
                with conexao.cursor() as cursor:
                    # Organiza cadastros simultâneos para não repetir logins.
                    cursor.execute("SELECT pg_advisory_xact_lock(730018)")

                    cursor.execute(
                        "SELECT id FROM usuario WHERE LOWER(email) = %s",
                        (dados["email"],),
                    )

                    if cursor.fetchone():
                        erro = "Este e-mail já possui cadastro."
                    else:
                        base = f"{primeiro}.{ultimo}"
                        login = base
                        numero = 2

                        # Pessoas com o mesmo nome recebem um número no login.
                        while True:
                            cursor.execute(
                                "SELECT id FROM usuario WHERE LOWER(login) = %s",
                                (login,),
                            )
                            if cursor.fetchone() is None:
                                break

                            login = f"{base}{numero}"
                            numero += 1

                                                # Salva os dados pessoais junto com o acesso da conta.
                        cursor.execute(
                            """
                            INSERT INTO usuario (
                                primeiro_nome, sobrenome, idade, email,
                                login, senha_hash, perfil, cidade,
                                uf, local_preferido, telefone
                            )
                            VALUES (
                                %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                dados["primeiro_nome"],
                                dados["sobrenome"],
                                idade,
                                dados["email"],
                                login,
                                senha_hash,
                                dados["perfil"],
                                dados["cidade"],
                                dados["uf"],
                                dados["local_preferido"],
                                dados["telefone"],
                            ),
                        )

            if erro is None:
                flash(f"Cadastro concluído! Seu login é: {login}")
                return redirect(url_for("usuarios.entrar"))

    # Em caso de erro, mantém os dados preenchidos, exceto as senhas.
    return render_template(
        "acesso.html",
        cadastro=True,
        dados=dados,
        erro=erro,
        cidades=CIDADES,
    )


@usuarios.route("/entrar", methods=["GET", "POST"])
def entrar():
    erro = None

    if request.method == "POST":
        # O login ignora maiúsculas e espaços nas pontas.
        login = request.form.get("login", "").strip().lower()

        # A senha não passa por strip() nem lower().
        senha = request.form.get("senha", "")

        if len(login) > 120 or not 8 <= len(senha) <= 128:
            erro = "Login ou senha incorretos."
        else:
            with conectar() as conexao:
                with conexao.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(
                        """
                        SELECT id, senha_hash
                        FROM usuario
                        WHERE LOWER(login) = %s
                        """,
                        (login,),
                    )
                    usuario = cursor.fetchone()

            if usuario and check_password_hash(usuario["senha_hash"], senha):
                # Limpa a sessão anterior antes de registrar o novo acesso.
                session.clear()
                session["id_usuario"] = usuario["id"]
                return redirect(url_for("inicio"))

            erro = "Login ou senha incorretos."

    return render_template(
        "acesso.html",
        cadastro=False,
        dados={},
        erro=erro,
        cidades=CIDADES,
    )


@usuarios.post("/sair")
def sair():
    # Remove o acesso salvo neste navegador.
    session.clear()
    return redirect(url_for("inicio"))