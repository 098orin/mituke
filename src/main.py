import os

import discord
from discord.ext import commands
from discord.ext.voice_recv import VoiceRecvClient
from dotenv import load_dotenv
from rich.console import Console

from audio import VoskSink

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN is None:
    raise RuntimeError("DISCORD_TOKEN is not set")


console = Console()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.command()
async def join(ctx: commands.Context):
    # Guild 内で使用されていることを保証する
    if not isinstance(ctx.author, discord.Member):
        await ctx.send("サーバー内でのみ使用できます")
        return

    voice_state = ctx.author.voice
    if voice_state is None or voice_state.channel is None:
        await ctx.send("ボイスチャンネルに参加してからコマンドを使用してください")
        return

    channel = voice_state.channel

    vc = await channel.connect(cls=VoiceRecvClient)
    vc.listen(VoskSink(ctx.channel))

    await ctx.send(f"Joined {channel.name}!")


@bot.event
async def on_voice_state_update(member, before, after):
    # 誰かが抜けた
    if before.channel is not None:
        channel = before.channel

        # ボットがそのチャンネルにいるか確認
        voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)
        if voice_client and voice_client.channel == channel:
            # Bot以外のメンバーが0人なら
            non_bot_members = [m for m in channel.members if not m.bot]

            if len(non_bot_members) == 0:
                await voice_client.disconnect(force=True)
                console.log(f"Disconnected from {channel.name} because it is empty.")


@bot.event
async def on_ready():
    console.log(f"Logged in as {bot.user}")


bot.run(TOKEN)
