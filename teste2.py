# bot.py
import os
import discord
from discord.ext import commands
import asyncio
from db import conectar
from dotenv import load_dotenv

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
def servidor_ativo(guild_id: int) -> bool:
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT ativo FROM servidores WHERE id = %s", (guild_id,))
    res = cursor.fetchone()
    cursor.close()
    conn.close()
    return res and res[0] == 1

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
    cursor.execute(
        "INSERT IGNORE INTO servidores (id, nome, ativo) VALUES (%s, %s, 1)",
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
    cursor.execute(
        "INSERT INTO guerras (servidor_id, data, mensagem_id, canal_id) VALUES (%s, %s, %s, %s)",
        (servidor_id, data, mensagem_id, canal_id),
    )
    conn.commit()
    gid = cursor.lastrowid
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
    cursor.execute(
        "REPLACE INTO funcoes_cargos (servidor_id, nome_funcao, nome_cargo) VALUES (%s, %s, %s)",
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
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM presets WHERE servidor_id = %s AND nome = %s", (servidor_id, nome))
    row = cursor.fetchone()
    if row:
        preset_id = row[0]
        cursor.execute("UPDATE presets SET ativo=1 WHERE id=%s", (preset_id,))
    else:
        cursor.execute(
            "INSERT INTO presets (servidor_id, nome, criado_por, ativo) VALUES (%s, %s, %s, 1)",
            (servidor_id, nome, criado_por),
        )
        preset_id = cursor.lastrowid
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
    cursor.execute("SELECT id FROM presets WHERE servidor_id = %s AND nome = %s AND ativo=1", (servidor_id, nome))
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
    cursor.execute(
        """
        SELECT username,
               COUNT(*) AS total_participacoes,
               SUM(status = 'espera') AS em_espera,
               GROUP_CONCAT(DISTINCT CASE WHEN status = 'confirmado' THEN emoji END) AS funcoes_confirmadas,
               GROUP_CONCAT(DISTINCT CASE WHEN status = 'espera' THEN emoji END) AS funcoes_espera
        FROM participantes p
        JOIN guerras g ON p.guerra_id = g.id
        WHERE g.servidor_id = %s
        GROUP BY username
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

# Token (NÃO inicie a task aqui!)
bot.run(os.getenv("DISCORD_TOKEN"))
