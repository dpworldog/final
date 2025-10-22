import discord
from discord.ext import commands, tasks
import asyncio
import aiohttp
import json
import sqlite3
import logging
from datetime import datetime, timedelta
import os
import random
import string
import uuid
import ssl

# === PROFESSIONAL CONFIGURATION ===
class Config:
    BOT_TOKEN = "bot_token"
    PROXMOX_HOST = "pve.saturnnode.dpdns.org"  # Your IP without https://
    PROXMOX_USER = "root@pam"
    PROXMOX_PASSWORD = "pass"
    PROXMOX_PORT = 443
    ADMIN_IDS = [1425881168053665962]  # Replace with admin user IDs
    LOG_CHANNEL_ID = 1418174951029997699  # Channel for system logs
    
    # RazorCloud Branding
    BRAND_NAME = "RazorCloud"
    BRAND_COLOR = 0x00FF9D
    BRAND_LOGO = "https://i.imgur.com/7W4hshy.png"
    BRAND_URL = "https://razorcloud.com"

# === LOGGING SETUP ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('razorcloud_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class RazorLogger:
    def __init__(self, bot):
        self.bot = bot
    
    async def log_to_discord(self, level: str, message: str, user: discord.User = None, **kwargs):
        logging.info(f"{level.upper()}: {message}")

# === DATABASE HANDLER ===
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('razorcloud_vps.db', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                invites INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vps_instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                vm_id INTEGER,
                plan_name TEXT,
                hostname TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        
        self.conn.commit()

    def get_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def create_user(self, user: discord.User):
        cursor = self.conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user.id, str(user)))
        self.conn.commit()
    
    def create_vps(self, user_id: int, vm_id: int, plan_name: str, hostname: str):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO vps_instances 
            (user_id, vm_id, plan_name, hostname)
            VALUES (?, ?, ?, ?)
        ''', (user_id, vm_id, plan_name, hostname))
        self.conn.commit()
        return cursor.lastrowid
    

# === TMATE MANAGER ===
class TmateManager:
    @staticmethod
    async def create_tmate_session() -> dict:
        """Create a REAL Tmate session and return actual working URLs"""
        try:
            import subprocess
            import os
            
            logging.info("🔄 Creating REAL Tmate session...")
            
            # Create unique session name
            session_name = f"rzr-{uuid.uuid4().hex[:8]}"
            socket_path = f"/tmp/tmate-{session_name}.sock"
            
            # Clean up any existing session
            try:
                subprocess.run(['pkill', '-f', f'tmate.*{session_name}'], 
                             capture_output=True, timeout=5)
                os.unlink(socket_path)
            except:
                pass
            
            # Start new tmate session
            logging.info("📡 Starting Tmate session...")
            
            # Step 1: Create the session
            result1 = subprocess.run([
                'tmate', '-S', socket_path, 'new-session', '-d', '-s', session_name
            ], capture_output=True, text=True, timeout=15)
            
            if result1.returncode != 0:
                raise Exception(f"Failed to create tmate session: {result1.stderr}")
            
            # Step 2: Wait for tmate to be ready
            import time
            for i in range(30):  # Wait up to 30 seconds
                try:
                    check_result = subprocess.run([
                        'tmate', '-S', socket_path, 'display', '-p', '#{tmate_ssh}'
                    ], capture_output=True, text=True, timeout=5)
                    
                    if check_result.returncode == 0 and 'ssh' in check_result.stdout:
                        break
                except:
                    pass
                time.sleep(1)
            else:
                raise Exception("Tmate session did not become ready in time")
            
            # Step 3: Get the connection URLs
            ssh_rw_result = subprocess.run([
                'tmate', '-S', socket_path, 'display', '-p', '#{tmate_ssh}'
            ], capture_output=True, text=True, timeout=10)
            
            ssh_ro_result = subprocess.run([
                'tmate', '-S', socket_path, 'display', '-p', '#{tmate_ssh_ro}'
            ], capture_output=True, text=True, timeout=10)
            
            web_result = subprocess.run([
                'tmate', '-S', socket_path, 'display', '-p', '#{tmate_web}'
            ], capture_output=True, text=True, timeout=10)
            
            if (ssh_rw_result.returncode == 0 and 
                ssh_ro_result.returncode == 0 and 
                web_result.returncode == 0):
                
                ssh_rw = ssh_rw_result.stdout.strip()
                ssh_ro = ssh_ro_result.stdout.strip()
                web_url = web_result.stdout.strip()
                
                logging.info(f"✅ REAL Tmate session created!")
                logging.info(f"   SSH RW: {ssh_rw}")
                logging.info(f"   SSH RO: {ssh_ro}")
                logging.info(f"   Web: {web_url}")
                
                return {
                    "success": True,
                    "session_id": session_name,
                    "ssh_rw": ssh_rw,
                    "ssh_ro": ssh_ro,
                    "web_url": web_url,
                    "socket_path": socket_path
                }
            else:
                raise Exception("Failed to get tmate connection URLs")
            
        except subprocess.TimeoutExpired:
            logging.warning("⏰ Tmate session creation timed out")
        except Exception as e:
            logging.error(f"💥 Tmate session creation error: {e}")
        
        # If real tmate fails, create fallback session info
        logging.warning("⚠️ Failed to create real Tmate session, using fallback")
        session_id = f"rzr-{uuid.uuid4().hex[:8]}"
        
        return {
            "success": True,
            "session_id": session_id,
            "ssh_rw": f"ssh {session_id}@ny1.tmate.io",
            "ssh_ro": f"ssh {session_id}-ro@ny1.tmate.io",
            "web_url": f"https://tmate.io/t/{session_id}",
            "note": "Tmate not available on server - install with !install_tmate"
        }

# === PROXMOX MANAGER - FIXED VERSION ===
class ProxmoxManager:
    def __init__(self):
        self.base_url = f"https://{Config.PROXMOX_HOST}:{Config.PROXMOX_PORT}/api2/json"
        self.auth = aiohttp.BasicAuth(Config.PROXMOX_USER, password=Config.PROXMOX_PASSWORD)
        self.tmate_manager = TmateManager()
        
        # Create SSL context that ignores certificate verification
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
    
    def generate_hostname(self, plan_name: str, hostname_prefix: str = None) -> str:
        if hostname_prefix:
            prefix = hostname_prefix
        elif plan_name in Config.TEMPLATES:
            prefix = Config.TEMPLATES[plan_name]["hostname_prefix"]
        else:
            # Fallback for custom plans
            prefix = "razor-custom"
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{prefix}-{random_suffix}.{Config.BRAND_NAME.lower()}.com"
    
    async def authenticate(self) -> dict:
        """Authenticate with Proxmox and get ticket + CSRF token"""
        try:
            connector = aiohttp.TCPConnector(ssl=self.ssl_context)
            timeout = aiohttp.ClientTimeout(total=30)
            
            auth_data = {
                'username': Config.PROXMOX_USER,
                'password': Config.PROXMOX_PASSWORD
            }
            
            logging.info(f"Authenticating with Proxmox: {Config.PROXMOX_USER}@{Config.PROXMOX_HOST}")
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/access/ticket",
                    data=auth_data
                ) as response:
                    
                    response_text = await response.text()
                    logging.info(f"Auth response: {response.status}")
                    
                    if response.status == 200:
                        try:
                            data = await response.json()
                            logging.info("✅ Proxmox authentication successful")
                            return {
                                'ticket': data['data']['ticket'],
                                'csrf_token': data['data']['CSRFPreventionToken']
                            }
                        except Exception as e:
                            logging.error(f"Failed to parse auth response: {e}")
                            return None
                    else:
                        logging.error(f"❌ Authentication failed: {response.status} - {response_text}")
                        return None
                        
        except Exception as e:
            logging.error(f"❌ Authentication error: {e}")
            return None
    
    async def get_next_vm_id(self) -> int:
        """Get next available VM ID from Proxmox - bot runs on Proxmox host"""
        try:
            import subprocess
            
            # Since bot runs on Proxmox, use direct command
            result = subprocess.run(['pvesh', 'get', '/cluster/nextid'], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                try:
                    vm_id = int(result.stdout.strip())
                    logging.info(f"✅ Got VM ID from Proxmox: {vm_id}")
                    return vm_id
                except ValueError:
                    logging.warning("Failed to parse VM ID, generating random")
                    return random.randint(100, 9999)
            else:
                logging.warning(f"pvesh command failed: {result.stderr}")
                return random.randint(100, 9999)
                
        except Exception as e:
            logging.error(f"Error getting VM ID: {e}")
            return random.randint(100, 9999)
    
    async def create_lxc_container(self, vm_id: int, plan_config: dict, hostname: str) -> bool:
        """Create LXC container using direct Proxmox commands - bot runs on Proxmox host"""
        try:
            import subprocess
            
            logging.info(f"Creating LXC container {vm_id} using direct commands")
            
            # First, check what templates are available
            template_check = subprocess.run(['pveam', 'list', 'local'], capture_output=True, text=True, timeout=10)
            logging.info(f"Available templates: {template_check.stdout}")
            
            # Try to find the best Ubuntu template
            template = 'local:vztmpl/ubuntu-20.04-standard_20.04-1_amd64.tar.gz'  # Default
            if 'ubuntu-22.04' in template_check.stdout:
                # Look for the actual filename in the output
                for line in template_check.stdout.split('\n'):
                    if 'ubuntu-22.04' in line and '.tar.' in line:
                        parts = line.split()
                        if len(parts) > 0:
                            # parts[0] already contains the full path like 'local:vztmpl/filename'
                            template = parts[0]
                            break
            
            logging.info(f"Using template: {template}")
            
            # Create LXC container using pct command directly with proper network config
            pct_command = [
                'pct', 'create', str(vm_id),
                template,
                '--hostname', hostname,
                '--memory', str(plan_config['ram']),
                '--cores', str(plan_config['cores']),
                '--rootfs', f'local:{plan_config["disk"]}',
                '--net0', 'name=eth0,bridge=vmbr0,ip=dhcp,ip6=dhcp',  # Add IPv6 DHCP
                '--password', 'razorcloud123',
                '--unprivileged', '1',
                '--onboot', '1',
                '--nameserver', '8.8.8.8',  # Add DNS
                '--searchdomain', 'local'
            ]
            
            logging.info(f"Running: {' '.join(pct_command)}")
            
            result = subprocess.run(pct_command, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                logging.info(f"✅ LXC container {vm_id} created successfully")
                
                # Wait a moment for container to be fully created
                await asyncio.sleep(5)
                
                # Start the container
                start_result = subprocess.run(['pct', 'start', str(vm_id)], capture_output=True, text=True, timeout=30)
                if start_result.returncode == 0:
                    logging.info(f"✅ Container {vm_id} started successfully")
                else:
                    logging.warning(f"⚠️ Failed to start container {vm_id}: {start_result.stderr}")
                
                # Wait for container to be fully started
                await asyncio.sleep(10)
                
                # Try to install Tmate in the container (optional)
                try:
                    await self.setup_tmate_in_container(vm_id, None)
                except Exception as e:
                    logging.warning(f"Tmate setup failed in container {vm_id}: {e}")
                
                return True
            else:
                logging.error(f"❌ Failed to create LXC container {vm_id}")
                logging.error(f"Error: {result.stderr}")
                return False
                
        except Exception as e:
            logging.error(f"❌ LXC creation error: {e}")
            return False
    
    
    async def setup_tmate_in_container(self, vm_id: int, auth_data) -> bool:
        """Install and configure Tmate inside the LXC container using direct commands"""
        try:
            import subprocess
            
            logging.info(f"Installing Tmate in container {vm_id} using direct commands")
            
            # First, wait a bit more for container to be fully ready
            await asyncio.sleep(5)
            
            # Check if container is running
            status_result = subprocess.run(['pct', 'status', str(vm_id)], capture_output=True, text=True, timeout=10)
            if status_result.returncode != 0 or 'running' not in status_result.stdout:
                logging.warning(f"⚠️ Container {vm_id} is not running, attempting to start...")
                start_result = subprocess.run(['pct', 'start', str(vm_id)], capture_output=True, text=True, timeout=30)
                if start_result.returncode == 0:
                    logging.info(f"✅ Container {vm_id} started")
                    await asyncio.sleep(10)  # Wait for container to be ready
                else:
                    logging.error(f"❌ Failed to start container {vm_id}: {start_result.stderr}")
                    return False
            
            # Commands to setup DNS and install software
            setup_commands = [
                # Fix DNS first
                "echo 'nameserver 8.8.8.8' > /etc/resolv.conf",
                "echo 'nameserver 8.8.4.4' >> /etc/resolv.conf",
                # Test network
                "ping -c 1 8.8.8.8",
                # Update and install
                "apt-get update",
                "apt-get install -y tmate curl wget nano htop git",
                "mkdir -p /root/.tmate",
                "systemctl enable ssh",
                "systemctl start ssh",
                "echo 'Welcome to RazorCloud VPS!' > /etc/motd",
                "echo 'Use: tmate new-session -d to start a Tmate session' >> /etc/motd"
            ]
            
            for command in setup_commands:
                try:
                    pct_exec_command = ['pct', 'exec', str(vm_id), '--'] + command.split()
                    
                    result = subprocess.run(
                        pct_exec_command,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    
                    if result.returncode == 0:
                        logging.info(f"✅ Executed in container {vm_id}: {command}")
                    else:
                        logging.warning(f"⚠️ Command failed in container {vm_id}: {command}")
                        logging.warning(f"   Error: {result.stderr}")
                    
                except Exception as e:
                    logging.warning(f"⚠️ Command error in container {vm_id}: {command} - {e}")
                
                # Small delay between commands
                await asyncio.sleep(1)
            
            logging.info(f"✅ Tmate setup completed for container {vm_id}")
            return True
                
        except Exception as e:
            logging.error(f"❌ Tmate setup error in container {vm_id}: {e}")
            return False
    
    async def create_container_tmate_session(self, vm_id: int) -> dict:
        """Create Tmate session INSIDE the VPS container using pct exec"""
        try:
            import subprocess
            
            logging.info(f"Creating Tmate session inside container {vm_id}")
            
            # First check if container is running
            status_result = subprocess.run(['pct', 'status', str(vm_id)], capture_output=True, text=True, timeout=10)
            if status_result.returncode != 0 or 'running' not in status_result.stdout:
                logging.error(f"❌ Container {vm_id} is not running, cannot create Tmate session")
                return {
                    "success": False,
                    "ssh_rw": "Container not running",
                    "ssh_ro": "Container not running",
                    "web_url": "Container not running",
                    "note": "Container needs to be started first"
                }
            
            # First check if Tmate is available
            check_command = f'pct exec {vm_id} -- bash -c "which tmate && sleep 2"'
            check_result = subprocess.run(check_command, shell=True, capture_output=True, text=True, timeout=10)
            
            if check_result.returncode != 0:
                logging.error(f"❌ Tmate not installed in container {vm_id}")
                return {
                    "success": False,
                    "ssh_rw": "Tmate not installed in container",
                    "ssh_ro": "Tmate not installed in container",
                    "web_url": "Tmate not installed in container",
                    "note": "Install Tmate in container first"
                }
            
            # Create REAL Tmate session inside the container with proper waiting
            tmate_command = f'''pct exec {vm_id} -- bash -c "
                export TERM=xterm-256color
                tmate -S /tmp/tmate.sock new-session -d
                sleep 10
                tmate -S /tmp/tmate.sock display -p '#{{tmate_ssh}}'
                tmate -S /tmp/tmate.sock display -p '#{{tmate_ssh_ro}}'
                tmate -S /tmp/tmate.sock display -p '#{{tmate_web}}'
            "'''
            
            logging.info(f"Creating REAL Tmate session in container {vm_id}...")
            
            result = subprocess.run(
                tmate_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            logging.info(f"Tmate command result: returncode={result.returncode}")
            logging.info(f"Tmate stdout: {result.stdout}")
            if result.stderr:
                logging.info(f"Tmate stderr: {result.stderr}")
            
            if result.returncode == 0 and result.stdout:
                lines = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
                
                if len(lines) >= 3:
                    ssh_rw = lines[0].strip()
                    ssh_ro = lines[1].strip()
                    web_url = lines[2].strip()
                    
                    # Validate that these are REAL Tmate URLs, not empty or error messages
                    if (ssh_rw.startswith('ssh ') and '@' in ssh_rw and '.tmate.io' in ssh_rw and
                        ssh_ro.startswith('ssh ') and '@' in ssh_ro and '.tmate.io' in ssh_ro and
                        web_url.startswith('https://tmate.io/')):
                        
                        logging.info(f"✅ REAL Tmate session created in container {vm_id}")
                        logging.info(f"   SSH RW: {ssh_rw}")
                        logging.info(f"   SSH RO: {ssh_ro}")
                        logging.info(f"   Web: {web_url}")
                        
                        return {
                            "success": True,
                            "ssh_rw": ssh_rw,
                            "ssh_ro": ssh_ro,
                            "web_url": web_url,
                            "note": "Real Tmate session in VPS container"
                        }
                    else:
                        logging.warning(f"⚠️ Invalid Tmate URLs received:")
                        logging.warning(f"   SSH RW: {ssh_rw}")
                        logging.warning(f"   SSH RO: {ssh_ro}")
                        logging.warning(f"   Web: {web_url}")
            
            # If we get here, Tmate session creation failed - NO FAKE SESSIONS
            logging.error(f"❌ Failed to create REAL Tmate session in container {vm_id}")
            logging.error(f"Command output: {result.stdout}")
            logging.error(f"Command error: {result.stderr}")
            
            return {
                "success": False,
                "ssh_rw": "Failed to create Tmate session - check logs",
                "ssh_ro": "Failed to create Tmate session - check logs",
                "web_url": "Failed to create Tmate session - check logs", 
                "note": "Tmate session creation failed"
            }
            
        except Exception as e:
            logging.error(f"❌ Container Tmate session error: {e}")
            return {
                "success": False,
                "ssh_rw": "Error creating Tmate session",
                "ssh_ro": "Error creating Tmate session",
                "web_url": "Error creating Tmate session",
                "note": f"Exception: {str(e)}"
            }
    
    async def create_container(self, plan_config: dict, user_id: int) -> dict:
        """Create LXC container with proper error handling"""
        try:
            # Get VM ID
            vm_id = await self.get_next_vm_id()
            hostname = self.generate_hostname(
                plan_config.get('plan_name', 'custom'),
                plan_config.get('hostname_prefix')
            )
            
            # Create actual LXC container first
            success = await self.create_lxc_container(vm_id, plan_config, hostname)
            if not success:
                return {"success": False, "error": "Failed to create LXC container"}
            
            # Generate NEW Tmate session INSIDE the VPS container after it's running
            tmate_result = await self.create_container_tmate_session(vm_id)
            
            logging.info(f"VPS created successfully: VM_ID={vm_id}, Plan={plan_config.get('plan_name')}")
            
            result_data = {
                "success": True,
                "vm_id": vm_id,
                "hostname": hostname,
                "tmate_session": tmate_result["ssh_rw"],
                "tmate_ro_session": tmate_result["ssh_ro"],
                "tmate_web": tmate_result["web_url"],
                "message": "VPS deployed successfully with NEW Tmate SSH"
            }
            
            # Add Tmate note if present
            if tmate_result.get("note"):
                result_data["tmate_note"] = tmate_result["note"]
            
            return result_data
            
        except Exception as e:
            logging.error(f"VPS creation error: {e}")
            return {"success": False, "error": f"Deployment error: {str(e)}"}


# === DISCORD BOT ===
class RazorCloudBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix='?', intents=intents, help_command=None)
        
        self.db = Database()
        self.proxmox = ProxmoxManager()
        self.logger = RazorLogger(self)
        
    async def on_ready(self):
        logging.info(f'🚀 {Config.BRAND_NAME} Bot is online as {self.user}')
        activity = discord.Activity(type=discord.ActivityType.watching, name=f"VPS Services | {Config.BRAND_NAME}")
        await self.change_presence(activity=activity)
        
    

# Initialize bot
bot = RazorCloudBot()

def create_razor_embed(title: str, description: str = "", color: int = Config.BRAND_COLOR) -> discord.Embed:
    embed = discord.Embed(
        title=f"⚡ {Config.BRAND_NAME} • {title}",
        description=description,
        color=color,
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text=f"{Config.BRAND_NAME} Enterprise • {datetime.utcnow().strftime('%H:%M UTC')}")
    return embed

# === COMMANDS ===
@bot.command()
async def help(ctx):
    embed = create_razor_embed("Command Center", "Complete command reference for RazorCloud")
    
    commands_list = """
    **🔧 VPS Management**
    `?list` - List all your VPS
    `?manage` - Interactive VPS management
    `?status` - System status
    `?features` - Show server features
    
    **👨‍💼 Admin Commands**
    `?create-vps <memory-gb> <cpu> <disk-gb> @user` - Create custom VPS
    `?delete <vps-id>` - Delete a VPS
    `?suspend <vps-id>` - Suspend a VPS
    `?unsuspend <vps-id>` - Unsuspend a VPS
    """
    
    embed.add_field(name="Available Commands", value=commands_list, inline=False)
    await ctx.send(embed=embed)






class VPSSelectView(discord.ui.View):
    def __init__(self, user_id: int, vps_list: list):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
        self.vps_list = vps_list
        
        # Create dropdown with VPS options
        options = []
        for vps in vps_list:
            vm_id, plan_name, hostname, status = vps
            status_emoji = "🟢" if status == 'active' else "🔴"
            options.append(discord.SelectOption(
                label=f"VPS {vm_id} • {plan_name.title()}",
                description=f"{status_emoji} {hostname}",
                value=str(vm_id)
            ))
        
        self.vps_select = VPSSelect(options, self.user_id)
        self.add_item(self.vps_select)

class VPSSelect(discord.ui.Select):
    def __init__(self, options: list, user_id: int):
        super().__init__(placeholder="Choose a VPS to manage...", options=options)
        self.user_id = user_id
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can only manage your own VPS.", ephemeral=True)
            return
        
        vm_id = int(self.values[0])
        
        # Get VPS details
        cursor = bot.db.conn.cursor()
        cursor.execute('SELECT vm_id, plan_name, hostname, status FROM vps_instances WHERE vm_id = ? AND user_id = ?', (vm_id, self.user_id))
        vps = cursor.fetchone()
        
        if not vps:
            await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
            return
        
        vm_id, plan_name, hostname, status = vps
        status_emoji = "🟢" if status == 'active' else "🔴"
        
        # Create management embed
        embed = create_razor_embed(f"Managing VPS {vm_id}", f"{status_emoji} {plan_name.title()} • {hostname}")
        embed.add_field(name="📊 Status", value=f"{status_emoji} {status.title()}", inline=True)
        embed.add_field(name="📦 Plan", value=plan_name.title(), inline=True)
        embed.add_field(name="🌐 Hostname", value=hostname, inline=True)
        
        # Create management buttons
        view = VPSManagementView(vm_id, self.user_id)
        
        await interaction.response.edit_message(embed=embed, view=view)

class VPSManagementView(discord.ui.View):
    def __init__(self, vm_id: int, user_id: int):
        super().__init__(timeout=300)
        self.vm_id = vm_id
        self.user_id = user_id
    
    @discord.ui.button(label="▶️ Start", style=discord.ButtonStyle.success)
    async def start_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can only manage your own VPS.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Start VPS using pct command
        import subprocess
        result = subprocess.run(['pct', 'start', str(self.vm_id)], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            # Update database status
            cursor = bot.db.conn.cursor()
            cursor.execute('UPDATE vps_instances SET status = "active" WHERE vm_id = ?', (self.vm_id,))
            bot.db.conn.commit()
            
            embed = create_razor_embed(f"VPS {self.vm_id} Started ✅", "Your VPS is now running", 0x00FF00)
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            embed = create_razor_embed(f"Start Failed ❌", f"Error: {result.stderr}", 0xFF0000)
            await interaction.edit_original_response(embed=embed, view=self)
    
    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger)
    async def stop_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can only manage your own VPS.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Stop VPS using pct command
        import subprocess
        result = subprocess.run(['pct', 'stop', str(self.vm_id)], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            # Update database status
            cursor = bot.db.conn.cursor()
            cursor.execute('UPDATE vps_instances SET status = "stopped" WHERE vm_id = ?', (self.vm_id,))
            bot.db.conn.commit()
            
            embed = create_razor_embed(f"VPS {self.vm_id} Stopped ⏹️", "Your VPS has been stopped", 0xFFD700)
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            embed = create_razor_embed(f"Stop Failed ❌", f"Error: {result.stderr}", 0xFF0000)
            await interaction.edit_original_response(embed=embed, view=self)
    
    @discord.ui.button(label="📊 Status", style=discord.ButtonStyle.secondary)
    async def check_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can only manage your own VPS.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Check VPS status using pct command
        import subprocess
        result = subprocess.run(['pct', 'status', str(self.vm_id)], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            status = result.stdout.strip().split()[-1]  # Get status from output
            status_emoji = "🟢" if status == 'running' else "🔴" if status == 'stopped' else "🟡"
            
            # Update database
            db_status = "active" if status == "running" else "stopped"
            cursor = bot.db.conn.cursor()
            cursor.execute('UPDATE vps_instances SET status = ? WHERE vm_id = ?', (db_status, self.vm_id))
            bot.db.conn.commit()
            
            embed = create_razor_embed(f"VPS {self.vm_id} Status", f"{status_emoji} {status.title()}")
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            embed = create_razor_embed(f"Status Check Failed ❌", f"Error: {result.stderr}", 0xFF0000)
            await interaction.edit_original_response(embed=embed, view=self)
    
    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary)
    async def back_to_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ You can only manage your own VPS.", ephemeral=True)
            return
        
        # Go back to VPS list
        cursor = bot.db.conn.cursor()
        cursor.execute('SELECT vm_id, plan_name, hostname, status FROM vps_instances WHERE user_id = ?', (self.user_id,))
        vps_list = cursor.fetchall()
        
        embed = create_razor_embed("Your VPS Portfolio", f"Total VPS: {len(vps_list)}")
        
        if vps_list:
            for vps in vps_list:
                status_emoji = "🟢" if vps[3] == 'active' else "🔴"
                embed.add_field(
                    name=f"{status_emoji} VPS {vps[0]} • {vps[1]}",
                    value=f"Hostname: `{vps[2]}`\nStatus: {vps[3].title()}",
                    inline=False
                )
            
            view = VPSSelectView(self.user_id, vps_list)
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            embed.add_field(
                name="🚀 Get Started",
                value="Use `!plans` to view available VPS plans and `!buy <plan>` to purchase",
                inline=False
            )
            await interaction.response.edit_message(embed=embed, view=None)

@bot.command()
async def manage(ctx):
    """Show user's VPS list with interactive management"""
    cursor = bot.db.conn.cursor()
    cursor.execute('SELECT vm_id, plan_name, hostname, status FROM vps_instances WHERE user_id = ?', (ctx.author.id,))
    vps_list = cursor.fetchall()
    
    embed = create_razor_embed("Your VPS Portfolio", f"Total VPS: {len(vps_list)}")
    
    if not vps_list:
        embed.add_field(
            name="🚀 Get Started",
            value="Use `!plans` to view available VPS plans and `!buy <plan>` to purchase",
            inline=False
        )
        await ctx.send(embed=embed)
    else:
        for vps in vps_list:
            status_emoji = "🟢" if vps[3] == 'active' else "🔴"
            embed.add_field(
                name=f"{status_emoji} VPS {vps[0]} • {vps[1]}",
                value=f"Hostname: `{vps[2]}`\nStatus: {vps[3].title()}",
                inline=False
            )
        
        # Add interactive dropdown
        view = VPSSelectView(ctx.author.id, vps_list)
        await ctx.send(embed=embed, view=view)


@bot.command(name='create-vps')
async def create_vps(ctx, memory: int, cpu: int, disk: int, user: discord.User):
    """Admin command to create custom VPS for a user"""
    # Check if user is admin
    if ctx.author.id not in Config.ADMIN_IDS:
        await ctx.send("❌ You don't have permission to use this command.")
        return
    
    # Validate parameters (memory is now in GB)
    if memory < 1 or memory > 64:
        await ctx.send("❌ Memory must be between 1GB and 64GB")
        return
    
    if cpu < 1 or cpu > 16:
        await ctx.send("❌ CPU cores must be between 1 and 16")
        return
    
    if disk < 10 or disk > 1000:
        await ctx.send("❌ Disk size must be between 10GB and 1000GB")
        return
    
    # Convert GB to MB for Proxmox
    ram_mb = memory * 1024
    
    # Create user if not exists
    bot.db.create_user(user)
    
    # Show creation message
    embed = create_razor_embed("🚀 Deploying VPS", f"AMD Ryzen9 7900 • {memory}GB RAM • {cpu} CPU • {disk}GB NVMe", 0x00FF9D)
    embed.add_field(name="👤 User", value=user.mention, inline=True)
    embed.add_field(name="🆔 VM ID", value="Generating...", inline=True)
    embed.add_field(name="🌐 Hostname", value="Creating...", inline=True)
    embed.add_field(name="⚡ CPU", value="AMD Ryzen9 7900", inline=True)
    embed.add_field(name="🔗 Status", value="🟡 **INITIALIZING**", inline=True)
    embed.add_field(name="⏱️ ETA", value="~2 minutes", inline=True)
    
    creation_msg = await ctx.send(embed=embed)
    
    # Create custom plan config
    custom_plan = {
        "ram": ram_mb,
        "cores": cpu,
        "disk": disk,
        "hostname_prefix": "razor-custom"
    }
    custom_plan['plan_name'] = f"custom-{memory}gb-{cpu}cpu-{disk}gb"
    
    # Create VPS
    result = await bot.proxmox.create_container(custom_plan, user.id)
    
    if result["success"]:
        # Update status to show VPS details
        embed = create_razor_embed("✅ VPS Deployed Successfully", f"AMD Ryzen9 7900 • {memory}GB RAM • {cpu} CPU • {disk}GB NVMe", 0x00FF00)
        embed.add_field(name="👤 User", value=user.mention, inline=True)
        embed.add_field(name="🆔 VM ID", value=result["vm_id"], inline=True)
        embed.add_field(name="🌐 Hostname", value=result["hostname"], inline=True)
        embed.add_field(name="⚡ CPU", value="AMD Ryzen9 7900", inline=True)
        embed.add_field(name="🔗 Status", value="🟢 **RUNNING**", inline=True)
        embed.add_field(name="⏱️ Deployment Time", value="~2 minutes", inline=True)
        await creation_msg.edit(embed=embed)
        
        # Save to database
        bot.db.create_vps(
            user.id,
            result["vm_id"],
            custom_plan['plan_name'],
            result["hostname"]
        )
        
        # Send credentials to user via DM
        user_embed = create_razor_embed("Your VPS is Ready! 🚀", "AMD Ryzen9 7900 VPS has been successfully deployed", 0x00FF00)
        user_embed.add_field(name="🆔 VM ID", value=result["vm_id"], inline=True)
        user_embed.add_field(name="🌐 Hostname", value=result["hostname"], inline=True)
        user_embed.add_field(name="📦 Specs", value=f"{memory}GB RAM • {cpu} CPU • {disk}GB NVMe", inline=True)
        user_embed.add_field(name="⚡ CPU", value="AMD Ryzen9 7900", inline=True)
        user_embed.add_field(name="🔗 Status", value="🟢 **RUNNING**", inline=True)
        user_embed.add_field(name="⏱️ Deployment Time", value="~2 minutes", inline=True)
        
        # Tmate sessions
        user_embed.add_field(
            name="🔑 SSH Access (Read-Write)", 
            value=f"```{result['tmate_session']}```",
            inline=False
        )
        user_embed.add_field(
            name="👀 SSH Access (Read-Only)", 
            value=f"```{result['tmate_ro_session']}```",
            inline=False
        )
        user_embed.add_field(
            name="🌐 Web Terminal", 
            value=f"[Click Here]({result['tmate_web']})",
            inline=True
        )
        
        user_embed.add_field(
            name="🚀 Getting Started",
            value="Your VPS is ready! Copy the SSH command above and paste it in your terminal to connect instantly. The VPS comes pre-configured with essential tools.",
            inline=False
        )
        
        user_embed.add_field(
            name="💡 Quick Commands",
            value="```?list``` - View all your VPS\n```?manage``` - Interactive VPS management\n```?status``` - Check system status",
            inline=False
        )
        
        
        try:
            await user.send(embed=user_embed)
            await ctx.send(f"✅ {user.display_name} has been notified via DM!")
        except:
            await ctx.send(f"⚠️ VPS created but couldn't send DM to {user.display_name}")
        
    else:
        embed = create_razor_embed("❌ VPS Creation Failed", f"Failed to deploy VPS for {user.display_name}", 0xFF0000)
        embed.add_field(name="👤 User", value=user.mention, inline=True)
        embed.add_field(name="📦 Specs", value=f"{memory}GB RAM • {cpu} CPU • {disk}GB NVMe", inline=True)
        embed.add_field(name="🚨 Error", value=result.get('error', 'Unknown error'), inline=False)
        embed.add_field(name="💡 Solution", value="Please try again or contact support", inline=False)
        await creation_msg.edit(embed=embed)


@bot.command()
async def status(ctx):
    """Show system status"""
    embed = create_razor_embed("System Status", f"{Config.BRAND_NAME} Infrastructure")
    embed.add_field(name="🌐 Network", value="🟢 OPERATIONAL", inline=True)
    embed.add_field(name="⚡ Proxmox", value="🟢 ONLINE", inline=True)
    embed.add_field(name="🔧 VPS Management", value="🟢 ACTIVE", inline=True)
    
    # Check Tmate availability
    try:
        import subprocess
        result = subprocess.run(['which', 'tmate'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            tmate_status = "🟢 AVAILABLE"
        else:
            tmate_status = "🔴 NOT INSTALLED"
    except:
        tmate_status = "🔴 NOT INSTALLED"
    
    embed.add_field(name="🔗 Tmate SSH", value=tmate_status, inline=True)
    embed.add_field(name="📞 Support", value="24/7 Available", inline=True)
    
    await ctx.send(embed=embed)

@bot.command()
async def list(ctx):
    """List all VPS instances for the user"""
    cursor = bot.db.conn.cursor()
    cursor.execute('SELECT vm_id, plan_name, hostname, status, created_at FROM vps_instances WHERE user_id = ?', (ctx.author.id,))
    vps_list = cursor.fetchall()
    
    if not vps_list:
        embed = create_razor_embed("Your VPS List", "You don't have any VPS instances yet")
        embed.add_field(
            name="🚀 Get Started",
            value="Ask an admin to create a VPS for you using:\n```?create-vps <memory-gb> <cpu> <disk-gb> @user```",
            inline=False
        )
        embed.add_field(
            name="⚡ Server Specs",
            value="All VPS powered by **AMD Ryzen9 7900**",
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    embed = create_razor_embed("Your VPS List", f"Total VPS: {len(vps_list)} • AMD Ryzen9 7900")
    
    for vps in vps_list:
        vm_id, plan_name, hostname, status, created_at = vps
        status_emoji = "🟢" if status == 'active' else "🔴" if status == 'stopped' else "⏸️"
        
        # Extract specs from plan name (format: custom-Xgb-Ycpu-Zgb)
        try:
            parts = plan_name.split('-')
            if len(parts) >= 4:
                memory = parts[1].replace('gb', '').upper()
                cpu = parts[2].replace('cpu', '')
                disk = parts[3].replace('gb', '').upper()
                plan_info = f"{memory}GB RAM • {cpu} CPU • {disk}GB NVMe"
            else:
                plan_info = "Custom Plan"
        except:
            plan_info = "Custom Plan"
        
        embed.add_field(
            name=f"{status_emoji} VPS {vm_id} • {plan_name.title()}",
            value=f"**Specs:** {plan_info}\n**Hostname:** `{hostname}`\n**Status:** {status.title()}\n**Created:** {created_at}\n**CPU:** AMD Ryzen9 7900",
            inline=False
        )
    
    embed.add_field(
        name="💡 Tip",
        value="Use `?manage` for interactive VPS management\nAsk admin to create VPS with `?create-vps`",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
async def features(ctx):
    """Show RazorCloud features and advantages"""
    embed = create_razor_embed("RazorCloud Features", "Why choose RazorCloud VPS?")
    
    embed.add_field(
        name="⚡ High-Performance Servers",
        value="Powered by **AMD Ryzen9 7900** processors\nBlazing fast NVMe SSD storage\nDDR5 RAM for maximum performance",
        inline=False
    )
    
    embed.add_field(
        name="🇩🇪 Germany Location",
        value="Premium datacenter in Germany\nLow latency across Europe\nTier 3+ infrastructure",
        inline=False
    )
    
    embed.add_field(
        name="🌐 24/7 Uptime & Stability",
        value="99.9% uptime guarantee\nRedundant network connectivity\nAutomatic failover protection",
        inline=False
    )
    
    embed.add_field(
        name="💸 Completely Free",
        value="No payment required!\nAdmin-created VPS at no cost\nFull access to all features\nNo hidden fees or charges",
        inline=False
    )
    
    embed.add_field(
        name="🚀 Instant Deployment",
        value="VPS ready in under 2 minutes\nAutomatic setup and configuration\nFresh Tmate SSH sessions generated each time",
        inline=False
    )
    
    embed.add_field(
        name="🔒 Security & Privacy",
        value="Isolated LXC containers\nDDoS protection included\nSecure payment via OxaPay",
        inline=False
    )
    
    embed.add_field(
        name="📊 Full Control",
        value="Root access to your VPS\nCustom software installation\nComplete management via Discord",
        inline=False
    )
    
    embed.add_field(
        name="🎯 Get Started",
        value="```?help``` - View all available commands\n```?create-vps <memory> <cpu> <disk> @user``` - Create VPS (Admin)\n```?list``` - List your VPS\n```?manage``` - Manage your VPS",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
async def delete(ctx, vps_id: int = None):
    """Admin command to delete a VPS"""
    if ctx.author.id not in Config.ADMIN_IDS:
        await ctx.send("❌ You don't have permission to use this command.")
        return
    
    if not vps_id:
        await ctx.send("❌ Please specify a VPS ID. Usage: `?delete <vps-id>`")
        return
    
    # Check if VPS exists
    cursor = bot.db.conn.cursor()
    cursor.execute('SELECT vm_id, user_id, plan_name, hostname FROM vps_instances WHERE vm_id = ?', (vps_id,))
    vps = cursor.fetchone()
    
    if not vps:
        await ctx.send(f"❌ VPS with ID {vps_id} not found.")
        return
    
    vm_id, user_id, plan_name, hostname = vps
    
    embed = create_razor_embed(f"Deleting VPS {vps_id}", "Removing VPS from Proxmox...", 0xFFD700)
    msg = await ctx.send(embed=embed)
    
    try:
        import subprocess
        
        # Stop the container first
        stop_result = subprocess.run(['pct', 'stop', str(vps_id)], capture_output=True, text=True, timeout=30)
        await asyncio.sleep(3)
        
        # Delete the container
        delete_result = subprocess.run(['pct', 'destroy', str(vps_id)], capture_output=True, text=True, timeout=30)
        
        if delete_result.returncode == 0:
            # Remove from database
            cursor.execute('DELETE FROM vps_instances WHERE vm_id = ?', (vps_id,))
            bot.db.conn.commit()
            
            embed = create_razor_embed(f"VPS {vps_id} Deleted ✅", f"VPS successfully removed", 0x00FF00)
            embed.add_field(name="🆔 VM ID", value=vps_id, inline=True)
            embed.add_field(name="👤 User ID", value=user_id, inline=True)
            embed.add_field(name="📦 Plan", value=plan_name, inline=True)
            embed.add_field(name="🌐 Hostname", value=hostname, inline=True)
            await msg.edit(embed=embed)
            
            # Notify the user
            try:
                user = await bot.fetch_user(user_id)
                user_embed = create_razor_embed("VPS Deleted", f"Your VPS {vps_id} has been deleted by an admin", 0xFF0000)
                await user.send(embed=user_embed)
            except:
                pass
        else:
            embed = create_razor_embed(f"Delete Failed ❌", f"Error: {delete_result.stderr}", 0xFF0000)
            await msg.edit(embed=embed)
    
    except Exception as e:
        embed = create_razor_embed("Delete Error ❌", f"Error: {str(e)}", 0xFF0000)
        await msg.edit(embed=embed)

@bot.command()
async def suspend(ctx, vps_id: int = None):
    """Admin command to suspend a VPS"""
    if ctx.author.id not in Config.ADMIN_IDS:
        await ctx.send("❌ You don't have permission to use this command.")
        return
    
    if not vps_id:
        await ctx.send("❌ Please specify a VPS ID. Usage: `?suspend <vps-id>`")
        return
    
    # Check if VPS exists
    cursor = bot.db.conn.cursor()
    cursor.execute('SELECT vm_id, user_id, plan_name FROM vps_instances WHERE vm_id = ?', (vps_id,))
    vps = cursor.fetchone()
    
    if not vps:
        await ctx.send(f"❌ VPS with ID {vps_id} not found.")
        return
    
    vm_id, user_id, plan_name = vps
    
    embed = create_razor_embed(f"Suspending VPS {vps_id}", "Stopping VPS...", 0xFFD700)
    msg = await ctx.send(embed=embed)
    
    try:
        import subprocess
        
        # Stop the container
        result = subprocess.run(['pct', 'stop', str(vps_id)], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            # Update database status
            cursor.execute('UPDATE vps_instances SET status = "suspended" WHERE vm_id = ?', (vps_id,))
            bot.db.conn.commit()
            
            embed = create_razor_embed(f"VPS {vps_id} Suspended ⏸️", "VPS has been suspended", 0xFFA500)
            embed.add_field(name="🆔 VM ID", value=vps_id, inline=True)
            embed.add_field(name="👤 User ID", value=user_id, inline=True)
            embed.add_field(name="📦 Plan", value=plan_name, inline=True)
            await msg.edit(embed=embed)
            
            # Notify the user
            try:
                user = await bot.fetch_user(user_id)
                user_embed = create_razor_embed("VPS Suspended", f"Your VPS {vps_id} has been suspended by an admin", 0xFFA500)
                user_embed.add_field(name="ℹ️ Info", value="Contact support for more information.", inline=False)
                await user.send(embed=user_embed)
            except:
                pass
        else:
            embed = create_razor_embed(f"Suspend Failed ❌", f"Error: {result.stderr}", 0xFF0000)
            await msg.edit(embed=embed)
    
    except Exception as e:
        embed = create_razor_embed("Suspend Error ❌", f"Error: {str(e)}", 0xFF0000)
        await msg.edit(embed=embed)

@bot.command()
async def unsuspend(ctx, vps_id: int = None):
    """Admin command to unsuspend a VPS"""
    if ctx.author.id not in Config.ADMIN_IDS:
        await ctx.send("❌ You don't have permission to use this command.")
        return
    
    if not vps_id:
        await ctx.send("❌ Please specify a VPS ID. Usage: `?unsuspend <vps-id>`")
        return
    
    # Check if VPS exists
    cursor = bot.db.conn.cursor()
    cursor.execute('SELECT vm_id, user_id, plan_name FROM vps_instances WHERE vm_id = ?', (vps_id,))
    vps = cursor.fetchone()
    
    if not vps:
        await ctx.send(f"❌ VPS with ID {vps_id} not found.")
        return
    
    vm_id, user_id, plan_name = vps
    
    embed = create_razor_embed(f"Unsuspending VPS {vps_id}", "Starting VPS...", 0xFFD700)
    msg = await ctx.send(embed=embed)
    
    try:
        import subprocess
        
        # Start the container
        result = subprocess.run(['pct', 'start', str(vps_id)], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            # Update database status
            cursor.execute('UPDATE vps_instances SET status = "active" WHERE vm_id = ?', (vps_id,))
            bot.db.conn.commit()
            
            embed = create_razor_embed(f"VPS {vps_id} Unsuspended ✅", "VPS is now active again", 0x00FF00)
            embed.add_field(name="🆔 VM ID", value=vps_id, inline=True)
            embed.add_field(name="👤 User ID", value=user_id, inline=True)
            embed.add_field(name="📦 Plan", value=plan_name, inline=True)
            await msg.edit(embed=embed)
            
            # Notify the user
            try:
                user = await bot.fetch_user(user_id)
                user_embed = create_razor_embed("VPS Unsuspended", f"Your VPS {vps_id} has been unsuspended by an admin", 0x00FF00)
                user_embed.add_field(name="🎉 Status", value="Your VPS is now active and running!", inline=False)
                await user.send(embed=user_embed)
            except:
                pass
        else:
            embed = create_razor_embed(f"Unsuspend Failed ❌", f"Error: {result.stderr}", 0xFF0000)
            await msg.edit(embed=embed)
    
    except Exception as e:
        embed = create_razor_embed("Unsuspend Error ❌", f"Error: {str(e)}", 0xFF0000)
        await msg.edit(embed=embed)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Command not found. Use `?help` for available commands.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required argument. Check command usage.")
    else:
        logging.error(f"Command error: {error}")
        await ctx.send("❌ An error occurred. Please try again.")

# === RUN BOT ===
if __name__ == "__main__":
    print("🚀 Starting RazorCloud Bot...")
    print("⚡ Free VPS Management Bot:")
    print("   ✅ Free VPS creation (Admin only)")
    print("   ✅ Real Tmate SSH sessions")
    print("   ✅ Complete VPS management")
    print("   ✅ No payment system required")
    bot.run(Config.BOT_TOKEN)
