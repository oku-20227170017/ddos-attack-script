#!/usr/bin/env python3
import asyncio
import aiohttp
import socket
import ssl
import argparse
import time
from urllib.parse import urlparse

DEFAULT_CONCURRENCY = 600
DEFAULT_DURATION = 15

def now_ts():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

class KillSwitch(Exception):
    pass

# ---------- SCENARIOS ----------
async def slow_read(session: aiohttp.ClientSession, url: str, duration: int, read_chunk: int = 64, delay_between_chunks: float = 0.5):
    while True:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=None)) as resp:
                print(f"{now_ts()} [slow_read] status={resp.status} url={url}")
                async for chunk, _ in resp.content.iter_chunks():
                    if not chunk:
                        await asyncio.sleep(0.01)
                    else:
                        await asyncio.sleep(delay_between_chunks)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"{now_ts()} [slow_read] exception: {e}")
            await asyncio.sleep(1)

async def idle_keepalive(target_host: str, target_port: int, duration: int):
    start = time.time()
    reader, writer = None, None
    try:
        reader, writer = await asyncio.open_connection(host=target_host, port=target_port)
        req = f"GET / HTTP/1.1\r\nHost: {target_host}\r\nConnection: keep-alive\r\nUser-Agent: lab-slow-client\r\n\r\n"
        writer.write(req.encode())
        await writer.drain()
        print(f"{now_ts()} [idle_keepalive] opened and sent minimal GET to {target_host}:{target_port}")
        while time.time() - start < duration:
            await asyncio.sleep(1)
        print(f"{now_ts()} [idle_keepalive] duration reached, closing connection.")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"{now_ts()} [idle_keepalive] exception: {e}")
    finally:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

async def dns_query_delay(name: str, duration: int, slow_process_chunk_count: int = 5):
    try:
        print(f"{now_ts()} [dns_query_delay] resolving {name}")
        loop = asyncio.get_event_loop()
        addrs = await loop.run_in_executor(None, socket.getaddrinfo, name, None)
        print(f"{now_ts()} [dns_query_delay] resolved, processing slowly...")
        parts = [repr(addrs[i::slow_process_chunk_count]) for i in range(slow_process_chunk_count)]
        for p in parts:
            print(f"{now_ts()} [dns_query_delay] part: {p[:120]}...")
            await asyncio.sleep(duration / max(1, slow_process_chunk_count))
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"{now_ts()} [dns_query_delay] exception: {e}")

async def tls_handshake_delay(target_host: str, target_port: int, duration: int, step_delay: float = 0.5):
    start = time.time()
    ctx = ssl.create_default_context()
    try:
        reader, writer = await asyncio.open_connection(target_host, target_port, ssl=ctx)
        print(f"{now_ts()} [tls_handshake_delay] TLS connection established to {target_host}:{target_port}")
        elapsed = 0.0
        while time.time() - start < duration:
            await asyncio.sleep(step_delay)
            elapsed += step_delay
            if elapsed > duration:
                break
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"{now_ts()} [tls_handshake_delay] exception: {e}")

# ---------- Orchestration ----------
async def run_scenario(args, sem):
    async with sem:
        if args.scenario == "slow_read":
            conn = aiohttp.TCPConnector(limit_per_host=1, ssl=False)
            async with aiohttp.ClientSession(connector=conn) as session:
                await slow_read(session, args.url, args.duration, read_chunk=64, delay_between_chunks=args.delay)
        elif args.scenario == "idle_keepalive":
            await idle_keepalive(args.host, args.port, args.duration)
        elif args.scenario == "dns_query_delay":
            await dns_query_delay(args.host, args.duration, slow_process_chunk_count=5)
        elif args.scenario == "tls_handshake_delay":
            await tls_handshake_delay(args.host, args.port, args.duration, step_delay=args.delay)
        else:
            print("[ERROR] Unknown scenario")

async def main_async(args):
    sem = asyncio.Semaphore(args.concurrency)
    tasks = []
    print(f"{now_ts()} [main] starting scenario {args.scenario} target={args.host}:{args.port} concurrency={args.concurrency}")
    print(f"{now_ts()} [main] Test will run for {args.duration} seconds.")

    for _ in range(args.concurrency):
        tasks.append(asyncio.create_task(run_scenario(args, sem)))

    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=args.duration)
    except asyncio.TimeoutError:
        print(f"{now_ts()} [main] {args.duration}s duration reached. Stopping all tasks.")
    except asyncio.CancelledError:
        print(f"{now_ts()} [main] Main task was cancelled.")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

# ---------- SSL Detection ----------
def check_ssl_support(host, port=443):
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=3) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                return True
    except Exception:
        return False

# ---------- CLI ----------
def parse_args():
    p = argparse.ArgumentParser(description="Lab-safe slow test simulator (stops after specified duration)")
    p.add_argument("--scenario", choices=["slow_read", "idle_keepalive", "dns_query_delay", "tls_handshake_delay"], required=True)
    p.add_argument("--port", type=int, default=None, help="Target port (optional, derived from --url if not specified)")
    p.add_argument("--url", required=True, help="Full URL for the target (e.g., http://example.com)")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--duration", type=int, default=DEFAULT_DURATION, help="Total duration of the test in seconds")
    p.add_argument("--delay", type=float, default=0.5)
    return p.parse_args()

# ---------- validate_args() ----------
def validate_args(args):
    if args.concurrency < 1:
        args.concurrency = 1
    if args.duration < 1:
        args.duration = 1
    if args.scenario == "tls_handshake_delay" and not args.port:
        print("[WARN] tls_handshake_delay usually uses port 443.")

def main():
    args = parse_args()

    # URL'den host ve port bilgilerini çıkar
    try:
        parsed_url = urlparse(args.url)
        args.host = parsed_url.hostname
        if not args.host:
            raise ValueError("Hostname could not be determined from the URL.")
        
        # Eğer port argüman olarak belirtilmemişse, URL'den al.
        if args.port is None:
            # URL'de port belirtilmişse onu kullan
            if parsed_url.port:
                args.port = parsed_url.port
            # Belirtilmemişse şemaya göre varsayılanı ata
            elif parsed_url.scheme == 'https':
                args.port = 443
            else:
                args.port = 80 # http ve diğerleri için varsayılan

    except ValueError as e:
        print(f"[ERROR] Invalid URL provided: '{args.url}'. {e}")
        return

    validate_args(args)
    print(f"{now_ts()} Starting lab-safe slow test. Scenario={args.scenario}, target={args.host}:{args.port}, concurrency={args.concurrency}")
    
    if asyncio.get_event_loop().is_running():
        loop = asyncio.get_event_loop()
    else:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    try:
        loop.run_until_complete(main_async(args))
    except KeyboardInterrupt:
        print("[*] Interrupted by user, shutting down.")
    finally:
        tasks = asyncio.all_tasks(loop=loop)
        for task in tasks:
            task.cancel()
        group = asyncio.gather(*tasks, return_exceptions=True)
        loop.run_until_complete(group)
        loop.close()
        print(f"{now_ts()} Done.")

if __name__ == "__main__":
    main()
