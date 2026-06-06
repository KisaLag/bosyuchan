import os
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from flask import Flask
from threading import Thread

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# 日本時間
JST = ZoneInfo("Asia/Tokyo")

# 空きVCを探す対象カテゴリID
# わからない場合は 0 のままでOK。サーバー内すべてのVCから探します。
VC_CATEGORY_ID = 0

# 使用中扱いにするVC
active_vc_ids = set()


intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


def parse_end_time(activity_time: str) -> datetime:
    """
    例:
    21:00-23:00
    21:00～23:00
    23:30
    """
    now = datetime.now(JST)

    text = activity_time.replace("～", "-").replace("〜", "-")

    if "-" in text:
        end_text = text.split("-")[-1].strip()
    else:
        end_text = text.strip()

    hour, minute = map(int, end_text.split(":"))

    end_time = now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0
    )

    # 終了時刻が現在より前なら翌日扱い
    if end_time <= now:
        end_time += timedelta(days=1)

    return end_time


def find_empty_voice_channel(guild: discord.Guild):
    """
    空いているVCを探す
    条件:
    - ボイスチャンネル
    - 人数が0人
    - active_vc_idsに入っていない
    - VC_CATEGORY_IDが設定されている場合はそのカテゴリ内だけ
    """

    channels = guild.voice_channels

    for vc in channels:
        if VC_CATEGORY_ID != 0:
            if vc.category_id != VC_CATEGORY_ID:
                continue

        if len(vc.members) == 0 and vc.id not in active_vc_ids:
            return vc

    return None


class RecruitModal(discord.ui.Modal, title="固定募集を作成"):

    game_name = discord.ui.TextInput(
        label="ゲーム名",
        placeholder="例：星の翼",
        max_length=50
    )

    member_count = discord.ui.TextInput(
        label="募集人数",
        placeholder="例：4",
        max_length=10
    )

    activity_time = discord.ui.TextInput(
        label="活動時間",
        placeholder="例：21:00-23:00",
        max_length=30
    )

    condition = discord.ui.TextInput(
        label="条件",
        placeholder="例：初心者歓迎 / VCあり / 楽しくできる人",
        max_length=100
    )

    memo = discord.ui.TextInput(
        label="メモ",
        placeholder="例：まったり、聞き専OKなど",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=300
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "サーバー内でのみ使えます。",
                ephemeral=True
            )
            return

        # 募集人数チェック
        try:
            count = int(str(self.member_count.value))
        except ValueError:
            await interaction.response.send_message(
                "募集人数は数字で入力してください。例：4",
                ephemeral=True
            )
            return

        # 終了時刻チェック
        try:
            end_time = parse_end_time(str(self.activity_time.value))
        except Exception:
            await interaction.response.send_message(
                "活動時間の形式が違います。例：21:00-23:00",
                ephemeral=True
            )
            return

        # 空きVCを探す
        vc = find_empty_voice_channel(guild)

        if vc is None:
            await interaction.response.send_message(
                "現在、空いているVCがありません。",
                ephemeral=True
            )
            return

        active_vc_ids.add(vc.id)

        embed = discord.Embed(
            title="🎮 固定募集",
            color=discord.Color.purple()
        )

        embed.add_field(
            name="ゲーム名",
            value=str(self.game_name.value),
            inline=False
        )

        embed.add_field(
            name="募集人数",
            value=f"{count}人",
            inline=False
        )

        embed.add_field(
            name="活動時間",
            value=str(self.activity_time.value),
            inline=False
        )

        embed.add_field(
            name="条件",
            value=str(self.condition.value),
            inline=False
        )

        embed.add_field(
            name="VCチャンネル",
            value=vc.mention,
            inline=False
        )

        embed.add_field(
            name="メモ",
            value=str(self.memo.value) if self.memo.value else "なし",
            inline=False
        )

        embed.add_field(
            name="募集者",
            value=interaction.user.mention,
            inline=False
        )

        embed.set_footer(
            text="活動時間を過ぎると、この募集は自動で削除されます"
        )

        view = RecruitPostView(vc.id)

        await interaction.response.send_message(
            content="募集を作成しました。",
            ephemeral=True
        )

        recruit_message = await interaction.channel.send(
            embed=embed,
            view=view
        )

        wait_seconds = (end_time - datetime.now(JST)).total_seconds()

        async def auto_delete():
            try:
                await asyncio.sleep(wait_seconds)
                active_vc_ids.discard(vc.id)
                await recruit_message.delete()
            except discord.NotFound:
                active_vc_ids.discard(vc.id)
            except Exception as e:
                active_vc_ids.discard(vc.id)
                print("自動削除エラー:", e)

        asyncio.create_task(auto_delete())


class RecruitPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="募集作成",
        style=discord.ButtonStyle.green,
        custom_id="recruit_create_button"
    )
    async def create_recruit(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.send_modal(RecruitModal())


class RecruitPostView(discord.ui.View):
    def __init__(self, vc_id: int):
        super().__init__(timeout=None)
        self.vc_id = vc_id

    @discord.ui.button(
        label="VCへ移動",
        style=discord.ButtonStyle.blurple
    )
    async def join_vc_info(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        vc = interaction.guild.get_channel(self.vc_id)

        if vc is None:
            await interaction.response.send_message(
                "VCが見つかりませんでした。",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"VCはこちら：{vc.mention}",
            ephemeral=True
        )

    @discord.ui.button(
        label="募集締切",
        style=discord.ButtonStyle.red
    )
    async def close_recruit(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        # 募集投稿者または管理者だけ締切可能
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "募集締切は管理権限がある人のみ使えます。",
                ephemeral=True
            )
            return

        active_vc_ids.discard(self.vc_id)

        await interaction.response.send_message(
            "募集を締め切りました。",
            ephemeral=True
        )

        await interaction.message.delete()


@bot.event
async def on_ready():
    bot.add_view(RecruitPanelView())
    await bot.tree.sync()
    print(f"ログイン完了: {bot.user}")


@bot.tree.command(
    name="panel",
    description="募集作成ボタンを設置します"
)
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎮 固定募集パネル",
        description="下のボタンから募集を作成できます。",
        color=discord.Color.purple()
    )

    embed.add_field(
        name="使い方",
        value=(
            "1. 募集作成を押す\n"
            "2. ゲーム名・人数・活動時間などを入力\n"
            "3. 空いているVCが自動で割り当てられます\n"
            "4. 活動時間終了後、自動で募集が削除されます"
        ),
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        view=RecruitPanelView()
    )


if TOKEN is None:
    raise RuntimeError(".env に DISCORD_TOKEN を設定してください")

app = Flask(__name__)

@app.route("/")
def home():
    return "募集ちゃん起動中"

def run_web():
    app.run(host="0.0.0.0", port=10000)

Thread(target=run_web).start()

bot.run(TOKEN)

bot.run(TOKEN)
