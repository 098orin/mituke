import os

import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN is None:
    raise RuntimeError("DISCORD_TOKEN is not set")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.command()
async def join(ctx: commands.Context, arg):
    # Guild 内で使用されていることを保証する
    if not isinstance(ctx.author, discord.Member):
        await ctx.send("サーバー内でのみ使用できます")
        return

    voice_state = ctx.author.voice
    if voice_state is None or voice_state.channel is None:
        await ctx.send("ボイスチャンネルに参加してからコマンドを使用してください")
        return

    channel = voice_state.channel

    await channel.connect()
    await ctx.send(f"Joined {channel.name}!")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


bot.run(TOKEN)
