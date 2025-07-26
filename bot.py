import time
import random
import requests
import json
import concurrent.futures
from web3 import Web3
from web3.exceptions import TransactionNotFound
from eth_account import Account
from eth_account.messages import encode_defunct
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from rich.live import Live
from rich.table import Table
from datetime import datetime, timedelta

ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"success","type":"bool"}],"payable":false,"stateMutability":"nonpayable","type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"remaining","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"}]')

class Config:
    PRIVATE_KEY_FILE = "privatekey.txt"
    RPC_URL = "https://testnet.dplabs-internal.com/"
    EXPLORER_URL = "https://pharos-testnet.socialscan.io"

    class Login:
        SIGN_MESSAGE = "pharos"
        WALLET_NAME = "OKX Wallet"

    class Swap:
        ENABLED = True
        ROUTER_ADDRESS = "0x1a4de519154ae51200b0ad7c90f7fac75547888a"
        AMOUNT_TO_SWAP_EACH = 0.0001 

        WPHRS_ADDRESS = "0x76aaada469d23216be5f7c596fa25f282ff9b364"

        TARGET_TOKENS = [
            "0x72df0bcd7276f2dfbac900d1ce63c272c4bccced",
            "0xd4071393f8716661958f766df660033b3d35fd29"
        ]
        FEE_TIER = 3000
        DEADLINE_MINUTES = 20

    class Liquidity:
        ENABLED = True
        POSITION_MANAGER_ADDRESS = "0xf8a1d4ff0f9b9af7ce58e1fc1833688f3bfd6115"
        AMOUNT_TO_ADD_PHRS = 0.0001

        TOKEN_IDS = {
            "0x72df0bcd7276f2dfbac900d1ce63c272c4bccced": 1234,
            "0xd4071393f8716661958f766df660033b3d35fd29": 501381747380774316
        }

    class FaroSwap:
        ENABLED = True
        ROUTER_ADDRESS = "0x3541423f25A1Ca5C98fdBCf478405d3f0aaD1164"
        USDT_ADDRESS = "0xD4071393f8716661958F766DF660033b3d35fD29"
        AMOUNT_TO_SWAP = 0.0001
        DELAY_AFTER_FAROSWAP = (10, 20)

    class Timers:
        DELAY_BETWEEN_SWAPS = (10, 25)
        DELAY_BETWEEN_ITERATIONS = (45, 90)
        DELAY_BEFORE_LIQUIDITY = (15, 30)
        DELAY_FOR_NEXT_RUN = 24 * 60 * 60

    BASE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "Referer": "https://testnet.pharosnetwork.xyz/"
    }

console = Console()

def generate_random_amount(max_amount):
    min_allowed_amount = 0.00001
    actual_min = min(min_allowed_amount, max_amount)
    return random.uniform(actual_min, max_amount)

def load_json_file(file_path):
    try:
        with open(file_path, 'r') as f: return json.load(f)
    except FileNotFoundError:
        console.print(Panel(f"[bold red]❌ ERROR:[/bold red] File '[italic yellow]{file_path}[/italic yellow]' not found.", title="[bold red]File Error[/bold red]", border_style="red"))
        return None
    except json.JSONDecodeError:
        console.print(Panel(f"[bold red]❌ ERROR:[/bold red] File '[italic yellow]{file_path}[/italic yellow]' is not valid JSON.", title="[bold red]File Error[/bold red]", border_style="red"))
        return None

def get_token_balance(w3, token_address, owner_address):
    try:
        token_contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
        balance = token_contract.functions.balanceOf(owner_address).call()
        return balance
    except Exception as e:
        console.print(f"[red]   [ERROR] Failed to get token balance {token_address[:10]}...: {e}[/red]")
        return 0

def wait_for_transaction(w3, tx_hash, account_address, action_name):
    console.print(f"[yellow]     [WAITING] ({account_address[:6]}) Waiting for {action_name} confirmation...[/yellow]")
    try:
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        return tx_receipt
    except TransactionNotFound:
        console.print(f"[bold red]     [FAILED] ({account_address[:6]}) ❌ {action_name} transaction not found (may have been canceled or never arrived).[/bold red]")
        return None
    except Exception as e:
        console.print(f"[bold red]     [ERROR] ({account_address[:6]}) ❌ Error while waiting for {action_name} transaction: {e}[/bold red]")
        return None

def approve_token(account, w3, token_address, spender_address, amount_to_approve):
    if account is None or not hasattr(account, 'address'):
        console.print("[bold red]     [ERROR] 'account' object is invalid or None in approve_token. Skipping approval.[/bold red]")
        return False

    console.print(f"[cyan]  [APPROVAL] ({account.address[:6]}) Giving permission to {spender_address[:10]}... for token {token_address[:10]}...[/cyan]")
    token_address_checksum = Web3.to_checksum_address(token_address)
    spender_address_checksum = Web3.to_checksum_address(spender_address)
    token_contract = w3.eth.contract(address=token_address_checksum, abi=ERC20_ABI)

    try:
        _account_address = account.address
        console.print(f"[DEBUG] Inside approve_token, account address: {_account_address[:6]}")

        allowance = token_contract.functions.allowance(_account_address, spender_address_checksum).call()
        if allowance >= amount_to_approve:
            console.print(f"[green]     [SUCCESS] ({_account_address[:6]}) ✅ Allowance is sufficient. Skipping approval.[/green]")
            return True

        tx_params = {'from': _account_address, 'gas': 100000, 'nonce': w3.eth.get_transaction_count(_account_address), 'gasPrice': w3.eth.gas_price}
        approve_tx = token_contract.functions.approve(spender_address_checksum, amount_to_approve).build_transaction(tx_params)
        signed_tx = account.sign_transaction(approve_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]     [SENT] ({_account_address[:6]}) 📤 Approval transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")
        
        tx_receipt = wait_for_transaction(w3, tx_hash, _account_address, "approval")

        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]     [SUCCESS] ({_account_address[:6]}) ✅ Approval Successful![/bold green]")
            return True
        else:
            console.print(f"[bold red]     [FAILED] ({_account_address[:6]}) ❌ Approval Failed! Transaction Reverted or Error.[/bold red]")
            return False
    except Exception as e:
        display_address = _account_address[:6] if '_account_address' in locals() else 'N/A'
        console.print(f"[bold red]     [ERROR] ({display_address}) ❌ An error occurred during the approval process: {e}[/bold red]")
        return False

def perform_swap_v3(account, w3, dex_abi, router_address_str, token_in, token_out, amount_in_wei, is_from_native):
    if account is None or not hasattr(account, 'address'):
        console.print("[bold red]     [ERROR] 'account' object is invalid or None in perform_swap_v3. Skipping swap.[/bold red]")
        return False

    try:
        _account_address = account.address
        console.print(f"[DEBUG] Inside perform_swap_v3, account address: {_account_address[:6]}")

        router_address = Web3.to_checksum_address(router_address_str)
        dex_router_contract = w3.eth.contract(address=router_address, abi=dex_abi)

        params = {
            'tokenIn': Web3.to_checksum_address(token_in),
            'tokenOut': Web3.to_checksum_address(token_out),
            'fee': Config.Swap.FEE_TIER,
            'recipient': _account_address,
            'deadline': int(time.time()) + 60 * Config.Swap.DEADLINE_MINUTES,
            'amountIn': amount_in_wei,
            'amountOutMinimum': 0,
            'sqrtPriceLimitX96': 0
        }

        tx_params = {'from': _account_address, 'gas': 400000, 'nonce': w3.eth.get_transaction_count(_account_address), 'gasPrice': w3.eth.gas_price}
        if is_from_native:
            tx_params['value'] = amount_in_wei

        swap_tx = dex_router_contract.functions.exactInputSingle(params).build_transaction(tx_params)
        signed_tx = account.sign_transaction(swap_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]     [SENT] ({_account_address[:6]}) 📤 Swap transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")
        
        tx_receipt = wait_for_transaction(w3, tx_hash, _account_address, "swap")

        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]     [SUCCESS] ({_account_address[:6]}) ✅ Swap Successful![/bold green]")
            return True
        else:
            console.print(f"[bold red]     [FAILED] ({_account_address[:6]}) ❌ Swap Failed! Transaction Reverted or Error.[/bold red]")
            return False
    except Exception as e:
        display_address = _account_address[:6] if '_account_address' in locals() else 'N/A'
        console.print(f"[bold red]     [ERROR] ({display_address}) ❌ An error occurred during the swap process: {e}[/bold red]")
        return False

def perform_increase_liquidity(account, w3, dex_abi, token_id, token_a, token_b, amount_a_wei, amount_b_wei):
    if account is None or not hasattr(account, 'address'):
        console.print("[bold red]     [ERROR] 'account' object is invalid or None in perform_increase_liquidity. Skipping liquidity addition.[/bold red]")
        return False

    position_manager_address = Web3.to_checksum_address(Config.Liquidity.POSITION_MANAGER_ADDRESS)
    pm_contract = w3.eth.contract(address=position_manager_address, abi=dex_abi)
    amount0, amount1 = (amount_a_wei, amount_b_wei) if token_a.lower() < token_b.lower() else (amount_b_wei, amount_a_wei)

    params = {
        'tokenId': token_id,
        'amount0Desired': amount0,
        'amount1Desired': amount1,
        'amount0Min': 0,
        'amount1Min': 0,
        'deadline': int(time.time()) + 60 * Config.Swap.DEADLINE_MINUTES
    }

    try:
        _account_address = account.address
        console.print(f"[DEBUG] Inside perform_increase_liquidity, account address: {_account_address[:6]}")

        tx_params = {'from': _account_address, 'gas': 800000, 'nonce': w3.eth.get_transaction_count(_account_address), 'gasPrice': w3.eth.gas_price}
        increase_tx = pm_contract.functions.increaseLiquidity(params).build_transaction(tx_params)
        signed_tx = account.sign_transaction(increase_tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        console.print(f"[yellow]     [SENT] ({_account_address[:6]}) 📤 'increaseLiquidity' transaction sent! Hash: {tx_hash.hex()[:15]}...[/yellow]")

        tx_receipt = wait_for_transaction(w3, tx_hash, _account_address, "'increaseLiquidity'")

        if tx_receipt and tx_receipt['status'] == 1:
            console.print(f"[bold green]     [SUCCESS] ({_account_address[:6]}) ✅ Liquidity Added Successfully![/bold green]")
            return True
        else:
            console.print(f"[bold red]     [FAILED] ({_account_address[:6]}) ❌ Failed to Add Liquidity! Transaction Reverted or Error.[/bold red]")
            return False
    except Exception as e:
        display_address = _account_address[:6] if '_account_address' in locals() else 'N/A'
        console.print(f"[bold red]     [ERROR] ({display_address}) ❌ An error occurred during 'increaseLiquidity': {e}[/bold red]")
        return False

def process_account(private_key, index, total, w3, dex_abi, loop_count):
    account_address = ""
    try:
        account = Account.from_key(private_key)
        account_address = account.address
        if not account or not hasattr(account, 'address'):
            console.print(Panel(f"[bold red]❌ Account object for [Account {index}] is invalid after creation. Skipping.[/bold red]", title="[bold red]Account Error[/bold red]", border_style="red"))
            return
    except Exception as e:
        console.print(Panel(f"[bold red]❌ Private key [Account {index}] is invalid or account creation failed: {e}. Skipping.[/bold red]", title="[bold red]Account Error[/bold red]", border_style="red"))
        return
    
    console.print(Rule(f"[bold blue]Processing Account {index}/{total} | Address: {account.address[:10]}...[/bold blue]", style="blue"))

    jwt_token = perform_login(account)
    if not jwt_token:
        return
    time.sleep(2)
    perform_daily_signin(account.address, jwt_token)
    
    for i in range(loop_count):
        console.print(Panel(f"[bold magenta]🚀 Swap & Transfer Iteration {i + 1}/{loop_count} for {account.address[:10]}... 🚀[/bold magenta]", title_align="center", border_style="magenta"))
        
        if Config.Swap.ENABLED:
            random_amount = generate_random_amount(Config.Swap.AMOUNT_TO_SWAP_EACH)
            amount_to_swap_wei = w3.to_wei(random_amount, 'ether')
            console.print(f"[dim]  ({account.address[:6]}) Random Zenith swap amount: {random_amount:.8f} PHRS[/dim]")

            for target_token in Config.Swap.TARGET_TOKENS:
                console.print(f"[cyan]  [ZENITH SWAP] ({account.address[:6]}) Round Trip for Token {target_token[:10]}...[/cyan]")
                swap1_success = perform_swap_v3(account, w3, dex_abi, Config.Swap.ROUTER_ADDRESS, Config.Swap.WPHRS_ADDRESS, target_token, amount_to_swap_wei, True)
                if not swap1_success:
                    continue
                time.sleep(random.uniform(*Config.Timers.DELAY_BETWEEN_SWAPS))

                balance_to_swap_back = get_token_balance(w3, target_token, account.address)
                if balance_to_swap_back == 0:
                    console.print(f"[yellow]     [SKIP] ({account.address[:6]}) No balance of {target_token[:10]}... to swap back. Skipping.[/yellow]")
                    continue
                
                approval_success = approve_token(account, w3, target_token, Config.Swap.ROUTER_ADDRESS, balance_to_swap_back)
                if not approval_success:
                    continue
                time.sleep(random.uniform(*Config.Timers.DELAY_BETWEEN_SWAPS))

                perform_swap_v3(account, w3, dex_abi, Config.Swap.ROUTER_ADDRESS, target_token, Config.Swap.WPHRS_ADDRESS, balance_to_swap_back, False)
                time.sleep(random.uniform(5, 10))
        
        if Config.FaroSwap.ENABLED:
            console.print(Rule(f"[bold blue]({account.address[:6]}) FaroSwap: PHRS -> USDT[/bold blue]", style="blue"))
            random_amount_faro = generate_random_amount(Config.FaroSwap.AMOUNT_TO_SWAP)
            amount_to_swap_wei_faro = w3.to_wei(random_amount_faro, 'ether')
            console.print(f"[dim]  ({account.address[:6]}) Random FaroSwap amount: {random_amount_faro:.8f} PHRS[/dim]")
            perform_swap_v3(account, w3, dex_abi, Config.FaroSwap.ROUTER_ADDRESS, Config.Swap.WPHRS_ADDRESS, Config.FaroSwap.USDT_ADDRESS, amount_to_swap_wei_faro, True)
            time.sleep(random.uniform(*Config.FaroSwap.DELAY_AFTER_FAROSWAP))
        
        if i < loop_count - 1:
            delay = random.uniform(*Config.Timers.DELAY_BETWEEN_ITERATIONS)
            console.print(f"[dim]  ({account.address[:6]}) Iteration delay for {delay:.1f} seconds...[/dim]")
            time.sleep(delay)

    if Config.Liquidity.ENABLED:
        console.print(Panel(f"[bold green]Adding Zenith Liquidity (1x) for {account.address[:10]}...[/bold green]", title_align="center", border_style="green"))
        time.sleep(random.uniform(*Config.Timers.DELAY_BEFORE_LIQUIDITY))

        if not Config.Swap.TARGET_TOKENS:
            console.print(f"[yellow]  ({account.address[:6]}) No target tokens to add liquidity.[/yellow]")
        else:
            token_to_add_liquidity_for = Config.Swap.TARGET_TOKENS[0]
            token_id_for_pair = Config.Liquidity.TOKEN_IDS.get(token_to_add_liquidity_for.lower())

            if not token_id_for_pair or token_id_for_pair == 0:
                console.print(f"[bold red]  [FAILED] ({account.address[:6]}) ❌ Token ID for pair {token_to_add_liquidity_for[:10]} not found.[/bold red]")
            else:
                amount_phrs_lp_wei = w3.to_wei(Config.Liquidity.AMOUNT_TO_ADD_PHRS, 'ether')
                initial_target_balance = get_token_balance(w3, token_to_add_liquidity_for, account.address)
                swap_for_lp_success = perform_swap_v3(account, w3, dex_abi, Config.Swap.ROUTER_ADDRESS, Config.Swap.WPHRS_ADDRESS, token_to_add_liquidity_for, amount_phrs_lp_wei, True)

                if swap_for_lp_success:
                    time.sleep(random.uniform(*Config.Timers.DELAY_BETWEEN_SWAPS))
                    amount_target_token_wei = get_token_balance(w3, token_to_add_liquidity_for, account.address) - initial_target_balance
                    
                    if amount_target_token_wei > 0:
                        approve_wphrs_success = approve_token(account, w3, Config.Swap.WPHRS_ADDRESS, Config.Liquidity.POSITION_MANAGER_ADDRESS, amount_phrs_lp_wei)
                        time.sleep(random.uniform(3, 6))
                        approve_target_success = approve_token(account, w3, token_to_add_liquidity_for, Config.Liquidity.POSITION_MANAGER_ADDRESS, amount_target_token_wei)

                        if approve_wphrs_success and approve_target_success:
                            time.sleep(random.uniform(*Config.Timers.DELAY_BETWEEN_SWAPS))
                            perform_increase_liquidity(account, w3, dex_abi, token_id_for_pair, Config.Swap.WPHRS_ADDRESS, token_to_add_liquidity_for, amount_phrs_lp_wei, amount_target_token_wei)
                    else:
                        console.print(f"[yellow]  [SKIP] ({account.address[:6]}) No target token received for liquidity. Skipping.[/yellow]")
                else:
                    console.print(f"[bold red]  [FAILED] ({account.address[:6]}) ❌ Initial swap for liquidity failed. Skipping liquidity addition.[/bold red]")


def main():
    console.print(Panel("[bold yellow]PHAROS NETWORK BOT - EARNINGDROP[/bold yellow]", title_align="center", border_style="magenta"))
    private_keys = load_private_keys(Config.PRIVATE_KEY_FILE)
    dex_abi = load_json_file('abi.json')
    if not private_keys or not dex_abi:
        console.print(Panel("[bold red]Bot stopped. Important files are missing/invalid.[/bold red]", title="[bold red]Initialization Error[/bold red]", border_style="red"))
        return
        
    w3 = Web3(Web3.HTTPProvider(Config.RPC_URL))
    if not w3.is_connected():
        console.print(Panel(f"[bold red]Failed to connect to RPC Node at {Config.RPC_URL}[/bold red]", title="[bold red]Connection Error[/bold red]", border_style="red"))
        return
    console.print(Panel(f"[bold green]Connected to RPC Node. Chain ID: {w3.eth.chain_id}[/bold green]", title="[bold green]RPC Status[/bold green]", border_style="green"))

    while True:
        try:
            loop_count_str = console.input("[bold yellow]❓ Enter the number of swap loops per account: [/bold yellow]")
            loop_count = int(loop_count_str)
            if loop_count > 0: break
            else: console.print("[red]Number of loops must be greater than 0.[/red]")
        except ValueError:
            console.print("[red]Invalid input. Please enter a number.[/red]")
    
    MAX_THREADS = 5
    console.print(Panel(f"[green]✅ OK! Each account will perform {loop_count} swap iterations.[/green]\n[bold blue]🚀 The bot will run with {MAX_THREADS} threads in parallel.[/bold blue]", title="[bold cyan]Bot Configuration[/bold cyan]", border_style="cyan"))

    run_count = 0
    while True:
        run_count += 1
        console.print(Rule(f"[bold green]🚀 Starting Global Cycle #{run_count} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 🚀[/bold green]", style="green"))
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            future_to_account = {
                executor.submit(process_account, pk, i + 1, len(private_keys), w3, dex_abi, loop_count): f"Account {i+1}"
                for i, pk in enumerate(private_keys)
            }

            for future in concurrent.futures.as_completed(future_to_account):
                account_info = future_to_account[future]
                try:
                    future.result()
                except Exception as exc:
                    console.print(Panel(f"[bold red]❌ {account_info} generated an unexpected error: {exc}[/bold red]", title="[bold red]Thread Error[/bold red]", border_style="red"))
        
        console.print(Rule("[bold green]✅ All Accounts Have Finished Processing for This Cycle[/bold green]", style="green"))
        run_countdown(duration_seconds=Config.Timers.DELAY_FOR_NEXT_RUN)

def load_private_keys(file_path):
    try:
        with open(file_path, 'r') as f:
            keys = [line.strip() for line in f if line.strip()]
            if not keys:
                console.print(Panel(f"[bold red]❌ ERROR:[/bold red] File '[italic yellow]{file_path}[/italic yellow]' is empty.", title="[bold red]File Error[/bold red]", border_style="red"))
                return None
            console.print(Panel(f"[bold green]✅ Successfully loaded {len(keys)} private keys.[/bold green]", title="[bold green]Key Load Status[/bold green]", border_style="green"))
            return keys
    except FileNotFoundError:
        console.print(Panel(f"[bold red]❌ ERROR:[/bold red] File '[italic yellow]{file_path}[/italic yellow]' not found.", title="[bold red]File Error[/bold red]", border_style="red"))
        return None

def perform_login(account):
    console.print(f"[cyan]  [LOGIN] ({account.address[:6]}) Starting Login Process...[/cyan]")
    login_url = "https://api.pharosnetwork.xyz/user/login"
    message_to_sign = encode_defunct(text=Config.Login.SIGN_MESSAGE)
    signed_message = account.sign_message(message_to_sign)
    
    params = {"address": account.address, "signature": signed_message.signature.hex(), "wallet": Config.Login.WALLET_NAME}
    
    headers = Config.BASE_HEADERS.copy()
    headers["Authorization"] = "Bearer null"
    max_retries = 10
    retry_delay = 10
    for attempt in range(max_retries):
        try:
            response = requests.post(login_url, params=params, headers=headers, timeout=20)
            try: response_data = response.json()
            except json.JSONDecodeError:
                console.print(f"[red]  [FAILED] ({account.address[:6]}) ❌ Login Failed. The server provided an invalid response. Status Code: {response.status_code}[/red]")
                time.sleep(retry_delay)
                continue
            if response.status_code == 200 and response_data.get("code") == 0:
                console.print(f"[green]  [SUCCESS] ({account.address[:6]}) ✅ Login successful.[/green]")
                return response_data.get("data", {}).get("jwt")
            server_message = response_data.get("msg", "")
            if "1040" in server_message or "too many connections" in server_message.lower():
                console.print(f"[yellow]  [RETRY] ({account.address[:6]}) ⚠️ Login Failed (Too many connections). Retrying...[/yellow]")
                time.sleep(retry_delay)
            else:
                console.print(f"[red]  [FAILED] ({account.address[:6]}) ❌ Login Failed. Server: '{server_message}'.[/red]")
                return None
        except requests.exceptions.RequestException as e:
            console.print(f"[red]  [ERROR] ({account.address[:6]}) ❌ Failed to connect to the login server: {e}[/red]")
            time.sleep(retry_delay)
    console.print(Panel(f"[bold red]({account.address[:6]}) ❌ Login Failed after {max_retries} attempts. Skipping this account.[/bold red]", title="[bold red]Login Failure[/bold red]", border_style="red"))
    return None

def perform_daily_signin(address, jwt_token):
    console.print(f"[cyan]  [CHECK-IN] ({address[:6]}) Starting Daily Check-in Process...[/cyan]")
    signin_url = f"https://api.pharosnetwork.xyz/sign/in?address={address}"
    headers = Config.BASE_HEADERS.copy()
    headers["Authorization"] = f"Bearer {jwt_token}"
    try:
        response = requests.post(signin_url, headers=headers, timeout=20)
        response_data = response.json()
        if response.status_code == 200 and response_data.get("code") == 0:
            console.print(f"[bold green]  [SUCCESS] ({address[:6]}) ✅ Daily Check-in Successful![/bold green]")
            return True
        msg = response_data.get("msg", "Error")
        if "already" in msg.lower():
            console.print(f"[yellow]  [INFO] ({address[:6]}) ℹ️  Already checked in today.[/yellow]")
            return True
        console.print(f"[red]  [FAILED] ({address[:6]}) ❌ Check-in Failed. Message: '{msg}'[/red]")
        return False
    except requests.exceptions.RequestException as e:
        console.print(f"[red]  [ERROR] ({address[:6]}) ❌ Failed to connect to the check-in server: {e}[/red]")
        return False

def run_countdown(duration_seconds):
    end_time = datetime.now() + timedelta(seconds=duration_seconds)
    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while datetime.now() < end_time:
            remaining = end_time - datetime.now()
            total_seconds = int(remaining.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            countdown_text = Text(f"Next cycle: {hours:02}:{minutes:02}:{seconds:02}", style="bold cyan")
            live.update(Panel(countdown_text, title="[bold green]💤 Break Time[/bold green]", border_style="green", expand=False))
            time.sleep(1)

if __name__ == "__main__":
    main()
