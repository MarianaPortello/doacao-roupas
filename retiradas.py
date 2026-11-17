from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Blueprint, abort, flash, g,
    redirect, render_template, request, url_for,
)
from psycopg.rows import dict_row

from banco import conectar

retiradas = Blueprint("retiradas", __name__)


def agora_local():
    # Os horários do projeto seguem o horário de Brasília.
    return datetime.now(ZoneInfo("America/Sao_Paulo")).replace(tzinfo=None)


def precisa_login(funcao):
    # Reaproveita a verificação de login nas páginas de retirada.
    @wraps(funcao)
    def verificar(*args, **kwargs):
        if g.usuario is None:
            return redirect(url_for("usuarios.entrar"))
        return funcao(*args, **kwargs)

    return verificar


@retiradas.route("/agendar/<int:id_doacao>", methods=["GET", "POST"])
@precisa_login
def agendar(id_doacao):
    if g.usuario["perfil"] not in ("coletor", "ambos"):
        abort(403, description="Seu perfil não permite solicitar retiradas.")

    erro = None

    with conectar() as conexao:
        with conexao.cursor(row_factory=dict_row) as cursor:
            # Ao enviar o formulário, bloqueia a peça até concluir a reserva.
            bloqueio = " FOR UPDATE OF d" if request.method == "POST" else ""

            cursor.execute(
                """
                SELECT
                    d.id, d.peca, d.tamanho, d.cidade, d.bairro,
                    d.status, d.id_usuario,
                    u.local_preferido
                FROM doacao d
                JOIN usuario u ON u.id = d.id_usuario
                WHERE d.id = %s
                """ + bloqueio,
                (id_doacao,),
            )
            doacao = cursor.fetchone()

            if doacao is None:
                abort(404, description="Doação não encontrada ou sem conta vinculada.")

            if doacao["id_usuario"] == g.usuario["id"]:
                abort(403, description="Você não pode reservar sua própria doação.")

            if doacao["status"] != "Disponível":
                flash("Esta peça já não está disponível para agendamento.")
                return redirect(url_for("retiradas.minhas"))

            if request.method == "POST":
                local = request.form.get("local_retirada", "").strip()
                observacao = request.form.get("observacao", "").strip()

                try:
                    data = datetime.fromisoformat(
                        request.form.get("data_retirada", "")
                    )
                    # O formulário trabalha com horário local, sem fuso no campo.
                    if data.tzinfo is not None or data <= agora_local():
                        erro = "Escolha uma data e um horário futuros."
                except ValueError:
                    erro = "Informe uma data e um horário válidos."

                if not local or len(local) > 200:
                    erro = "Informe o local de retirada em até 200 caracteres."
                elif len(observacao) > 500:
                    erro = "A observação deve ter até 500 caracteres."

                if erro is None:
                    # A solicitação e a reserva são salvas juntas.
                    cursor.execute(
                        """
                        INSERT INTO agendamento (
                            id_doacao, id_beneficiario, data_retirada,
                            local_retirada, observacao
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (id_doacao, g.usuario["id"], data, local, observacao),
                    )

                    cursor.execute(
                        "UPDATE doacao SET status = 'Reservada' WHERE id = %s",
                        (id_doacao,),
                    )

    if request.method == "POST" and erro is None:
        flash("Peça reservada para você! Aguarde o aceite do doador.")
        return redirect(url_for("retiradas.minhas"))

    return render_template(
        "agendar.html",
        doacao=doacao,
        erro=erro,
        dados=request.form,
        minimo=agora_local().strftime("%Y-%m-%dT%H:%M"),
    )


def listar_agendamentos(como_doador):
    # A coluna é escolhida pelo código, nunca por um valor vindo do navegador.
    coluna = "d.id_usuario" if como_doador else "a.id_beneficiario"

    with conectar() as conexao:
        with conexao.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT
                    a.id, a.status, a.data_retirada,
                    a.local_retirada, a.observacao,
                    d.peca, d.tamanho, d.cidade, d.bairro,
                    dono.primeiro_nome || ' ' || dono.sobrenome AS doador,
                    beneficiario.primeiro_nome || ' ' ||
                        beneficiario.sobrenome AS beneficiario
                FROM agendamento a
                JOIN doacao d ON d.id = a.id_doacao
                JOIN usuario dono ON dono.id = d.id_usuario
                JOIN usuario beneficiario ON beneficiario.id = a.id_beneficiario
                WHERE {coluna} = %s
                ORDER BY
                    CASE WHEN a.status IN ('Pendente', 'Aceito') THEN 0 ELSE 1 END,
                    a.criado_em DESC
                """,
                (g.usuario["id"],),
            )
            return cursor.fetchall()


@retiradas.get("/caixa-de-entrada")
@precisa_login
def caixa():
    # Cada doador vê apenas as solicitações das próprias peças.
    return render_template(
        "agendamentos.html",
        registros=listar_agendamentos(True),
        como_doador=True,
    )


@retiradas.get("/minhas-retiradas")
@precisa_login
def minhas():
    # Cada beneficiário acompanha apenas as próprias solicitações.
    return render_template(
        "agendamentos.html",
        registros=listar_agendamentos(False),
        como_doador=False,
    )


@retiradas.post("/agendamentos/<int:id_agendamento>/responder")
@precisa_login
def responder(id_agendamento):
    acao = request.form.get("acao", "")
    destino = "retiradas.minhas" if acao == "cancelar" else "retiradas.caixa"
    mensagem = "Não foi possível realizar essa ação."

    with conectar() as conexao:
        with conexao.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id_doacao FROM agendamento WHERE id = %s",
                (id_agendamento,),
            )
            referencia = cursor.fetchone()

            if referencia is None:
                abort(404)

            # Bloqueia sempre a peça primeiro, como na criação da reserva.
            cursor.execute(
                "SELECT * FROM doacao WHERE id = %s FOR UPDATE",
                (referencia["id_doacao"],),
            )
            doacao = cursor.fetchone()

            cursor.execute(
                "SELECT * FROM agendamento WHERE id = %s FOR UPDATE",
                (id_agendamento,),
            )
            agendamento = cursor.fetchone()

            eh_doador = doacao["id_usuario"] == g.usuario["id"]
            eh_beneficiario = agendamento["id_beneficiario"] == g.usuario["id"]

            # Não basta esconder o botão: a permissão é conferida aqui também.
            if acao == "cancelar":
                if not eh_beneficiario:
                    abort(403)
            elif acao in ("aceitar", "recusar", "concluir"):
                if not eh_doador:
                    abort(403)
            else:
                abort(400)

            atual = agendamento["status"]
            novo = None
            situacao_peca = None

            if acao == "aceitar" and atual == "Pendente":
                if agendamento["data_retirada"] <= agora_local():
                    mensagem = "O horário passou. Recuse para liberar um novo agendamento."
                else:
                    novo, situacao_peca = "Aceito", "Reservada"
                    mensagem = "Agendamento aceito. O beneficiário já pode consultar a resposta."

            elif acao == "recusar" and atual in ("Pendente", "Aceito"):
                novo, situacao_peca = "Recusado", "Disponível"
                mensagem = "Agendamento recusado. A peça voltou a ficar disponível."

            elif acao == "cancelar" and atual in ("Pendente", "Aceito"):
                novo, situacao_peca = "Cancelado", "Disponível"
                mensagem = "Solicitação cancelada. A peça voltou a ficar disponível."

            elif acao == "concluir" and atual == "Aceito":
                novo, situacao_peca = "Concluído", "Entregue"
                mensagem = "Entrega registrada com sucesso."

            else:
                mensagem = "Este agendamento já mudou de situação. Confira a lista."

            if novo:
                # Atualiza o agendamento e a peça na mesma transação.
                cursor.execute(
                    "UPDATE agendamento SET status = %s WHERE id = %s",
                    (novo, id_agendamento),
                )
                cursor.execute(
                    "UPDATE doacao SET status = %s WHERE id = %s",
                    (situacao_peca, doacao["id"]),
                )

    flash(mensagem)
    return redirect(url_for(destino))