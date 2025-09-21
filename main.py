import discord
from discord.ext import commands
from discord.ext import tasks
import datetime
import json
import os
import calendar
from aiohttp import web
import asyncio

# --- CONFIG ---
EVENT_ID = 1348525255257231393   # 🔹 replace with your event ID
HOST_ROLE_ID = 1304575329892827236  # 🔹 replace with your role ID
DATA_FILE = "data.json"

# Intents required
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Store user values
user_values = {}
last_reset = {}

# Reaction-to-value mapping
reaction_values = {
    1419153448498106398: 1,
    1419153477015306251: -1,
    1419156407902802002: 10,
    1419156381000269874: -10,
}

# --- Load / Save Host Persistence ---
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

data_store = load_data()
corememories_host = data_store.get("host")  # Load last saved host

# --- Reaction tracking (new reactions) ---
@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:  # Ignore bot reactions
        return

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        return

    target_user_id = message.author.id  # Score belongs to the message's author
    emoji = payload.emoji

    if emoji.is_custom_emoji():
        emoji_id = emoji.id
        if emoji_id in reaction_values:
            user_values[target_user_id] = user_values.get(target_user_id, 0) + reaction_values[emoji_id]
    else:
        emoji_str = str(emoji)
        if emoji_str in reaction_values:
            user_values[target_user_id] = user_values.get(target_user_id, 0) + reaction_values[emoji_str]


# --- Slash Commands ---
@bot.tree.command(name="value", description="Check your current value")
async def value(interaction: discord.Interaction):
    user_id = interaction.user.id
    val = user_values.get(user_id, 0)
    await interaction.response.send_message(
        f"{interaction.user.mention}, your current value is **{val}**"
    )


@bot.tree.command(name="reset", description="Reset your value to 0 (once every 2 weeks)")
async def reset(interaction: discord.Interaction):
    user_id = interaction.user.id
    now = datetime.datetime.utcnow()

    if user_id in last_reset:
        diff = now - last_reset[user_id]
        if diff < datetime.timedelta(weeks=2):
            remaining = datetime.timedelta(weeks=2) - diff
            days = remaining.days
            hours = remaining.seconds // 3600
            await interaction.response.send_message(
                f"{interaction.user.mention}, you can reset again in {days}d {hours}h."
            )
            return

    user_values[user_id] = 0
    last_reset[user_id] = now

    # Send confirmation + image
    file = discord.File("assets/card.png", filename="card.png")
    await interaction.response.send_message(
        content=f"{interaction.user.mention}, your value has been reset to **0** ✅",
        file=file
    )

# --- Core Memories Commands ---
@bot.tree.command(name="corememories", description="Check the Core Memories event info")
async def corememories(interaction: discord.Interaction):
    global corememories_host

    await interaction.response.defer(ephemeral=False)  # acknowledge instantly

    # Get today's date
    now = datetime.datetime.utcnow()
    year = now.year
    month = now.month

    # Last day of current month
    last_day = calendar.monthrange(year, month)[1]
    event_date = datetime.date(year, month, last_day)

    # Month name
    month_name = now.strftime("%B")

    # Resolve host user
    host_str = "Not set yet"
    if corememories_host:
        host_user = interaction.guild.get_member(corememories_host)
        if host_user:
            host_str = host_user.mention
        else:
            host_str = f"<{corememories_host}>"

    # Format date string (always 5PM EST)
    date_str = event_date.strftime("%B %d, %Y at 05:00 PM EST")

    await interaction.followup.send(
        f"📅 **{month_name} Core Memories Wrapped**\n"
        f"🗓 When: {date_str}\n"
        f"🙋 Host: {host_str}"
    )

@bot.tree.command(name="sethost", description="Set the Core Memories host (requires special role)")
async def sethost(interaction: discord.Interaction, host: str):
    global corememories_host

    role = interaction.guild.get_role(HOST_ROLE_ID)
    if role not in interaction.user.roles:
        await interaction.response.send_message("❌ You do not have permission to set the host.", ephemeral=True)
        return

    # Save to memory + file
    corememories_host = host
    data_store["host"] = host
    save_data(data_store)

    await interaction.response.send_message(f"✅ Core Memories host has been set to **{host}**")

@tasks.loop(minutes=5)
async def corememories_reminder():
    now = datetime.datetime.utcnow()

    year = now.year
    month = now.month
    last_day = calendar.monthrange(year, month)[1]

    # Target time: last day of month at 22:30 UTC (5:30 PM EST)
    target_time = datetime.datetime(year, month, last_day, 22, 30)

    # If current time is within 5 minutes after the target
    if target_time <= now < target_time + datetime.timedelta(minutes=5):
        for guild in bot.guilds:
            role = guild.get_role(HOST_ROLE_ID)
            if role:
                # Send reminder in the first text channel the bot can send messages in
                channel = discord.utils.get(guild.text_channels, permissions_for=guild.me).permissions_for(guild.me).send_messages
                if channel:
                    await channel.send(
                        f"⏰ {role.mention}, it's time to set the next Core Memories host and create the new channel!"
                    )

# --- keep the bot alive
async def handle(request):
    return web.Response(text="Bot is alive!")

async def start_webserver():
    app = web.Application()
    app.add_routes([web.get("/", handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)  # Render assigns a PORT dynamically
    await site.start()
    print("🌐 Webserver started for UptimeRobot keep-alive")

# --- On Ready: Sync slash commands + backfill old reactions ---
@bot.event
async def on_ready():
    guild = discord.Object(id=1265727385031020626)  # put your server ID here
    await bot.tree.sync(guild=guild)  # sync to just that server
    print(f"✅ Synced slash commands to guild {1265727385031020626}")
    print(f"✅ Bot is ready! Logged in as {bot.user} (id={bot.user.id})")

    # Start webserver
    asyncio.create_task(start_webserver())

    # Backfill existing reactions
    for g in bot.guilds:
        for channel in g.text_channels:
            try:
                async for message in channel.history(limit=50):
                    for reaction in message.reactions:
                        key = reaction.emoji.id if isinstance(reaction.emoji, discord.Emoji) else str(reaction.emoji)
                        if key in reaction_values:
                            async for user in reaction.users():
                                if user.bot:
                                    continue
                                target_user_id = message.author.id
                                user_values[target_user_id] = user_values.get(target_user_id, 0) + reaction_values[key]
            except (discord.Forbidden, discord.HTTPException):
                continue

# --- Run Bot ---
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("⚠️ No Discord token found! Make sure DISCORD_TOKEN is set in Render environment variables. please!")

bot.run(TOKEN)
