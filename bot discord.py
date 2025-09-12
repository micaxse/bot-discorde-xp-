import os
import math
import asyncio
from dotenv import load_dotenv

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import init_db, get_xp, set_xp, get_top

# ---------- Config ----------
load_dotenv()
TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # optionnel pour sync rapide
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "0"))

# Salon pour le ping automatique (mettre l'ID dans le .env)
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

XP_PER_MESSAGE = 100
XP_PER_LEVEL = 1000
MAX_LEVEL = 100
MAX_XP = MAX_LEVEL * XP_PER_LEVEL  # 100_000

# ---------- Helpers ----------
def level_from_xp(xp: int) -> int:
    return min(MAX_LEVEL, xp // XP_PER_LEVEL)

def progress_to_next(xp: int):
    lvl = level_from_xp(xp)
    if lvl >= MAX_LEVEL:
        return lvl, XP_PER_LEVEL, XP_PER_LEVEL
    cur_in_level = xp - (lvl * XP_PER_LEVEL)
    return lvl, cur_in_level, XP_PER_LEVEL

# ---------- Bot ----------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

db_conn = None
cooldowns = {}

@bot.event
async def on_ready():
    global db_conn
    if db_conn is None:
        db_conn = await init_db()
    print(f"✅ Connecté en tant que {bot.user} (latency {round(bot.latency*1000)} ms)")

    if not auto_ping.is_running():
        auto_ping.start()

    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        await bot.tree.sync(guild=guild)
        print("🔁 Slash commands sync sur le GUILD_ID fourni.")
    else:
        await bot.tree.sync()
        print("🔁 Slash commands sync global (peut prendre un peu de temps lors de la première).")

# ---------- Gain d'XP sur message ----------
@bot.event
async def on_message(message: discord.Message):
    if message.guild is None or message.author.bot:
        return

    now = asyncio.get_event_loop().time()
    key = f"{message.guild.id}:{message.author.id}"
    if COOLDOWN_SECONDS > 0:
        last = cooldowns.get(key, 0.0)
        if now - last < COOLDOWN_SECONDS:
            return
        cooldowns[key] = now

    current_xp = await get_xp(db_conn, str(message.guild.id), str(message.author.id))
    old_level = level_from_xp(current_xp)

    new_xp = min(MAX_XP, current_xp + XP_PER_MESSAGE)
    await set_xp(db_conn, str(message.guild.id), str(message.author.id), new_xp)

    new_level = level_from_xp(new_xp)
    if new_level > old_level:
        await message.channel.send(f"🎉 {message.author.mention} passe **niveau {new_level}** !")

    await bot.process_commands(message)

# ---------- Slash: /rank ----------
@bot.tree.command(name="rank", description="Affiche ton niveau et ton XP")
@app_commands.describe(user="Voir le rang d'un autre membre (optionnel)")
async def rank(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    xp = await get_xp(db_conn, str(interaction.guild_id), str(target.id))
    lvl, cur, need = progress_to_next(xp)

    if lvl >= MAX_LEVEL:
        text = (
            f"🏅 **{target.display_name}** — **Niveau {lvl} (MAX)**\n"
            f"🧪 XP total: **{xp}/{MAX_XP}**"
        )
    else:
        remain = need - cur
        pct = math.floor((cur / need) * 100)
        text = (
            f"🏅 **{target.display_name}** — Niveau **{lvl}**\n"
            f"🧪 XP: **{cur}/{need}** pour le prochain niveau (**{pct}%**, reste {remain})\n"
            f"📈 XP total: **{xp}/{MAX_XP}**"
        )

    await interaction.response.send_message(text, ephemeral=(user is None))

# ---------- Slash: /leaderboard ----------
@bot.tree.command(name="leaderboard", description="Top XP du serveur")
@app_commands.describe(limit="Nombre d’entrées (1-20)")
async def leaderboard(interaction: discord.Interaction, limit: int = 10):
    limit = max(1, min(20, limit))
    rows = await get_top(db_conn, str(interaction.guild_id), limit)
    if not rows:
        return await interaction.response.send_message("Aucun classement pour l’instant.", ephemeral=True)

    lines = []
    for i, (user_id, xp) in enumerate(rows, start=1):
        member = interaction.guild.get_member(int(user_id))
        name = member.display_name if member else f"Utilisateur {user_id}"
        lvl = level_from_xp(xp)
        lines.append(f"**#{i}** — {name}: {xp} XP (niv. {lvl})")

    await interaction.response.send_message("📜 **Leaderboard XP**\n" + "\n".join(lines))

# ---------- Slash: /ping ----------
@bot.tree.command(name="ping", description="Affiche la latence du bot")
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong ! Latence : **{latency_ms} ms**")

# ---------- Slash: /givexp (admin) ----------
@bot.tree.command(name="givexp", description="Donne de l'XP à un membre (admin)")
@app_commands.describe(user="Membre à récompenser", amount="Montant d'XP (>=1)")
@app_commands.checks.has_permissions(manage_guild=True)
async def givexp(interaction: discord.Interaction, user: discord.User, amount: int = 1000):
    if amount < 1:
        return await interaction.response.send_message("Le montant doit être >= 1.", ephemeral=True)

    guild_id = str(interaction.guild_id)
    user_id = str(user.id)

    current = await get_xp(db_conn, guild_id, user_id)
    new_xp = min(MAX_XP, current + amount)
    await set_xp(db_conn, guild_id, user_id, new_xp)

    old_lvl = level_from_xp(current)
    new_lvl = level_from_xp(new_xp)

    msg = f"✅ Donné **{amount} XP** à **{user.display_name}** (total: {new_xp} XP, niv. {new_lvl})."
    await interaction.response.send_message(msg)

    if new_lvl > old_lvl:
        channel = interaction.channel
        if channel:
            await channel.send(f"🎉 {user.mention} passe **niveau {new_lvl}** !")

@givexp.error
async def givexp_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("Tu n’as pas la permission pour cette commande.", ephemeral=True)

# ---------- Slash: /clearxp (admin) ----------
@bot.tree.command(name="clearxp", description="Réinitialise l'XP d'un membre (admin)")
@app_commands.describe(user="Membre à réinitialiser")
@app_commands.checks.has_permissions(manage_guild=True)
async def clearxp(interaction: discord.Interaction, user: discord.User):
    guild_id = str(interaction.guild_id)
    user_id = str(user.id)

    await set_xp(db_conn, guild_id, user_id, 0)
    await interaction.response.send_message(f"♻️ XP de **{user.display_name}** réinitialisé à 0.")

@clearxp.error
async def clearxp_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("Tu n’as pas la permission pour cette commande.", ephemeral=True)

# ---------- Ping automatique toutes les 5 minutes ----------
@tasks.loop(minutes=5)
async def auto_ping():
    if CHANNEL_ID == 0:
        return
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        latency_ms = round(bot.latency * 1000)
        await channel.send(f"🏓 Ping automatique — latence **{latency_ms} ms**")

@auto_ping.before_loop
async def before_auto_ping():
    await bot.wait_until_ready()

# ---------- Lancement ----------
if not TOKEN:
    raise SystemExit("❌ TOKEN manquant dans .env")

bot.run(TOKEN)
