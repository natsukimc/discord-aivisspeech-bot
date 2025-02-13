import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
from collections import deque
from aivis_speech_util import AivisSpeechClient, synthesize_text_to_file
from datetime import datetime, timedelta


class VoiceBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        self.aivis_client = AivisSpeechClient()
        self.speaker_id = 888753760

    async def setup_hook(self):
        await self.aivis_client.init_session()
        await self.add_cog(VoiceCog(self))
        await self.tree.sync() # コマンドツリーを同期

    async def close(self):
        await self.aivis_client.close()
        await super().close()


class VoiceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_clients = {}
        self.reading_queues = {}
        self.reading_channels = {}
        self.is_reading = {}
        self.synthesis_tasks = {}
        self.temp_dir = "temp_audio"
        os.makedirs(self.temp_dir, exist_ok=True)

    @app_commands.command(name="join", description="ボイスチャンネルに参加してテキスト読み上げを開始")
    async def join_voice(self, interaction: discord.Interaction):
        """ボイスチャンネルに参加してテキスト読み上げを開始"""
        if not interaction.user.voice:
            return await interaction.response.send_message("ボイスチャンネルに参加してください。", ephemeral=True)

        guild_id = interaction.guild.id
        channel_id = interaction.user.voice.channel.id

        try:
            voice_channel = interaction.user.voice.channel
            if guild_id not in self.voice_clients:
                self.voice_clients[guild_id] = {}
                self.reading_queues[guild_id] = {}
                self.reading_channels[guild_id] = {}
                self.is_reading[guild_id] = {}

            if channel_id not in self.voice_clients[guild_id]:
                print(f"Connecting to voice channel: {voice_channel.name}")
                self.voice_clients[guild_id][channel_id] = await voice_channel.connect()
                self.reading_queues[guild_id][channel_id] = deque()
                self.reading_channels[guild_id][channel_id] = interaction.channel.id
                self.is_reading[guild_id][channel_id] = False
                await interaction.response.send_message("読み上げを開始します。")
            else:
                await interaction.response.send_message("既にボイスチャンネルに接続しています。", ephemeral=True)
        except Exception as e:
            print(f"Error in join command: {e}")

    async def process_reading_queue(self, guild_id, channel_id):
        self.is_reading[guild_id][channel_id] = True

        while self.reading_queues[guild_id][channel_id]:
            if guild_id not in self.voice_clients or channel_id not in self.voice_clients[guild_id]:
                break

            temp_file, _ = self.reading_queues[guild_id][channel_id].popleft()
            voice_client = self.voice_clients[guild_id][channel_id]

            if os.path.exists(temp_file):
                try:
                    voice_client.play(discord.FFmpegPCMAudio(temp_file))
                    while voice_client.is_playing():
                        await asyncio.sleep(0.1)
                    os.remove(temp_file)
                except Exception as e:
                    print(f"Playback error: {e}")
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
            else:
                print(f"File not found: {temp_file}")

        self.is_reading[guild_id][channel_id] = False

    async def read_message(self, message):
        if not self._should_read_message(message):
            return

        guild_id = message.guild.id
        channel_id = message.author.voice.channel.id
        temp_file = os.path.join(self.temp_dir, f"temp_{message.id}.wav")

        try:
            text = self._format_message(message)

            # Create synthesis task
            success = await synthesize_text_to_file(
                self.bot.aivis_client,
                text,
                self.bot.speaker_id,
                temp_file
            )

            if success and os.path.exists(temp_file):
                print(f"Adding to queue: {temp_file}")
                self.reading_queues[guild_id][channel_id].append((temp_file, message.id))

                if not self.is_reading[guild_id][channel_id]:
                    print("Starting queue processing")
                    await self.process_reading_queue(guild_id, channel_id)
            else:
                print("Speech synthesis failed")

        except Exception as e:
            print(f"Error in read_message: {e}")
            if os.path.exists(temp_file):
                os.remove(temp_file)

    def _should_read_message(self, message):
        """読み上げるべきメッセージかどうかを判定"""
        if not message.author.voice:
            return False
        guild_id = message.guild.id
        channel_id = message.author.voice.channel.id
        return (
            guild_id in self.reading_channels and
            channel_id in self.reading_channels[guild_id] and
            message.channel.id == self.reading_channels[guild_id][channel_id] and
            not message.author.bot and
            not message.content.startswith('!')
        )

    def _format_message(self, message):
        """メッセージを読み上げ用にフォーマット"""
        CHAR_LIMIT = 70
        text = message.content
        # 画像が含まれている場合
        if message.attachments:
            text = "画像"
        # URLが含まれている場合
        elif any(word.startswith('http') for word in text.split()):
            text = "URL"
        # リプライの場合
        elif message.reference:
            text = message.author.display_name + "がリプ"
        # メンションが含まれている場合
        elif message.mentions:
            text = message.author.display_name + "がメンション"
        else:
            if len(text) > CHAR_LIMIT:
                text = text[:CHAR_LIMIT] + " 以下省略"
        return text

    def _cleanup_file(self, file_path):
        """一時ファイルの削除"""
        if os.path.exists(file_path):
            os.remove(file_path)

    @commands.Cog.listener()
    async def on_message(self, message):
        """メッセージ受信時の処理"""
        if message.guild and self._should_read_message(message):
            await self.read_message(message)

    async def leave_voice_channel(self, guild_id: int, channel_id: int):
        """ボイスチャンネルから退出する処理"""
        if guild_id in self.voice_clients and channel_id in self.voice_clients[guild_id]:
            await self.voice_clients[guild_id][channel_id].disconnect()
            del self.voice_clients[guild_id][channel_id]
            del self.reading_queues[guild_id][channel_id]
            del self.reading_channels[guild_id][channel_id]
            del self.is_reading[guild_id][channel_id]

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """ボイスチャンネルの状態が変更された時の処理"""
        if before and before.channel:
            # チャンネルから誰かが退出した場合
            guild_id = before.channel.guild.id
            channel_id = before.channel.id

            if guild_id in self.voice_clients and channel_id in self.voice_clients[guild_id]:
                # ボットが参加しているチャンネルの場合
                remaining_members = len([m for m in before.channel.members if not m.bot])

                if remaining_members == 0:
                    await self.leave_voice_channel(guild_id, channel_id)
                    # システムチャンネルまたは最初のテキストチャンネルにメッセージを送信
                    guild = before.channel.guild

    @app_commands.command(name="leave", description="ボイスチャンネルから退出")
    async def leave_voice(self, interaction: discord.Interaction):
        """ボイスチャンネルから退出"""
        if not interaction.user.voice:
            await interaction.response.send_message("あなたはボイスチャンネルに接続していません。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        channel_id = interaction.user.voice.channel.id
        await self.leave_voice_channel(guild_id, channel_id)
        await interaction.response.send_message("読み上げを終了します。")

    @app_commands.command(name="chars", description="残りの文字数を確認")
    async def remaining_chars(self, interaction: discord.Interaction):
        """残りの文字数を確認"""
        await interaction.response.send_message("この機能は無効になっています。")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """ボイスチャンネルの状態が変更された時の処理"""
        guild_id = member.guild.id

        if before.channel is None and after.channel is not None:
            # ユーザーがボイスチャンネルに参加した場合
            channel_id = after.channel.id
            if guild_id in self.voice_clients and channel_id in self.voice_clients[guild_id]:
                message = f"{member.display_name} が参加しました。"
                await self.read_message_from_text(guild_id, channel_id, message)

        elif before.channel is not None and after.channel is None:
            # ユーザーがボイスチャンネルから退出した場合
            channel_id = before.channel.id
            if guild_id in self.voice_clients and channel_id in self.voice_clients[guild_id]:
                message = f"{member.display_name} が退出しました。"
                await self.read_message_from_text(guild_id, channel_id, message)

    async def read_message_from_text(self, guild_id, channel_id, text):
        """テキストから読み上げメッセージを生成"""
        temp_file = os.path.join(self.temp_dir, f"temp_{datetime.now().timestamp()}.wav")

        try:
            success = await synthesize_text_to_file(
                self.bot.aivis_client,
                text,
                self.bot.speaker_id,
                temp_file
            )

            if success and os.path.exists(temp_file):
                print(f"Adding to queue: {temp_file}")
                self.reading_queues[guild_id][channel_id].append((temp_file, None))

                if not self.is_reading[guild_id][channel_id]:
                    print("Starting queue processing")
                    await self.process_reading_queue(guild_id, channel_id)
            else:
                print("Speech synthesis failed")

        except Exception as e:
            print(f"Error in read_message_from_text: {e}")
            if os.path.exists(temp_file):
                os.remove(temp_file)

def main():
    bot = VoiceBot()
    asyncio.run(bot.start('BOT_TOKEN'))

if __name__ == "__main__":
    print("Starting bot...")
    main()
