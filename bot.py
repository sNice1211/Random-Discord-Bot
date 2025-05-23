import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
import pytz
import requests
import os
import sys
import signal
import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler
import threading
import cmd
import shlex
from aiohttp import web
import websockets
import json
import functools
import random
from typing import Optional, Dict, List, Any
import time

# ===================== Configuration ====================
# Load configuration from environment variables or use defaults
BOT_PREFIX = os.getenv("BOT_PREFIX", "/")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
WEATHER_API_CACHE_MINUTES = int(os.getenv("WEATHER_API_CACHE_MINUTES", "30"))
COMMAND_COOLDOWN_SECONDS = int(os.getenv("COMMAND_COOLDOWN", "3"))

# ===================== Logging Setup ====================
log_directory = "logs"
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

# Create a timestamp for the log file name
timestamp = datetime.now().strftime("%Y-%m-%d")
log_file = os.path.join(log_directory, f'bot_{timestamp}.log')

# Configure logging
logger = logging.getLogger('discord_bot')
logger.setLevel(getattr(logging, LOG_LEVEL))

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(getattr(logging, LOG_LEVEL))
console_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console_handler.setFormatter(console_format)

# File handler with daily rotation
file_handler = TimedRotatingFileHandler(
    log_file,
    when='midnight',
    backupCount=30  # Keep logs for 30 days
)
file_handler.setLevel(getattr(logging, LOG_LEVEL))
file_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_format)

# Add handlers to logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# ===================== Bot Setup ====================
# Bot setup with intents
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)

# Flag to track shutdown status
is_shutting_down = False

# Cache for weather data
weather_cache = {}

# ===== UTILITY FUNCTIONS =====
# Command logging function
async def log_command(interaction: discord.Interaction, command_name: str):
    user = interaction.user
    channel = interaction.channel
    guild = interaction.guild
    
    # Format the log message
    log_message = (
        f"Command: /{command_name} | "
        f"User: {user.name} (ID: {user.id}) | "
        f"Channel: {channel.name if channel else 'DM'} (ID: {channel.id if channel else 'N/A'}) | "
        f"Guild: {guild.name if guild else 'DM'} (ID: {guild.id if guild else 'N/A'})"
    )
    
    # Log to console and file
    logger.info(log_message)

# Read the API key from the apikey.txt file
def get_apikey_from_file():
    try:
        with open("apikey.txt", "r") as file:
            return file.read().strip()
    except Exception as e:
        logger.error(f"Failed to read API key from file: {e}")
        return None

# Read the token from the token.txt file
def get_token_from_file():
    try:
        with open("token.txt", "r") as file:
            return file.read().strip()
    except Exception as e:
        logger.error(f"Failed to read token from file: {e}")
        sys.exit(1)  # Exit the program if token cannot be read

# Cooldown decorator for commands
def cooldown(seconds=COMMAND_COOLDOWN_SECONDS):
    def decorator(func):
        cooldowns = {}
        
        @functools.wraps(func)
        async def wrapper(interaction: discord.Interaction, *args, **kwargs):
            user_id = interaction.user.id
            current_time = time.time()
            
            if user_id in cooldowns and current_time - cooldowns[user_id] < seconds:
                remaining = seconds - (current_time - cooldowns[user_id])
                await interaction.response.send_message(
                    f"Please wait {remaining:.1f} seconds before using this command again.",
                    ephemeral=True
                )
                return
            
            cooldowns[user_id] = current_time
            return await func(interaction, *args, **kwargs)
        
        return wrapper
    return decorator

# ===================== Discord Commands ====================
# Slash command: Help command
@bot.tree.command(name="help", description="List available bot commands")
@cooldown()
async def help_command(interaction: discord.Interaction):
    await log_command(interaction, "help")

    commands = bot.tree.get_commands()

    help_message = (
        "Registered commands:\n" + 
        "\n".join([f"  /{command.name} - {command.description}" for command in commands])
    )

    await interaction.response.send_message(help_message)

# Slash command: Get current UTC time
@bot.tree.command(name="utctime", description="Get the current UTC time")
@cooldown()
async def utctime(interaction: discord.Interaction):
    await log_command(interaction, "utctime")
    
    utc_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    await interaction.response.send_message(f"The current time in UTC is: {utc_time}")

# Slash command: Get local time of a specific timezone
@bot.tree.command(name="localtime", description="Get the current time in a specific timezone")
@app_commands.describe(timezone="The timezone (e.g., 'US/Eastern')")
@cooldown()
async def localtime(interaction: discord.Interaction, timezone: str):
    await log_command(interaction, f"localtime {timezone}")
    
    try:
        # Convert the timezone string to the correct timezone object
        tz = pytz.timezone(timezone)
        local_time = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
        await interaction.response.send_message(f"The current time in {timezone} is: {local_time}")
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Invalid timezone requested: {timezone} by {interaction.user.name} (ID: {interaction.user.id})")
        await interaction.response.send_message(
            "Invalid timezone. Use `/timezones` to see available options.",
            ephemeral=True
        )

# Slash command: List available timezones
@bot.tree.command(name="timezones", description="List available timezone regions")
@cooldown()
async def timezones(interaction: discord.Interaction):
    await log_command(interaction, "timezones")
    
    # Get common timezone regions
    common_timezones = [
        "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
        "Europe/London", "Europe/Berlin", "Europe/Moscow",
        "Asia/Tokyo", "Asia/Shanghai", "Asia/Dubai",
        "Australia/Sydney", "Pacific/Auckland"
    ]
    
    message = "Common timezones:\n" + "\n".join(common_timezones)
    message += "\n\nFor a full list of timezones, visit: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
    
    await interaction.response.send_message(message, ephemeral=True)

# Get the weather API key
apikey = get_apikey_from_file()

# Slash command: Get the weather in a specific city using OpenWeather API
@bot.tree.command(name="weather", description="Get weather info for a specific city")
@app_commands.describe(city="The name of the city (e.g., 'London')")
@cooldown()
async def weather(interaction: discord.Interaction, city: str):
    await log_command(interaction, f"weather {city}")
    
    if not apikey:
        await interaction.response.send_message("Weather API is not configured.", ephemeral=True)
        return
    
    # Check cache first
    cache_key = city.lower()
    current_time = datetime.now()
    
    if cache_key in weather_cache:
        cached_data, timestamp = weather_cache[cache_key]
        # If cache is still valid (less than WEATHER_API_CACHE_MINUTES old)
        if current_time - timestamp < timedelta(minutes=WEATHER_API_CACHE_MINUTES):
            await interaction.response.send_message(cached_data)
            return
    
    url = f'http://api.openweathermap.org/data/2.5/weather?q={city}&appid={apikey}&units=metric'
    
    try:
        # Use a timeout to prevent hanging
        async with asyncio.timeout(10):
            response = await asyncio.to_thread(requests.get, url)
        
        if response.status_code == 200:
            data = response.json()
            main = data['main']
            weather_description = data['weather'][0]['description']
            temperature = main['temp']
            feels_like = main['feels_like']
            humidity = main['humidity']
            wind_speed = data['wind']['speed']
            city_name = data['name']
            country = data['sys']['country']
            
            weather_message = (
                f"Weather in {city_name}, {country}: {weather_description.capitalize()}\n"
                f"Temperature: {temperature}°C (Feels like: {feels_like}°C)\n"
                f"Humidity: {humidity}%\n"
                f"Wind Speed: {wind_speed} m/s"
            )
            
            # Cache the result
            weather_cache[cache_key] = (weather_message, current_time)
            
            await interaction.response.send_message(weather_message)
        elif response.status_code == 404:
            logger.warning(f"City not found: {city}")
            await interaction.response.send_message(f"City {city} not found. Please check the city name.", ephemeral=True)
        else:
            logger.error(f"Weather API error: {response.status_code} - {response.text}")
            await interaction.response.send_message("An error occurred while fetching weather data. Please try again later.", ephemeral=True)
    except asyncio.TimeoutError:
        logger.error(f"Weather API timeout for city: {city}")
        await interaction.response.send_message("The weather service is taking too long to respond. Please try again later.", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in weather command: {str(e)}")
        await interaction.response.send_message("An error occurred while fetching weather data. Please try again later.", ephemeral=True)

# Slash command: Get information about the server (guild)
@bot.tree.command(name="serverinfo", description="Get info about the server")
@cooldown()
async def serverinfo(interaction: discord.Interaction):
    await log_command(interaction, "serverinfo")
    
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    
    # Get more detailed server information
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    categories = len(guild.categories)
    roles = len(guild.roles)
    
    server_info = (
        f"**Server Information**\n"
        f"Name: {guild.name}\n"
        f"ID: {guild.id}\n"
        f"Owner: {guild.owner.mention if guild.owner else 'Unknown'}\n"
        f"Created: {guild.created_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"Member Count: {guild.member_count}\n"
        f"Channels: {text_channels} text, {voice_channels} voice, {categories} categories\n"
        f"Roles: {roles}\n"
        f"Boost Level: {guild.premium_tier}"
    )
    
    # Add server icon if available
    if guild.icon:
        server_info += f"\nIcon URL: {guild.icon.url}"
    
    await interaction.response.send_message(server_info)

# Slash command: Get information about a user
@bot.tree.command(name="userinfo", description="Get info about a user")
@app_commands.describe(user="The user you want to get info about (mention or username)")
@cooldown()
async def userinfo(interaction: discord.Interaction, user: discord.Member = None):
    target_user = user.name if user else "self"
    await log_command(interaction, f"userinfo {target_user}")
    
    if user is None:
        user = interaction.user  # If no user is mentioned, use the invoking user
    
    # Get more detailed user information
    roles = [role.mention for role in user.roles if role.name != "@everyone"] # Lists all assigned roles for the user in the server the command was invoked in. This excludes the @everyone role
    joined_server = user.joined_at.strftime('%Y-%m-%d %H:%M:%S') if user.joined_at else "Unknown"
    account_created = user.created_at.strftime('%Y-%m-%d %H:%M:%S')
    
    user_info = (
        f"**User Information**\n"
        f"Name: {user.name}\n"
        f"ID: {user.id}\n"
        f"Nickname: {user.nick if user.nick else 'None'}\n"
        f"Joined Server: {joined_server}\n"
        f"Account Created: {account_created}\n"
        f"Bot Account: {'Yes' if user.bot else 'No'}\n"
        f"Roles: {', '.join(roles) if roles else 'None'}"
    )
    
    # Add user avatar if available
    if user.avatar:
        user_info += f"\nAvatar URL: {user.avatar.url}"
    
    await interaction.response.send_message(user_info)

# Slash command: Return "Ping"
@bot.tree.command(name="pong", description="Ping")
@cooldown()
async def pong(interaction: discord.Interaction):
    await log_command(interaction, "pong")
    await interaction.response.send_message("Ping")
# Slash command: Ping command to check latency
@bot.tree.command(name="ping", description="Check bot latency")
@cooldown()
async def ping(interaction: discord.Interaction):
    await log_command(interaction, "ping")
    
    start_time = time.time()
    
    # First response to measure Discord API latency
    await interaction.response.send_message("Pinging...")
    
    # Edit the message to include both Discord API latency and bot latency
    end_time = time.time()
    response_time = (end_time - start_time) * 1000  # Convert to ms
    websocket_latency = bot.latency * 1000  # Convert to ms
    
    latency_message = (
        f"**Ping Results**\n"
        f"Bot Response Time: {response_time:.2f}ms\n"
        f"Discord API Latency: {websocket_latency:.2f}ms"
    )
    
    await interaction.edit_original_response(content=latency_message)

# Slash command: Get bot statistics
@bot.tree.command(name="stats", description="Get bot statistics")
@cooldown()
async def stats(interaction: discord.Interaction):
    await log_command(interaction, "stats")
    
    # Calculate uptime
    current_time = datetime.now()
    uptime = current_time - bot.start_time if hasattr(bot, 'start_time') else timedelta(seconds=0)
    
    # Format uptime
    days, remainder = divmod(int(uptime.total_seconds()), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
    
    # Get guild and user counts
    guild_count = len(bot.guilds)
    user_count = sum(guild.member_count for guild in bot.guilds)
    
    # Get memory usage
    process = psutil.Process(os.getpid()) if 'psutil' in sys.modules else None
    memory_usage = f"{process.memory_info().rss / 1024 / 1024:.2f} MB" if process else "N/A"
    
    stats_message = (
        f"**Bot Statistics**\n"
        f"Uptime: {uptime_str}\n"
        f"Servers: {guild_count}\n"
        f"Users: {user_count}\n"
        f"Memory Usage: {memory_usage}\n"
        f"Python Version: {sys.version.split()[0]}\n"
        f"Discord.py Version: {discord.__version__}"
    )
    
    await interaction.response.send_message(stats_message)

# ===================== Bot Backbone =====================
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}!')
    
    # Store start time for uptime tracking
    bot.start_time = datetime.now()
    
    try:
        # Sync commands globally instead of per guild
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s) globally")
        logger.info("Note: Global commands may take up to 1 hour to appear in all servers")
    except Exception as e:
        logger.error(f"Failed to sync global commands: {e}")


# Track errors in commands
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    command_name = interaction.command.name if interaction.command else "Unknown"
    
    # Log different types of errors differently
    if isinstance(error, app_commands.CommandOnCooldown):
        logger.info(f"Cooldown triggered for command '{command_name}' by {interaction.user.name} (ID: {interaction.user.id})")
    elif isinstance(error, app_commands.MissingPermissions):
        logger.warning(f"Permission error in command '{command_name}' by {interaction.user.name} (ID: {interaction.user.id}): {str(error)}")
    else:
        logger.error(f"Error in command '{command_name}': {str(error)}")
    
    # Send appropriate error message to user
    try:
        error_message = str(error)
        
        # Customize error messages for better user experience
        if isinstance(error, app_commands.CommandOnCooldown):
            error_message = f"This command is on cooldown. Try again in {error.retry_after:.1f} seconds."
        elif isinstance(error, app_commands.MissingPermissions):
            error_message = "You don't have permission to use this command."
        
        if interaction.response.is_done():
            await interaction.followup.send(f"An error occurred: {error_message}", ephemeral=True)
        else:
            await interaction.response.send_message(f"An error occurred: {error_message}", ephemeral=True)
    except Exception as e:
        logger.error(f"Failed to send error message to user: {e}")

# Log when the bot joins a new guild
@bot.event
async def on_guild_join(guild):
    logger.info(f"Bot joined a new guild: {guild.name} (ID: {guild.id}) | Owner: {guild.owner.name if guild.owner else 'Unknown'} | Members: {guild.member_count}")
    
    # Sync commands to the new guild
    try:
        synced = await bot.tree.sync(guild=discord.Object(id=guild.id))
        logger.info(f"Synced {len(synced)} command(s) to new guild: {guild.name} (ID: {guild.id})")
    except Exception as e:
        logger.error(f"Failed to sync commands to new guild {guild.name} (ID: {guild.id}): {e}")

# Log when the bot is removed from a guild
@bot.event
async def on_guild_remove(guild):
    logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})")

# ===================== Console Interface =====================
class BotConsole(cmd.Cmd):
    intro = "Discord Bot Console. Type 'help' for available commands."
    prompt = "Bot> "
    
    def __init__(self, bot_instance):
        super().__init__()
        self.bot = bot_instance
        self.loop = None  # Initialize the event loop later

    def do_status(self, arg):
        """Show the current status of the bot"""
        if self.bot.is_closed():
            print("Bot is currently offline")
        else:
            latency = self.bot.latency * 1000
            guild_count = len(self.bot.guilds)
            user_count = sum(guild.member_count for guild in self.bot.guilds)
            uptime = datetime.now() - self.bot.start_time if hasattr(self.bot, 'start_time') else timedelta(seconds=0)
            days, remainder = divmod(int(uptime.total_seconds()), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
            
            print(f"Bot is online | Latency: {latency:.2f}ms | Guilds: {guild_count} | Users: {user_count} | Uptime: {uptime_str}")

    def do_guilds(self, arg):
        """List all guilds (servers) the bot is in"""
        if self.bot.is_closed():
            print("Bot is offline")
            return
            
        guilds = self.bot.guilds
        if not guilds:
            print("Bot is not in any guilds")
            return
            
        print(f"Bot is in {len(guilds)} guilds:")
        for guild in guilds:
            print(f"  - {guild.name} (ID: {guild.id}) | Members: {guild.member_count} | Owner: {guild.owner.name if guild.owner else 'Unknown'}")

    def do_commands(self, arg):
        """List all the registered commands for the bot"""
        if self.bot.is_closed():
            print("Bot is offline")
            return
        
        # Get all the registered commands from the bot's command tree
        commands = self.bot.tree.get_commands()
        
        if not commands:
            print("No commands are registered.")
            return
        
        print("Registered commands:")
        for command in commands:
            print(f"  /{command.name} - {command.description}")
    
    def do_send(self, arg):
        """Send a message to a specific channel: send <channel_id> <message>"""
        if self.bot.is_closed():
            print("Bot is offline")
            return
            
        args = shlex.split(arg)
        if len(args) < 2:
            print("Usage: send <channel_id> <message>")
            return
            
        channel_id = args[0]
        message = ' '.join(args[1:])
        
        try:
            channel_id = int(channel_id)
        except ValueError:
            print("Channel ID must be a number")
            return
            
        async def send_message():
            try:
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    print(f"Channel with ID {channel_id} not found")
                    return
                await channel.send(message)
                print(f"Message sent to {channel.name} in {channel.guild.name}")
            except Exception as e:
                print(f"Error sending message: {e}")
        
        asyncio.run_coroutine_threadsafe(send_message(), self.loop).result()
    
    def do_broadcast(self, arg):
        """Send a message to all guilds: broadcast <message>"""
        if self.bot.is_closed():
            print("Bot is offline")
            return
        
        if not arg:
            print("Usage: broadcast <message>")
            return
        
        async def broadcast_message():
            success_count = 0
            fail_count = 0
            
            for guild in self.bot.guilds:
                try:
                    # Try to find a suitable channel (system channel or first text channel)
                    channel = guild.system_channel
                    if not channel:
                        for ch in guild.text_channels:
                            if ch.permissions_for(guild.me).send_messages:
                                channel = ch
                                break
                    
                    if channel:
                        await channel.send(arg)
                        success_count += 1
                    else:
                        print(f"No suitable channel found in {guild.name}")
                        fail_count += 1
                except Exception as e:
                    print(f"Error sending to {guild.name}: {e}")
                    fail_count += 1
            
            print(f"Broadcast complete: {success_count} successful, {fail_count} failed")
        
        asyncio.run_coroutine_threadsafe(broadcast_message(), self.loop).result()
    
    def do_reload(self, arg):
        """Reload the bot commands without restarting"""
        if self.bot.is_closed():
            print("Bot is offline")
            return

# ===================== Shutdown Function ====================
async def shutdown_bot():
    logger.info("Initiating graceful shutdown sequence...")
    try:
        # Perform any cleanup tasks here if needed
        
        # Close the bot connection
        await bot.close()
        logger.info("Bot connection closed successfully.")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
        # Force exit if graceful shutdown fails
        sys.exit(1)

# ===================== Main Function ====================
def start_console():
    console = BotConsole(bot)
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    console.loop = new_loop
    console.cmdloop()

if __name__ == "__main__":
    # Get the bot token
    bot_token = get_token_from_file()
    
    if not bot_token:
        logger.critical("No bot token found. Please create a token.txt file with your bot token.")
        sys.exit(1)
    
    # Get the weather API key if not set in environment
    if not WEATHER_API_KEY:
        api_key = get_apikey_from_file()
        if api_key:
            globals()['WEATHER_API_KEY'] = api_key
            logger.info("Weather API key loaded from file")
    
    # Start the console interface in a separate thread
    console_thread = threading.Thread(target=start_console, daemon=True)
    console_thread.start()
    
    # Set up signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        global is_shutting_down
        if is_shutting_down:
            logger.warning("Forced shutdown initiated.")
            sys.exit(0)
        
        logger.info("Shutdown signal received. Closing bot gracefully...")
        is_shutting_down = True
        
        # Create a task to stop the bot and ensure it's run in the bot's event loop
        asyncio.run_coroutine_threadsafe(shutdown_bot(), bot.loop)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start the bot with the token
    try:
        logger.info(f"Starting bot with prefix: {BOT_PREFIX}")
        bot.run(bot_token)
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")
        sys.exit(1)
    finally:
        logger.info("Bot has shut down.")
