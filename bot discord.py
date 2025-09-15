import os
import math
import asyncio
from dotenv import load_dotenv
from keep_alive import keep_alive
import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import init_db, get_xp, set_xp, get_top

# ---------- Config ----------
load_dotenv()
TOKEN = os.getenv("TOKEN")
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "0"))

STATUS_ROTATE_SECONDS = int(os.getenv("STATUS_ROTATE_SECONDS", "30"))
DEFAULT_STATUS = "Protéger le serveur 🛡️"

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

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

db_conn = None
cooldowns: dict[str, float] = {}

def total_member_count() -> int:
    return sum((g.member_count or 0) for g in bot.guilds)

def status_messages():
    members = total_member_count()
    return [
        "Protéger le serveur 🛡️",
        "/rank • /leaderboard",
        f"Niveau max {MAX_LEVEL} • {XP_PER_LEVEL} XP/niveau",
        f"{len(bot.guilds)} serveur(s) • {members} membre(s)",
    ]

# ---------- Présence rotative ----------
@tasks.loop(seconds=STATUS_ROTATE_SECONDS)
async def rotate_status():
    msgs = status_messages()
    if not msgs:
        return
    idx = getattr(rotate_status, "idx", 0)
    name = msgs[idx % len(msgs)]
    rotate_status.idx = idx + 1
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name=name),
        status=discord.Status.online
    )

@rotate_status.before_loop
async def before_rotate_status():
    await bot.wait_until_ready()

# ---------- Events ----------
@bot.event
async def on_ready():
    global db_conn
    if db_conn is None:
        db_conn = await init_db()
    print(f"✅ Connecté en tant que {bot.user} (latence {round(bot.latency*1000)} ms)")

    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name=DEFAULT_STATUS),
        status=discord.Status.online
    )
    if not rotate_status.is_running():
        rotate_status.start()

    # sync global directement
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Sync GLOBAL → {len(synced)} commandes (peut prendre un peu de temps à apparaître)")
    except app_commands.errors.CommandSignatureMismatch as e:
        print("⚠️ SignatureMismatch détecté, HARD RESYNC…", repr(e))
        bot.tree.clear_commands()
        await bot.tree.sync()     # purge global
        synced = await bot.tree.sync()
        print(f"✅ HARD RESYNC (global) → {len(synced)} commandes")
    except Exception as e:
        print("❌ ERREUR PENDANT tree.sync():", repr(e))

# ---------- Gain d'XP ----------
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

# ---------- Slash Commands ----------
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

@bot.tree.command(name="ping", description="Affiche la latence du bot")
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong ! Latence : **{latency_ms} ms**")

@bot.tree.command(name="givexp", description="Donne de l'XP à un membre (admin)")
@app_commands.describe(user="Membre à récompenser", amount="Montant d'XP (>=1)")
@app_commands.checks.has_permissions(manage_guild=True)
async def givexp(interaction: discord.Interaction, user: discord.User, amount: int = 1000):
    await interaction.response.defer(ephemeral=True)
    try:
        if amount < 1:
            return await interaction.followup.send("Le montant doit être >= 1.", ephemeral=True)
        global db_conn
        if db_conn is None:
            db_conn = await init_db()
        guild_id = str(interaction.guild_id)
        user_id = str(user.id)
        current = await get_xp(db_conn, guild_id, user_id)
        new_xp = min(MAX_XP, current + amount)
        await set_xp(db_conn, guild_id, user_id, new_xp)
        old_lvl = level_from_xp(current)
        new_lvl = level_from_xp(new_xp)
        await interaction.followup.send(
            f"✅ Donné **{amount} XP** à **{user.display_name}** (total: {new_xp} XP, niv. {new_lvl}).",
            ephemeral=True
        )
        if new_lvl > old_lvl and interaction.channel:
            await interaction.channel.send(f"🎉 {user.mention} passe **niveau {new_lvl}** !")
    except Exception as e:
        print("❌ /givexp error:", repr(e))
        try:
            await interaction.followup.send("Une erreur est survenue pendant /givexp.", ephemeral=True)
        except Exception:
            pass

@bot.tree.command(name="clearxp", description="Réinitialise l'XP d'un membre (admin)")
@app_commands.describe(user="Membre à réinitialiser")
@app_commands.checks.has_permissions(manage_guild=True)
async def clearxp(interaction: discord.Interaction, user: discord.User):
    await interaction.response.defer(ephemeral=True)
    try:
        global db_conn
        if db_conn is None:
            db_conn = await init_db()
        guild_id = str(interaction.guild_id)
        user_id = str(user.id)
        await set_xp(db_conn, guild_id, user_id, 0)
        await interaction.followup.send(f"♻️ XP de **{user.display_name}** réinitialisé à 0.", ephemeral=True)
    except Exception as e:
        print("❌ /clearxp error:", repr(e))
        try:
            await interaction.followup.send("Une erreur est survenue pendant /clearxp.", ephemeral=True)
        except Exception:
            pass

@bot.tree.command(name="resync", description="Force un resync des commandes (admin)")
@app_commands.checks.has_permissions(administrator=True)
async def resync_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        bot.tree.clear_commands()
        await bot.tree.sync()
        synced = await bot.tree.sync()
        await interaction.followup.send(f"✅ Hard resync global OK : **{len(synced)}** commandes.", ephemeral=True)
    except Exception as e:
        print("❌ /resync error:", repr(e))
        try:
            await interaction.followup.send("Erreur pendant /resync.", ephemeral=True)
        except Exception:
            pass

@bot.tree.command(name="debug", description="Diagnostics rapides du bot")
async def debug_cmd(interaction: discord.Interaction):
    app = bot.application
    try:
        cmds = [c.name for c in bot.tree.get_commands()]
    except Exception:
        cmds = []
    text = (
        f"**Bot:** {bot.user} (id: {bot.user.id})\n"
        f"**Application ID:** {app.id if app else '??'}\n"
        f"**Serveur courant (guild_id):** {interaction.guild_id}\n"
        f"**Nb commandes enregistrées:** {len(cmds)} → {', '.join(cmds) if cmds else '(aucune)'}\n"
        f"**Permission manage_guild:** "
        f"{'OK' if interaction.user.guild_permissions.manage_guild else 'PAS OK'}"
    )
    await interaction.response.send_message(text, ephemeral=True)

# ---------- Handler global ----------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    print("🌋 AppCmd ERROR:", repr(error))
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("Erreur lors de l’exécution de la commande.", ephemeral=True)
        else:
            await interaction.followup.send("Erreur lors de l’exécution de la commande.", ephemeral=True)
    except Exception:
        pass

# ---------- Lancement ----------
if not TOKEN:
    raise SystemExit("❌ TOKEN manquant dans .env")

keep_alive()
bot.run(TOKEN)

