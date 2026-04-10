import discord
from discord.ext import commands
import json
import os
from keep_alive import keep_alive
import asyncio
# ── Config ──────────────────────────────────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

CONFIG_FILE = "config.json"
TICKETS_FILE = "tickets.json"

# ── Helpers ──────────────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def get_config():
    return load_json(CONFIG_FILE, {
        "sendmsg_roles": [],
        "ticket_support_roles": [],
        "ticket_free_roles": [],
        "ticket_options": [],
        "ticket_panel_channel": None,
        "ticket_category": None,
        "role_button_role": None,
    })

def save_config(cfg):
    save_json(CONFIG_FILE, cfg)

def get_tickets():
    return load_json(TICKETS_FILE, {})

def save_tickets(t):
    save_json(TICKETS_FILE, t)

def has_any_role(member, role_ids):
    return any(r.id in role_ids for r in member.roles)

def embed(title, description, color=0x5865F2, footer=None):
    e = discord.Embed(title=title, description=description, color=color)
    if footer:
        e.set_footer(text=footer)
    return e

# ══════════════════════════════════════════════════════════════════════════════
#  VIEWS / BUTTONS
# ══════════════════════════════════════════════════════════════════════════════

class TicketPanelView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=None)
        for opt in options:
            self.add_item(TicketOptionButton(opt["label"], opt["emoji"]))

class TicketOptionButton(discord.ui.Button):
    def __init__(self, label, emoji=None):
        super().__init__(
            label=label,
            emoji=emoji if emoji else None,
            style=discord.ButtonStyle.primary,
            custom_id=f"ticket_opt_{label}"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketFormModal(self.label))


class TicketFormModal(discord.ui.Modal, title="🎫 Create a Ticket"):
    trade = discord.ui.TextInput(
        label="What is the trade?",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your trade here...",
        required=True
    )
    user_id = discord.ui.TextInput(
        label="@user or User ID of the other person",
        placeholder="@username or 123456789012345678",
        required=True
    )
    can_join_ps = discord.ui.TextInput(
        label="Can you join PS?",
        placeholder="Yes / No",
        required=True
    )

    def __init__(self, option_label):
        super().__init__()
        self.option_label = option_label

    async def on_submit(self, interaction: discord.Interaction):
        cfg = get_config()
        guild = interaction.guild
        member = interaction.user

        # Resolve the other user
        raw = self.user_id.value.strip().lstrip("@<>!").split(">")[0]
        other_member = None
        try:
            uid = int(raw.replace("<@", "").replace("!", "").replace(">", ""))
            other_member = guild.get_member(uid) or await guild.fetch_member(uid)
        except:
            pass

        # Create ticket channel
        category = None
        if cfg.get("ticket_category"):
            category = guild.get_channel(int(cfg["ticket_category"]))

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        if other_member:
            overwrites[other_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # Free roles (can see & msg without claiming)
        for rid in cfg.get("ticket_free_roles", []):
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, use_application_commands=True
                )

        # Support roles — view only until claimed
        for rid in cfg.get("ticket_support_roles", []):
            role = guild.get_role(int(rid))
            if role and role not in overwrites:
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=False, use_application_commands=False
                )

        channel = await guild.create_text_channel(
            name=f"ticket-{member.name}",
            category=category,
            overwrites=overwrites
        )

        # Save ticket data
        tickets = get_tickets()
        tickets[str(channel.id)] = {
            "creator_id": str(member.id),
            "other_user_id": str(other_member.id) if other_member else None,
            "option": self.option_label,
            "trade": self.trade.value,
            "user_field": self.user_id.value,
            "can_join_ps": self.can_join_ps.value,
            "claimed_by": None,
            "confirm_users": []
        }
        save_tickets(tickets)

        # Build ticket embed
        other_display = other_member.mention if other_member else f"⚠️ User not found (`{self.user_id.value}`)"
        ticket_embed = discord.Embed(
            title=f"🎫 Ticket — {self.option_label}",
            color=0x5865F2
        )
        ticket_embed.add_field(name="📦 Trade", value=self.trade.value, inline=False)
        ticket_embed.add_field(name="👤 Other User", value=other_display, inline=True)
        ticket_embed.add_field(name="🎮 Can Join PS?", value=self.can_join_ps.value, inline=True)
        ticket_embed.add_field(name="🙋 Opened by", value=member.mention, inline=False)
        ticket_embed.set_footer(text="Use !claim to claim this ticket | !close to close it")

        # Ping support roles
        pings = " ".join(
            guild.get_role(int(rid)).mention
            for rid in cfg.get("ticket_support_roles", [])
            if guild.get_role(int(rid))
        )

        await channel.send(
            content=pings if pings else None,
            embed=ticket_embed,
            view=TicketActionsView()
        )

        await interaction.response.send_message(
            embed=embed("✅ Ticket Created", f"Your ticket has been opened: {channel.mention}", color=0x57F287),
            ephemeral=True
        )


class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, emoji="✋", custom_id="claim_ticket")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_config()
        if not has_any_role(interaction.user, [int(r) for r in cfg.get("ticket_support_roles", [])]):
            await interaction.response.send_message(
                embed=embed("❌ No Permission", "Only support roles can claim tickets.", color=0xED4245),
                ephemeral=True
            )
            return

        tickets = get_tickets()
        tid = str(interaction.channel.id)
        if tid not in tickets:
            await interaction.response.send_message(embed=embed("❌ Error", "Ticket data not found.", color=0xED4245), ephemeral=True)
            return

        if tickets[tid]["claimed_by"]:
            claimer = interaction.guild.get_member(int(tickets[tid]["claimed_by"]))
            await interaction.response.send_message(
                embed=embed("⚠️ Already Claimed", f"This ticket is already claimed by {claimer.mention if claimer else 'someone'}.", color=0xFEE75C),
                ephemeral=True
            )
            return

        tickets[tid]["claimed_by"] = str(interaction.user.id)
        save_tickets(tickets)

        # Grant send perms to claimer
        await interaction.channel.set_permissions(
            interaction.user,
            read_messages=True, send_messages=True, use_application_commands=True
        )

        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            embed=embed("✅ Ticket Claimed", f"{interaction.user.mention} has claimed this ticket!", color=0x57F287)
        )

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await close_ticket_channel(interaction.channel, interaction.user)
        await interaction.response.defer()


class ConfirmTradeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success, custom_id="confirm_trade")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets = get_tickets()
        tid = str(interaction.channel.id)
        if tid not in tickets:
            await interaction.response.send_message(embed=embed("❌ Error", "Ticket not found.", color=0xED4245), ephemeral=True)
            return

        uid = str(interaction.user.id)
        if uid in tickets[tid]["confirm_users"]:
            await interaction.response.send_message(
                embed=embed("⚠️ Already Confirmed", "You already confirmed the trade.", color=0xFEE75C),
                ephemeral=True
            )
            return

        tickets[tid]["confirm_users"].append(uid)
        save_tickets(tickets)

        await interaction.response.send_message(
            f"✅ {interaction.user.mention} **confirmed the trade!**"
        )


class MMInfoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ I Understood", style=discord.ButtonStyle.success, custom_id="mm_understood")
    async def understood(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} **understood!**"
        )


class RoleButtonView(discord.ui.View):
    def __init__(self, button_label, role_id):
        super().__init__(timeout=None)
        self.add_item(RoleClaimButton(button_label, role_id))


class RoleClaimButton(discord.ui.Button):
    def __init__(self, label, role_id):
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=f"rolebtn_{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(int(self.role_id))
        if not role:
            await interaction.response.send_message(embed=embed("❌ Error", "Role not found.", color=0xED4245), ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message(embed=embed("⚠️", f"You already have {role.mention}.", color=0xFEE75C), ephemeral=True)
            return
        await interaction.user.add_roles(role)
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} has been given the role {role.mention}!"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

async def close_ticket_channel(channel, closer):
    tickets = get_tickets()
    tid = str(channel.id)
    if tid in tickets:
        del tickets[tid]
        save_tickets(tickets)
    close_embed = discord.Embed(
        title="🔒 Ticket Closing",
        description=f"Closed by {closer.mention}. Channel will be deleted in 5 seconds.",
        color=0xED4245
    )
    await channel.send(embed=close_embed)
    await asyncio.sleep(5)
    await channel.delete()


# ══════════════════════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    # Re-register persistent views
    bot.add_view(TicketActionsView())
    bot.add_view(ConfirmTradeView())
    bot.add_view(MMInfoView())
    # Reload ticket option buttons
    cfg = get_config()
    if cfg.get("ticket_options"):
        bot.add_view(TicketPanelView(cfg["ticket_options"]))


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

# ── !sendmsg <message> <channel_id> ─────────────────────────────────────────
@bot.command(name="sendmsg")
async def sendmsg(ctx, channel_id: str = None, *, message: str = None):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("sendmsg_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return
    if not channel_id or not message:
        await ctx.send(embed=embed("❌ Usage", "`!sendmsg <channel_id> <message>`", color=0xED4245))
        return
    try:
        ch = ctx.guild.get_channel(int(channel_id))
        if not ch:
            await ctx.send(embed=embed("❌ Error", "Channel not found.", color=0xED4245))
            return
        await ch.send(embed=embed("📢 Message", message))
        await ctx.send(embed=embed("✅ Sent", f"Message sent to {ch.mention}.", color=0x57F287))
    except Exception as e:
        await ctx.send(embed=embed("❌ Error", str(e), color=0xED4245))


# ── !setsendrole <role_id> ───────────────────────────────────────────────────
@bot.command(name="setsendrole")
@commands.has_permissions(administrator=True)
async def setsendrole(ctx, role_id: str):
    cfg = get_config()
    if role_id not in cfg["sendmsg_roles"]:
        cfg["sendmsg_roles"].append(role_id)
        save_config(cfg)
    await ctx.send(embed=embed("✅ Updated", f"Role `{role_id}` can now use `!sendmsg`.", color=0x57F287))


# ── !setsupportrole <role_id> ────────────────────────────────────────────────
@bot.command(name="setsupportrole")
@commands.has_permissions(administrator=True)
async def setsupportrole(ctx, role_id: str):
    cfg = get_config()
    if role_id not in cfg["ticket_support_roles"]:
        cfg["ticket_support_roles"].append(role_id)
        save_config(cfg)
    await ctx.send(embed=embed("✅ Updated", f"Role `{role_id}` is now a ticket support role.", color=0x57F287))


# ── !setfreerole <role_id> ───────────────────────────────────────────────────
@bot.command(name="setfreerole")
@commands.has_permissions(administrator=True)
async def setfreerole(ctx, role_id: str):
    """Role that can message in tickets WITHOUT claiming"""
    cfg = get_config()
    if role_id not in cfg["ticket_free_roles"]:
        cfg["ticket_free_roles"].append(role_id)
        save_config(cfg)
    await ctx.send(embed=embed("✅ Updated", f"Role `{role_id}` can now message in tickets without claiming.", color=0x57F287))


# ── !ticketpanel <image_url> ─────────────────────────────────────────────────
@bot.command(name="ticketpanel")
@commands.has_permissions(administrator=True)
async def ticketpanel(ctx, image_url: str = None):
    cfg = get_config()
    options = cfg.get("ticket_options", [])
    if not options:
        await ctx.send(embed=embed("❌ No Options", "Add ticket options first with `!addticketoption <label> [emoji]`.", color=0xED4245))
        return

    panel_embed = discord.Embed(
        title="🎫 Support Tickets",
        description="Click a button below to open a ticket.\nFill out the form and our team will assist you shortly.",
        color=0x5865F2
    )
    if image_url:
        panel_embed.set_image(url=image_url)
    panel_embed.set_footer(text="One ticket per issue please.")

    view = TicketPanelView(options)
    await ctx.send(embed=panel_embed, view=view)

    # Save options so persistent view reloads on restart
    save_config(cfg)


# ── !addticketoption <label> [emoji] ────────────────────────────────────────
@bot.command(name="addticketoption")
@commands.has_permissions(administrator=True)
async def addticketoption(ctx, label: str, emoji: str = None):
    cfg = get_config()
    cfg["ticket_options"].append({"label": label, "emoji": emoji})
    save_config(cfg)
    await ctx.send(embed=embed("✅ Option Added", f"Button `{emoji or ''} {label}` added to ticket panel.", color=0x57F287))


# ── !clearticketoptions ──────────────────────────────────────────────────────
@bot.command(name="clearticketoptions")
@commands.has_permissions(administrator=True)
async def clearticketoptions(ctx):
    cfg = get_config()
    cfg["ticket_options"] = []
    save_config(cfg)
    await ctx.send(embed=embed("✅ Cleared", "All ticket options removed.", color=0x57F287))


# ── !setticketcategory <category_id> ────────────────────────────────────────
@bot.command(name="setticketcategory")
@commands.has_permissions(administrator=True)
async def setticketcategory(ctx, category_id: str):
    cfg = get_config()
    cfg["ticket_category"] = category_id
    save_config(cfg)
    await ctx.send(embed=embed("✅ Updated", f"Tickets will be created in category `{category_id}`.", color=0x57F287))


# ── !adduser <user_id> ───────────────────────────────────────────────────────
@bot.command(name="adduser")
async def adduser(ctx, user_id: str):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return

    tickets = get_tickets()
    if str(ctx.channel.id) not in tickets:
        await ctx.send(embed=embed("❌ Error", "This command can only be used inside a ticket channel.", color=0xED4245))
        return

    try:
        uid = int(user_id.strip().lstrip("<@!>"))
        member = ctx.guild.get_member(uid) or await ctx.guild.fetch_member(uid)
        await ctx.channel.set_permissions(member, read_messages=True, send_messages=True)
        await ctx.send(embed=embed("✅ User Added", f"{member.mention} has been added to this ticket.", color=0x57F287))
    except discord.NotFound:
        await ctx.send(embed=embed("❌ User Not Found", "The user was not found in this server.", color=0xED4245))
    except Exception as e:
        await ctx.send(embed=embed("❌ Error", str(e), color=0xED4245))


# ── !claim ───────────────────────────────────────────────────────────────────
@bot.command(name="claim")
async def claim(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "Only support roles can claim tickets.", color=0xED4245))
        return

    tickets = get_tickets()
    tid = str(ctx.channel.id)
    if tid not in tickets:
        await ctx.send(embed=embed("❌ Error", "This is not a ticket channel.", color=0xED4245))
        return

    if tickets[tid]["claimed_by"]:
        claimer = ctx.guild.get_member(int(tickets[tid]["claimed_by"]))
        await ctx.send(embed=embed("⚠️ Already Claimed", f"Claimed by {claimer.mention if claimer else 'someone'}.", color=0xFEE75C))
        return

    tickets[tid]["claimed_by"] = str(ctx.author.id)
    save_tickets(tickets)

    await ctx.channel.set_permissions(ctx.author, read_messages=True, send_messages=True, use_application_commands=True)
    await ctx.send(embed=embed("✅ Claimed", f"{ctx.author.mention} has claimed this ticket!", color=0x57F287))


# ── !close ───────────────────────────────────────────────────────────────────
@bot.command(name="close")
async def close(ctx):
    tickets = get_tickets()
    if str(ctx.channel.id) not in tickets:
        await ctx.send(embed=embed("❌ Error", "This is not a ticket channel.", color=0xED4245))
        return
    await close_ticket_channel(ctx.channel, ctx.author)


# ── !confirmtrade ─────────────────────────────────────────────────────────────
@bot.command(name="confirmtrade")
async def confirmtrade(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return

    tickets = get_tickets()
    if str(ctx.channel.id) not in tickets:
        await ctx.send(embed=embed("❌ Error", "This command can only be used in a ticket channel.", color=0xED4245))
        return

    confirm_embed = discord.Embed(
        title="🤝 Confirm Trade?",
        description="Both parties must confirm to complete the trade.\nClick **Confirm** below to agree.",
        color=0xFEE75C
    )
    await ctx.send(embed=confirm_embed, view=ConfirmTradeView())


# ── !mminfoeng ───────────────────────────────────────────────────────────────
@bot.command(name="mminfoeng")
async def mminfoeng(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return

    info_embed = discord.Embed(
        title="🛡️ How This MM Deal Works",
        color=0x5865F2
    )
    info_embed.add_field(name="1️⃣ Item Secured", value="The Seller gives the in-game item to the MM. The MM confirms they have it in their inventory.", inline=False)
    info_embed.add_field(name="2️⃣ Direct Payment", value='Once the MM confirms they have the item, the Buyer sends the PayPal payment directly to the Seller (usually via "Friends & Family").', inline=False)
    info_embed.add_field(name="3️⃣ Proof of Payment", value="The Buyer sends a screenshot of the completed payment to the group chat. The Seller confirms they've received the funds in their PayPal balance.", inline=False)
    info_embed.add_field(name="4️⃣ Item Release", value='Once the Seller says "received," the MM trades the in-game item to the Buyer.', inline=False)
    info_embed.add_field(name="5️⃣ Deal Done", value="The MM leaves the chat, and the trade is complete.", inline=False)

    await ctx.send(embed=info_embed, view=MMInfoView())


# ── !mminfofrc ───────────────────────────────────────────────────────────────
@bot.command(name="mminfofrc")
async def mminfofrc(ctx):
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed):
        await ctx.send(embed=embed("❌ No Permission", "You don't have permission to use this command.", color=0xED4245))
        return

    info_embed = discord.Embed(
        title="🛡️ Fonctionnement de la Transaction MM",
        color=0x5865F2
    )
    info_embed.add_field(name="1️⃣ Sécurisation de l'objet", value="Le Vendeur donne l'objet en jeu au MM. Le MM confirme qu'il l'a bien dans son inventaire.", inline=False)
    info_embed.add_field(name="2️⃣ Paiement Direct", value='Une fois que le MM confirme avoir l\'objet, l\'Acheteur envoie le paiement PayPal directement au Vendeur (généralement via "Entre proches").', inline=False)
    info_embed.add_field(name="3️⃣ Preuve de Paiement", value="L'Acheteur envoie une capture d'écran du paiement effectué dans le groupe. Le Vendeur confirme qu'il a bien reçu les fonds sur son solde PayPal.", inline=False)
    info_embed.add_field(name="4️⃣ Remise de l'objet", value='Dès que le Vendeur confirme la réception ("reçu"), le MM donne l\'objet en jeu à l\'Acheteur.', inline=False)
    info_embed.add_field(name="5️⃣ Transaction Terminée", value="Le MM quitte la discussion et l'échange est validé.", inline=False)

    await ctx.send(embed=info_embed, view=MMInfoView())


# ── !rolemsg <role_id> <button_label> <message> ─────────────────────────────
@bot.command(name="rolemsg")
@commands.has_permissions(administrator=True)
async def rolemsg(ctx, role_id: str, button_label: str, *, message: str):
    role = ctx.guild.get_role(int(role_id))
    if not role:
        await ctx.send(embed=embed("❌ Error", "Role not found.", color=0xED4245))
        return
    msg_embed = discord.Embed(description=message, color=0x5865F2)
    view = RoleButtonView(button_label, role_id)
    await ctx.send(embed=msg_embed, view=view)


# ── !help ─────────────────────────────────────────────────────────────────────
@bot.command(name="help")
async def help_cmd(ctx):
    h = discord.Embed(title="📖 Bot Commands", color=0x5865F2)

    h.add_field(name="🔧 Admin Setup", value="""
`!setsendrole <role_id>` — Allow role to use `!sendmsg`
`!setsupportrole <role_id>` — Set ticket support/MM role (claim tickets)
`!setfreerole <role_id>` — Role that can msg in tickets without claiming
`!setticketcategory <category_id>` — Set category for ticket channels
`!addticketoption <label> [emoji]` — Add a button to ticket panel
`!clearticketoptions` — Clear all ticket panel buttons
`!ticketpanel [image_url]` — Send the ticket panel message
`!rolemsg <role_id> <btn_label> <message>` — Message with a role-grant button
""", inline=False)

    h.add_field(name="📨 Messaging", value="""
`!sendmsg <channel_id> <message>` — Send a message to a channel *(allowed roles)*
""", inline=False)

    h.add_field(name="🎫 Ticket Commands", value="""
`!claim` — Claim a ticket *(support role)*
`!close` — Close & delete the ticket channel
`!adduser <user_id>` — Add a user to the ticket *(support/free role)*
`!confirmtrade` — Show confirm trade buttons *(support/free role)*
""", inline=False)

    h.add_field(name="🛡️ MM Info", value="""
`!mminfoeng` — MM deal explanation in English *(support/free role)*
`!mminfofrc` — MM deal explanation in French *(support/free role)*
""", inline=False)

    h.set_footer(text="Prefix: !  |  Admin commands require Administrator permission")
    await ctx.send(embed=h)


# ══════════════════════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════════════════════
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: Set DISCORD_TOKEN environment variable.")
else:
    keep_alive()
    bot.run(TOKEN)