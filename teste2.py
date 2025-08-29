# teste2.py (antes estava como "bot.py" no cabeçalho do seu texto)
import os
import discord
from discord.ext import commands
import asyncio
from db import conectar
from dotenv import load_dotenv

# --- ADIÇÃO: servidor HTTP p/ healthcheck do Render ---
try:
    from aiohttp import web
except ImportError:
    web = None  # em produção, inclua aiohttp no requirements.txt

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
ID_CANAL_AUTORIZADO = 1398460521681915965

# Memória do evento atual
# emoji -> {nome, usuarios[], limite, fila[], sem_permissao[]}
listas_reacoes = {}
mensagem_evento_id = None
mensagem_evento_obj = None
dia_evento = ""
guerra_id = None

# =========== manter o banco ativo ==========
from discord.ext import tasks

@tasks.loop(minutes=7)
async def keep_alive_db():
    try:
        conn = conectar()
        cursor = conn.cursor()
        cursor.execute("SELECT 1;")  # ping simples
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Keep-alive executado com sucesso!")
    except Exception as e:
        print(f"⚠️ Erro no keep-alive: {e}")

@keep_alive_db.before_loop
async def before_keep_alive():
    await bot.wait_until_ready()
    print("⏳ Aguardando bot iniciar para começar o keep-alive...")

# ========== Utils ==========
def registrar_servidor(guild: discord.Guild):
    conn = conectar()
    cursor = conn.cursor()
    # Garante que cria se não existir e "reativa" se já existir
    cursor.execute(
        """
        INSERT INTO servidores (id, nome, ativo)
        VALUES (%s, %s, 1)
        ON CONFLICT (id)
        DO UPDATE SET nome = EXCLUDED.nome, ativo = TRUE
        """,
        (guild.id, guild.name),
    )
    conn.commit()
    cursor.close()
    conn.close()


def ensure_schema():
    """Cria todas as tabelas/índices necessários (idempotente)."""
    DDLs = [
        # 1) Servidores
        """
        CREATE TABLE IF NOT EXISTS servidores (
          id        BIGINT PRIMARY KEY,
          nome      TEXT NOT NULL,
          ativo     BOOLEAN NOT NULL DEFAULT TRUE
        );
        """,

        # 2) Funções
        """
        CREATE TABLE IF NOT EXISTS funcoes (
          id           BIGSERIAL PRIMARY KEY,
          servidor_id  BIGINT NOT NULL,
          nome         TEXT NOT NULL,
          emoji        TEXT NOT NULL,
          FOREIGN KEY (servidor_id) REFERENCES servidores(id) ON DELETE CASCADE
        );
        """,
        # (opcional) evitar duplicatas de nome+emoji por servidor
        "CREATE UNIQUE INDEX IF NOT EXISTS funcoes_servidor_nome_emoji_uidx ON funcoes(servidor_id, LOWER(nome), emoji);",

        # 3) Funções ↔ Cargos (UPSERT por (servidor_id, nome_funcao))
        """
        CREATE TABLE IF NOT EXISTS funcoes_cargos (
          servidor_id BIGINT NOT NULL,
          nome_funcao TEXT   NOT NULL,
          nome_cargo  TEXT   NOT NULL,
          PRIMARY KEY (servidor_id, nome_funcao),
          FOREIGN KEY (servidor_id) REFERENCES servidores(id) ON DELETE CASCADE
        );
        """,

        # 4) Guerras
        """
        CREATE TABLE IF NOT EXISTS guerras (
          id          BIGSERIAL PRIMARY KEY,
          servidor_id BIGINT NOT NULL,
          data        TEXT   NOT NULL,
          mensagem_id BIGINT NOT NULL,
          canal_id    BIGINT NOT NULL,
          criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          FOREIGN KEY (servidor_id) REFERENCES servidores(id) ON DELETE CASCADE
        );
        """,

        # 5) Participantes
        """
        CREATE TABLE IF NOT EXISTS participantes (
          id        BIGSERIAL PRIMARY KEY,
          guerra_id BIGINT NOT NULL,
          user_id   BIGINT NOT NULL,
          username  TEXT   NOT NULL,
          emoji     TEXT   NOT NULL,
          status    TEXT   NOT NULL,
          FOREIGN KEY (guerra_id) REFERENCES guerras(id) ON DELETE CASCADE
        );
        """,
        # ajuda a não repetir o mesmo user duas vezes na mesma guerra
        "CREATE UNIQUE INDEX IF NOT EXISTS participantes_guerra_user_uidx ON participantes(guerra_id, user_id);",

        # 6) Presets
        """
        CREATE TABLE IF NOT EXISTS presets (
          id          BIGSERIAL PRIMARY KEY,
          servidor_id BIGINT NOT NULL,
          nome        TEXT   NOT NULL,
          criado_por  BIGINT NOT NULL,
          criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          ativo       BOOLEAN NOT NULL DEFAULT TRUE,
          UNIQUE (servidor_id, nome),
          FOREIGN KEY (servidor_id) REFERENCES servidores(id) ON DELETE CASCADE
        );
        """,

        # 7) Preset → Funções
        """
        CREATE TABLE IF NOT EXISTS preset_funcoes (
          id          BIGSERIAL PRIMARY KEY,
          preset_id   BIGINT NOT NULL,
          funcao_nome TEXT   NOT NULL,
          limite      INTEGER NOT NULL,
          FOREIGN KEY (preset_id) REFERENCES presets(id) ON DELETE CASCADE
        );
        """,
        # opcional: não repetir mesma função no mesmo preset
        "CREATE UNIQUE INDEX IF NOT EXISTS preset_funcoes_preset_funcao_uidx ON preset_funcoes(preset_id, LOWER(funcao_nome));",
    ]

    conn = conectar()
    cur = conn.cursor()
    try:
        for ddl in DDLs:
            cur.execute(ddl)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def registrar_servidor(guild: discord.Guild):
    """Cria/ativa o servidor (idempotente)."""
    conn = conectar()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO servidores (id, nome, ativo)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (id)
            DO UPDATE SET nome = EXCLUDED.nome, ativo = TRUE
            """,
            (guild.id, guild.name),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()



def servidor_ativo(guild_id: int) -> bool:
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT ativo FROM servidores WHERE id = %s", (guild_id,))
    res = cursor.fetchone()
    cursor.close()
    conn.close()
    # Se 'ativo' for BOOL no Postgres, aceita True; se for inteiro (1/0), aceita 1.
    return bool(res and (res[0] is True or res[0] == 1))

def parse_funcoes_limites(texto: str):
    resultado = {}
    itens = [p.strip() for p in texto.split(",") if p.strip()]
    for item in itens:
        nome, limite = item.rsplit(" ", 1)
        resultado[nome.strip().lower()] = int(limite.strip())
    return resultado

# ========== Banco (existentes) ==========
def registrar_servidor(guild: discord.Guild):
    conn = conectar()
    cursor = conn.cursor()
    # Postgres: ON CONFLICT (id) DO NOTHING requer UNIQUE/PK em servidores.id
    cursor.execute(
        """
        INSERT INTO servidores (id, nome, ativo)
        VALUES (%s, %s, 1)
        ON CONFLICT (id) DO NOTHING
        """,
        (guild.id, guild.name),
    )
    conn.commit()
    cursor.close()
    conn.close()

def inserir_funcao(servidor_id: int, nome: str, emoji: str):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO funcoes (servidor_id, nome, emoji) VALUES (%s, %s, %s)",
        (servidor_id, nome, emoji),
    )
    conn.commit()
    cursor.close()
    conn.close()

def buscar_funcoes_do_servidor(servidor_id: int):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT nome, emoji FROM funcoes WHERE servidor_id = %s", (servidor_id,))
    dados = cursor.fetchall()
    cursor.close()
    conn.close()
    return {
        emoji: {"nome": nome, "usuarios": [], "limite": 0, "fila": [], "sem_permissao": []}
        for nome, emoji in dados
    }

def criar_guerra(servidor_id: int, data: str, mensagem_id: int, canal_id: int) -> int:
    conn = conectar()
    cursor = conn.cursor()
    # Postgres: use RETURNING para obter id
    cursor.execute(
        """
        INSERT INTO guerras (servidor_id, data, mensagem_id, canal_id)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (servidor_id, data, mensagem_id, canal_id),
    )
    gid = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    return gid

def atualizar_participacao(guerra_id: int, user_id: int, username: str, emoji: str, status: str):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM participantes WHERE guerra_id = %s AND user_id = %s", (guerra_id, user_id))
    cursor.execute(
        "INSERT INTO participantes (guerra_id, user_id, username, emoji, status) VALUES (%s, %s, %s, %s, %s)",
        (guerra_id, user_id, username, emoji, status),
    )
    conn.commit()
    cursor.close()
    conn.close()

def salvar_cargo_funcao(servidor_id: int, nome_funcao: str, nome_cargo: str):
    conn = conectar()
    cursor = conn.cursor()
    # Postgres não tem REPLACE. Use UPSERT:
    cursor.execute(
        """
        INSERT INTO funcoes_cargos (servidor_id, nome_funcao, nome_cargo)
        VALUES (%s, %s, %s)
        ON CONFLICT (servidor_id, nome_funcao)
        DO UPDATE SET nome_cargo = EXCLUDED.nome_cargo
        """,
        (servidor_id, nome_funcao, nome_cargo),
    )
    conn.commit()
    cursor.close()
    conn.close()

def buscar_cargo_funcao(servidor_id: int):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT nome_funcao, nome_cargo FROM funcoes_cargos WHERE servidor_id = %s", (servidor_id,))
    dados = cursor.fetchall()
    cursor.close()
    conn.close()
    return {nome_funcao.lower(): nome_cargo for nome_funcao, nome_cargo in dados}

# ========== Banco (NOVO) – Presets ==========
def upsert_preset(servidor_id: int, nome: str, criado_por: int) -> int:
    """
    UPSERT real em Postgres, assumindo UNIQUE (servidor_id, nome) em presets.
    Ativa (ativo=1) se já existir; cria se não existir.
    """
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO presets (servidor_id, nome, criado_por, ativo)
        VALUES (%s, %s, %s, 1)
        ON CONFLICT (servidor_id, nome)
        DO UPDATE SET ativo = TRUE
        RETURNING id
        """,
        (servidor_id, nome, criado_por),
    )
    preset_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    return preset_id

def set_preset_funcoes(preset_id: int, mapa: dict):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM preset_funcoes WHERE preset_id = %s", (preset_id,))
    for funcao_nome, limite in mapa.items():
        cursor.execute(
            "INSERT INTO preset_funcoes (preset_id, funcao_nome, limite) VALUES (%s, %s, %s)",
            (preset_id, funcao_nome.lower(), int(limite)),
        )
    conn.commit()
    cursor.close()
    conn.close()

def get_preset_funcoes(servidor_id: int, nome: str):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM presets WHERE servidor_id = %s AND nome = %s AND ativo = 1", (servidor_id, nome))
    row = cursor.fetchone()
    if not row:
        cursor.close()
        conn.close()
        return None
    preset_id = row[0]
    cursor.execute("SELECT funcao_nome, limite FROM preset_funcoes WHERE preset_id = %s", (preset_id,))
    linhas = cursor.fetchall()
    cursor.close()
    conn.close()
    return {fn.lower(): lim for fn, lim in linhas}

def listar_presets_db(servidor_id: int):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, nome, criado_em, ativo FROM presets WHERE servidor_id = %s ORDER BY criado_em DESC",
        (servidor_id,),
    )
    linhas = cursor.fetchall()
    cursor.close()
    conn.close()
    return linhas

def deletar_preset_db(servidor_id: int, nome: str) -> bool:
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM presets WHERE servidor_id = %s AND nome = %s", (servidor_id, nome))
    apagou = cursor.rowcount > 0
    conn.commit()
    cursor.close()
    conn.close()
    return apagou

# ========== VIEW com botões ==========
class GuerraView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild
        for emoji, info in listas_reacoes.items():
            self.add_item(GuerraButton(emoji=emoji, label=info["nome"].capitalize(), custom_id=f"guerra:{guild.id}:{emoji}"))

class GuerraButton(discord.ui.Button):
    def __init__(self, emoji: str, label: str, custom_id: str):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, emoji=emoji, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        if interaction.channel_id != ID_CANAL_AUTORIZADO:
            return await interaction.response.send_message("⛔ Este comando só funciona no canal autorizado.", ephemeral=True)
        if not servidor_ativo(interaction.guild_id):
            return await interaction.response.send_message("❌ Este servidor está inativo.", ephemeral=True)

        guild = interaction.guild
        member = interaction.user
        emoji = str(self.emoji)

        for info in listas_reacoes.values():
            info["usuarios"] = [u for u in info["usuarios"] if u != member.display_name]
            info["fila"] = [u for u in info["fila"] if u != member.display_name]
            info["sem_permissao"] = [u for u in info["sem_permissao"] if u != member.display_name]

        cargos_map = buscar_cargo_funcao(guild.id)
        nome_func = listas_reacoes[emoji]["nome"].lower()
        cargo_necessario = cargos_map.get(nome_func)
        if cargo_necessario:
            nomes_cargos_usuario = [r.name for r in member.roles]
            if cargo_necessario not in nomes_cargos_usuario:
                listas_reacoes[emoji]["sem_permissao"].append(member.display_name)
                if not interaction.response.is_done():
                    await interaction.response.defer()
                await _refresh_embed(interaction.client, guild)
                return await interaction.followup.send(
                    f"🚫 Você não possui o cargo **{cargo_necessario}** exigido para **{listas_reacoes[emoji]['nome']}**.",
                    ephemeral=True,
                )

        if len(listas_reacoes[emoji]["usuarios"]) < listas_reacoes[emoji]["limite"]:
            listas_reacoes[emoji]["usuarios"].append(member.display_name)
            atualizar_participacao(guerra_id, member.id, member.display_name, emoji, "confirmado")
            msg = f"✅ Você entrou em **{listas_reacoes[emoji]['nome']}**."
        else:
            listas_reacoes[emoji]["fila"].append(member.display_name)
            atualizar_participacao(guerra_id, member.id, member.display_name, emoji, "espera")
            msg = f"⏳ **{listas_reacoes[emoji]['nome']}** está cheio. Você entrou na **fila**."

        if not interaction.response.is_done():
            await interaction.response.defer()
        await _refresh_embed(interaction.client, guild)
        await interaction.followup.send(msg, ephemeral=True)

async def _refresh_embed(client: commands.Bot, guild: discord.Guild):
    canal = client.get_channel(ID_CANAL_AUTORIZADO)
    msg = await canal.fetch_message(mensagem_evento_id)
    await msg.edit(embed=gerar_texto_evento_embed(), view=GuerraView(guild))

# ========== Comandos ==========
@bot.command(name="ativar")
@commands.has_permissions(administrator=True)
async def ativar_servidor_cmd(ctx):
    """Cria o schema e ativa/cadastra este servidor."""
    try:
        ensure_schema()            # cria tabelas/índices que faltarem
        registrar_servidor(ctx.guild)  # cadastra/ativa este guild
        await ctx.send("✅ Servidor cadastrado/ativado e schema verificado!")
    except Exception as e:
        await ctx.send("❌ Falha ao ativar. Veja os logs.")
        # log detalhado no console
        import traceback
        traceback.print_exception(type(e), e, e.__traceback__)

@bot.command()
async def cargo(ctx, nome_funcao: str, *, nome_cargo: str):
    if not servidor_ativo(ctx.guild.id):
        return await ctx.send("❌ Este servidor está inativo.")
    salvar_cargo_funcao(ctx.guild.id, nome_funcao.lower(), nome_cargo)
    await ctx.send(f"✅ Função **{nome_funcao}** agora exige o cargo **{nome_cargo}** para participar.")

@bot.command()
async def novaRole(ctx, nome: str, emoji: str):
    if ctx.channel.id != ID_CANAL_AUTORIZADO:
        return
    if not servidor_ativo(ctx.guild.id):
        return await ctx.send("❌ Este servidor está inativo. Contate o administrador.")
    registrar_servidor(ctx.guild)
    inserir_funcao(ctx.guild.id, nome, emoji)
    await ctx.send(f"✅ Função '{nome}' com emoji {emoji} registrada!")

@bot.command()
async def relatorio(ctx):
    if not servidor_ativo(ctx.guild.id):
        return await ctx.send("❌ Este servidor está inativo.")
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM guerras WHERE servidor_id = %s", (ctx.guild.id,))
    total_guerras = cursor.fetchone()[0]
    # Ajustes para Postgres:
    # - COUNT(*) FILTER (WHERE ...)
    # - string_agg(DISTINCT ..., ',')
    cursor.execute(
        """
        SELECT
            p.username,
            COUNT(*) AS total_participacoes,
            COUNT(*) FILTER (WHERE p.status = 'espera') AS em_espera,
            string_agg(DISTINCT CASE WHEN p.status = 'confirmado' THEN p.emoji END, ',') AS funcoes_confirmadas,
            string_agg(DISTINCT CASE WHEN p.status = 'espera' THEN p.emoji END, ',') AS funcoes_espera
        FROM participantes p
        JOIN guerras g ON p.guerra_id = g.id
        WHERE g.servidor_id = %s
        GROUP BY p.username
        """,
        (ctx.guild.id,),
    )
    rel = cursor.fetchall()
    cursor.close()
    conn.close()
    if not rel:
        return await ctx.send("Nenhuma participação registrada ainda.")
    embed = discord.Embed(title="📊 Relatório de Participação nas Guerras", color=discord.Color.purple())
    embed.add_field(name="Guerras Totais", value=str(total_guerras), inline=False)
    for username, total, espera, funcoes, espera_funcoes in rel:
        texto = f"Participou de **{total}** guerra(s)\n"
        texto += f"Ficou em espera em **{espera}** guerra(s)\n"
        if funcoes:
            texto += f"Funções: {funcoes}\n"
        if espera_funcoes:
            texto += f"Em espera como: {espera_funcoes}"
        embed.add_field(name=username, value=texto, inline=False)
    await ctx.send(embed=embed)

# ---------- PRESETS ----------
@bot.group(name="preset", invoke_without_command=True)
async def preset_group(ctx):
    await ctx.send("Use: `!preset criar <nome> \"ataque 10, defesa 5\"`, `!preset listar`, `!preset ver <nome>`, `!preset deletar <nome>`")

@preset_group.command(name="criar")
async def preset_criar(ctx, nome: str, *, lista: str):
    if not servidor_ativo(ctx.guild.id):
        return await ctx.send("❌ Este servidor está inativo.")
    try:
        mapa = parse_funcoes_limites(lista)
    except Exception:
        return await ctx.send("❌ Formato inválido. Exemplo: `!preset criar guerra1 \"ataque 10, defesa 5\"`")
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT LOWER(nome) FROM funcoes WHERE servidor_id = %s", (ctx.guild.id,))
    existentes = {row[0] for row in cursor.fetchall()}
    cursor.close()
    conn.close()
    nao_encontradas = [fn for fn in mapa.keys() if fn not in existentes]
    if nao_encontradas:
        return await ctx.send(f"⚠️ Funções não cadastradas neste servidor: {', '.join(nao_encontradas)}")
    preset_id = upsert_preset(ctx.guild.id, nome, ctx.author.id)
    set_preset_funcoes(preset_id, mapa)
    await ctx.send(f"✅ Preset **{nome}** salvo com {len(mapa)} função(ões).")

@preset_group.command(name="listar")
async def preset_listar(ctx):
    linhas = listar_presets_db(ctx.guild.id)
    if not linhas:
        return await ctx.send("Não há presets neste servidor.")
    embed = discord.Embed(title="📦 Presets do servidor", color=discord.Color.teal())
    for pid, nome, criado_em, ativo in linhas:
        status = "ativo" if ativo else "inativo"
        embed.add_field(name=nome, value=f"{status} • criado em {criado_em}", inline=False)
    await ctx.send(embed=embed)

@preset_group.command(name="ver")
async def preset_ver(ctx, nome: str):
    mapa = get_preset_funcoes(ctx.guild.id, nome)
    if mapa is None:
        return await ctx.send("❌ Preset não encontrado.")
    corpo = "\n".join(f"• {fn} ({lim})" for fn, lim in mapa.items())
    embed = discord.Embed(title=f"Preset: {nome}", description=corpo or "_vazio_", color=discord.Color.blue())
    await ctx.send(embed=embed)

@preset_group.command(name="deletar")
async def preset_deletar(ctx, nome: str):
    ok = deletar_preset_db(ctx.guild.id, nome)
    if ok:
        await ctx.send(f"🗑️ Preset **{nome}** apagado.")
    else:
        await ctx.send("❌ Preset não encontrado.")

# ---------- EVENTO ----------
@bot.command()
async def evento(ctx, preset: str = None, *, data_opcional: str = None):
    global mensagem_evento_id, mensagem_evento_obj, dia_evento, listas_reacoes, guerra_id

    if ctx.channel.id != ID_CANAL_AUTORIZADO:
        return
    if not servidor_ativo(ctx.guild.id):
        return await ctx.send("❌ Este servidor está inativo. Contate o administrador.")

    registrar_servidor(ctx.guild)
    requisitadas = None

    if preset:
        requisitadas = get_preset_funcoes(ctx.guild.id, preset)
        if requisitadas is None or not requisitadas:
            return await ctx.send("❌ Preset não encontrado ou vazio.")
        if data_opcional:
            dia_evento = data_opcional
        else:
            await ctx.send("📅 Qual o dia da guerra?")
            try:
                msg = await bot.wait_for("message", timeout=60.0, check=lambda m: m.author == ctx.author and m.channel == ctx.channel)
                dia_evento = msg.content
            except asyncio.TimeoutError:
                return await ctx.send("⏰ Tempo esgotado.")
    else:
        def check(m): return m.author == ctx.author and m.channel == ctx.channel
        await ctx.send("📅 Qual o dia da guerra?")
        try:
            msg = await bot.wait_for("message", timeout=60.0, check=check)
            dia_evento = msg.content
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Tempo esgotado.")
        await ctx.send("🛡️ Quais funções vão participar e seus limites? (ex: ataque 10, defesa 15)")
        try:
            msg = await bot.wait_for("message", timeout=90.0, check=check)
            requisitadas = parse_funcoes_limites(msg.content)
        except Exception:
            return await ctx.send("❌ Formato inválido. Use: `ataque 10, defesa 15`")

    todas_funcoes = buscar_funcoes_do_servidor(ctx.guild.id)
    listas_reacoes = {}
    for emoji, info in todas_funcoes.items():
        nm = info["nome"].lower()
        if nm in requisitadas:
            info["limite"] = int(requisitadas[nm])
            info["usuarios"] = []
            info["fila"] = []
            info["sem_permissao"] = []
            listas_reacoes[emoji] = info

    if not listas_reacoes:
        return await ctx.send("⚠️ Nenhuma função válida encontrada (verifique se as funções existem neste servidor).")

    embed = gerar_texto_evento_embed()
    view = GuerraView(ctx.guild)
    mensagem_evento_obj = await ctx.send(embed=embed, view=view)
    mensagem_evento_id = mensagem_evento_obj.id
    guerra_id = criar_guerra(ctx.guild.id, dia_evento, mensagem_evento_id, ctx.channel.id)

    async for m in ctx.channel.history(limit=100):
        if m.id != mensagem_evento_id:
            try:
                await m.delete()
            except discord.Forbidden:
                pass

@bot.command(name="ajuda")
async def ajuda(ctx):
    embed = discord.Embed(title="📘 Comandos disponíveis", color=discord.Color.green())
    embed.add_field(name="!novaRole <nome> <emoji>", value="Adiciona uma nova função com emoji.", inline=False)
    embed.add_field(name="!removeRole <nome>", value="Remove uma função existente.", inline=False)
    embed.add_field(name="!funções", value="Lista funções registradas.", inline=False)
    embed.add_field(name="!cargo <função> <cargo>", value="Define o cargo necessário para a função.", inline=False)
    embed.add_field(name="!cargos", value="Lista todas as funções e seus respectivos cargos necesarios!.", inline=False)
    embed.add_field(name="!preset criar <nome> \"ataque 10, defesa 5\"", value="Cria/atualiza um preset com as funções e limites.", inline=False)
    embed.add_field(name="!preset listar", value="Lista os presets do servidor.", inline=False)
    embed.add_field(name="!preset ver <nome>", value="Mostra as funções/limites do preset.", inline=False)
    embed.add_field(name="!preset deletar <nome>", value="Apaga um preset.", inline=False)
    embed.add_field(name="!evento <preset> [data]", value="Abre o evento usando um preset (ex.: `!evento guerra01 30/08`). Sem preset, segue o fluxo por perguntas.", inline=False)
    embed.add_field(name="!relatorio", value="Mostra o relatório de participação.", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="funções")
async def listar_funcoes(ctx):
    if not servidor_ativo(ctx.guild.id):
        return await ctx.send("❌ Este servidor está inativo.")
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT nome, emoji FROM funcoes WHERE servidor_id = %s", (ctx.guild.id,))
    funcoes = cursor.fetchall()
    cursor.close()
    conn.close()
    if not funcoes:
        return await ctx.send("Nenhuma função cadastrada neste servidor.")
    linhas = [f"- {nome} ({emoji})" for nome, emoji in funcoes]
    await ctx.send("Funções cadastradas:\n" + "\n".join(linhas))

@bot.command()
async def cargos(ctx):
    if not servidor_ativo(ctx.guild.id):
        await ctx.send("❌ Este servidor está inativo.")
        return
    funcoes = buscar_funcoes_do_servidor(ctx.guild.id)
    dados = buscar_cargo_funcao(ctx.guild.id)
    if not funcoes:
        await ctx.send("⚠️ Nenhuma função configurada neste servidor.")
        return
    msg = "⚔️ **Funções e cargos configurados:**\n"
    for emoji, info in funcoes.items():
        funcao_nome = info["nome"]
        cargo = dados.get(funcao_nome.lower())
        if cargo:
            msg += f"• **{funcao_nome.capitalize()}** → {cargo}\n"
        else:
            msg += f"• **{funcao_nome.capitalize()}** → _sem cargo cadastrado_\n"
    await ctx.send(msg)

@bot.command(name="removeRole")
async def remover_funcao(ctx, *, nome_funcao):
    if not servidor_ativo(ctx.guild.id):
        return await ctx.send("❌ Este servidor está inativo.")
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM funcoes WHERE servidor_id = %s AND nome = %s", (ctx.guild.id, nome_funcao))
    conn.commit()
    ok = cursor.rowcount > 0
    cursor.close()
    conn.close()
    if ok:
        await ctx.send(f"🗑️ Função '{nome_funcao}' removida com sucesso.")
    else:
        await ctx.send(f"Função '{nome_funcao}' não encontrada.")

# ========== Eventos globais ==========
_keep_alive_started = False

@bot.event
async def on_ready():
    global _keep_alive_started
    if not _keep_alive_started:
        keep_alive_db.start()   # inicia a task AQUI (com loop já rodando)
        _keep_alive_started = True
    print(f"✅ Bot online como {bot.user}")

# ========== Embed ==========
def gerar_texto_evento_embed() -> discord.Embed:
    embed = discord.Embed(
        title=f"📣 Guerra no dia {dia_evento}",
        description="Clique nos botões abaixo para entrar na sua função.",
        color=discord.Color.blue(),
    )
    sem_permissao_geral = []
    for emoji, info in listas_reacoes.items():
        total = len(info["usuarios"])
        limite = info["limite"]
        fila = info["fila"]
        sem_perm = info.get("sem_permissao", [])
        lista = "\n".join([f"• {nome}" for nome in info["usuarios"]]) if total else "• _Vazio_"
        if fila:
            lista += f"\n\n_Em espera: {', '.join(fila)}_"
        embed.add_field(name=f"{emoji} {info['nome'].capitalize()} ({total}/{limite})", value=lista, inline=False)
        sem_permissao_geral.extend(sem_perm)
    if sem_permissao_geral:
        embed.add_field(name="🚫 Sem permissão", value="\n".join(f"• {n}" for n in sem_permissao_geral), inline=False)
    return embed

# ========== Launcher: bot + HTTP (Render Web Service precisa abrir porta) ==========
def _build_app():
    if web is None:
        return None
    app = web.Application()
    async def health(_):
        return web.Response(text="ok")
    app.router.add_get("/healthz", health)
    # opcional: raiz responde 200 também
    async def root(_):
        return web.Response(text="Bot up")
    app.router.add_get("/", root)
    return app

async def _start_http_server():
    app = _build_app()
    if app is None:
        return
    port = int(os.getenv("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[keepalive] HTTP ok em 0.0.0.0:{port}")
    # mantém a task viva
    while True:
        await asyncio.sleep(3600)

async def _start_bot():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN não definido")
    await bot.start(token)

async def main():
    await asyncio.gather(
        _start_http_server(),
        _start_bot(),
    )

if __name__ == "__main__":
    asyncio.run(main())
