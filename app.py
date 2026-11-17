from retiradas import retiradas
from flask import g, abort
import os
from flask_wtf.csrf import CSRFProtect
from usuarios import usuarios
from psycopg.rows import dict_row
from flask import Flask, redirect, render_template, request, url_for

from banco import conectar

app = Flask(__name__)
# O banco.py já carrega as configurações do arquivo .env.
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]

# Restringe o acesso ao cookie e seu envio entre sites.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Confere se os formulários enviados vieram da nossa aplicação.
CSRFProtect(app)

# Disponibiliza as páginas de cadastro, entrada e saída.
app.register_blueprint(usuarios)
# Ativa os agendamentos e as caixas de acompanhamento.
app.register_blueprint(retiradas)


# Define a página inicial do site, acessada pelo endereço "/".
@app.get("/")
def inicio():
    # Obtém os filtros enviados pela URL.
    # Quando um filtro não é informado, utiliza um texto vazio.
    # strip() remove espaços extras no começo e no final.
    peca = request.args.get("peca", "").strip()
    tamanho = request.args.get("tamanho", "").strip()
    bairro = request.args.get("bairro", "").strip()

    # A consulta começa mostrando somente as doações disponíveis.
    sql = """
        SELECT
            id, peca, tamanho, estado_conservacao,
            cidade, bairro, descricao, id_usuario
        FROM doacao
        WHERE status = 'Disponível'
    """

    # Guarda os valores dos filtros separados do texto SQL.
    # O Psycopg envia esses valores ao banco de forma parametrizada.
    parametros = []

    # ILIKE permite buscar sem diferenciar maiúsculas de minúsculas.
    # Os sinais % permitem encontrar o texto em qualquer parte do nome.
    if peca:
        sql += " AND peca ILIKE %s"
        parametros.append(f"%{peca}%")

    # No tamanho, a comparação é exata, ignorando maiúsculas/minúsculas.
    # Assim, pesquisar "M" não retorna automaticamente "GG" ou "Infantil M".
    if tamanho:
        sql += " AND LOWER(tamanho) = LOWER(%s)"
        parametros.append(tamanho)

    if bairro:
        sql += " AND bairro ILIKE %s"
        parametros.append(f"%{bairro}%")

    # Exibe as doações mais recentes primeiro.
    sql += " ORDER BY criado_em DESC, id DESC"

    # Abre a conexão com o PostgreSQL.
    # O bloco with encerra a conexão ao terminar.
    with conectar() as conexao:
        # dict_row permite acessar os campos pelo nome,
        # como doacao.peca no arquivo HTML.
        with conexao.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, parametros)

            # Recupera todos os registros encontrados pela consulta.
            doacoes = cursor.fetchall()

    # Envia as doações e os filtros para a página HTML.
    # Os filtros retornam para manter os campos preenchidos após a busca.
    return render_template(
        "inicio.html",
        doacoes=doacoes,
        peca=peca,
        tamanho=tamanho,
        bairro=bairro,
    )

@app.route("/doar", methods=["GET", "POST"])
def doar():
    # Quem ainda não entrou precisa se identificar antes de doar.
    if g.usuario is None:
        return redirect(url_for("usuarios.entrar"))

    # O perfil coletor pode consultar as roupas, mas não cadastrar doações.
    if g.usuario["perfil"] not in ("doador", "ambos"):
        abort(403, description="Seu perfil está cadastrado apenas para coleta.")

    # Contas antigas precisam receber o telefone antes da primeira doação.
    if not g.usuario["telefone"]:
        return render_template(
            "doar.html",
            dados={},
            bloqueado=True,
            erro="Seu cadastro ainda está sem telefone. Atualize-o para doar.",
        ), 400

    # Confere a região também no servidor.
    if (
        g.usuario["uf"] != "PR"
        or g.usuario["cidade"] not in ("Curitiba", "São José dos Pinhais")
    ):
        abort(400, description="A cidade do cadastro não pertence à região atendida.")

    if request.method == "GET":
        return render_template("doar.html", dados={})

    # Recebe apenas as informações que a pessoa pode preencher nesta tela.
    limites = {
        "peca": 100,
        "tamanho": 20,
        "estado_conservacao": 50,
        "bairro": 100,
    }

    dados = {
        campo: request.form.get(campo, "").strip()
        for campo in limites
    }
    dados["descricao"] = request.form.get("descricao", "").strip()

    erro = None

    if any(
        not valor or len(valor) > limites[campo]
        for campo, valor in dados.items()
        if campo in limites
    ):
        erro = "Preencha os campos obrigatórios dentro dos limites."
    elif dados["estado_conservacao"] not in ("Nova", "Usada em bom estado"):
        erro = "Selecione um estado de conservação válido."
    elif len(dados["descricao"]) > 2000:
        erro = "A descrição deve ter até 2.000 caracteres."

    if erro:
        return render_template("doar.html", dados=dados, erro=erro), 400

    # A identificação vem da conta, sem depender de campos enviados pelo navegador.
    nome = f'{g.usuario["primeiro_nome"]} {g.usuario["sobrenome"]}'
    telefone = g.usuario["telefone"]
    cidade = g.usuario["cidade"]

    with conectar() as conexao:
        with conexao.cursor() as cursor:
            # Mantém o registro de doador usado pela estrutura atual do projeto.
            cursor.execute(
                """
                INSERT INTO doador (nome, telefone)
                VALUES (%s, %s)
                RETURNING id
                """,
                (nome, telefone),
            )
            id_doador = cursor.fetchone()[0]

            # Liga a peça à conta de quem está logado.
            cursor.execute(
                """
                INSERT INTO doacao (
                    id_doador, id_usuario, peca, tamanho,
                    estado_conservacao, cidade, bairro, descricao
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    id_doador,
                    g.usuario["id"],
                    dados["peca"],
                    dados["tamanho"],
                    dados["estado_conservacao"],
                    cidade,
                    dados["bairro"],
                    dados["descricao"],
                ),
            )

    return redirect(url_for("cadastro_concluido"))

@app.get("/cadastro-concluido")
def cadastro_concluido():
    # Mostra a confirmação com os caminhos para continuar navegando.
    return render_template("doacao_salva.html")