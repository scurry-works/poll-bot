# --- Environment setup ---
import os
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
APP_ID = 1386436781330923753
GUILD_ID = 905167903224123473

# --- Core library imports ---
from scurrypy import (
    Client, Intents,
    CommandOptionPart, CommandOptionTypes, CommandOptionChoicePart, 
    Interaction, InteractionEvent, 
    MessagePart, EmbedPart, EmojiModel
)

client = Client(TOKEN, Intents.set(guild_expressions=True))

from scurry_kit import setup_default_logger, CommandsAddon, ComponentsAddon, ActionRowBuilder as A, GuildEmojiCacheAddon

logger = setup_default_logger()

commands = CommandsAddon(client, APP_ID)
components = ComponentsAddon(client)
emojis_cache = GuildEmojiCacheAddon(client)

TOKEN_SEPARATOR = '::' # for custom IDs

from dataclasses import dataclass, field
from time import time
from typing import Union

@dataclass
class Poll:
    title: str
    created_by: int
    created_at: int
    expires_after: int = 86400
    emojis: list[EmojiModel] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    votes: list[int] = field(default_factory=list)
    voted: set[int] = field(default_factory=set)

    def is_expired(self) -> bool:
        return int(time()) > self.created_at + self.expires_after
    
    @property
    def discord_expire_ts(self) -> str:
        return f"<t:{self.created_at + self.expires_after}:R>"

import asyncio

class Poller:
    def __init__(self, client: Client):
        self.bot = client
        self.polls = {}
        self.poll_lock = asyncio.Lock()

        client.add_startup_hook(self.start_cleanup_task)

    async def start_cleanup_task(self):
        asyncio.create_task(self.cleanup_polls())

    async def cleanup_polls(self):
        while True:
            logger.info("ROUTINE: Cleaning up expired polls...")
            async with self.poll_lock:
                for k in list(self.polls.keys()):
                    if self.polls[k].is_expired():
                        self.polls.pop(k)
            await asyncio.sleep(60)

    async def get_poll(self, poll_id: str) -> Poll: # helper methods for single source of truth
        async with self.poll_lock:
            return self.polls.get(poll_id)
    
    async def add_poll(self, poll_id: str, poll: Poll) -> None:
        async with self.poll_lock:
            self.polls[poll_id] = poll

    async def add_poll_vote(self, poll_id: str, voter_id: int, vote: int) -> bool:
        async with self.poll_lock:
            p: Poll = self.polls.get(poll_id)
            if not p:
                return False

            p.votes[vote] += 1
            p.voted.add(voter_id)
            return True

    async def pop_poll(self, poll_id: str) -> Poll | None:
        async with self.poll_lock:
            return self.polls.pop(poll_id, None)

poller = Poller(client)

import re
CUSTOM_EMOJI_REGEX = re.compile(r'^<a?:\w+:\d+>$')
DEFAULT_EMOJIS = ['🔴', '🟠', '🟡', '🟢', '🔵']

def emoji_from_mention(emoji_mention: str) -> Union[EmojiModel, False]:
    first_colon = emoji_mention.find(':')
    second_colon = emoji_mention.find(':', first_colon +1)

    name = emoji_mention[first_colon+1:second_colon]

    try:
        id = int(emoji_mention[second_colon+1:-1])
    except ValueError:
        return False
    
    return EmojiModel(name, id, 'a:' in emoji_mention)

@commands.slash_command(
    'poll', 
    'Create a poll for users to react to!', 
    options=[
        CommandOptionPart(
            CommandOptionTypes.STRING,
            'title',
            'A descriptive title or question for your poll.',
            True
        ),
        CommandOptionPart(
            CommandOptionTypes.STRING,
            'options',
            'A comma-separated list of options for users to vote on. (Min 2, Max 5)',
            True
        ),
        CommandOptionPart(
            CommandOptionTypes.INTEGER,
            'expires-after',
            'Select how long this poll should last if not ended. Defaults to 7 HR.',
            choices=[
                CommandOptionChoicePart('1 HR', 3600),
                CommandOptionChoicePart('7 HR', 25200),
                CommandOptionChoicePart('1 D', 86400),
                CommandOptionChoicePart('7 D', 604800),
            ]
        ),
        CommandOptionPart(
            CommandOptionTypes.STRING,
            'emojis',
            'A comma-separated list of emojis for voting buttons. Cannot be custom emojis.',
        )
    ],
    guild_ids=[GUILD_ID])
async def on_poll_init(bot: Client, interaction: Interaction):
    event: InteractionEvent = interaction.context

    title = event.data.get_option('title')
    expires_after = int(event.data.get_option('expires-after', 25200))
    
    # validate options
    options = [ i.strip() for i in event.data.get_option('options').split(',')[:5] ]
    option_len = len(options)
    if option_len < 2:
        await interaction.respond("Not enough options! Make sure your options are comma-separated or add more options.", ephemeral=True)
        return
    
    # validate emojis (default to list given if no emojis are provided)
    emojis = event.data.get_option('emojis')

    # make there is an emoji for every option
    if emojis and len(emojis) < option_len:
        await interaction.respond("Oops, if you supply emojis, you need an emoji for every option!", ephemeral=True)
        return

    emoji_list = []

    if emojis:
        emojis = [i.strip() for i in emojis.split(',')[:option_len]]

        for e in emojis:
            if CUSTOM_EMOJI_REGEX.match(e):
                emoji = emoji_from_mention(e)
                if not emoji:
                    await interaction.respond("Oops, looks like a custom emoji was not formatted correctly!", ephemeral=True)
                    return
                else:
                    emoji_list.append(emoji)
            else:
                emoji_list.append(EmojiModel(e))
    else:
        emoji_list = [EmojiModel(name) for name in DEFAULT_EMOJIS[:option_len]]
    
    poll = Poll(
        title=title, 
        created_by=event.member.user.id, 
        created_at=int(time()),
        expires_after=expires_after, 
        emojis=emoji_list,
        options=options, 
        votes=[0] * option_len
    )

    embed = EmbedPart(
        title=title, 
        description='\n'.join(
            f"{e.mention}  {i}" 
            for e, i in zip(poll.emojis, poll.options)
        ) + f"\n\n ⏱️ Ends {poll.discord_expire_ts}"
    )

    # prepare poll ID
    import uuid
    poll_id = str(uuid.uuid4())

    ready_btn = A.row([A.success(f'ready{TOKEN_SEPARATOR}{poll_id}', 'Post')])

    await poller.add_poll(poll_id, poll)

    await interaction.respond(MessagePart(embeds=[embed], components=[ready_btn]), ephemeral=True)

@components.button(f'ready{TOKEN_SEPARATOR}*')
async def on_poll_ready(bot: Client, interaction: Interaction):
    event: InteractionEvent = interaction.context

    poll_id = event.data.custom_id.split(TOKEN_SEPARATOR)[1]
    poll = await poller.get_poll(poll_id)

    if not poll:
        await interaction.respond("Oops, looks like this poll as ended!", ephemeral=True)
        return
    
    embed = EmbedPart(
        title=poll.title, 
        description='\n'.join(
            f"{e.mention}  {i}" 
            for e, i in zip(poll.emojis, poll.options)
        ) + f"\n\n ⏱️ Ends {poll.discord_expire_ts}"
    )

    select_row = A.row([
        A.primary(f'vote{TOKEN_SEPARATOR}{poll_id}{TOKEN_SEPARATOR}{i}', emoji=poll.emojis[i]) for i in range(len(poll.options))
    ])

    end_btn = A.row([
        A.danger(f'end{TOKEN_SEPARATOR}{poll_id}{TOKEN_SEPARATOR}{poll.created_by}', 'End Poll')
    ])

    await bot.channel(event.channel_id).send(MessagePart(embeds=[embed], components=[select_row, end_btn]))
    await interaction.update(content="Poll has been posted!")

@components.button(f'vote{TOKEN_SEPARATOR}*')
async def on_poll_vote(bot: Client, interaction: Interaction):
    event: InteractionEvent = interaction.context

    poll_id = event.data.custom_id.split(TOKEN_SEPARATOR)[1]
    poll: Poll = await poller.get_poll(poll_id)

    if not poll:
        await interaction.respond("Oops, looks like this poll as ended!", ephemeral=True)
        return
    
    if event.member.user.id in poll.voted:
        await interaction.respond("Oops, looks like you already voted here!", ephemeral=True)
        return
    
    vote_idx = int(event.data.custom_id.split(TOKEN_SEPARATOR)[2])

    success = await poller.add_poll_vote(poll_id, event.member.user.id, vote_idx)

    if not success:
        await interaction.respond("Oops, looks like this poll has expired!", ephemeral=True)

    total = sum(poll.votes) or 1

    embed = EmbedPart(
        title=poll.title, 
        description='\n'.join(
            f"{e.mention}  {i}  -- **{v}** ({poll.votes[idx]/total:.0%})" 
            for idx, e, i, v in zip(range(len(poll.votes)), poll.emojis, poll.options, poll.votes)
        ) + f"\n\n ⏱️ Ends {poll.discord_expire_ts}"
    )

    select_row = A.row([
        A.primary(f'vote{TOKEN_SEPARATOR}{poll_id}{TOKEN_SEPARATOR}{i}', emoji=poll.emojis[i]) for i in range(len(poll.options))
    ])

    end_btn = A.row([
        A.danger(f'end{TOKEN_SEPARATOR}{poll_id}{TOKEN_SEPARATOR}{poll.created_by}', 'End Poll')
    ])

    await interaction.update(embeds=[embed], components=[select_row, end_btn])

@components.button(f'end{TOKEN_SEPARATOR}*')
async def on_poll_end(bot: Client, interaction: Interaction):
    event: InteractionEvent = interaction.context

    poll_id = event.data.custom_id.split(TOKEN_SEPARATOR)[1]

    poll = await poller.get_poll(poll_id)

    if not poll:
        await interaction.respond("Oops, looks like this poll as ended!", ephemeral=True)
        return
    
    if event.member.user.id != poll.created_by:
        await interaction.respond("Oops, looks like this poll was created by someone else!", ephemeral=True)
        return

    await poller.pop_poll(poll_id)

    total = sum(poll.votes) or 1

    embed = EmbedPart(
        title=poll.title, 
        description='\n'.join(
            f"{e.mention}  {i}  -- **{v}** ({poll.votes[idx]/total:.0%})" 
            for idx, e, i, v in zip(range(len(poll.votes)), poll.emojis, poll.options, poll.votes)
        )
    )

    await interaction.update(embeds=[embed])

client.run()
